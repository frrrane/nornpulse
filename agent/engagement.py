# agent/engagement.py
"""
⚡ NornPulse: Engagement as a size-independent measure (engagement.py)
Norn Labs (nornlabs.ai)

Views measure distribution. Engagement measures the video.

On a channel with an audience those two travel together, which is why every
piece of short-form advice is denominated in views. On a channel without one
they come apart. Across the comedy channel's Shorts, once the thin ones are
excluded, **view count and like rate correlate at +0.13** — near enough to
zero that view count carries almost no information about whether anyone
liked the video.

A cautionary note, because it happened here. The same correlation computed
over the *whole* catalogue, thin videos included, is −0.25, and that number
was briefly reported as a finding: "more views, fewer likes". It was an
artifact. A video with two likes on forty-four views scores 4.55%, far above
anything a real hit achieves, and a handful of those at the bottom of the
view range drag the correlation negative on their own. The floor below is
not a refinement of the measurement, it is the difference between a result
and its opposite.

The conclusion survives the correction, but it is the weaker one: views and
engagement are not moving together, so a forecast denominated in views on a
channel this size is largely forecasting distribution. Engagement rate is
the measure that survives the change of scale, and this module computes it.

The floor matters more than the rate
------------------------------------
A rate needs a denominator before it means anything. The comedy channel's
apparent top performer by like rate had **two likes on forty-four views** —
4.55%, and pure noise; one more or fewer like moves it by half. Anything
below MIN_VIEWS_FOR_RATE is therefore excluded from the summary rather than
ranked, and the count of what was excluded is reported, because "we ignored
two thirds of your catalogue" is part of the answer.

Nothing here replaces the reach forecast. Views remain what a publisher
plans capacity around. This is the second measure, the one that can be
trusted at a size where the first cannot.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import pandas as pd

from agent import clickhouse_mcp_client as ch

logger = logging.getLogger("nornpulse.engagement")

HISTORY_TABLE = "channel_video_history"

# Below this many views, a rate is arithmetic rather than evidence. Chosen so
# that a single like moves the rate by less than a third of a percentage
# point; at 44 views, one like is 2.3 points.
MIN_VIEWS_FOR_RATE = 300

# Fewer videos than this and the median of the rates is itself unstable, so
# it is reported with the count attached rather than as a finding.
MIN_CONFIDENT_VIDEOS = 8


def _history(where: str) -> pd.DataFrame:
    """
    Newest row per video, deduplicated per (channel, video).

    Not a global max(snapshot_at): channels are inserted separately and
    their timestamps differ by milliseconds, so filtering on the single
    global maximum drops every channel except whichever was written last.
    That bug has already been fixed once elsewhere in this codebase; it is
    not going to be reintroduced here.
    """
    try:
        return ch.run_query_df(f"""
            SELECT channel_slug, video_id, title, view_count, like_count,
                   comment_count, is_short, size_band, published_at
            FROM (
                SELECT * FROM {HISTORY_TABLE}
                ORDER BY channel_slug, video_id, snapshot_at DESC
                LIMIT 1 BY channel_slug, video_id
            )
            WHERE {where}
        """)
    except Exception as e:
        logger.warning(f"Could not read engagement history: {ch._unwrap_exception(e)[:160]}")
        return pd.DataFrame()


def rates(channel_slug: Optional[str] = None, size_band: Optional[str] = None,
          shorts_only: bool = True) -> pd.DataFrame:
    """
    Per-video engagement rates, with a `ratable` flag rather than a filter.

    Thin videos are marked, not dropped, so a caller can still count them
    and say how much of the catalogue was set aside.
    """
    clauses = ["view_count > 0"]
    if channel_slug:
        clauses.append(f"channel_slug = {ch.sql_literal(channel_slug)}")
    if size_band:
        clauses.append(f"size_band = {ch.sql_literal(size_band)}")
    if shorts_only:
        clauses.append("is_short")

    df = _history(" AND ".join(clauses))
    if df.empty:
        return df

    # Dropped here as well as in the query. A rate needs a denominator, and
    # relying on a WHERE clause inside a format string to guarantee that is
    # the kind of assumption that survives right up until someone edits the
    # SQL — at which point this returns NaN rates rather than failing.
    df = df[df.view_count > 0].copy()
    if df.empty:
        return df

    df["like_rate"] = df.like_count / df.view_count * 100
    df["comment_rate"] = df.comment_count / df.view_count * 100
    df["ratable"] = df.view_count >= MIN_VIEWS_FOR_RATE
    return df


def summary(channel_slug: Optional[str] = None, size_band: Optional[str] = None,
            shorts_only: bool = True) -> Optional[Dict[str, Any]]:
    """
    Median engagement for a channel or band, and what it rests on.

    Returns None rather than a number when nothing clears the floor. A
    median computed over videos with single-digit like counts is not a
    smaller version of the right answer, it is a different quantity.
    """
    df = rates(channel_slug, size_band, shorts_only)
    if df.empty:
        return None

    usable = df[df.ratable]
    if usable.empty:
        return {
            "videos": 0,
            "excluded_thin": int(len(df)),
            "median_like_rate": None,
            "median_comment_rate": None,
            "confident": False,
            "why": (f"no video has {MIN_VIEWS_FOR_RATE}+ views, so every rate "
                    f"here would be arithmetic on a handful of likes"),
        }

    return {
        "videos": int(len(usable)),
        "excluded_thin": int(len(df) - len(usable)),
        "median_like_rate": round(float(usable.like_rate.median()), 2),
        "median_comment_rate": round(float(usable.comment_rate.median()), 2),
        "median_views": float(usable.view_count.median()),
        "confident": bool(len(usable) >= MIN_CONFIDENT_VIDEOS),
        "why": None if len(usable) >= MIN_CONFIDENT_VIDEOS else (
            f"{len(usable)} video(s) clear the {MIN_VIEWS_FOR_RATE}-view floor, "
            f"below the {MIN_CONFIDENT_VIDEOS} this treats as confident"),
    }


def views_vs_engagement(channel_slug: Optional[str] = None,
                        size_band: Optional[str] = None,
                        shorts_only: bool = True) -> Optional[Dict[str, Any]]:
    """
    How far apart views and engagement have come on this channel.

    The number this exists to surface. A correlation at or below zero says
    view count is not measuring the video, and any ranking built on it is
    ranking distribution — which is the entire thesis of this project,
    turned on its own outputs.
    """
    df = rates(channel_slug, size_band, shorts_only)
    if df.empty:
        return None
    usable = df[df.ratable]
    if len(usable) < 3:
        return {
            "videos": int(len(usable)),
            "correlation": None,
            "reading": (f"{len(usable)} video(s) clear the view floor, too few "
                        f"for a correlation to mean anything"),
        }

    corr = float(usable.view_count.corr(usable.like_rate))
    if corr <= 0:
        reading = ("negative: more views came with proportionally fewer likes, "
                   "so view count here is measuring distribution rather than "
                   "the video")
    elif corr < 0.3:
        reading = ("near zero: view count carries almost no information about "
                   "whether a video was liked")
    else:
        reading = ("positive: views and engagement are moving together, which "
                   "is what a channel with an audience looks like")

    return {
        "videos": int(len(usable)),
        "correlation": round(corr, 2),
        "reading": reading,
    }
