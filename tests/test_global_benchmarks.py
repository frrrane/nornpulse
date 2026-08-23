"""
Unit tests for the global-grounding layer.

Two things here are worth guarding hard. First, stratification: reading
this dataset without controlling for channel size produces a confidently
wrong answer (captioned videos skew to big channels, so the marginal
comparison measures audience rather than captioning). Second, the honest
degradation path: when a fact hasn't been materialised the accessors must
return None so the UI omits the claim, never fabricate or mis-attribute one.

Nothing here touches ClickHouse or the remote dataset — facts are passed
in as frames.
"""

import pandas as pd
import pytest

from agent import global_benchmarks as gb
from agent.trending_ingest import parse_iso_duration, _array_literal, SHORT_MAX_SEC


# --------------------------------------------------------------------------
# Channel size banding
# --------------------------------------------------------------------------

@pytest.mark.parametrize("subs,band", [
    (0, "0-100"), (99, "0-100"), (100, "100-1k"), (999, "100-1k"),
    (1_000, "1k-10k"), (10_000, "10k-100k"), (100_000, "100k-1M"),
    (1_000_000, "1M+"), (50_000_000, "1M+"),
])
def test_size_band_boundaries(subs, band):
    assert gb.size_band_for(subs) == band


# --------------------------------------------------------------------------
# Stratified subtitle evidence
# --------------------------------------------------------------------------

def _facts(rows):
    return pd.DataFrame(rows)


_SUBTITLE_FACTS = _facts([
    # Small channels: captions do NOT lift views, but lift engagement.
    {"dimension": "has_subtitles", "bucket": "false", "size_band": "0-100",
     "median_views": 2556.0, "median_views_per_sub": 153.3, "like_rate_pct": 0.721,
     "sample_videos": 29000},
    {"dimension": "has_subtitles", "bucket": "true", "size_band": "0-100",
     "median_views": 2421.0, "median_views_per_sub": 110.3, "like_rate_pct": 1.208,
     "sample_videos": 29044},
    # Large channels: captions lift both.
    {"dimension": "has_subtitles", "bucket": "false", "size_band": "100k-1M",
     "median_views": 10177.0, "median_views_per_sub": 0.039, "like_rate_pct": 2.300,
     "sample_videos": 31000},
    {"dimension": "has_subtitles", "bucket": "true", "size_band": "100k-1M",
     "median_views": 13285.0, "median_views_per_sub": 0.052, "like_rate_pct": 3.884,
     "sample_videos": 31737},
])


def test_subtitle_lift_is_read_within_a_band():
    """
    The whole point of stratifying. A new channel and a large channel get
    genuinely different answers, and averaging them produces a number that
    describes neither.
    """
    small = gb.subtitle_lift("0-100", facts=_SUBTITLE_FACTS)
    large = gb.subtitle_lift("100k-1M", facts=_SUBTITLE_FACTS)
    assert small["views_lift_pct"] < 0        # captions don't buy reach yet
    assert large["views_lift_pct"] > 25       # they do once there's an audience
    # Engagement lift is the effect that holds at both sizes.
    assert small["like_lift_pct"] > 50 and large["like_lift_pct"] > 50


def test_subtitle_lift_reports_the_band_it_used():
    """A caption claiming a lift must be able to say who it applies to."""
    assert gb.subtitle_lift("0-100", facts=_SUBTITLE_FACTS)["size_band"] == "0-100"


def test_subtitle_lift_sums_the_sample_across_both_buckets():
    assert gb.subtitle_lift("0-100", facts=_SUBTITLE_FACTS)["sample_videos"] == 58044


def test_subtitle_lift_returns_none_for_an_unmaterialised_band():
    """Omit the claim rather than fall back to another band's number."""
    assert gb.subtitle_lift("1M+", facts=_SUBTITLE_FACTS) is None


def test_subtitle_lift_returns_none_when_a_bucket_is_missing():
    only_true = _SUBTITLE_FACTS[_SUBTITLE_FACTS["bucket"] == "true"]
    assert gb.subtitle_lift("0-100", facts=only_true) is None


def test_subtitle_lift_returns_none_on_empty_facts():
    assert gb.subtitle_lift("0-100", facts=pd.DataFrame()) is None


def test_subtitle_lift_survives_a_zero_baseline():
    """A zero median can't be a denominator; degrade instead of raising."""
    zeroed = _facts([
        {"dimension": "has_subtitles", "bucket": "false", "size_band": "0-100",
         "median_views": 0.0, "like_rate_pct": 0.0, "sample_videos": 10},
        {"dimension": "has_subtitles", "bucket": "true", "size_band": "0-100",
         "median_views": 100.0, "like_rate_pct": 1.0, "sample_videos": 10},
    ])
    assert gb.subtitle_lift("0-100", facts=zeroed) is None


