"""
Unit tests for engagement as a size-independent measure.

No database. The behaviour worth guarding is the view floor, because
removing it does not make the answer noisier — it makes it the opposite
answer. Computed over a whole catalogue, this channel's views and like rate
correlate at −0.25 ("more views, fewer likes"); computed over the videos
with enough views to have a meaningful rate, +0.13 ("no relationship").
The first was reported as a finding before the floor existed. It was wrong.
"""

import pandas as pd
import pytest

from agent import engagement as eng


def _frame(rows):
    """rows: (views, likes, comments)."""
    return pd.DataFrame([
        {"channel_slug": "c", "video_id": f"v{i}", "title": f"t{i}",
         "view_count": v, "like_count": l, "comment_count": c,
         "is_short": True, "size_band": "0-100", "published_at": "2026-01-01"}
        for i, (v, l, c) in enumerate(rows)
    ])


@pytest.fixture
def stub(monkeypatch):
    def _install(rows):
        monkeypatch.setattr(eng, "_history", lambda where: _frame(rows))
    return _install


# --- the floor -------------------------------------------------------------

def test_a_thin_video_is_marked_not_rated(stub):
    """Two likes on forty-four views is 4.55% and means nothing."""
    stub([(44, 2, 0), (1000, 5, 1)])
    df = eng.rates("c")
    thin = df[df.view_count == 44].iloc[0]
    assert thin.like_rate > 4          # the arithmetic still happens
    assert not thin.ratable            # it just does not count


def test_thin_videos_are_excluded_from_the_summary_and_counted(stub):
    stub([(44, 2, 0), (50, 3, 0), (1000, 5, 1), (2000, 20, 2)])
    s = eng.summary("c")
    assert s["videos"] == 2
    assert s["excluded_thin"] == 2


def test_a_catalogue_below_the_floor_reports_no_rate_at_all(stub):
    """
    Silence rather than a number. A median over single-digit like counts is
    not a smaller version of the right answer.
    """
    stub([(44, 2, 0), (120, 1, 0), (207, 0, 0)])
    s = eng.summary("c")
    assert s["median_like_rate"] is None
    assert s["videos"] == 0
    assert "arithmetic" in s["why"]


def test_the_floor_is_what_flips_the_correlation(stub):
    """
    The regression this module exists because of. Thin videos with inflated
    rates sit at the bottom of the view range and drag the correlation
    negative on their own; excluding them removes the effect entirely.
    """
    rows = [(40, 2, 0), (60, 3, 0), (80, 4, 0),        # noise, huge rates
            (1000, 5, 1), (2000, 12, 2), (3000, 20, 3)]  # real, modest rates
    stub(rows)

    everything = _frame(rows)
    everything["like_rate"] = everything.like_count / everything.view_count * 100
    assert everything.view_count.corr(everything.like_rate) < 0, \
        "the unfiltered set should show the spurious negative"

    result = eng.views_vs_engagement("c")
    assert result["correlation"] > 0, "the floor should remove it"
    assert result["videos"] == 3


def test_too_few_ratable_videos_refuses_a_correlation(stub):
    stub([(1000, 5, 1), (2000, 12, 2)])
    result = eng.views_vs_engagement("c")
    assert result["correlation"] is None
    assert "too few" in result["reading"]


# --- honesty about confidence ---------------------------------------------

def test_a_thin_sample_is_reported_but_not_called_confident(stub):
    stub([(1000 + i, 5, 1) for i in range(4)])
    s = eng.summary("c")
    assert s["videos"] == 4
    assert not s["confident"]
    assert str(eng.MIN_CONFIDENT_VIDEOS) in s["why"]


def test_enough_videos_is_confident(stub):
    stub([(1000 + i, 5, 1) for i in range(eng.MIN_CONFIDENT_VIDEOS)])
    s = eng.summary("c")
    assert s["confident"] and s["why"] is None


def test_no_history_is_none_not_zero(stub):
    stub([])
    assert eng.summary("c") is None
    assert eng.views_vs_engagement("c") is None


def test_a_zero_view_video_cannot_divide_by_zero(stub):
    stub([(0, 0, 0), (1000, 5, 1)])
    df = eng.rates("c")
    assert df.like_rate.notna().all()


# --- the reading -----------------------------------------------------------

@pytest.mark.parametrize("rows,expect", [
    ([(1000, 50, 5), (2000, 20, 2), (3000, 6, 1)], "negative"),
    ([(1000, 5, 1), (2000, 12, 2), (3000, 20, 3)], "positive"),
])
def test_the_reading_describes_the_direction(rows, expect, stub):
    stub(rows)
    assert expect in eng.views_vs_engagement("c")["reading"]
