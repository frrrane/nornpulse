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
        print(f"  {'hook_pattern':20s} {'windows':7s} Title-pattern hooks from English "
              f"titles, sampled across uploader ranges.")
        return 0

    # hook_pattern is not a generic Fact: it is the only one that reads the
    # title column, so it needs uploader-window sampling and a language
    # filter rather than the shared hash-modulo path.
    want_hooks = args.dimension in (None, "hook_pattern")
    if args.dimension:
        facts = [f for f in facts if f.dimension == args.dimension]
        if not facts and not want_hooks:
            raise SystemExit(f"❌ Unknown dimension '{args.dimension}'. Try --list.")
    if args.divisor:
        facts = [gb.Fact(f.dimension, f.bucket_expr, f.extra_filter, args.divisor, f.note)
                 for f in facts]

    print(f"🌍 Materialising {len(facts)} fact(s) from the public YouTube dataset "
          f"(4.56B rows). Each takes roughly a minute.\n")

    results = gb.materialise_all(facts) if facts else {}

    if want_hooks:
        hooks = gb.materialise_hook_patterns()
        results["hook_pattern"] = hooks
        if hooks is not None:
            band = hooks[hooks["size_band"] == "0-100"].sort_values(
                "median_views", ascending=False)
            print(f"✅ hook_pattern: {len(hooks)} bucket(s), "
                  f"{int(hooks['sample_videos'].sum()):,} English videos classified")
            for _, r in band.iterrows():
                print(f"      [   0-100] {str(r['bucket']):20s} "
                      f"median_views={r['median_views']:>9,.0f}  n={int(r['sample_videos']):,}")
    ok = [d for d, df in results.items() if df is not None]
    skipped = [d for d, df in results.items() if df is None]

    for dimension, df in results.items():
        if dimension == "hook_pattern":
            continue                      # already printed above, in band order
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
