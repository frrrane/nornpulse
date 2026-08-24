# agent/channel_history.py
"""
⚡ NornPulse: Existing channel history (channel_history.py)
Norn Labs (nornlabs.ai)

Reads what a channel has already published, and what it got for it.

This exists because the project's own published output is a terrible
evidence base. A few dozen clips, most of them days old, cannot calibrate
anything — but the channels themselves are not new. SlopTokDaily has been
posting daily for weeks. That history is real outcome data from a real
0-100 subscriber channel, and it is free: statistics on public videos need
an API key rather than OAuth, and cost about three quota units for a whole
channel against a 10,000/day budget. An upload costs 1,600.

What it is good for, precisely: checking whether the global benchmarks —
derived from 4.56 billion videos, banded by channel size — actually
predict what happened on this specific small channel. That is a calibration
question, and it is answerable today without publishing anything new.

What it is not good for: claiming these videos as NornPulse output. They
were not produced by this pipeline. They are the control group, and the
table keeps them separate from published_clip_outcomes for exactly that
reason — mixing them would inflate the pipeline's apparent track record
with work it did not do.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import pandas as pd

from agent import clickhouse_mcp_client as ch
from agent.global_benchmarks import size_band_for
from agent.trending_ingest import parse_iso_duration

logger = logging.getLogger(__name__)

TABLE = "channel_video_history"

# YouTube counts anything at or under 60 seconds as a Short.
SHORT_MAX_SEC = 60

# playlistItems and videos both page at 50.
_PAGE = 50


def ensure_table() -> None:
    ch.run_query(f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        snapshot_at DateTime DEFAULT now(),
        channel_slug LowCardinality(String),
        youtube_channel_id String,
        channel_subscribers UInt32,
        size_band LowCardinality(String),
        video_id String,
        title String,
        published_at DateTime,
        duration_sec UInt32,
        is_short Bool,
        view_count UInt64,
        like_count UInt64,
        comment_count UInt64,
        tags Array(String)
    ) ENGINE = MergeTree()
    ORDER BY (channel_slug, video_id, snapshot_at);
    """)


def _youtube(api_key: Optional[str] = None):
    from googleapiclient.discovery import build
    key = api_key or os.getenv("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError(
            "YOUTUBE_API_KEY is not set. Channel history reads public statistics "
            "only, so it needs an API key rather than OAuth."
        )
    return build("youtube", "v3", developerKey=key)


