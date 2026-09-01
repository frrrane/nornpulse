# utils/transcribe.py
"""
⚡ NornPulse: Native Multimodal Video Transcription with Strict Timestamp Enforcement
"""

import os
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from google import genai
from google.genai import types
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv(override=True)
logger = logging.getLogger("nornpulse.transcribe")

# Per-source cache. A transcript belongs to one video and nothing else.
CACHE_DIR = Path("sample_data/transcripts")

# How much video goes into one transcription call.
#
# Timestamps drift over a long video. Measured on a 22-minute NASA source
# transcribed in a single call: accurate at 1, 5 and 10 minutes, then 88
# SECONDS early by the 16-minute mark -- so a clip cut there was captioned
# with words from a minute and a half earlier. Nothing downstream can
# recover from that, and three separate caption fixes were made before the
# timeline itself was checked.
#
# Chunking bounds the error instead of trying to correct it: each call sees
# a short segment, and its timestamps are offset by where that segment
# starts. Drift cannot accumulate past one chunk.
TRANSCRIBE_CHUNK_SEC = 420.0

# Below this a video is transcribed in one call, as before.
TRANSCRIBE_CHUNK_THRESHOLD_SEC = 540.0

_TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d+)?)\]")


def _shift_timestamps(text: str, offset_sec: float) -> str:
    """Rewrite every [MM:SS.mmm] in a chunk's transcript into source time."""
    def bump(m):
        total = int(m.group(1)) * 60 + float(m.group(2)) + offset_sec
        return f"[{int(total // 60):02d}:{total % 60:06.3f}]"
    return _TIMESTAMP_RE.sub(bump, text)


def _segment(video_path: str, start: float, length: float, out: Path) -> bool:
    """Cut one chunk out for transcription. Re-encoded, so the cut is exact."""
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
           "-i", str(video_path), "-c:v", "libx264", "-preset", "ultrafast",
           "-crf", "32", "-c:a", "aac", "-y", str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        return out.exists() and out.stat().st_size > 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Could not cut {start:.0f}s-{start+length:.0f}s: {str(e)[:100]}")
        return False

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


def _file_digest(path: str | Path, chunk_size: int = 1 << 20) -> str:
    """
    A short content hash of the video.

    A twin of agent.verdandi_orchestrator._file_digest, inlined rather than
    imported because that module imports this one — importing back would be
    circular. Same reasoning behind both: the pipeline reuses fixed local
    names for whatever it is working on.
    """
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def _cache_path_for(video_path: str) -> Path:
    """
    Where this video's transcript is cached, keyed on its CONTENT.

    Keyed on the filename alone, this cache handed one video another's
    words. Every download lands at the same fixed path — sample_data/
    yt_input.mp4 — so downloading a new source silently inherited the
    previous one's transcript, and the only check was whether the file
    looked like a transcript at all, which a wrong one does. That is how a
    161-second Cosmos video came to be paired with a 22-minute Artemis
    transcript: nothing lied, nothing failed, the cache just answered a
    question about a different video.

    The digest costs a read of the file. A wrong transcript costs a clip
    captioned with someone else's words, which is worse and much harder to
    notice.
    """
    stem = Path(video_path).stem
    try:
        return CACHE_DIR / f"{stem}_{_file_digest(video_path)}_transcript.txt"
    except OSError:
        # Unreadable video: fall back to the name so a caller that can still
        # transcribe is not blocked by the cache's own bookkeeping.
        return CACHE_DIR / f"{stem}_transcript.txt"

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
        duration = _duration_of(video_path)
        if duration and duration > TRANSCRIBE_CHUNK_THRESHOLD_SEC:
            transcript_text = _transcribe_in_chunks(
                video_path, duration, client, transcribe_model)
        else:
            transcript_text = _transcribe_one(
                Path(video_path).read_bytes(), client, transcribe_model)

        # Verify timestamps exist in output
        if transcript_text and "[" in transcript_text and ":" in transcript_text:
            path = _cache_path_for(video_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(transcript_text, encoding="utf-8")
            logger.info(f"\u2728 Transcribed and cached to {path.name}.")
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
        raise TranscriptionUnavailable(
            f"Transcription failed for {video_path}: {e}") from e


def _duration_of(video_path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=120).stdout.strip()
        return float(out or 0.0)
    except Exception:
        return 0.0


def _transcribe_in_chunks(video_path: str, duration: float, client, model) -> str:
    """
    Transcribe a long video as consecutive bounded segments.

    A chunk that fails to cut or comes back empty is skipped with a warning
    rather than aborting: losing seven minutes of captions is bad, losing
    the whole transcript is worse, and the gap is visible in the output.
    """
    starts = []
    t = 0.0
    while t < duration:
        starts.append(t)
        t += TRANSCRIBE_CHUNK_SEC
    logger.info(
        f"Transcribing {duration:.0f}s in {len(starts)} chunks of "
        f"{TRANSCRIBE_CHUNK_SEC:.0f}s — timestamps drift over a long video, "
        f"so each chunk is timed from its own start.")

    parts = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, start in enumerate(starts):
            length = min(TRANSCRIBE_CHUNK_SEC, duration - start)
            piece = Path(tmp) / f"chunk_{i:02d}.mp4"
            if not _segment(video_path, start, length, piece):
                continue
            text = _transcribe_one(piece.read_bytes(), client, model)
            if not text:
                logger.warning(f"Chunk {i} ({start:.0f}s) returned nothing; skipping.")
                continue
            parts.append(_shift_timestamps(text, start))
            logger.info(f"   chunk {i + 1}/{len(starts)} at {start:.0f}s ✓")
    if not parts:
        raise TranscriptionUnavailable(
            f"No chunk of {video_path} could be transcribed.")
    return "\n".join(parts)


def _transcribe_one(video_bytes: bytes, client, model) -> str:
    """One transcription call. Timestamps are relative to these bytes."""
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=video_bytes, mime_type="video/mp4"),
            (
                "You are a professional closed-captioning engine. "
                "Watch the video and output a transcript. "
                "EVERY SINGLE WORD MUST BE IMMEDIATELY PRECEDED BY A TIMESTAMP "
                "FORMATTED EXACTLY LIKE THIS: [MM:SS.mmm] — to the millisecond, at "
                "the exact moment THAT WORD is spoken, not just the first word of "
                "the line. Whole-second timestamps round every caption to the "
                "nearest second, which is visibly out of sync with the speech.\n"
                "Start a new line at each natural sentence or clause boundary, so "
                "a line is never left hanging mid-phrase.\n"
                "Example:\n"
                "[00:00.480]First [00:00.610]sentence [00:00.890]here.\n"
                "[00:04.920]Second [00:05.140]sentence [00:05.430]here."
            )
        ]
    )
    
    return response.text.strip() if response and response.text else ""