def test_a_zero_like_baseline_omits_only_the_like_figure():
    partial = _facts([
        {"dimension": "has_subtitles", "bucket": "false", "size_band": "0-100",
         "median_views": 100.0, "like_rate_pct": 0.0, "sample_videos": 10},
        {"dimension": "has_subtitles", "bucket": "true", "size_band": "0-100",
         "median_views": 150.0, "like_rate_pct": 1.0, "sample_videos": 10},
    ])
    lift = gb.subtitle_lift("0-100", facts=partial)
    assert lift["views_lift_pct"] == pytest.approx(50.0)
    assert lift["like_lift_pct"] is None


# --------------------------------------------------------------------------
# Expected reach
# --------------------------------------------------------------------------

_REACH_FACTS = _facts([
    {"dimension": "channel_size_band", "bucket": "0-100", "size_band": "",
     "median_views": 2492.0, "median_views_per_sub": 139.2, "like_rate_pct": 0.82,
     "sample_videos": 58044},
    {"dimension": "channel_size_band", "bucket": "1M+", "size_band": "",
     "median_views": 18408.0, "median_views_per_sub": 0.006, "like_rate_pct": 2.10,
     "sample_videos": 20000},
])


def test_expected_reach_picks_the_band_for_the_subscriber_count():
    assert gb.expected_reach(0, facts=_REACH_FACTS)["median_views"] == 2492.0
    assert gb.expected_reach(5_000_000, facts=_REACH_FACTS)["median_views"] == 18408.0


def test_expected_reach_returns_none_for_a_band_with_no_data():
    assert gb.expected_reach(5_000, facts=_REACH_FACTS) is None


# --------------------------------------------------------------------------
# Upload timing
# --------------------------------------------------------------------------

_WEEKDAY_FACTS = _facts([
    {"dimension": "upload_weekday", "bucket": str(d), "size_band": "0-100",
     "median_views": mv, "median_views_per_sub": vps, "sample_videos": 1400}
    for d, mv, vps in zip(
        range(1, 8),
        [2099, 2110, 2231, 2068, 2101, 2156, 2178],
        [72.6, 67.8, 77.4, 71.4, 71.7, 73.1, 74.4])
] + [
    {"dimension": "upload_weekday", "bucket": str(d), "size_band": "1M+",
     "median_views": mv, "median_views_per_sub": 0.005, "sample_videos": 900}
    for d, mv in zip(range(1, 8), [9000, 9100, 8000, 8200, 9500, 7000, 7200])
])


def test_best_upload_days_ranks_within_a_band():
    days = gb.best_upload_days(2, size_band="0-100", facts=_WEEKDAY_FACTS)
    assert [d["day"] for d in days] == ["Wednesday", "Sunday"]
    # A different band genuinely has a different best day.
    assert gb.best_upload_days(1, size_band="1M+", facts=_WEEKDAY_FACTS)[0]["day"] == "Friday"


def test_best_upload_day_agrees_with_the_forecast_multiplier():
    """
    Regression guard for a contradiction that shipped briefly: the metric
    ranked days by views-per-subscriber while the forecast scaled by median
    views, so the dashboard could name Sunday the best day while the
    forecast simultaneously docked Sunday. Both must read the same banded
    quantity, so the best day can never carry a multiplier below 1.
    """
    facts = pd.concat([_WEEKDAY_FACTS, _REACH_FACTS, _SUBTITLE_FACTS])
    best = gb.best_upload_days(1, size_band="0-100", facts=facts)[0]["day"]
    forecast = gb.forecast_reach(0, has_subtitles=False, upload_day=best, facts=facts)
    day_component = next(c for c in forecast["components"] if best in c["factor"])
    assert day_component["multiplier"] > 1.0
    assert day_component["banded"] is True


def test_best_upload_days_returns_none_when_unmaterialised():
    assert gb.best_upload_days(facts=pd.DataFrame()) is None


def test_best_upload_days_returns_none_for_an_unmaterialised_band():
    assert gb.best_upload_days(size_band="10k-100k", facts=_WEEKDAY_FACTS) is None


# --------------------------------------------------------------------------
# Query construction
# --------------------------------------------------------------------------

def test_stratified_facts_group_by_size_band():
    fact = next(f for f in gb.FACTS if f.stratify_by_size)
    sql = " ".join(gb._fact_query(fact).split())
    assert "GROUP BY bucket, size_band" in sql
    assert "uploader_sub_count < 100" in sql   # the banding expression is present


def test_unstratified_facts_do_not():
    fact = next(f for f in gb.FACTS if not f.stratify_by_size)
    sql = " ".join(gb._fact_query(fact).split())
    assert "GROUP BY bucket " in sql + " "
    assert "GROUP BY bucket, size_band" not in sql


