# agent/calibration.py
"""
⚡ NornPulse: Per-channel forecast calibration (calibration.py)
Norn Labs (nornlabs.ai)

Corrects a population-level forecast using what a channel actually gets.

The global benchmarks are read from 4.56 billion real videos, banded by
channel size, and they are still badly wrong as a prediction for a specific
small channel. Measured against real history:

    Norn Labs      (2 subs,  5 videos)  median    13 views vs 2,570 → 0.01x
    SlopTokDaily  (14 subs, 37 videos)  median   343 views vs 2,570 → 0.13x

Both channels sit in the 0-100 band, and the band's median is roughly
SlopTokDaily's single best video rather than its typical one. Some of that
gap is format — these are 6-9 second Shorts against an all-format
population — but format does not explain two orders of magnitude.

What explains it is that the public dataset is a crawl. It contains videos
that were discoverable enough to be crawled, which is a filtered sample of
what small channels actually publish. A new channel posting into the void
is not in it. So the band median is not the median of "videos from small
channels"; it is the median of "videos from small channels that got seen".

That is the product's own thesis turned on itself: advice measured on
channels that already made it does not transfer, and banding by size does
not fix it, because the population inside the band is survivorship-filtered
too. Saying so is more useful than shipping a forecast that is 200x high.

The correction, and why it is shaped this way
---------------------------------------------
The obvious move — shrink each channel's estimate toward the global band
median — is wrong here. Shrinkage pulls a noisy estimate toward a prior,
and is only sound when the prior is unbiased. We have direct evidence this
one is not. Shrinking toward it would drag every channel back toward a
number we already know is far too high.

So the target is instead the *pooled observed reality*: the median across
every video from every channel we have real history for, within the same
size band. A channel with plenty of history is trusted mostly on its own
evidence; a channel with almost none falls back to what comparable real
channels actually get, rather than to what the crawl suggests.

Shrinkage runs in log space, because reach is multiplicative and heavy
tailed: an arithmetic blend of 13 and 343 is dominated by the larger value
in a way that misrepresents both.

With two channels this pooled figure is itself thin, and every function
here returns its sample size so the caller can say so rather than
presenting a median of forty-two as settled. The structure is what matters:
it gets better as history accumulates, and it never silently substitutes a
population figure for a channel one.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import pandas as pd

from agent import clickhouse_mcp_client as ch
from agent import global_benchmarks as gb
from agent.channel_history import TABLE as HISTORY_TABLE

logger = logging.getLogger(__name__)

# How much observed history is worth a 50/50 split against the pooled
# fallback. At n = PRIOR_STRENGTH a channel is trusted half on its own
# record; below that the pooled figure carries more of the estimate.
# Deliberately small: the thing being estimated is a channel's own typical
# reach, and a dozen of its own videos genuinely is better evidence for
# that than forty from other channels.
PRIOR_STRENGTH = 12

# Below this, a channel-specific figure is reported but flagged as too thin
# to act on by itself.
MIN_CONFIDENT_VIDEOS = 8


def _median_views(where: str) -> Optional[Dict[str, Any]]:
    try:
        # Deduplicate to the newest row per video, NOT by a global
        # max(snapshot_at). Each channel is inserted separately, so their
        # snapshot timestamps differ by milliseconds — filtering on the
        # single global maximum silently dropped every channel except
        # whichever was written last, and reported it as having no history.
        df = ch.run_query_df(f"""
            SELECT count() AS videos,
                   round(median(view_count)) AS median_views,
                   round(quantile(0.1)(view_count)) AS p10,
                   round(quantile(0.9)(view_count)) AS p90
            FROM (
                SELECT channel_slug, video_id, view_count, is_short, size_band
                FROM {HISTORY_TABLE}
                ORDER BY channel_slug, video_id, snapshot_at DESC
                LIMIT 1 BY channel_slug, video_id
            )
            WHERE {where}
        """)
    except Exception as e:
        logger.warning(f"Could not read observed history: {ch._unwrap_exception(e)[:160]}")
        return None
    if df.empty or not int(df.iloc[0]["videos"]):
        return None
    r = df.iloc[0]
    return {
        "videos": int(r["videos"]),
        "median_views": float(r["median_views"]),
        "p10": float(r["p10"]),
        "p90": float(r["p90"]),
    }


def observed_reality(size_band: str, shorts_only: bool = True) -> Optional[Dict[str, Any]]:
    """
    What channels we have real history for actually get, within a band.

    This is the shrinkage target: measured outcomes from real channels,
    rather than the crawl's view of which small-channel videos exist.
    """
    where = f"size_band = {ch.sql_literal(size_band)}"
    if shorts_only:
        where += " AND is_short"
    return _median_views(where)


def channel_observed(channel_slug: str, shorts_only: bool = True) -> Optional[Dict[str, Any]]:
    """One channel's own measured reach."""
    where = f"channel_slug = {ch.sql_literal(channel_slug)}"
    if shorts_only:
        where += " AND is_short"
    return _median_views(where)


