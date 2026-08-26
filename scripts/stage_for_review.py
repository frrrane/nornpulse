# scripts/stage_for_review.py
"""
Manual HITL staging runner: generate N clips through the real pipeline
(Urðr -> Verðandi -> Bragi/Heimdall/Mímir -> Skuld) and email each one
for human review before anything is published.

This is NOT part of the pytest suite (see pytest.ini) — it calls the
real Gemini, ClickHouse and Gmail APIs and costs money. Run it directly:

    python scripts/stage_for_review.py <url|video_path> [transcript_path] [count]

NORNPULSE_CHANNEL selects the channel whose profile applies (caption
font, music mood, forbidden crops and motions); NORNPULSE_CHANNEL_SUBS
sets the size band the hook ranking is read within.

A URL is downloaded and transcribed first, so staging from a fresh source
is one command. A local path skips both and needs its transcript passed
alongside (or generated on the fly if omitted).

Nothing is uploaded to YouTube here. Approving a staged clip is a
separate, deliberate step (check_approvals.py or the dashboard).
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Running a file in scripts/ puts scripts/ on sys.path, not the repo root, so
# `import agent` fails with ModuleNotFoundError. pytest.ini sets pythonpath for
# the suite, which is why nothing caught this when the runner moved here — the
# tests kept passing and the script stopped working.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
                subscribers: int = 0, channel_slug: str | None = None) -> int:
    from agent import channels as chans
    from agent.verdandi_orchestrator import CLIP_MIN_SEC, CLIP_MAX_SEC
    from agent.verdandi_orchestrator import VerdandiOrchestrator
    from agent.norn_publisher import NornPublisher

    # The channel's own editorial constraints.
    #
    # This runner used to call the orchestrator with no profile at all, so
    # every avoid_crop, avoid_motion, caption_font and music_mood in
    # channels.json was silently ignored on this path -- a NASA clip came
    # back cropped blurred_background, which nornpulse explicitly forbids,
    # and captioned in the default face rather than the channel's.
    channel = chans.get_channel(channel_slug)
    profile = getattr(channel, "profile", None)
    print(f"\U0001F4FA {channel.slug} ({channel.title})")
    if profile:
        print(f"   font={getattr(profile, 'caption_font', None)!r} "
              f"music={getattr(profile, 'music_mood', None)!r} "
              f"avoid_crop={list(getattr(profile, 'avoid_crop', []) or [])}")

    video, transcript_text = _resolve_source(source, transcript_path)

    # The most-replayed graph, when the source is a URL that has one.
    #
    # This runner downloads first and then hands a local path to the
    # orchestrator, which is how it lost the evidence: only the batch path
    # fetched the heatmap, so staging a URL from here cut a long video at a
    # random offset while the README advertised cuts grounded in what
    # viewers of the source actually re-watched.
    rewatch_evidence, rewatch_peak_sec = "", None
    if source.startswith(("http://", "https://")):
        from agent import heatmap as hm
        try:
            moments = hm.fetch(source)
            rewatch_evidence = hm.describe(moments)
            found = hm.peaks(moments)
            if found:
                rewatch_peak_sec = found[0].mid_sec
                print(f"📈 most-replayed: {len(found)} peak(s), strongest at "
                      f"{found[0].as_timestamp()} — the window will centre there")
            else:
                print("📈 no usable most-replayed graph; window will be picked at random")
        except Exception as e:
            print(f"📈 could not read the most-replayed graph: {str(e)[:80]}")

    print(f"🚀 Staging {target_count} clip(s) from {video.name}...")
    clips = VerdandiOrchestrator().orchestrate_generation(
        transcript_text=transcript_text,
        video_path=str(video),
        target_count=target_count,
        source_ref=source,
        rewatch_evidence=rewatch_evidence,
        rewatch_peak_sec=rewatch_peak_sec,
        channel_profile=profile,
        caption_font=getattr(profile, "caption_font", None) if profile else None,
        # The hook ranking is only meaningful within a channel-size band.
        channel_subscribers=subscribers,
        progress_callback=lambda stage, message: print(f"   [{stage}] {message}"),
    )

    if not clips:
        print("❌ Pipeline rendered no clips.")
        return 1

    publisher = NornPublisher(channel)
    failures = 0
    for clip in clips:
        rendered = clip.get("output_video_path")
        # Sidecar JSON keeps the full record next to the render, so the
        # dashboard and the publisher agree on what was staged.
        Path(rendered).with_name(f"{clip['clip_id']}_metadata.json").write_text(
            json.dumps(clip, indent=2, default=str), encoding="utf-8")

        # Checked before it costs a human any attention. Reported, not
        # enforced: every fault on the list came from a real rejection, but
        # a clip with one is still the reviewer's call to make.
        from agent import preflight
        report = preflight.check_clip(
            clip, transcript_text=transcript_text, profile=profile,
            min_sec=CLIP_MIN_SEC, max_sec=CLIP_MAX_SEC)
        if not report.clean:
            print(report.describe())

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
        os.getenv("NORNPULSE_CHANNEL") or None,
    ))
