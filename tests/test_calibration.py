"""
Unit tests for per-channel forecast calibration.

ClickHouse is never touched: the two reads this module makes are replaced
with fixtures, which is enough because the logic under test is the blend,
not the SQL.

The properties worth guarding are the ones that would quietly restore a
200x-high forecast: shrinking toward the biased population prior instead of
observed reality, arithmetic blending of heavy-tailed ratios, or a channel
with no history silently inheriting the raw benchmark.
"""

import math

import pandas as pd
import pytest

from agent import calibration as cal


BAND = "0-100"
BENCHMARK_MEDIAN = 2570.0

# Stands in for the materialised global facts.
FACTS = pd.DataFrame([{
    "dimension": "channel_size_band",
    "bucket": BAND,
    "size_band": BAND,
    "median_views": BENCHMARK_MEDIAN,
    "sample_videos": 58044,
}])


class _Channel:
    def __init__(self, slug, subscribers):
        self.slug = slug
        self.subscribers = subscribers


def _patch_history(monkeypatch, per_channel, pooled):
    """Replace the two history reads with fixtures."""
    def fake_median(where):
        for slug, payload in per_channel.items():
            if f"'{slug}'" in where:
                return payload
        return pooled
    monkeypatch.setattr(cal, "_median_views", fake_median)


def _stats(videos, median, p10=None, p90=None):
    return {"videos": videos, "median_views": median,
            "p10": p10 if p10 is not None else median / 3,
            "p90": p90 if p90 is not None else median * 5}


# --- the correction actually happens --------------------------------------

def test_calibration_pulls_the_forecast_down_toward_reality(monkeypatch):
    _patch_history(monkeypatch,
                   {"sloptokdaily": _stats(37, 343.0)},
                   _stats(42, 256.0))
    factor = cal.calibration_factor("sloptokdaily", BAND, facts=FACTS)
    assert factor is not None
    # The benchmark is an order of magnitude high; the factor must reflect
    # that rather than hovering near 1.0.
    assert 0.05 < factor["factor"] < 0.30
    assert factor["confident"] is True


def test_calibrated_p50_lands_near_what_the_channel_actually_gets(monkeypatch):
    _patch_history(monkeypatch,
                   {"sloptokdaily": _stats(37, 343.0)},
                   _stats(42, 256.0))
    factor = cal.calibration_factor("sloptokdaily", BAND, facts=FACTS)
    predicted = BENCHMARK_MEDIAN * factor["factor"]
    assert 200 < predicted < 500, predicted


# --- the shrinkage target --------------------------------------------------

def test_shrinkage_target_is_observed_reality_not_the_benchmark(monkeypatch):
    """
    A channel with almost no history must fall back to what comparable real
    channels get. Falling back to the benchmark would restore the very
    overestimate this module exists to correct.
    """
    _patch_history(monkeypatch,
                   {"newchannel": _stats(1, 300.0)},
                   _stats(42, 256.0))
    factor = cal.calibration_factor("newchannel", BAND, facts=FACTS)
    # Nowhere near 1.0 (the benchmark), close to the pooled ratio instead.
    assert factor["factor"] < 0.30
    assert factor["confident"] is False


def test_channel_with_no_history_uses_pooled_reality(monkeypatch):
    monkeypatch.setattr(cal, "_median_views",
                        lambda where: None if "'ghost'" in where else _stats(42, 256.0))
    factor = cal.calibration_factor("ghost", BAND, facts=FACTS)
    assert factor["own_videos"] == 0
    assert factor["weight"] == 0.0
    assert factor["factor"] == pytest.approx(256.0 / BENCHMARK_MEDIAN)
    assert factor["confident"] is False


# --- the blend behaves ------------------------------------------------------

def test_more_history_shifts_weight_toward_the_channel(monkeypatch):
    weights = []
    for n in (1, 12, 100):
        _patch_history(monkeypatch, {"c": _stats(n, 343.0)}, _stats(42, 256.0))
        weights.append(cal.calibration_factor("c", BAND, facts=FACTS)["weight"])
    assert weights == sorted(weights)
    assert weights[0] < 0.15
    # PRIOR_STRENGTH is defined as the 50/50 point.
    assert weights[1] == pytest.approx(0.5)
    assert weights[2] > 0.85


