"""
⚡ NornPulse: Lightweight Transcription (Chromebook-friendly)
Uses faster-whisper with tiny/base models to save disk space.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nornpulse.transcribe")


def transcribe_video(
    video_path: str | Path,
    model_size: str = "tiny",          # "tiny" (\~75 MB) or "base" (\~150 MB)
    language: Optional[str] = "en",
    output_txt: Optional[str] = None,
) -> str:
    """
    Returns a timestamped transcript in Verðandi format:
    [00:00 - 00:12] text...
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError("faster-whisper is not installed. Run: pip install faster-whisper")

    logger.info(f"Loading faster-whisper model '{model_size}' (this may download once)...")
    # device="cpu" and compute_type="int8" are best for Chromebooks
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    logger.info(f"Transcribing {video_path.name}...")
    segments, info = model.transcribe(
        str(video_path),
        language=language,
        beam_size=1,          # faster + less memory
        vad_filter=True,
    )

    lines = []
    for seg in segments:
        start = _sec_to_mmss(seg.start)
        end = _sec_to_mmss(seg.end)
        text = seg.text.strip()
        if text:
            lines.append(f"[{start} - {end}] {text}")

    transcript = "\n".join(lines)

    if output_txt:
        Path(output_txt).write_text(transcript, encoding="utf-8")
        logger.info(f"Transcript saved → {output_txt}")

    return transcript


def _sec_to_mmss(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def get_or_create_transcript(
    video_path: str | Path,
    cache_dir: str = "sample_data",
    force: bool = False,
    model_size: str = "tiny",
) -> str:
    """
    Returns transcript, using a cache file so we don't re-transcribe every time.
    """
    video_path = Path(video_path)
    cache_path = Path(cache_dir) / f"{video_path.stem}_transcript.txt"

    if cache_path.exists() and not force and cache_path.stat().st_size > 50:
        logger.info(f"Using cached transcript: {cache_path}")
        return cache_path.read_text(encoding="utf-8")

    return transcribe_video(
        video_path,
        model_size=model_size,
        output_txt=str(cache_path),
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m utils.transcribe path/to/video.mp4 [tiny|base]")
        sys.exit(1)

    path = sys.argv[1]
    size = sys.argv[2] if len(sys.argv) > 2 else "tiny"
    text = get_or_create_transcript(path, force=True, model_size=size)
    print("\n===== GENERATED TRANSCRIPT =====\n")
    print(text)