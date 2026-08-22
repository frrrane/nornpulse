# agent/trending_ingest.py
"""
⚡ NornPulse: Current YouTube trending ingestion (trending_ingest.py)
Norn Labs (nornlabs.ai)

Pulls the live "most popular" chart from the YouTube Data API and stores
each snapshot in ClickHouse, giving the warehouse a *current* layer
alongside the frozen 2021 public dataset and your own published outcomes.

Why this exists: the public 4.5-billion-row dataset was crawled in
December 2021, so it is good evidence for structural questions and no
evidence at all about what is travelling today. It also has no duration
column, so Shorts cannot be separated from long-form. The API returns
contentDetails.duration, which fixes both — this layer knows what is
viral right now, and can restrict to actual Shorts.

Quota: videos.list costs 1 unit per call against a 10,000/unit daily
default, so a snapshot of several regions is negligible.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

import agent.clickhouse_mcp_client as ch

logger = logging.getLogger("nornpulse.trending")

TABLE = "trending_snapshots"
# YouTube's own threshold for the Shorts shelf was 60s for the period this
# tooling targets; duration_sec is stored raw so a different cut can be
# applied later without re-ingesting.
SHORT_MAX_SEC = 60

_ISO_DURATION = re.compile(
    r"P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?"
)


def parse_iso_duration(value: str) -> int:
    """
    ISO 8601 duration -> seconds. Returns 0 for anything unparseable
    (live streams report P0D), so a malformed value can't crash an
    ingest run over one video.
    """
    if not value:
        return 0
    m = _ISO_DURATION.match(value)
    if not m:
        return 0
    parts = {k: int(v) for k, v in m.groupdict(default="0").items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def ensure_table() -> None:
    ch.run_query(f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        snapshot_at DateTime DEFAULT now(),
        region LowCardinality(String),
        video_id String,
        title String,
        channel_title String,
        channel_id String,
        category_id LowCardinality(String),
        published_at DateTime,
        duration_sec UInt32,
        is_short Bool,
        view_count UInt64,
        like_count UInt64,
        comment_count UInt64,
        tags Array(String),
        topics Array(String)
    ) ENGINE = MergeTree()
    ORDER BY (snapshot_at, region, video_id);
    """)