def test_blend_is_geometric_not_arithmetic(monkeypatch):
    """
    Reach is multiplicative and heavy tailed. An arithmetic blend of two
    ratios an order of magnitude apart is dominated by the larger one.
    """
    _patch_history(monkeypatch, {"c": _stats(12, 25.7)}, _stats(42, 257.0))
    factor = cal.calibration_factor("c", BAND, facts=FACTS)["factor"]
    own_ratio, pooled_ratio = 0.01, 0.1
    assert factor == pytest.approx(math.sqrt(own_ratio * pooled_ratio), rel=1e-6)
    assert factor < (own_ratio + pooled_ratio) / 2


def test_factor_sits_between_the_two_ratios(monkeypatch):
    _patch_history(monkeypatch, {"c": _stats(20, 100.0)}, _stats(42, 500.0))
    f = cal.calibration_factor("c", BAND, facts=FACTS)
    lo, hi = sorted([f["own_ratio"], f["pooled_ratio"]])
    assert lo <= f["factor"] <= hi


# --- the reality gap --------------------------------------------------------

def test_reality_gap_reports_both_sides_and_their_samples(monkeypatch):
    _patch_history(monkeypatch, {}, _stats(42, 256.0))
    gap = cal.reality_gap(BAND, facts=FACTS)
    assert gap["predicted_median_views"] == BENCHMARK_MEDIAN
    assert gap["observed_median_views"] == 256.0
    assert gap["benchmark_sample_videos"] == 58044
    assert gap["observed_videos"] == 42
    assert gap["ratio"] == pytest.approx(256.0 / BENCHMARK_MEDIAN)


def test_no_observed_history_yields_no_gap(monkeypatch):
    monkeypatch.setattr(cal, "_median_views", lambda where: None)
    assert cal.reality_gap(BAND, facts=FACTS) is None


# --- the forecast wrapper ---------------------------------------------------

def test_forecast_keeps_the_uncalibrated_figures(monkeypatch):
    """The difference between them is the finding, not something to hide."""
    _patch_history(monkeypatch, {"sloptokdaily": _stats(37, 343.0)}, _stats(42, 256.0))
    monkeypatch.setattr(cal.gb, "forecast_reach", lambda **kw: {
        "size_band": BAND, "p10": 500.0, "p50": 2455.0, "p90": 12000.0,
        "components": [], "sample_videos": 58044,
    })
    out = cal.calibrated_forecast(_Channel("sloptokdaily", 14), facts=FACTS)
    assert out["calibrated"] is True
    assert out["uncalibrated_p50"] == 2455.0
    assert out["p50"] < out["uncalibrated_p50"]
    assert out["p10"] < out["p50"] < out["p90"]


def test_forecast_without_history_is_marked_uncalibrated(monkeypatch):
    monkeypatch.setattr(cal, "_median_views", lambda where: None)
    monkeypatch.setattr(cal.gb, "forecast_reach", lambda **kw: {
        "size_band": BAND, "p10": 500.0, "p50": 2455.0, "p90": 12000.0,
        "components": [], "sample_videos": 58044,
    })
    out = cal.calibrated_forecast(_Channel("ghost", 0), facts=FACTS)
    assert out["calibrated"] is False
    assert out["p50"] == 2455.0
    assert "not a prediction for this channel" in out["calibration_note"]


def test_calibration_appears_as_a_labelled_component(monkeypatch):
    _patch_history(monkeypatch, {"sloptokdaily": _stats(37, 343.0)}, _stats(42, 256.0))
    monkeypatch.setattr(cal.gb, "forecast_reach", lambda **kw: {
        "size_band": BAND, "p10": 500.0, "p50": 2455.0, "p90": 12000.0,
        "components": [{"factor": "Channel size"}], "sample_videos": 58044,
    })
    out = cal.calibrated_forecast(_Channel("sloptokdaily", 14), facts=FACTS)
    added = [c for c in out["components"] if c["factor"] == "Channel calibration"]
    assert len(added) == 1
    assert added[0]["confident"] is True
    assert "own history" in added[0]["detail"]


def test_no_forecast_at_all_returns_none(monkeypatch):
    monkeypatch.setattr(cal.gb, "forecast_reach", lambda **kw: None)
    assert cal.calibrated_forecast(_Channel("c", 0), facts=FACTS) is None
