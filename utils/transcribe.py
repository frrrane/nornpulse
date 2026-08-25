# utils/transcribe.py
"""
⚡ NornPulse: Native Multimodal Video Transcription with Strict Timestamp Enforcement
"""

import os
import logging
from pathlib import Path
from google import genai
from google.genai import types
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv(override=True)
logger = logging.getLogger("nornpulse.transcribe")

# Per-source cache. A transcript belongs to one video and nothing else.
CACHE_DIR = Path("sample_data/transcripts")

class TranscriptionUnavailable(RuntimeError):
    """Raised when a transcript could not be produced for THIS video."""


def _cached_transcript_for(video_path: str) -> str | None:
    """
    The cached transcript, but only if it belongs to this video.

    The cache is keyed on the source file, because a transcript is only
    valid for the video it was made from. The previous version returned
    whatever happened to be in one shared file — and, failing that, four
    hardcoded lines about plants — so a failed API call captioned an
    unrelated video with someone else's words while overwriting the real
    cached transcript on its way out.
    """
    path = _cache_path_for(video_path)
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if "[" in content and ":" in content:
            logger.info(f"Using cached transcript for {Path(video_path).name}.")
            return content
    return None


def _cache_path_for(video_path: str) -> Path:
    return CACHE_DIR / f"{Path(video_path).stem}_transcript.txt"

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_or_create_transcript(video_path: str) -> str:
    cached = _cached_transcript_for(video_path)
    if cached:
        return cached

    from agent import genai_client as gc

    api_key = os.getenv("GEMINI_API_KEY")
    if not gc.use_vertex() and not api_key:
        raise TranscriptionUnavailable(
            "GEMINI_API_KEY is not set, so this video cannot be transcribed. "
            "Refusing to substitute another video's transcript.")

    # The video goes inline as bytes rather than through the Files API,
    # which is what makes this call portable: Vertex has no Files API at
    # all. The ceiling is the request size, so a long source can still be
    # refused here where an upload would have coped.
    client, transcribe_model = gc.client_for("gemini-3.6-flash", api_key=api_key)
    logger.info(f"Reading {video_path} locally for GenAI analysis...")
    
    try:
        with open(video_path, "rb") as f:
            video_bytes = f.read()
            
        # Using chat/structured approach or explicit system instructions to enforce timestamps
        response = client.models.generate_content(
            model=transcribe_model,
            contents=[
                types.Part.from_bytes(data=video_bytes, mime_type="video/mp4"),
                (
                    "You are a professional closed-captioning engine. "
                    "Watch the video and output a transcript. "
                    "EVERY SINGLE LINE MUST START WITH A TIMESTAMP FORMATTED EXACTLY "
                    "LIKE THIS: [MM:SS.mmm] — to the millisecond, at the exact moment "
                    "the first word of that line is spoken. Whole-second timestamps "
                    "round every caption to the nearest second, which is visibly out "
                    "of sync with the speech.\n"
                    "Start a new line at each natural sentence or clause boundary, so "
                    "a line is never left hanging mid-phrase.\n"
                    "Example:\n[00:00.480] First sentence here.\n[00:04.920] Second sentence here."
                )
            ]
        )
        
        transcript_text = response.text.strip() if response and response.text else ""
        
        # Verify timestamps exist in output
        if transcript_text and "[" in transcript_text and ":" in transcript_text:
            path = _cache_path_for(video_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(transcript_text, encoding="utf-8")
            logger.info(f"✨ Transcribed and cached to {path.name}.")
            return transcript_text
        raise TranscriptionUnavailable(
            "The transcription response carried no timestamps, so it cannot be "
            "used for captioning.")

    except TranscriptionUnavailable:
        raise
    except Exception as e:
        # Deliberately not falling back to another video's transcript: a
        # wrong transcript produces a clip that renders perfectly and says
        # the wrong thing, which is worse than a visible failure.
        raise TranscriptionUnavailable(f"Transcription failed for {video_path}: {e}") from e