# seed_global_benchmarks.py
"""
Materialise global YouTube facts into local ClickHouse tables.

Reads ClickHouse's public 4.56-billion-row YouTube dataset through
remoteSecure and stores compact aggregates locally, so the pipeline and
dashboard ground their decisions in real data without depending on a
public endpoint at request time.

    python seed_global_benchmarks.py [--dimension has_subtitles] [--divisor 500]

Each fact is a separate query. The playground caps execution at 120s, so
a fact that overruns is reported and skipped rather than aborting the run
— a partial set of real facts beats none.

Set CLICKHOUSE_MCP_QUERY_TIMEOUT (seconds) above the MCP default of 30
before running; 180 is comfortable.
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# The MCP server's own 30s ceiling would kill these long aggregations well
# before the server-side cap. Raise it unless the caller already has.
os.environ.setdefault("CLICKHOUSE_MCP_QUERY_TIMEOUT", "180")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dimension", help="materialise only this fact (default: all)")
    ap.add_argument("--divisor", type=int, help="override the 1/N sampling divisor")
    ap.add_argument("--list", action="store_true", help="list available facts and exit")
    args = ap.parse_args()

    from agent import global_benchmarks as gb

    facts = gb.FACTS
    if args.list:
        for f in facts:
            print(f"  {f.dimension:20s} 1/{f.divisor:<5d} {f.note}")
        return 0

    if args.dimension:
        facts = [f for f in facts if f.dimension == args.dimension]
        if not facts:
            raise SystemExit(f"❌ Unknown dimension '{args.dimension}'. Try --list.")
    if args.divisor:
        facts = [gb.Fact(f.dimension, f.bucket_expr, f.extra_filter, args.divisor, f.note)
                 for f in facts]

    print(f"🌍 Materialising {len(facts)} fact(s) from the public YouTube dataset "
          f"(4.56B rows). Each takes roughly a minute.\n")

    results = gb.materialise_all(facts)
    ok = [d for d, df in results.items() if df is not None]
    skipped = [d for d, df in results.items() if df is None]

    for dimension, df in results.items():
        if df is None:
            print(f"⏭️  {dimension}: skipped (query did not complete)")
            continue
        print(f"✅ {dimension}: {len(df)} bucket(s), "
              f"{int(df['sample_videos'].sum()):,} videos sampled")
        for _, r in df.sort_values(["size_band", "bucket"]).iterrows():
            band = str(r.get("size_band", "") or "—")
            print(f"      [{band:>8s}] {str(r['bucket']):10s} "
                  f"median_views={r['median_views']:>10,.0f}  "
                  f"views/sub={r['median_views_per_sub']:>9.3f}  like%={r['like_rate_pct']:.3f}")

    print(f"\n{len(ok)}/{len(results)} fact(s) materialised."
          + (f" Skipped: {', '.join(skipped)}" if skipped else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
