#!/usr/bin/env python3
# sync_retention.py
"""
Pull owner-only YouTube Analytics data back into ClickHouse.

`agent/yt_analytics.py` has existed in this codebase for a while but is
never called anywhere: it wraps the YouTube Analytics API, the only source
for retention curves and hook-window retention (the share of viewers still
watching after the opening) -- the thing that answers *why* a clip did what
it did, not just how many views it got. This script is the missing caller.

    python sync_retention.py                    # sync everything reportable
    python sync_retention.py --dry-run           # show what would be written
    python sync_retention.py --min-age-days 5    # default is 3

Needs each channel's token re-authorized with the yt-analytics.readonly
scope first:

    python reauth_youtube.py --channel <slug>

That needs a local browser and an open port 8080, so it cannot run in a
container or over SSH -- it has to be run by hand, once per channel. Until
a channel's token carries the scope, its clips are reported as
"not reportable on any re-authed channel" rather than silently skipped, so
it is clear which is the missing step.

published_clip_outcomes has no channel_slug column, so this tries every
configured channel's credentials against each video: the Analytics API
scopes to channel==MINE, so a channel that does not own a given video
simply gets nothing back for it. That avoids a schema change to answer a
question the API already answers implicitly.

Writes to its own table, owner_retention_diagnostics, rather than the
avg_3s/15s/30s columns on video_hook_retention -- those are second-based
and were sized for longer video, not a 6-15s Short. Wiring real retention
into hook-type benchmarks and forecast calibration is a deliberate next
step, not this script's job; this one's job is only to get the real
numbers flowing and visible.

This project's OAuth consent screen is in Testing, so refresh tokens
expire after 7 days. Unlike sync_stats.py (an API key, runs unattended
indefinitely), this needs a channel re-authed recently enough -- schedule
it if you like, but expect "not reportable" on every channel once a week
until the app leaves Testing.
"""

import argparse
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    ap.add_argument("--min-age-days", type=int, default=3,
                     help="skip clips published more recently than this "
                          "(YouTube Analytics lags publication by a day or more)")
    args = ap.parse_args()

    import agent.clickhouse_mcp_client as ch
    from agent import channels as chans
    from agent import yt_analytics as yta
    from agent.norn_publisher import NornPublisher
    from agent.urdr_analytics import UrdrAnalytics

    urdr = UrdrAnalytics()
    if not urdr.is_connected():
        print(f"❌ ClickHouse unavailable: {urdr.connection_error}")
        return 1

    ch.run_query("""
    CREATE TABLE IF NOT EXISTS owner_retention_diagnostics (
        video_id String,
        channel_slug LowCardinality(String),
        hook_retention Nullable(Float32),
        avg_view_percentage Nullable(Float32),
        findings String,
        synced_at DateTime DEFAULT now()
    ) ENGINE = MergeTree()
    ORDER BY (channel_slug, video_id, synced_at);
    """)

    try:
        clips = ch.run_query_df("""
            SELECT youtube_video_id, published_at
            FROM published_clip_outcomes
            ORDER BY youtube_video_id, published_at DESC
            LIMIT 1 BY youtube_video_id
        """)
    except Exception as e:
        print(f"❌ Could not list published clips: {ch._unwrap_exception(e)[:160]}")
        return 1

    channel_list = chans.list_channels()
    creds_cache: dict = {}

    def creds_for(channel):
        """Cached per channel: tried once, not retried per video."""
        if channel.slug not in creds_cache:
            try:
                creds_cache[channel.slug] = NornPublisher(channel)._get_youtube_credentials()
            except Exception as e:
                print(f"  ⚠️  {channel.slug}: no usable credentials ({str(e)[:100]})")
                creds_cache[channel.slug] = None
        return creds_cache[channel.slug]

    cutoff = date.today() - timedelta(days=args.min_age_days)
    end_date = date.today().isoformat()
    synced = skipped_young = skipped_thin = 0

    for _, row in clips.iterrows():
        video_id = row["youtube_video_id"]
        published_at = row["published_at"]
        pub_date = (published_at.date() if hasattr(published_at, "date")
                    else date.fromisoformat(str(published_at)[:10]))
        if pub_date > cutoff:
            print(f"  {video_id}: too recent (published {pub_date}) — skipping")
            skipped_young += 1
            continue

        # Try each channel's creds until one actually owns this video.
        result = owning_channel = None
        for channel in channel_list:
            creds = creds_for(channel)
            if creds is None:
                continue
            diag = yta.diagnose(creds, video_id, pub_date.isoformat(), end_date)
            if diag.get("reportable"):
                result, owning_channel = diag, channel
                break

        if result is None:
            print(f"  {video_id}: not reportable on any re-authed channel "
                  f"(too few viewers, or no channel re-authed yet)")
            skipped_thin += 1
            continue

        hook_retention = result.get("hook_retention")
        avg_pct = (result.get("overview") or {}).get("averageViewPercentage")
        findings = "; ".join(result.get("findings", []))
        print(f"  {video_id} ({owning_channel.slug}): "
              f"hook_retention={hook_retention} avg_view_pct={avg_pct} — {findings}")

        if not args.dry_run:
            ch.run_query(
                "INSERT INTO owner_retention_diagnostics "
                "(video_id, channel_slug, hook_retention, avg_view_percentage, findings) VALUES ("
                + ", ".join([
                    ch.sql_literal(video_id),
                    ch.sql_literal(owning_channel.slug),
                    ch.sql_literal(hook_retention),
                    ch.sql_literal(avg_pct),
                    ch.sql_literal(findings),
                ]) + ")"
            )
        synced += 1

    print(f"\n{'(dry run) ' if args.dry_run else ''}"
          f"{synced} synced, {skipped_young} too young, {skipped_thin} not reportable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
