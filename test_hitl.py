# test_hitl.py
"""
Manual HITL staging runner: generate N clips through the real pipeline
(Urðr -> Verðandi -> Bragi/Heimdall/Mímir -> Skuld) and email each one
for human review before anything is published.

This is NOT part of the pytest suite (see pytest.ini) — it calls the
real Gemini, ClickHouse and Gmail APIs and costs money. Run it directly:

    python test_hitl.py [video_path] [transcript_path] [count]

Nothing is uploaded to YouTube here. Approving a staged clip is a
separate, deliberate step (approve_and_publish.py or the dashboard).
"""

import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_VIDEO = "sample_data/yt_input.mp4"
DEFAULT_TRANSCRIPT = "sample_data/raw_transcript.txt"
DEFAULT_COUNT = 3


def stage_clips(video_path: str, transcript_path: str, target_count: int) -> int:
    from agent.verdandi_orchestrator import VerdandiADK
    from agent.norn_publisher import NornPublisher

    video, transcript = Path(video_path), Path(transcript_path)
    for label, path in (("video", video), ("transcript", transcript)):
        if not path.exists():
            print(f"❌ {label} not found: {path}")
            return 1

    print(f"🚀 Staging {target_count} clip(s) from {video.name}...")
    clips = VerdandiADK().orchestrate_generation(
        transcript_text=transcript.read_text(encoding="utf-8"),
        video_path=str(video),
        target_count=target_count,
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
    sys.exit(stage_clips(
        args[0] if len(args) > 0 else DEFAULT_VIDEO,
        args[1] if len(args) > 1 else DEFAULT_TRANSCRIPT,
        int(args[2]) if len(args) > 2 else DEFAULT_COUNT,
    ))
