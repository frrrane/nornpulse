# agent/publications.py
"""
⚡ NornPulse: The publish event (publications.py)
Norn Labs (nornlabs.ai)

One row per clip that actually went out, describing the conditions it went
out under: which channel, how big that channel was at the time, which size
band that put it in, and which tags it carried.

This is deliberately separate from published_clip_outcomes. That table is
append-only and re-inserted in full on every stats sync, so every column on
it has to be manually carried forward by the sync — a pattern that has
already silently dropped the forecast once and restamped published_at once,
and would drop these columns a third time. Nothing here ever changes after
publication, so it is written exactly once and joined on youtube_video_id.

The channel columns are what make the comparison possible. Both of the
channels in play are in the 0-100 subscriber band, so the interesting axis
between them is content type rather than size — but the band is recorded
anyway, because it is the thing that would change the advice if it ever
diverged, and reconstructing it later from a subscriber count that has
since moved is not possible.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from agent import clickhouse_mcp_client as ch
from agent import provenance as pv
from agent.global_benchmarks import size_band_for

logger = logging.getLogger(__name__)

TABLE = "clip_publications"


def ensure_table() -> None:
    ch.run_query(f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        clip_id String,
        youtube_video_id String,
        channel_slug LowCardinality(String),
        youtube_channel_id String,
        channel_subscribers UInt32,
        size_band LowCardinality(String),
        category_id LowCardinality(String),
        hook_type LowCardinality(String),
        tags Array(String),
        measured_tags Array(String),
        source LowCardinality(String) DEFAULT 'pipeline',
        published_at DateTime DEFAULT now()
    ) ENGINE = MergeTree()
    ORDER BY (channel_slug, youtube_video_id);
    """)
    # Older rows predate the column; adding it separately keeps an existing
    # table usable instead of requiring a drop.
    ch.run_query(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        f"source LowCardinality(String) DEFAULT 'pipeline'")


def _array(values: Sequence[str]) -> str:
    return "[" + ", ".join(ch.sql_literal(v) for v in values) + "]"


def record_publication(
    clip_id: str,
    youtube_video_id: str,
    channel,
    tags: Sequence[str] = (),
    decisions: Optional[Sequence[pv.Decision]] = None,
    hook_type: str = "",
    source: str = "pipeline",
) -> bool:
    """
    Record that a clip was published, and under what conditions.

    source distinguishes clips this pipeline generated from videos merely
    published through it. Both are worth recording — a forecast made for a
    channel is testable against any video that channel publishes — but
    counting externally-made videos toward NornPulse's own track record
    would credit it with work it did not do.

    Never raises. A clip that uploaded successfully must not be reported as
    a failure because its bookkeeping row did not land — the video is
    already public at this point, and the caller's error path would be
    wrong about what actually happened.
    """
    measured = [d.choice for d in (decisions or []) if d.level == pv.MEASURED]
    try:
        ensure_table()
        ch.run_query(
            f"INSERT INTO {TABLE} (clip_id, youtube_video_id, channel_slug, "
            f"youtube_channel_id, channel_subscribers, size_band, category_id, "
            f"hook_type, tags, measured_tags, source) VALUES ("
            + ", ".join([
                ch.sql_literal(clip_id),
                ch.sql_literal(youtube_video_id),
                ch.sql_literal(channel.slug),
                ch.sql_literal(channel.youtube_channel_id),
                ch.sql_literal(int(channel.subscribers)),
                ch.sql_literal(size_band_for(channel.subscribers)),
                ch.sql_literal(channel.profile.category_id),
                ch.sql_literal(hook_type or ""),
                _array(list(tags)),
                _array(measured),
                ch.sql_literal(source),
            ])
            + ")"
        )
        logger.info(
            f"Recorded publication {clip_id} -> {youtube_video_id} "
            f"on {channel.slug} [{source}] ({len(tags)} tags, {len(measured)} measured)"
        )
        return True
    except Exception as e:
        logger.warning(
            f"Could not record publication for {clip_id}: {ch._unwrap_exception(e)[:160]}")
        return False


# --- the outcome join ------------------------------------------------------
#
# Latest stats per video, deduplicated the same way get_published_outcomes
# does it: published_clip_outcomes appends a new row on every sync, so a
# naive join multiplies every publication by its sync count.

_LATEST_OUTCOMES = """
    SELECT youtube_video_id, actual_view_count, actual_like_count, published_at
    FROM (
        SELECT youtube_video_id, actual_view_count, actual_like_count, published_at
        FROM published_clip_outcomes
        WHERE NOT video_unavailable
        ORDER BY youtube_video_id, row_written_at DESC
        LIMIT 1 BY youtube_video_id
    )
"""


def channel_comparison() -> pd.DataFrame:
    """
    Our own published clips, side by side by channel.

    Thin evidence by construction — a few dozen clips cannot settle
    anything, and this is a calibration check rather than a result. The
    clip count is returned alongside every figure so the UI can say so
    rather than presenting a median of three as a finding.
    """
    try:
        return ch.run_query_df(f"""
            SELECT p.channel_slug AS channel,
                   any(p.size_band) AS size_band,
                   count() AS clips,
                   round(median(o.actual_view_count)) AS median_views,
                   round(avg(o.actual_view_count)) AS mean_views,
                   sum(o.actual_view_count) AS total_views,
                   round(100 * avg(o.actual_like_count / nullIf(o.actual_view_count, 0)), 2) AS like_rate_pct
            FROM {TABLE} AS p
            INNER JOIN ({_LATEST_OUTCOMES}) AS o
                ON o.youtube_video_id = p.youtube_video_id
            GROUP BY p.channel_slug
            ORDER BY clips DESC
        """)
    except Exception as e:
        logger.warning(f"Could not compare channels: {ch._unwrap_exception(e)[:160]}")
        return pd.DataFrame()


def tag_lift(min_clips: int = 3, channel_slug: Optional[str] = None) -> pd.DataFrame:
    """
    Median reach of our own clips by tag.

    min_clips exists so the UI can decline to show a tag that has only been
    tried once or twice: with this little data a single lucky clip would
    otherwise present as a tag that works.
    """
    where = (f"WHERE p.channel_slug = {ch.sql_literal(channel_slug)}"
             if channel_slug else "")
    try:
        return ch.run_query_df(f"""
            SELECT tag,
                   count() AS clips,
                   round(median(o.actual_view_count)) AS median_views
            FROM {TABLE} AS p
            ARRAY JOIN p.tags AS tag
            INNER JOIN ({_LATEST_OUTCOMES}) AS o
                ON o.youtube_video_id = p.youtube_video_id
            {where}
            GROUP BY tag
            HAVING clips >= {int(min_clips)}
            ORDER BY median_views DESC
        """)
    except Exception as e:
        logger.warning(f"Could not read tag lift: {ch._unwrap_exception(e)[:160]}")
        return pd.DataFrame()


def publication_counts() -> Dict[str, int]:
    """Clips published per channel, whether or not stats have synced yet."""
    try:
        df = ch.run_query_df(
            f"SELECT channel_slug, uniqExact(youtube_video_id) AS clips "
            f"FROM {TABLE} GROUP BY channel_slug")
    except Exception as e:
        logger.warning(f"Could not count publications: {ch._unwrap_exception(e)[:160]}")
        return {}
    if df.empty:
        return {}
    return {str(r["channel_slug"]): int(r["clips"]) for _, r in df.iterrows()}