def test_every_fact_query_samples_and_filters_low_view_noise():
    for fact in gb.FACTS:
        sql = gb._fact_query(fact)
        assert f"cityHash64(id) % {fact.divisor}" in sql, fact.dimension
        assert "view_count > 1000" in sql, fact.dimension


def test_select_filters_a_preloaded_frame_without_a_query():
    combined = pd.concat([_SUBTITLE_FACTS, _REACH_FACTS])
    assert set(gb._select(combined, "channel_size_band")["dimension"]) == {"channel_size_band"}
    assert gb._select(pd.DataFrame(), "anything").empty


# --------------------------------------------------------------------------
# Trending ingest helpers
# --------------------------------------------------------------------------

@pytest.mark.parametrize("iso,seconds", [
    ("PT59S", 59), ("PT1M", 60), ("PT3M20S", 200), ("PT1H2M3S", 3723),
    ("P1DT2H", 93600), ("PT0S", 0),
])
def test_iso_duration_parsing(iso, seconds):
    assert parse_iso_duration(iso) == seconds


@pytest.mark.parametrize("bad", ["", "garbage", "P0D", None])
def test_unparseable_duration_is_zero_not_a_crash(bad):
    """Live streams report P0D; one odd value must not abort an ingest run."""
    assert parse_iso_duration(bad) == 0


def test_the_shorts_threshold_is_the_documented_one():
    assert SHORT_MAX_SEC == 60
    assert parse_iso_duration("PT60S") <= SHORT_MAX_SEC
    assert parse_iso_duration("PT61S") > SHORT_MAX_SEC


def test_tag_arrays_are_escaped_into_sql():
    """Tags are arbitrary user text going into an INSERT by concatenation."""
    literal = _array_literal(["funny", "it's", "a'); DROP TABLE x; --"])
    assert literal.startswith("[") and literal.endswith("]")
    assert "\\'" in literal
    assert _array_literal([]) == "[]"


# --------------------------------------------------------------------------
# Reach forecast
# --------------------------------------------------------------------------

_REACH_WITH_QUANTILES = _facts([
    {"dimension": "channel_size_band", "bucket": "0-100", "size_band": "",
     "median_views": 2492.0, "p10_views": 1170.0, "p90_views": 15680.0,
     "median_views_per_sub": 139.2, "like_rate_pct": 0.82, "sample_videos": 58044},
])


def test_forecast_returns_a_range_not_a_point():
    f = gb.forecast_reach(0, has_subtitles=False, facts=_REACH_WITH_QUANTILES)
    assert f["p10"] < f["p50"] < f["p90"]


def test_forecast_applies_the_banded_subtitle_factor():
    facts = pd.concat([_REACH_WITH_QUANTILES, _SUBTITLE_FACTS])
    without = gb.forecast_reach(0, has_subtitles=False, facts=facts)
    with_subs = gb.forecast_reach(0, has_subtitles=True, facts=facts)
    # In the 0-100 band captions slightly *reduce* median views, so the
    # forecast must go down, not up. Getting this backwards is exactly the
    # unstratified reading.
    assert with_subs["p50"] < without["p50"]


def test_forecast_exposes_its_derivation():
    """A number a user is asked to act on has to show where it came from."""
    facts = pd.concat([_REACH_WITH_QUANTILES, _SUBTITLE_FACTS])
    f = gb.forecast_reach(0, has_subtitles=True, facts=facts)
    factors = [c["factor"] for c in f["components"]]
    assert "Channel size" in factors and "Kinetic subtitles" in factors
    assert all(c["basis"] for c in f["components"])
    assert f["multiplier"] == pytest.approx(
        f["p50"] / f["base_p50"], rel=1e-6)


def test_forecast_returns_none_for_an_unmaterialised_band():
    assert gb.forecast_reach(5_000_000, facts=_REACH_WITH_QUANTILES) is None


def test_forecast_returns_none_without_materialised_facts():
    assert gb.forecast_reach(0, facts=pd.DataFrame()) is None


# --------------------------------------------------------------------------
# Hook patterns
# --------------------------------------------------------------------------
# This is the only fact that reads `title`, the one expensive column in a
# 4.5B-row table, so its query shape is load-bearing rather than incidental.
# Both guards below encode a failure that already happened once: an
# unfiltered, storage-order sample returned zero contrarian_claim rows
# across 2.9M videos and filed 94% of everything as "plain", because the
# sample was overwhelmingly non-English.

def test_hook_query_samples_across_many_uploader_windows():
    """
    One contiguous block is whichever channels sort first alphabetically.
    Windowing on the sort key spreads the sample and still prunes on the
    primary index, which is what keeps it under the 120s server cap.
    """
    sql = gb.hook_pattern_query()
    assert sql.count("UNION ALL") == len(gb.UPLOADER_WINDOWS) - 2
    assert sql.count("uploader >=") == len(gb.UPLOADER_WINDOWS) - 1
    for boundary in ("'A'", "'M'", "'Y'"):
        assert f"uploader >= {boundary}" in sql or f"uploader < {boundary}" in sql


