# publish_staged.py
"""
Publish approved staged clips to YouTube and record them in ClickHouse.

Reads the sidecar metadata JSON written by the staging run, uploads each
clip with its Verðandi-generated title/caption and Heimdall cover, then
logs an anchor row in published_clip_outcomes so `sync_actual_stats` can
later fill in real views/likes/comments — the prediction-to-ground-truth
loop the Predicted-vs-Actual table is built on.

    python publish_staged.py --channel UCxxxx --privacy public clip_1 clip_2

The channel check is not optional: the cached OAuth token is bound to
whichever account granted consent, and uploading to the wrong channel is
silent and public. --channel is verified against the live token before a
single byte is uploaded.
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path("output_clips")


def _verify_channel(publisher, expected: str, allow_interactive: bool = True) -> str:
    """
    Confirm the cached token belongs to `expected` before anything uploads.

    With allow_interactive=False (dry runs) a missing or unusable token is
    reported rather than triggering the browser consent flow — a check
    that silently opens a browser and grabs port 8080 is not a dry run,
    and it collides with a re-auth already in progress.
    """
    from googleapiclient.discovery import build
    from agent.norn_publisher import TOKEN_PATH

    if not allow_interactive and not TOKEN_PATH.exists():
        raise SystemExit(
            f"❌ No cached token at {TOKEN_PATH}, and --dry-run will not start "
            f"an interactive sign-in.\n   Authorize first: python reauth_youtube.py {expected}"
        )

    items = build("youtube", "v3", credentials=publisher._get_youtube_credentials()) \
        .channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        raise SystemExit("❌ The cached token returned no channel.")

    got, title = items[0]["id"], items[0]["snippet"].get("title", "?")
    if got != expected:
        raise SystemExit(
            f"❌ Refusing to upload. The cached token is bound to {got} ('{title}'), "
            f"not {expected}.\n   Fix it with: python reauth_youtube.py {expected}"
        )
    print(f"✅ Token verified against {got} ('{title}').")
    return title


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip_ids", nargs="+", help="clip ids with sidecar metadata in output_clips/")
    ap.add_argument("--channel", required=True, help="expected YouTube channel id")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    ap.add_argument("--dry-run", action="store_true", help="validate everything, upload nothing")
    args = ap.parse_args()

    from agent import review_queue as rq
    from agent.norn_publisher import NornPublisher, PublishError
    from agent.urdr_analytics import UrdrAnalytics

    # Load and validate every clip up front, so a bad record fails before
    # a partial batch is already live on the channel.
    clips = []
    for clip_id in args.clip_ids:
        sidecar = OUTPUT_DIR / f"{clip_id}_metadata.json"
        if not sidecar.exists():
            raise SystemExit(f"❌ No metadata for '{clip_id}' at {sidecar}")
        clip = json.loads(sidecar.read_text(encoding="utf-8"))
        video = Path(clip.get("output_video_path", ""))
        if not video.exists():
            raise SystemExit(f"❌ {clip_id}: rendered video missing at {video}")
        clips.append(clip)

    publisher = NornPublisher()
    _verify_channel(publisher, args.channel, allow_interactive=not args.dry_run)

    print(f"\n{len(clips)} clip(s) to publish as {args.privacy.upper()}:")
    for c in clips:
        print(f"  • {c['clip_id']:10s} {c.get('hook_title')}  ({c.get('virality_score')}/100)")
    if args.dry_run:
        print("\n(dry run — nothing uploaded)")
        return 0

    urdr = UrdrAnalytics()
    published, failed = [], []
    for c in clips:
        print(f"\n⬆️  Uploading {c['clip_id']} — {c.get('hook_title')}...")
        try:
            res = publisher.upload_to_youtube_shorts(
                video_path=c["output_video_path"],
                title=c.get("hook_title", "Untitled"),
                description=c.get("social_caption", ""),
                privacy_status=args.privacy,
                thumbnail_path=c.get("thumbnail_path"),
            )
        except PublishError as e:
            print(f"   ❌ {e}")
            failed.append(c["clip_id"])
            continue

        print(f"   ✨ {res['url']}  (privacy: {res['privacy_status']}, "
              f"thumbnail_set: {res['thumbnail_set']})")

        # The clip record doesn't carry a retention prediction, so look it
        # up from Urðr's benchmarks the same way the dashboard does.
        # Defaulting it to 0.0 silently emptied half of the Predicted-vs-
        # Actual comparison for every clip published from this script.
        hook_type = c.get("hook_type", "unknown")
        predicted_3s = float(c.get("predicted_3s_retention_pct") or 0.0)
        if not predicted_3s:
            bench = urdr.query_hook_retention(hook_category=hook_type, limit=1)
            predicted_3s = float(bench.iloc[0]["avg_3s_retention_pct"]) if not bench.empty else 85.0

        logged = urdr.log_published_outcome(
            clip_id=c["clip_id"],
            youtube_video_id=res["video_id"],
            youtube_url=res["url"],
            hook_type=hook_type,
            predicted_virality_score=float(c.get("virality_score", 0.0)),
            predicted_3s_retention_pct=predicted_3s,
        )
        # A telemetry miss must not read as an upload failure — the video
        # is already live at this point.
        print("   📊 logged to ClickHouse" if logged
              else "   ⚠️  ClickHouse logging failed; re-log later to keep the outcomes loop intact")
        # Record in the shared review ledger so a later email reply or
        # dashboard click can see this clip is already live and refuse to
        # publish it twice.
        rq.record_decision(
            c["clip_id"], rq.APPROVED, "published via publish_staged.py", source="cli",
            extra={"youtube_url": res["url"], "youtube_video_id": res["video_id"]},
        )
        published.append({**res, "clip_id": c["clip_id"], "logged": logged})

    Path("output_clips/published_urls.json").write_text(
        json.dumps(published, indent=2), encoding="utf-8")
    print(f"\n✨ Published {len(published)}/{len(clips)}."
          + (f" Failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
