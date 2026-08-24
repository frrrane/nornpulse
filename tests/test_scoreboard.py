"""
Unit tests for the forecast calibration scoreboard.

ClickHouse is replaced with a fixture frame. What matters here is not the
arithmetic but the refusals: the scoreboard's whole value is that it does
not flatter itself, and every way it could is a silent one.

The failure modes guarded below:
  * grading a clip too young to have accumulated its reach, which would
    report a permanent negative bias caused by nothing but recency
  * quietly dropping rows that have no forecast or point at deleted videos,
    which would compute a hit rate over only the convenient rows
  * presenting a percentage derived from one or two clips as a track record
"""

import pandas as pd
import pytest

from agent import scoreboard as sb


BAND = "0-100"

# maturity_fraction is stubbed per-test; these stand in for the age curve.
MATURE = {"fraction": 0.9, "extrapolated": False}
YOUNG = {"fraction": 0.2, "extrapolated": True}


def _row(clip_id="c", vid=None, p50=1000.0, p90=5000.0, p10=200.0,
         actual=900, age=30, unavailable=False):
    return {
        "clip_id": clip_id,
        "youtube_video_id": vid or f"vid_{clip_id}",
        "youtube_url": "",
        "hook_type": "curiosity_gap",
        "forecast_views_p50": p50,
        "forecast_views_p90": p90,
        "forecast_views_p10": p10,
        "actual_view_count": actual,
        "video_unavailable": unavailable,
        "age_days": age,
        "published_at": "2026-01-01 00:00:00",
    }


def _patch(monkeypatch, rows, maturity=MATURE):
    monkeypatch.setattr(sb, "_outcomes", lambda: pd.DataFrame(rows))
    monkeypatch.setattr(sb.gb, "load_facts", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(sb.gb, "maturity_fraction", lambda *a, **k: maturity)


# --- the refusals ----------------------------------------------------------

def test_clip_below_age_floor_is_pending(monkeypatch):
    _patch(monkeypatch, [_row(age=1)], maturity=MATURE)
    recs = sb.grade_records(BAND)
    assert recs[0]["status"] == "pending"


def test_clip_below_maturity_threshold_is_pending(monkeypatch):
    _patch(monkeypatch, [_row(age=90)], maturity=YOUNG)
    assert sb.grade_records(BAND)[0]["status"] == "pending"


def test_missing_forecast_is_counted_not_dropped(monkeypatch):
    """Grading only the rows that happen to be gradeable flatters the result."""
    _patch(monkeypatch, [_row(p50=0.0), _row(clip_id="d")])
    s = sb.calibration_summary(BAND)
    assert s["total"] == 2
    assert s["no_forecast"] == 1
    assert s["graded"] == 1


def test_unavailable_video_is_excluded_but_still_counted(monkeypatch):
    """A deleted video reads as zero views; scoring it is a fabricated miss."""
    _patch(monkeypatch, [_row(unavailable=True, actual=0), _row(clip_id="d")])
    s = sb.calibration_summary(BAND)
    assert s["unavailable"] == 1
    assert s["graded"] == 1
    assert s["total"] == 2


def test_every_row_lands_in_exactly_one_bucket(monkeypatch):
    _patch(monkeypatch, [
        _row(clip_id="a"),
        _row(clip_id="b", age=1),
        _row(clip_id="c", p50=0.0),
        _row(clip_id="d", unavailable=True),
    ])
    s = sb.calibration_summary(BAND)
    assert s["graded"] + s["pending"] + s["no_forecast"] + s["unavailable"] == s["total"] == 4


# --- the grading itself ----------------------------------------------------

def test_forecast_is_scaled_to_the_clip_age(monkeypatch):
    """
    The forecast is a lifetime figure. Comparing a 90%-mature clip against
    the full lifetime number would understate every result by 10%.
    """
    _patch(monkeypatch, [_row(p50=1000.0, actual=900)], maturity=MATURE)
    rec = sb.grade_records(BAND)[0]
    assert rec["expected_by_now"] == pytest.approx(900.0)
    assert rec["ratio"] == pytest.approx(1.0)


def test_actual_inside_the_scaled_band_counts_as_in_band(monkeypatch):
    _patch(monkeypatch, [_row(p10=200.0, p90=5000.0, actual=900)])
    assert sb.grade_records(BAND)[0]["in_band"] is True


def test_actual_above_the_band_is_out(monkeypatch):
    _patch(monkeypatch, [_row(p10=200.0, p90=5000.0, actual=99_000)])
    assert sb.grade_records(BAND)[0]["in_band"] is False


def test_actual_below_the_band_is_out(monkeypatch):
    _patch(monkeypatch, [_row(p10=200.0, p90=5000.0, actual=1)])
    assert sb.grade_records(BAND)[0]["in_band"] is False


def test_band_is_scaled_too_not_just_the_midpoint(monkeypatch):
    """
    An unscaled p90 would make almost everything look in-band while young.
    """
    # 0.6, not 0.5: below MIN_MATURITY the clip is refused before the band
    # is ever evaluated, which would test the refusal instead of the scaling.
    _patch(monkeypatch, [_row(p10=200.0, p90=1000.0, actual=950)],
           maturity={"fraction": 0.6, "extrapolated": False})
    rec = sb.grade_records(BAND)[0]
    assert rec["status"] == "graded"
    # Scaled band is 120-600, so 950 is outside even though it is under p90.
    assert rec["in_band"] is False


# --- honesty about sample size --------------------------------------------

def test_two_clips_is_not_enough_to_judge(monkeypatch):
    _patch(monkeypatch, [_row(clip_id="a"), _row(clip_id="b")])
    s = sb.calibration_summary(BAND)
    assert s["graded"] == 2
    assert s["enough_to_judge"] is False


def test_enough_graded_clips_flips_the_flag(monkeypatch):
    _patch(monkeypatch, [_row(clip_id=f"c{i}") for i in range(6)])
    s = sb.calibration_summary(BAND)
    assert s["graded"] == 6
    assert s["enough_to_judge"] is True


def test_summary_is_safe_with_no_rows_at_all(monkeypatch):
    _patch(monkeypatch, [])
    s = sb.calibration_summary(BAND)
    assert s["total"] == 0
    assert s["in_band_pct"] is None
    assert s["enough_to_judge"] is False


def test_no_graded_rows_reports_none_not_zero_percent(monkeypatch):
    """0% would read as "every forecast missed"; None reads as "not yet"."""
    _patch(monkeypatch, [_row(age=1)])
    s = sb.calibration_summary(BAND)
    assert s["graded"] == 0
    assert s["in_band_pct"] is None
    assert s["median_ratio"] is None