def test_hook_query_filters_to_english():
    sql = gb.hook_pattern_query()
    assert "lengthUTF8(title)" in sql          # ASCII-only
    assert "the|a|an|of|to|in|is" in sql       # an English function word


def test_hook_query_is_stratified_and_drops_tiny_buckets():
    sql = gb.hook_pattern_query()
    assert "GROUP BY bucket, size_band" in sql
    assert "HAVING sample_videos > 200" in sql


def test_every_taxonomy_hook_is_reachable_in_the_classifier():
    """
    multiIf returns the first match, so a pattern shadowed by an earlier
    branch can never appear. Each of these must be present as an outcome.
    """
    sql = gb.hook_pattern_query()
    for hook in ("shock_stat", "contrarian_claim", "problem_agitation",
                 "metaphor_analogy", "curiosity_gap", "direct_question",
                 "story_in_medias_res", "plain"):
        assert f"'{hook}'" in sql, hook


def test_curiosity_gap_is_tested_before_direct_question():
    """"Why does X happen?" is a curiosity gap that happens to end in "?"."""
    sql = gb.hook_pattern_query()
    assert sql.index("'curiosity_gap'") < sql.index("'direct_question'")


_HOOK_FACTS = _facts([
    {"dimension": "hook_pattern", "bucket": b, "size_band": "0-100",
     "median_views": mv, "sample_videos": n, "median_views_per_sub": 1.0,
     "like_rate_pct": 0.4}
    for b, mv, n in [("curiosity_gap", 3965.0, 9100), ("plain", 3457.0, 160751),
                     ("shock_stat", 3223.0, 3998)]
] + [
    {"dimension": "hook_pattern", "bucket": "plain", "size_band": "1M+",
     "median_views": 20024.0, "sample_videos": 27022, "median_views_per_sub": 0.005,
     "like_rate_pct": 1.4},
])


def test_hook_benchmarks_are_banded_and_ranked():
    rows = gb.hook_benchmarks("0-100", facts=_HOOK_FACTS)
    assert list(rows["bucket"]) == ["curiosity_gap", "plain", "shock_stat"]
    assert set(rows["size_band"]) == {"0-100"}


def test_hook_benchmarks_empty_for_an_unmaterialised_band():
    assert gb.hook_benchmarks("10k-100k", facts=_HOOK_FACTS).empty


def test_hook_benchmarks_empty_without_facts():
    assert gb.hook_benchmarks("0-100", facts=pd.DataFrame()).empty


def test_best_hook_ignores_thin_buckets():
    """
    Regression guard. Ranking on median alone made problem_agitation the
    headline for new channels on 310 videos, ahead of curiosity_gap on
    9,100. The first is a coin flip; only the second is a finding.
    """
    facts = _facts([
        {"dimension": "hook_pattern", "bucket": "problem_agitation", "size_band": "0-100",
         "median_views": 4721.0, "sample_videos": 310},
        {"dimension": "hook_pattern", "bucket": "curiosity_gap", "size_band": "0-100",
         "median_views": 3965.0, "sample_videos": 9100},
        {"dimension": "hook_pattern", "bucket": "plain", "size_band": "0-100",
         "median_views": 3457.0, "sample_videos": 160751},
    ])
    best = gb.best_hook("0-100", facts=facts)
    assert best["hook"] == "curiosity_gap"
    assert best["sample_videos"] == 9100
    assert best["thin_buckets"] == 1
    assert best["lift_pct"] == pytest.approx(14.69, abs=0.1)


def test_best_hook_never_returns_plain():
    """"plain" is the baseline, not a hook you can choose."""
    facts = _facts([
        {"dimension": "hook_pattern", "bucket": "plain", "size_band": "0-100",
         "median_views": 9999.0, "sample_videos": 160751},
        {"dimension": "hook_pattern", "bucket": "curiosity_gap", "size_band": "0-100",
         "median_views": 3965.0, "sample_videos": 9100},
    ])
    assert gb.best_hook("0-100", facts=facts)["hook"] == "curiosity_gap"


def test_best_hook_returns_none_when_everything_is_thin():
    facts = _facts([
        {"dimension": "hook_pattern", "bucket": "curiosity_gap", "size_band": "0-100",
         "median_views": 3965.0, "sample_videos": 12},
    ])
    assert gb.best_hook("0-100", facts=facts) is None


def test_best_hook_returns_none_without_facts():
    assert gb.best_hook("0-100", facts=pd.DataFrame()) is None
