# sync_stats.py
"""
Pull real YouTube performance back into ClickHouse.

Reads public statistics for every published clip and writes them onto the
matching published_clip_outcomes row, closing the loop from prediction to
ground truth.

    python sync_stats.py              # sync everything
    python sync_stats.py --dry-run    # show what would change

Runs unattended. It uses YOUTUBE_API_KEY rather than OAuth, because this
project's consent screen is in Testing and Google expires those refresh
tokens after 7 days — a scheduled job on the OAuth path would break every
week. Publishing still needs OAuth; reading public stats does not.

Schedule it with cron, e.g. every 6 hours:
    0 */6 * * * cd /path/to/nornpulse && venv/bin/python sync_stats.py >> sync.log 2>&1
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("CLICKHOUSE_MCP_QUERY_TIMEOUT", "120")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = ap.parse_args()

    import agent.clickhouse_mcp_client as ch
    from agent.norn_publisher import NornPublisher, PublishError
    from agent.urdr_analytics import UrdrAnalytics

    publisher = NornPublisher()
    if not publisher.youtube_api_key:
        print("⚠️  YOUTUBE_API_KEY is not set — falling back to OAuth, which "
              "cannot run unattended for more than 7 days.")

    urdr = UrdrAnalytics()
    if not urdr.is_connected():
        print(f"❌ ClickHouse unavailable: {urdr.connection_error}")
        return 1

    try:
        ids = ch.run_query_df(
            "SELECT DISTINCT youtube_video_id FROM published_clip_outcomes")
    except Exception as e:
        print(f"❌ Could not list published clips: {ch._unwrap_exception(e)[:160]}")
        return 1

    synced = unavailable = failed = 0
    for video_id in ids.get("youtube_video_id", []):
        try:
            stats = publisher.get_video_statistics(video_id)
        except PublishError:
            # Deleted, private, or never published. Recorded as unmeasurable
            # rather than as zero views, so it is excluded from the
            # cross-validation charts instead of counting as a missed
            # prediction.
            print(f"  {video_id}: not public — flagged unmeasurable")
            if not args.dry_run:
                urdr.sync_actual_stats(video_id, 0, 0, 0, unavailable=True)
            unavailable += 1
            continue
        except Exception as e:
            print(f"  {video_id}: lookup failed — {str(e)[:80]}")
            failed += 1
            continue

        print(f"  {video_id}: views={stats['view_count']:,} "
              f"likes={stats['like_count']:,} comments={stats['comment_count']:,}")
        if not args.dry_run:
            urdr.sync_actual_stats(video_id, stats["view_count"],
                                   stats["like_count"], stats["comment_count"])
        synced += 1

    print(f"\n{'(dry run) ' if args.dry_run else ''}"
          f"{synced} synced, {unavailable} unmeasurable, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
