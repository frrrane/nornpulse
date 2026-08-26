#!/usr/bin/env python3
"""
Publish an existing vertical video to a channel, with grounded tags and a
calibrated forecast.

For video that this pipeline did not make. A SlopTokDaily clip comes out of
Grok Imagine already 9:16 and six seconds long; running it through a
16:9-to-9:16 clip extractor would be nonsense. But uploading it by hand
through YouTube Studio throws away the three things worth having:

  * tags chosen from the clip and validated against what is trending now,
    instead of one unsearchable blob of hashtags (63% of SlopTokDaily's
    existing tag entries are a single space-separated string, which matches
    nothing)
  * a reach forecast recorded *before* publication, so it can be wrong
    in public rather than adjusted afterwards
  * an outcome row that stats sync fills in, which is what makes the
    forecast checkable at all

Publications default to source='external' so they never count toward
NornPulse's own track record; pass --source generated for a file this
pipeline did make. The forecast is logged and graded either way: a
forecast is a claim about a channel, and any video that channel publishes
tests it.

    python publish_file.py slop.mp4 --title "..." --channel sloptokdaily --stage
    python publish_file.py slop.mp4 --title "..." --dry-run

--stage emails the clip for approval instead of uploading it, and is the
reviewed path; check_approvals.py picks the reply up. Without it the
upload happens immediately.

Uploading costs 1,600 YouTube quota units against a 10,000/day budget, so
roughly six uploads a day is the ceiling.
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path("output_clips")
SHORT_MAX_SEC = 60


def probe(video: Path):
    """Duration and dimensions, or None if ffprobe cannot read the file."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=60, check=True).stdout
        data = json.loads(out)
        stream = (data.get("streams") or [{}])[0]
        return {
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
            "duration": float((data.get("format") or {}).get("duration") or 0.0),
        }
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", help="path to the vertical video file")
    ap.add_argument("--title", required=True, help="video title")
    ap.add_argument("--description", default="", help="video description")
    ap.add_argument("--channel", default=None,
                    help="channel slug from channels.json (default: primary)")
    ap.add_argument("--privacy", default="public",
                    choices=["private", "unlisted", "public"])
    ap.add_argument("--thumbnail", default=None, help="optional custom thumbnail")
    ap.add_argument("--hook-type", default="", help="hook taxonomy label, if known")
    ap.add_argument("--stage", action="store_true",
                    help="email for approval instead of uploading; reply "
                         "APPROVE, then run check_approvals.py")
    ap.add_argument("--source", default="external",
                    choices=["external", "generated"],
                    help="'generated' if this pipeline made the file, so it "
                         "counts toward NornPulse's own track record")
    ap.add_argument("--no-guard", action="store_true",
                    help="skip the rights check on the title and tags")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the tags and forecast, upload nothing")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    from agent import calibration as cal
    from agent import channels as chans
    from agent.norn_publisher import NornPublisher, PublishError
    from agent.urdr_analytics import UrdrAnalytics
    from agent.verdandi_orchestrator import unique_clip_id

    video = Path(args.video)
    if not video.exists():
        print(f"❌ No such file: {video}")
        return 1

    try:
        channel = chans.get_channel(args.channel)
    except KeyError as e:
        print(f"❌ {e}")
        return 1

    print(f"📺 Channel: {channel.slug} ({channel.title}) — {channel.youtube_channel_id}")
    print(f"🎬 {video.name}")

    info = probe(video)
    if info:
        ratio = (info["width"] / info["height"]) if info["height"] else 0
        print(f"   {info['width']}x{info['height']}  {info['duration']:.1f}s")
        # Warnings, not errors: YouTube accepts these, they just will not be
        # treated as a Short, and the channel's whole history is Shorts.
        if info["duration"] > SHORT_MAX_SEC:
            print(f"   ⚠️  longer than {SHORT_MAX_SEC}s — YouTube will not shelve "
                  f"this as a Short")
        if ratio > 1.0:
            print(f"   ⚠️  landscape ({ratio:.2f}:1) — Shorts expects 9:16 (0.56:1)")
    else:
        print("   ⚠️  ffprobe could not read this file; publishing anyway")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # The prefix follows --source, because the id is what shows up in the
    # approval email and in ClickHouse, and calling a clip this pipeline
    # generated "ext_" is a lie told in the one place someone reads it.
    prefix = "gen" if args.source == "generated" else "ext"
    clip_id = unique_clip_id(f"{prefix}_{video.stem}"[:48], OUTPUT_DIR)

    # The minimum a clip needs to be tagged: tag selection reads the title
    # and caption, and nothing else about a video it did not produce.
    clip = {
        "clip_id": clip_id,
        "hook_title": args.title,
        "social_caption": args.description,
        "hook_type": args.hook_type,
        "has_subtitles": False,
    }

    publisher = NornPublisher(channel)
    tags, decisions = publisher._tags_for(clip, args.title, args.description)
    measured = [d.choice for d in decisions if d.level == "measured"]
    print(f"\n🏷️  tags ({len(tags)}): {', '.join(tags)}")
    if measured:
        print(f"    measured against trending: {', '.join(measured)}")

    # The metadata infringes on its own: a title reproducing a property is a
    # claim whether or not a single frame does. The footage itself is not
    # checked here — this path publishes video the pipeline did not make and
    # cannot inspect, which is exactly the limit the verdict states.
    if not args.no_guard:
        from agent import watchdog as wd
        verdict = wd.check_clip(clip, tags=tags)
        print()
        print(wd.describe(verdict))
        if verdict.blocked and not args.dry_run:
            print("\n⛔ Not publishing. Change the title, or pass --no-guard if "
                  "you hold the rights.")
            return 1

    forecast = cal.calibrated_forecast(channel, has_subtitles=False) or {}
    if forecast:
        print(f"\n📊 forecast p50 {forecast['p50']:,.0f} views "
              f"(p10-p90 {forecast['p10']:,.0f}-{forecast['p90']:,.0f})")
        k = forecast.get("calibration")
        if k:
            print(f"    {k['basis']}"
                  + ("" if k["confident"] else "  ⚠️ thin history"))

    if args.dry_run:
        print(f"\n(dry run — nothing uploaded, clip_id would be {clip_id})")
        return 0

    if args.stage:
        # The sidecar goes down before the email does. check_approvals.py
        # looks the clip up by id when the reply lands, so an approval whose
        # sidecar was never written is a dead end — the reply is read, the
        # clip cannot be found, and the decision is lost.
        clip_record = dict(clip)
        clip_record.update({
            "output_video_path": str(video.resolve()),
            "virality_score": 0.0,
            "source": args.source,
            "tags": tags,
            "thumbnail_path": args.thumbnail,
        })
        if not args.no_guard:
            clip_record["rights_check"] = (
                f"{verdict.level.upper()} — {verdict.summary()}")
            clip_record["rights_not_checked"] = ", ".join(verdict.checks_not_run)
        else:
            clip_record["rights_check"] = "skipped (--no-guard)"
        if forecast:
            clip_record["forecast_p50"] = f"{forecast['p50']:,.0f} views"
            clip_record["forecast_range"] = (
                f"{forecast['p10']:,.0f} - {forecast['p90']:,.0f} views")

        sidecar = OUTPUT_DIR / f"{clip_id}_metadata.json"
        sidecar.write_text(json.dumps(clip_record, indent=2), encoding="utf-8")

        print(f"\n📧 Emailing {clip_id} for approval...")
        ok = publisher.send_gmail_staged_approval(
            clip_id=clip_id, title=args.title, virality=0.0,
            video_path=str(video), clip=clip_record)
        if not ok:
            print(f"❌ Could not send the staging email — see logs. The clip is "
                  f"still at {video}")
            return 1
        print("✅ sent. Reply APPROVE or REJECT, then run:")
        print(f"   python check_approvals.py --channel {channel.slug} "
              f"--privacy {args.privacy}")
        return 0

    print(f"\n⚠️  Uploading directly, without review. --stage is the reviewed path.")
    print(f"\n⬆️  Uploading as {args.privacy}...")
    try:
        res = publisher.upload_to_youtube_shorts(
            video_path=video,
            title=args.title,
            description=args.description,
            privacy_status=args.privacy,
            thumbnail_path=args.thumbnail,
            clip=clip,
            source=args.source,
        )
    except PublishError as e:
        print(f"❌ {e}")
        return 1

    print(f"✅ {res['url']}")

    try:
        UrdrAnalytics().log_published_outcome(
            clip_id=clip_id,
            youtube_video_id=res["video_id"],
            youtube_url=res["url"],
            hook_type=args.hook_type or "unknown",
            predicted_virality_score=0.0,
            predicted_3s_retention_pct=0.0,
            forecast_views_p50=float(forecast.get("p50", 0.0)),
            forecast_views_p90=float(forecast.get("p90", 0.0)),
        )
        print("   logged forecast — run sync_stats.py in a few days to grade it")
    except Exception as e:
        print(f"   ⚠️  published fine, but could not log the outcome row: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
