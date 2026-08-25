# test_hitl.py
"""
Manual HITL staging runner: generate N clips through the real pipeline
(Urðr -> Verðandi -> Bragi/Heimdall/Mímir -> Skuld) and email each one
for human review before anything is published.

This is NOT part of the pytest suite (see pytest.ini) — it calls the
real Gemini, ClickHouse and Gmail APIs and costs money. Run it directly:

    python test_hitl.py <url|video_path> [transcript_path] [count]

A URL is downloaded and transcribed first, so staging from a fresh source
is one command. A local path skips both and needs its transcript passed
alongside (or generated on the fly if omitted).

Nothing is uploaded to YouTube here. Approving a staged clip is a
separate, deliberate step (approve_and_publish.py or the dashboard).
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_VIDEO = "sample_data/yt_input.mp4"
DEFAULT_TRANSCRIPT = "sample_data/raw_transcript.txt"
DEFAULT_COUNT = 3


def _resolve_source(source: str, transcript_path: str | None):
    """
    Accept either a URL or a local path, and return (video_path, transcript).

    Downloading and transcribing here rather than requiring a pre-prepared
    file is what makes staging from a fresh source a single command; it is
    the same path the dashboard takes.
    """
    from utils.ingest import download_youtube_video
    from utils.transcribe import get_or_create_transcript

    if source.startswith(("http://", "https://")):
        print(f"⬇️  Downloading {source} ...")
        video = Path(download_youtube_video(source))
    else:
        video = Path(source)
        if not video.exists():
            raise SystemExit(f"❌ video not found: {video}")

    if transcript_path and Path(transcript_path).exists():
        return video, Path(transcript_path).read_text(encoding="utf-8")

    print("📝 Transcribing (Gemini) ...")
    return video, get_or_create_transcript(str(video))


def stage_clips(source: str, transcript_path: str | None, target_count: int,
                subscribers: int = 0) -> int:
    from agent.verdandi_orchestrator import VerdandiOrchestrator
    from agent.norn_publisher import NornPublisher

    video, transcript_text = _resolve_source(source, transcript_path)

    print(f"🚀 Staging {target_count} clip(s) from {video.name}...")
    clips = VerdandiOrchestrator().orchestrate_generation(
        transcript_text=transcript_text,
        video_path=str(video),
        target_count=target_count,
        # The hook ranking is only meaningful within a channel-size band.
        channel_subscribers=subscribers,
        progress_callback=lambda stage, message: print(f"   [{stage}] {message}"),
    )

    if not clips:
        print("❌ Pipeline rendered no clips.")
        return 1

    publisher = NornPublisher()
    failures = 0
    for clip in clips:
        rendered = clip.get("output_video_path")
        # Sidecar JSON keeps the full record next to the render, so the
        # dashboard and the publisher agree on what was staged.
        Path(rendered).with_name(f"{clip['clip_id']}_metadata.json").write_text(
            json.dumps(clip, indent=2, default=str), encoding="utf-8")

        print(f"📧 Staging {clip['clip_id']} — {clip.get('hook_title')}...")
        # Passing the whole clip record is what fills the review table in
        # the email (caption, hook type/rank, cut range, crop/motion/grade)
        # and inlines Heimdall's cover.
        ok = publisher.send_gmail_staged_approval(
            clip_id=clip["clip_id"],
            title=clip.get("hook_title", "Untitled"),
            virality=clip.get("virality_score", 0.0),
            video_path=rendered,
            clip=clip,
        )
        print("   ✅ sent" if ok else "   ❌ send failed — see logs")
        failures += (not ok)

    print(f"\n✨ Staged {len(clips) - failures}/{len(clips)} clip(s). Check your inbox.")
    return 1 if failures else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    args = sys.argv[1:]
    source = args[0] if args else DEFAULT_VIDEO
    # The bundled transcript belongs to the bundled video and nothing else.
    # Defaulting to it for any local path would silently cut a different
    # source against the wrong words — the clips would render, and every
    # caption and hook would be about the wrong video.
    if len(args) > 1:
        transcript = args[1]
    elif source == DEFAULT_VIDEO:
        transcript = DEFAULT_TRANSCRIPT
    else:
        transcript = None
    sys.exit(stage_clips(
        source, transcript,
        int(args[2]) if len(args) > 2 else DEFAULT_COUNT,
        int(os.getenv("NORNPULSE_CHANNEL_SUBS", "0")),
    ))