def reality_gap(size_band: str, facts: Optional[pd.DataFrame] = None,
                shorts_only: bool = True) -> Optional[Dict[str, Any]]:
    """
    How far the global band median sits from observed reality in that band.

    The headline number behind the pitch: not "the benchmark is wrong", but
    "a population median drawn from a crawl overstates what a real channel
    of this size gets, by this much, on this much evidence".
    """
    observed = observed_reality(size_band, shorts_only=shorts_only)
    if not observed:
        return None
    facts = gb.load_facts() if facts is None else facts
    base = gb._select(facts, "channel_size_band")
    row = base[base["bucket"] == size_band] if not base.empty else base
    if row.empty:
        return None
    predicted = float(row.iloc[0]["median_views"])
    if not predicted:
        return None
    return {
        "size_band": size_band,
        "predicted_median_views": predicted,
        "benchmark_sample_videos": int(row.iloc[0]["sample_videos"]),
        "observed_median_views": observed["median_views"],
        "observed_videos": observed["videos"],
        "ratio": observed["median_views"] / predicted,
        "shorts_only": shorts_only,
    }


def calibration_factor(channel_slug: str, size_band: str,
                       facts: Optional[pd.DataFrame] = None,
                       shorts_only: bool = True) -> Optional[Dict[str, Any]]:
    """
    The multiplier that turns a global band forecast into a channel one.

    Blends the channel's own observed ratio with the pooled observed ratio
    for its band, weighted by how much history the channel has. Returns the
    derivation as well as the number, because a correction of this size
    should never be applied invisibly.
    """
    gap = reality_gap(size_band, facts=facts, shorts_only=shorts_only)
    if not gap:
        return None
    pooled_ratio = gap["ratio"]
    predicted = gap["predicted_median_views"]

    own = channel_observed(channel_slug, shorts_only=shorts_only)
    if not own:
        # No history for this channel: fall back to what comparable real
        # channels get, which is still far better than the raw benchmark.
        return {
            "channel": channel_slug,
            "factor": pooled_ratio,
            "own_videos": 0,
            "own_ratio": None,
            "pooled_ratio": pooled_ratio,
            "pooled_videos": gap["observed_videos"],
            "weight": 0.0,
            "confident": False,
            "predicted_median_views": predicted,
            "basis": "no history for this channel; using pooled observed reality for its band",
        }

    own_ratio = own["median_views"] / predicted
    n = own["videos"]
    weight = n / (n + PRIOR_STRENGTH)

    # Geometric blend. Reach is multiplicative and heavy tailed, so an
    # arithmetic blend of two ratios an order of magnitude apart is
    # dominated by the larger one and misrepresents both.
    factor = math.exp(weight * math.log(max(own_ratio, 1e-9))
                      + (1 - weight) * math.log(max(pooled_ratio, 1e-9)))

    return {
        "channel": channel_slug,
        "factor": factor,
        "own_videos": n,
        "own_ratio": own_ratio,
        "own_median_views": own["median_views"],
        "pooled_ratio": pooled_ratio,
        "pooled_videos": gap["observed_videos"],
        "weight": weight,
        "confident": n >= MIN_CONFIDENT_VIDEOS,
        "predicted_median_views": predicted,
        "basis": (f"{n} of this channel's own videos, weighted {weight:.0%} against "
                  f"{gap['observed_videos']} videos from comparable real channels"),
    }


def calibrated_forecast(
    channel,
    has_subtitles: bool = True,
    upload_day: Optional[str] = None,
    facts: Optional[pd.DataFrame] = None,
    age_days: Optional[float] = None,
    shorts_only: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    The global forecast, corrected by what this channel actually gets.

    Returns the uncalibrated figures alongside the calibrated ones. Both
    are kept because the difference between them is the finding, not an
    embarrassment to be hidden: it is the measure of how far a
    population-level number is from this specific channel's reality.

    Falls back to the raw global forecast, clearly marked uncalibrated,
    when there is no observed history to calibrate against.
    """
    facts = gb.load_facts() if facts is None else facts
    base = gb.forecast_reach(
        subscriber_count=channel.subscribers,
        has_subtitles=has_subtitles,
        upload_day=upload_day,
        facts=facts,
        age_days=age_days,
    )
    if not base:
        return None

    cal = calibration_factor(
        channel.slug, base["size_band"], facts=facts, shorts_only=shorts_only)
    if not cal:
        base["calibrated"] = False
        base["calibration_note"] = (
            "No observed channel history yet — these are population figures "
            "for the size band, not a prediction for this channel."
        )
        return base

    factor = cal["factor"]
    result = dict(base)
    result.update({
        "calibrated": True,
        "calibration": cal,
        "uncalibrated_p10": base["p10"],
        "uncalibrated_p50": base["p50"],
        "uncalibrated_p90": base["p90"],
        "p10": base["p10"] * factor,
        "p50": base["p50"] * factor,
        "p90": base["p90"] * factor,
    })
    if "expected_by_now_p50" in result:
        result["expected_by_now_p50"] = base["expected_by_now_p50"] * factor

    components: List[Dict[str, Any]] = list(base.get("components") or [])
    components.append({
        "factor": "Channel calibration",
        "detail": (f"{(factor - 1) * 100:+.0f}% against the population figure, "
                   f"from this channel's own history"),
        "multiplier": factor,
        "basis": cal["basis"],
        "banded": True,
        "confident": cal["confident"],
    })
    result["components"] = components
    return result
