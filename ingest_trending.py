# ingest_trending.py
"""
Snapshot YouTube's live trending chart into ClickHouse.

Gives the warehouse a current layer next to the frozen 2021 public
dataset: what is travelling right now, which tags carry it, and — via
contentDetails.duration — actual Shorts rather than all video.

    python ingest_trending.py [--regions US,GB] [--max 50] [--dry-run]

Costs 1 API quota unit per region against a 10,000/day default.
Run it on a schedule to build a time series.
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
    ap.add_argument("--regions", default="US", help="comma-separated region codes")
    ap.add_argument("--max", type=int, default=50, help="videos per region (max 50)")
    ap.add_argument("--category", help="restrict to one videoCategoryId (e.g. 28 = Science & Tech)")
    ap.add_argument("--shorts", action="store_true",
                    help="collect Shorts instead of the trending chart. There is no "
                         "Shorts chart in the API, so this searches recent short "
                         "videos ordered by view count -- a different claim, stored "
                         "under its own source. Costs 100 quota units per region "
                         "against 10,000/day, versus 1 for the chart.")
    ap.add_argument("--query", help="with --shorts, restrict the search (e.g. '#aislop')")
    ap.add_argument("--days", type=int, default=7,
                    help="with --shorts, how recent the videos must be")
    ap.add_argument("--dry-run", action="store_true", help="fetch and summarise, store nothing")
    args = ap.parse_args()

    from googleapiclient.discovery import build
    from agent import trending_ingest as ti
    from agent.norn_publisher import NornPublisher

    youtube = build("youtube", "v3", credentials=NornPublisher()._get_youtube_credentials())
    regions = [r.strip().upper() for r in args.regions.split(",") if r.strip()]

    all_rows, stored = [], 0
    for region in regions:
        try:
            if args.shorts:
                rows = ti.fetch_trending_shorts(
                    youtube, region=region, max_results=args.max,
                    query=args.query, days=args.days)
            else:
                rows = ti.fetch_trending(youtube, region=region, max_results=args.max,
                                         category_id=args.category)
        except Exception as e:
            print(f"❌ {region}: {e}")
            continue

        shorts = sum(r["is_short"] for r in rows)
        tagged = sum(bool(r["tags"]) for r in rows)
        label = "shorts search" if args.shorts else "trending chart"
        print(f"📈 {region} ({label}): {len(rows)} videos, {shorts} shorts, "
              f"{tagged} with tags exposed")
        all_rows += rows

    if not all_rows:
        print("❌ Nothing fetched.")
        return 1

    top = sorted(all_rows, key=lambda r: r["view_count"], reverse=True)[:5]
    print("\nTop by views in this snapshot:")
    for r in top:
        kind = "SHORT" if r["is_short"] else f"{r['duration_sec']}s"
        print(f"  {r['view_count']:>12,}  [{kind:>5s}]  {r['title'][:52]}")

    if args.dry_run:
        print("\n(dry run — nothing stored)")
        return 0

    stored = ti.store_snapshot(all_rows)
    print(f"\n✅ Stored {stored} row(s) in ClickHouse.")

    tags = ti.top_tags(limit=12)
    if not tags.empty:
        print("\nMost common tags in this snapshot:")
        for _, t in tags.iterrows():
            print(f"  {t['tag'][:32]:34s} {int(t['videos']):>3d} videos  "
                  f"median {int(t['median_views']):,} views")
    else:
        print("\n⚠️  No tags available — YouTube did not expose tags for these videos.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
