#!/usr/bin/env python3
"""
Ingest a channel's existing published videos and their statistics.

Reads public data with an API key — no OAuth, so this runs unattended and
does not expire the way the project's Testing-mode refresh tokens do. Costs
roughly three quota units per channel against a 10,000/day budget, which is
negligible next to the 1,600 a single upload costs.

    python ingest_channel_history.py                 # every configured channel
    python ingest_channel_history.py --channel sloptokdaily
    python ingest_channel_history.py --calibrate     # also print the
                                                     # benchmark comparison
"""

import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from agent import channel_history as chist
from agent import channels as chans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", help="Slug to ingest. Default: all configured channels.")
    parser.add_argument("--max-videos", type=int, default=500)
    parser.add_argument("--calibrate", action="store_true",
                        help="Compare actual reach against the global size-band benchmark.")
    parser.add_argument("--shorts-only", action="store_true",
                        help="Calibrate against Shorts only (<=60s).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s")

    try:
        targets = ([chans.get_channel(args.channel)] if args.channel
                   else chans.list_channels())
    except KeyError as e:
        print(f"❌ {e}")
        return 1

    targets = [c for c in targets if c.youtube_channel_id]
    if not targets:
        print("❌ No channels with a youtube_channel_id configured. See channels.json.")
        return 1

    failures = 0
    for channel in targets:
        print(f"\n📥 {channel.slug} ({channel.title}) — {channel.youtube_channel_id}")
        try:
            summary = chist.ingest(channel, max_videos=args.max_videos)
        except Exception as e:
            print(f"   ❌ {e}")
            failures += 1
            continue

        print(f"   stored {summary['videos']} videos "
              f"({summary['shorts']} Shorts), {summary['subscribers']} subscribers "
              f"→ band {summary['size_band']}")
        print(f"   median views: {summary['median_views']:,.0f} all · "
              f"{summary['median_short_views']:,.0f} Shorts")

        if args.calibrate:
            cal = chist.calibration(channel.slug, shorts_only=args.shorts_only)
            if not cal:
                print("   (no history stored, cannot calibrate)")
                continue
            predicted = cal["predicted_median_views"]
            if predicted is None:
                print("   (no global benchmark for this band, cannot calibrate)")
                continue
            ratio = cal["ratio"]
            print(f"   calibration · actual {cal['actual_median_views']:,.0f} vs "
                  f"benchmark {predicted:,.0f} for band {cal['size_band']} "
                  f"(n={cal['benchmark_sample_videos']:,}) → {ratio:.2f}x")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