def fetch_trending(youtube, region: str = "US", max_results: int = 50,
                   category_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """One page of the live most-popular chart, normalised into flat rows."""
    request = youtube.videos().list(
        part="snippet,statistics,contentDetails,topicDetails",
        chart="mostPopular", regionCode=region,
        maxResults=min(max_results, 50),
        **({"videoCategoryId": category_id} if category_id else {}),
    )
    rows = []
    for item in request.execute().get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        duration = parse_iso_duration(item.get("contentDetails", {}).get("duration", ""))
        rows.append({
            "region": region,
            "video_id": item.get("id", ""),
            "title": snippet.get("title", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "channel_id": snippet.get("channelId", ""),
            "category_id": str(snippet.get("categoryId", "")),
            "published_at": snippet.get("publishedAt", ""),
            "duration_sec": duration,
            "is_short": 0 < duration <= SHORT_MAX_SEC,
            "view_count": int(stats.get("viewCount", 0) or 0),
            "like_count": int(stats.get("likeCount", 0) or 0),
            "comment_count": int(stats.get("commentCount", 0) or 0),
            # Tags are only exposed for some videos; an absent list is
            # normal and must not be confused with a video having none.
            "tags": snippet.get("tags") or [],
            "topics": [t.rsplit("/", 1)[-1] for t in
                       item.get("topicDetails", {}).get("topicCategories", [])],
        })
    return rows


def _array_literal(values: List[str]) -> str:
    return "[" + ", ".join(ch.sql_literal(v) for v in values) + "]"


def store_snapshot(rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    ensure_table()
    values = []
    for r in rows:
        published = (r["published_at"] or "").replace("T", " ").replace("Z", "")[:19] \
            or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        values.append("(" + ", ".join([
            ch.sql_literal(r["region"]), ch.sql_literal(r["video_id"]),
            ch.sql_literal(r["title"]), ch.sql_literal(r["channel_title"]),
            ch.sql_literal(r["channel_id"]), ch.sql_literal(r["category_id"]),
            ch.sql_literal(published), ch.sql_literal(int(r["duration_sec"])),
            ch.sql_literal(bool(r["is_short"])), ch.sql_literal(int(r["view_count"])),
            ch.sql_literal(int(r["like_count"])), ch.sql_literal(int(r["comment_count"])),
            _array_literal(r["tags"]), _array_literal(r["topics"]),
        ]) + ")")
    ch.run_query(
        f"INSERT INTO {TABLE} (region, video_id, title, channel_title, channel_id, "
        f"category_id, published_at, duration_sec, is_short, view_count, like_count, "
        f"comment_count, tags, topics) VALUES " + ", ".join(values)
    )
    return len(rows)


def top_tags(limit: int = 20, shorts_only: bool = False, region: Optional[str] = None) -> pd.DataFrame:
    """
    Most frequent tags across the most recent snapshot, with the median
    reach of the videos carrying them. This is what feeds a social_caption
    with hashtags that are actually travelling rather than invented.
    """
    filters = ["snapshot_at = (SELECT max(snapshot_at) FROM " + TABLE + ")"]
    if shorts_only:
        filters.append("is_short")
    if region:
        filters.append(f"region = {ch.sql_literal(region)}")
    try:
        return ch.run_query_df(f"""
            SELECT tag, count() AS videos, round(median(view_count)) AS median_views
            FROM {TABLE} ARRAY JOIN tags AS tag
            WHERE {' AND '.join(filters)}
            GROUP BY tag ORDER BY videos DESC, median_views DESC LIMIT {int(limit)}
        """)
    except Exception as e:
        logger.warning(f"Could not read trending tags: {ch._unwrap_exception(e)[:160]}")
        return pd.DataFrame()


def latest_snapshot(shorts_only: bool = False, limit: int = 50) -> pd.DataFrame:
    where = "snapshot_at = (SELECT max(snapshot_at) FROM " + TABLE + ")"
    if shorts_only:
        where += " AND is_short"
    try:
        return ch.run_query_df(f"""
            SELECT title, channel_title, region, duration_sec, is_short,
                   view_count, like_count, comment_count, published_at, tags
            FROM {TABLE} WHERE {where}
            ORDER BY view_count DESC LIMIT {int(limit)}
        """)
    except Exception as e:
        logger.warning(f"Could not read trending snapshot: {ch._unwrap_exception(e)[:160]}")
        return pd.DataFrame()


def snapshot_summary() -> Optional[Dict[str, Any]]:
    """Headline numbers for the most recent snapshot, or None if there is none."""
    try:
        # The alias must not collide with the column name: aliasing this
        # `snapshot_at` makes ClickHouse resolve the alias into the WHERE
        # subquery and reject the whole query with ILLEGAL_AGGREGATION.
        df = ch.run_query_df(f"""
            SELECT max(snapshot_at) AS latest_snapshot_at, count() AS videos,
                   countIf(is_short) AS shorts,
                   round(median(view_count)) AS median_views,
                   countIf(length(tags) > 0) AS videos_with_tags
            FROM {TABLE} WHERE snapshot_at = (SELECT max(snapshot_at) FROM {TABLE})
        """)
    except Exception as e:
        logger.warning(f"Could not summarise trending: {ch._unwrap_exception(e)[:160]}")
        return None
    if df.empty or not int(df.iloc[0]["videos"]):
        return None
    r = df.iloc[0]
    return {
        "snapshot_at": str(r["latest_snapshot_at"]), "videos": int(r["videos"]),
        "shorts": int(r["shorts"]), "median_views": float(r["median_views"]),
        "videos_with_tags": int(r["videos_with_tags"]),
    }