def fetch_channel_videos(youtube, youtube_channel_id: str,
                         max_videos: int = 500) -> List[Dict[str, Any]]:
    """
    Every uploaded video on a channel, with statistics.

    Walks the channel's uploads playlist rather than using search(), which
    costs 100 quota units per call and silently omits older videos.
    """
    meta = youtube.channels().list(
        part="contentDetails,statistics", id=youtube_channel_id).execute()
    items = meta.get("items") or []
    if not items:
        raise RuntimeError(f"Channel {youtube_channel_id} not found or has no public data.")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    subscribers = int(items[0].get("statistics", {}).get("subscriberCount", 0) or 0)

    video_ids: List[str] = []
    page_token = None
    while len(video_ids) < max_videos:
        resp = youtube.playlistItems().list(
            part="contentDetails", playlistId=uploads,
            maxResults=_PAGE, pageToken=page_token).execute()
        video_ids.extend(i["contentDetails"]["videoId"] for i in resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    rows: List[Dict[str, Any]] = []
    for i in range(0, len(video_ids), _PAGE):
        batch = video_ids[i:i + _PAGE]
        resp = youtube.videos().list(
            part="snippet,statistics,contentDetails", id=",".join(batch)).execute()
        for item in resp.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            duration = parse_iso_duration(
                item.get("contentDetails", {}).get("duration", "PT0S"))
            rows.append({
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "published_at": (snippet.get("publishedAt") or "").replace("T", " ").replace("Z", ""),
                "duration_sec": duration,
                "is_short": duration <= SHORT_MAX_SEC,
                "view_count": int(stats.get("viewCount", 0) or 0),
                "like_count": int(stats.get("likeCount", 0) or 0),
                "comment_count": int(stats.get("commentCount", 0) or 0),
                "tags": snippet.get("tags") or [],
            })
    return rows, subscribers


def store_history(channel, rows: List[Dict[str, Any]],
                  subscribers: Optional[int] = None) -> int:
    """
    Append a snapshot of a channel's history.

    Appended rather than replaced: view counts move, and keeping successive
    snapshots is what makes it possible to see how fast a video on this
    channel actually accumulates reach — which is the input the age-aware
    forecast needs and currently takes from a global prior.
    """
    if not rows:
        return 0
    ensure_table()
    subs = int(subscribers if subscribers is not None else channel.subscribers)
    band = size_band_for(subs)
    array = lambda vs: "[" + ", ".join(ch.sql_literal(v) for v in vs) + "]"

    values = []
    for r in rows:
        values.append("(" + ", ".join([
            ch.sql_literal(channel.slug),
            ch.sql_literal(channel.youtube_channel_id),
            ch.sql_literal(subs),
            ch.sql_literal(band),
            ch.sql_literal(r["video_id"]),
            ch.sql_literal(r["title"]),
            ch.sql_literal(r["published_at"]),
            ch.sql_literal(int(r["duration_sec"])),
            ch.sql_literal(bool(r["is_short"])),
            ch.sql_literal(int(r["view_count"])),
            ch.sql_literal(int(r["like_count"])),
            ch.sql_literal(int(r["comment_count"])),
            array(r["tags"]),
        ]) + ")")

    ch.run_query(
        f"INSERT INTO {TABLE} (channel_slug, youtube_channel_id, channel_subscribers, "
        f"size_band, video_id, title, published_at, duration_sec, is_short, "
        f"view_count, like_count, comment_count, tags) VALUES " + ", ".join(values)
    )
    return len(values)


def ingest(channel, api_key: Optional[str] = None, max_videos: int = 500) -> Dict[str, Any]:
    """Fetch and store one channel's history. Returns a short summary."""
    youtube = _youtube(api_key)
    rows, subscribers = fetch_channel_videos(
        youtube, channel.youtube_channel_id, max_videos=max_videos)
    stored = store_history(channel, rows, subscribers=subscribers)
    shorts = [r for r in rows if r["is_short"]]
    return {
        "channel": channel.slug,
        "subscribers": subscribers,
        "size_band": size_band_for(subscribers),
        "videos": stored,
        "shorts": len(shorts),
        "median_views": (
            float(pd.Series([r["view_count"] for r in rows]).median()) if rows else 0.0),
        "median_short_views": (
            float(pd.Series([r["view_count"] for r in shorts]).median()) if shorts else 0.0),
    }


def latest_history(channel_slug: Optional[str] = None,
                   shorts_only: bool = False) -> pd.DataFrame:
    """The most recent snapshot of a channel's videos."""
    # Newest row per video rather than a global max(snapshot_at): channels
    # are inserted separately, so a global maximum matches only the last
    # channel written and reports the others as empty.
    filters = []
    if channel_slug:
        filters.append(f"channel_slug = {ch.sql_literal(channel_slug)}")
    if shorts_only:
        filters.append("is_short")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    try:
        return ch.run_query_df(f"""
            SELECT * FROM (
                SELECT channel_slug, video_id, title, published_at, duration_sec,
                       is_short, view_count, like_count, comment_count, tags,
                       channel_subscribers, size_band
                FROM {TABLE}
                ORDER BY channel_slug, video_id, snapshot_at DESC
                LIMIT 1 BY channel_slug, video_id
            )
            {where}
            ORDER BY published_at DESC
        """)
    except Exception as e:
        logger.warning(f"Could not read channel history: {ch._unwrap_exception(e)[:160]}")
        return pd.DataFrame()


def calibration(channel_slug: str, shorts_only: bool = True) -> Optional[Dict[str, Any]]:
    """
    What this channel actually gets, against what the global band predicts.

    The honest comparison for the pitch: the benchmarks say a 0-100
    subscriber channel sees a certain median reach; here is what one real
    0-100 subscriber channel actually saw. A large gap is not a bug in the
    benchmarks — the band is a population median across every topic and
    format — but it is the number that says how much a band-level figure is
    worth as a prediction for one specific channel, which is precisely the
    claim the product makes.
    """
    df = latest_history(channel_slug, shorts_only=shorts_only)
    if df.empty:
        return None

    from agent.global_benchmarks import expected_reach, load_facts
    predicted = None
    sample_videos = None
    try:
        facts = load_facts()
        subs = int(df.iloc[0]["channel_subscribers"])
        reach = expected_reach(subs, facts)
        if reach:
            predicted = float(reach["median_views"])
            sample_videos = int(reach["sample_videos"])
    except Exception as e:
        logger.warning(f"Could not load global facts for calibration: {e}")

    actual_median = float(df["view_count"].median())
    return {
        "channel": channel_slug,
        "videos": int(len(df)),
        "shorts_only": shorts_only,
        "size_band": str(df.iloc[0]["size_band"]),
        "subscribers": int(df.iloc[0]["channel_subscribers"]),
        "actual_median_views": actual_median,
        "predicted_median_views": predicted,
        "benchmark_sample_videos": sample_videos,
        "ratio": (actual_median / predicted) if predicted else None,
    }


def voice_reference(channel_slug: str, limit: int = 12,
                    shorts_only: bool = True) -> List[Dict[str, Any]]:
    """
    A channel's own best-performing titles, strongest first.

    Written for the trend loop's brief prompt. Asking a model to "be funny"
    produces the median of everything it has read, which is polished
    corporate deadpan — and for a channel whose actual voice is chaotic
    mashups and deliberately mangled names, that is not merely bland, it is
    off-brand in a way a viewer notices immediately.

    The channel has already answered the question. These are real titles
    with real view counts attached, so the voice handed to the model is
    measured rather than assumed, which is the same standard every other
    decision here is held to.
    """
    df = latest_history(channel_slug, shorts_only=shorts_only)
    if df.empty:
        return []
    top = df.nlargest(limit, "view_count")
    return [
        {"title": str(r["title"]), "views": int(r["view_count"])}
        for _, r in top.iterrows()
        if str(r["title"]).strip()
    ]
