# agent/scoreboard.py
"""
⚡ NornPulse: Forecast calibration scoreboard (scoreboard.py)
Norn Labs (nornlabs.ai)

Grades this system's own predictions against what actually happened.

The forecast is written down before a clip publishes, so it can be wrong in
public. This is the module that checks whether it was. Almost no tool in
this space publishes its own error rate; both numbers are already stored
here, so declining to compute it would be a choice rather than a limitation.

Two things make an honest scoreboard harder than a division:

**Age.** Every benchmark figure is a lifetime median — the crawl observed
each video once, at whatever age it happened to be. A clip published
yesterday has not had the time those videos had, so scoring it against a
lifetime forecast is a category error, not a forecast miss, and it would
report a large negative bias forever simply because clips are young. So a
clip is only graded once `maturity_fraction` says it has accumulated a
meaningful share of its eventual reach, and clips younger than that are
reported as pending rather than counted as misses.

**Absence.** Most published rows predate forecast logging entirely, and
several point at videos that no longer exist. A scoreboard that silently
dropped those would flatter itself by grading only the rows that happen to
be gradeable. Every category is counted and returned, so the UI can say
"2 of 13" rather than implying the other eleven agreed.

With the numbers currently in the warehouse this reports almost nothing,
and that is the correct output. It becomes meaningful as forecasts age.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from agent import clickhouse_mcp_client as ch
from agent import global_benchmarks as gb

logger = logging.getLogger(__name__)

# A clip is graded once the age curve says it should have accumulated at
# least this share of its lifetime reach. Below it, the comparison says more
# about the clip's age than about the forecast.
MIN_MATURITY = 0.55

# Never grade anything younger than this regardless of what the curve says.
# The young age buckets are sparse in the 2021 crawl, and maturity_fraction
# marks its answer `extrapolated` there — a floor is cheaper than trusting an
# extrapolated denominator.
MIN_AGE_DAYS = 3


def _outcomes() -> pd.DataFrame:
    """Latest row per video, with age, excluding videos that no longer exist."""
    try:
        return ch.run_query_df("""
            SELECT clip_id, youtube_video_id, youtube_url, hook_type,
                   forecast_views_p50, forecast_views_p90, forecast_views_p10,
                   actual_view_count, video_unavailable,
                   dateDiff('day', published_at, now()) AS age_days,
                   published_at
            FROM (
                SELECT * FROM published_clip_outcomes
                ORDER BY youtube_video_id, row_written_at DESC
                LIMIT 1 BY youtube_video_id
            )
            ORDER BY published_at DESC
        """)
    except Exception as e:
        logger.warning(f"Could not read outcomes: {ch._unwrap_exception(e)[:160]}")
        return pd.DataFrame()


def grade_records(size_band: str = "0-100",
                  facts: Optional[pd.DataFrame] = None) -> List[Dict[str, Any]]:
    """
    One record per published video, with its grading status.

    status is one of:
      graded      — old enough to judge, and had a forecast
      pending     — had a forecast, still too young to judge
      no_forecast — published before the forecast was recorded
      unavailable — the video is gone, so there is nothing to measure
    """
    df = _outcomes()
    if df.empty:
        return []
    facts = gb.load_facts() if facts is None else facts

    records: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        p50 = float(r["forecast_views_p50"] or 0)
        p90 = float(r["forecast_views_p90"] or 0)
        p10 = float(r["forecast_views_p10"] or 0)
        actual = int(r["actual_view_count"] or 0)
        age = float(r["age_days"] or 0)

        rec: Dict[str, Any] = {
            "clip_id": str(r["clip_id"]),
            "youtube_video_id": str(r["youtube_video_id"]),
            "hook_type": str(r["hook_type"]),
            "age_days": age,
            "forecast_p50": p50,
            "forecast_p90": p90,
            "forecast_p10": p10,
            "actual_views": actual,
        }

        if bool(r["video_unavailable"]):
            rec["status"] = "unavailable"
            records.append(rec)
            continue
        if not p50:
            rec["status"] = "no_forecast"
            records.append(rec)
            continue

        maturity = gb.maturity_fraction(age, size_band, facts)
        fraction = float(maturity["fraction"]) if maturity else None
        rec["maturity"] = fraction
        rec["extrapolated"] = bool(maturity["extrapolated"]) if maturity else None

        if age < MIN_AGE_DAYS or fraction is None or fraction < MIN_MATURITY:
            rec["status"] = "pending"
            records.append(rec)
            continue

        # Compare like with like: scale the lifetime forecast down to what a
        # clip of this age should have reached by now.
        expected = p50 * fraction
        rec["expected_by_now"] = expected
        rec["status"] = "graded"
        rec["ratio"] = (actual / expected) if expected else None
        # The band is also a lifetime range, so it scales the same way.
        lo = (p10 * fraction) if p10 else 0.0
        hi = (p90 * fraction) if p90 else None
        rec["in_band"] = bool(hi and lo <= actual <= hi)
        records.append(rec)

    return records


def calibration_summary(size_band: str = "0-100",
                        facts: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """
    Headline calibration numbers, with every excluded row accounted for.

    `graded` is deliberately reported next to the total. A hit rate computed
    over two of thirteen videos is not a track record, and the UI needs the
    denominator in order to say so.
    """
    records = grade_records(size_band, facts)
    graded = [r for r in records if r["status"] == "graded"]

    summary: Dict[str, Any] = {
        "total": len(records),
        "graded": len(graded),
        "pending": sum(1 for r in records if r["status"] == "pending"),
        "no_forecast": sum(1 for r in records if r["status"] == "no_forecast"),
        "unavailable": sum(1 for r in records if r["status"] == "unavailable"),
        "in_band": None,
        "in_band_pct": None,
        "median_ratio": None,
        "enough_to_judge": False,
    }
    if not graded:
        return summary

    in_band = sum(1 for r in graded if r.get("in_band"))
    ratios = [r["ratio"] for r in graded if r.get("ratio") is not None]
    summary["in_band"] = in_band
    summary["in_band_pct"] = 100.0 * in_band / len(graded)
    summary["median_ratio"] = float(pd.Series(ratios).median()) if ratios else None
    # Below this the percentage moves by 20 points per clip and reads as
    # precision it does not have.
    summary["enough_to_judge"] = len(graded) >= 5
    return summary


def scoreboard_frame(size_band: str = "0-100",
                     facts: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Records as a display frame, newest first."""
    records = grade_records(size_band, facts)
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)
