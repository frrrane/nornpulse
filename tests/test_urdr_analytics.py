"""
Unit tests for Urðr's pure logic and the ClickHouse MCP bridge's
failure handling.

Nothing here touches a real ClickHouse instance: the bridge module is
monkeypatched, and the in-memory fallback path is exercised directly.
That keeps the suite fast and runnable with no credentials, while still
covering the parts most likely to break silently — SQL literal escaping,
the visual-dimension whitelist, connection diagnostics, and the
UNION ALL result splitting.
"""

import datetime

import pandas as pd
import pytest

import agent.clickhouse_mcp_client as ch
from agent.urdr_analytics import UrdrAnalytics, _compute_actual_virality_score


# --------------------------------------------------------------------------
# SQL literal escaping — the injection boundary
# --------------------------------------------------------------------------
# Urðr builds INSERTs by string concatenation because the MCP run_query
# tool takes raw SQL rather than parameters, so sql_literal is the only
# thing standing between transcript/hook text and the database.

def test_sql_literal_escapes_single_quotes():
    assert ch.sql_literal("it's") == "'it\\'s'"


def test_sql_literal_escapes_backslashes_before_quotes():
    # Order matters: escaping quotes first would leave a backslash that
    # then gets doubled, changing the string.
    assert ch.sql_literal("a\\b") == "'a\\\\b'"


def test_sql_literal_neutralises_a_statement_break_attempt():
    hostile = "'); DROP TABLE video_hook_retention; --"
    literal = ch.sql_literal(hostile)
    # The closing quote of the injected prefix must be escaped, so the
    # whole payload stays one string literal.
    assert literal.startswith("'") and literal.endswith("'")
    assert "\\'" in literal
    assert literal.count("'") - literal.count("\\'") == 2  # only the outer pair is live


@pytest.mark.parametrize("value,expected", [
    (None, "NULL"),
    (True, "1"),
    (False, "0"),
    (42, "42"),
    (3.5, "3.5"),
])
def test_sql_literal_scalars(value, expected):
    assert ch.sql_literal(value) == expected


def test_sql_literal_datetime_uses_clickhouse_format():
    dt = datetime.datetime(2026, 8, 21, 14, 30, 5)
    assert ch.sql_literal(dt) == "'2026-08-21 14:30:05'"


# --------------------------------------------------------------------------
# Connection diagnostics
# --------------------------------------------------------------------------

def test_unwrap_exception_digs_through_exception_groups():
    """
    The MCP stdio client runs inside an asyncio TaskGroup, so real
    errors surface as "unhandled errors in a TaskGroup (1 sub-exception)"
    with the actual cause nested inside. Reporting that raw string tells
    a user nothing about what to fix.
    """
    real = ConnectionRefusedError("connection refused")
    wrapped = BaseExceptionGroup("unhandled errors in a TaskGroup", [real])
    nested = BaseExceptionGroup("outer", [wrapped])
    assert "connection refused" in ch._unwrap_exception(nested)
    assert "TaskGroup" not in ch._unwrap_exception(nested)


def test_unwrap_exception_on_a_plain_exception():
    assert ch._unwrap_exception(ValueError("boom")) == "ValueError: boom"


def test_describe_connection_reports_a_missing_binary_actionably(monkeypatch):
    monkeypatch.setattr(ch, "resolve_mcp_command", lambda: (_ for _ in ()).throw(
        ch.ClickHouseUnavailable("The 'mcp-clickhouse' executable could not be found")
    ))
    problem = ch.describe_connection()
    assert problem is not None
    assert "mcp-clickhouse" in problem


def test_describe_connection_reports_query_failure_with_the_host(monkeypatch):
    monkeypatch.setattr(ch, "resolve_mcp_command", lambda: "/fake/mcp-clickhouse")
    monkeypatch.setattr(ch, "run_query", lambda q: (_ for _ in ()).throw(RuntimeError("dns boom")))
    monkeypatch.setenv("CLICKHOUSE_HOST", "db.example.invalid")
    problem = ch.describe_connection()
    assert "db.example.invalid" in problem
    assert "dns boom" in problem
    # Should point at the settings a user can actually change.
    assert "CLICKHOUSE_HOST" in problem


def test_describe_connection_returns_none_when_healthy(monkeypatch):
    monkeypatch.setattr(ch, "resolve_mcp_command", lambda: "/fake/mcp-clickhouse")
    monkeypatch.setattr(ch, "run_query", lambda q: {"columns": ["1"], "rows": [[1]]})
    assert ch.describe_connection() is None


def test_check_connection_is_a_thin_wrapper(monkeypatch):
    monkeypatch.setattr(ch, "describe_connection", lambda: None)
    assert ch.check_connection() is True
    monkeypatch.setattr(ch, "describe_connection", lambda: "broken")
    assert ch.check_connection() is False


# --------------------------------------------------------------------------
# Virality scoring heuristic
# --------------------------------------------------------------------------

def test_virality_score_is_bounded():
    assert 0.0 <= _compute_actual_virality_score(0, 0, 0) <= 100.0
    assert _compute_actual_virality_score(10_000_000, 1_000_000, 500_000) <= 100.0


def test_virality_score_rewards_reach_with_diminishing_returns():
    low = _compute_actual_virality_score(1_000, 0, 0)
    mid = _compute_actual_virality_score(100_000, 0, 0)
    high = _compute_actual_virality_score(10_000_000, 0, 0)
    assert low < mid < high
    # Log-scaled: the first 100x of reach should buy more than the next.
    assert (mid - low) > (high - mid)


def test_virality_score_rewards_engagement_at_equal_reach():
    plain = _compute_actual_virality_score(10_000, 0, 0)
    engaged = _compute_actual_virality_score(10_000, 1_000, 200)
    assert engaged > plain


def test_virality_score_weights_comments_above_likes():
    """A comment costs more effort than a like, so it should count for more."""
    likes = _compute_actual_virality_score(10_000, 300, 0)
    comments = _compute_actual_virality_score(10_000, 0, 300)
    assert comments > likes


def test_virality_score_handles_zero_views_without_dividing_by_zero():
    assert _compute_actual_virality_score(0, 5, 5) >= 0.0


# --------------------------------------------------------------------------
# Visual dimension handling
# --------------------------------------------------------------------------

@pytest.fixture
def offline_urdr(monkeypatch):
    """An Urðr that never touches ClickHouse (fallback mode)."""
    monkeypatch.setattr(ch, "check_connection", lambda: False)
    monkeypatch.setattr(ch, "describe_connection", lambda: "offline for tests")
    return UrdrAnalytics()


def test_unknown_visual_dimension_is_rejected(offline_urdr):
    """
    The dimension becomes a column name interpolated into SQL, which
    can't be passed as a literal — so it must be whitelisted.
    """
    with pytest.raises(ValueError, match="Unknown visual dimension"):
        offline_urdr.get_visual_dimension_benchmarks("crop_mode; DROP TABLE x")


@pytest.mark.parametrize("dimension", UrdrAnalytics.VISUAL_DIMENSIONS)
def test_known_visual_dimensions_are_accepted(offline_urdr, dimension):
    # Offline returns an empty frame rather than raising.
    assert offline_urdr.get_visual_dimension_benchmarks(dimension).empty


def test_get_all_visual_benchmarks_returns_all_keys_when_offline(offline_urdr):
    result = offline_urdr.get_all_visual_benchmarks()
    assert set(result) == set(UrdrAnalytics.VISUAL_DIMENSIONS)
    assert all(df.empty for df in result.values())


def test_get_all_visual_benchmarks_splits_the_union_result(offline_urdr, monkeypatch):
    """
    The combined query returns one frame tagged by `dimension`; it must
    be split back into per-dimension frames whose column is named after
    the dimension, matching what the single-dimension query returns.
    """
    combined = pd.DataFrame([
        {"dimension": "crop_mode", "value": "center_crop", "total_samples": 5,
         "avg_3s_retention": 90.0, "avg_completion_rate": 60.0, "avg_virality_score": 88.0},
        {"dimension": "motion_effect", "value": "shake", "total_samples": 2,
         "avg_3s_retention": 80.0, "avg_completion_rate": 50.0, "avg_virality_score": 70.0},
        {"dimension": "color_grade", "value": "warm_glow", "total_samples": 3,
         "avg_3s_retention": 85.0, "avg_completion_rate": 55.0, "avg_virality_score": 77.0},
    ])
    monkeypatch.setattr(offline_urdr, "_connected", True)
    monkeypatch.setattr(ch, "run_query_df", lambda q: combined)

    result = offline_urdr.get_all_visual_benchmarks()
    assert list(result["crop_mode"].columns)[0] == "crop_mode"
    assert result["crop_mode"].iloc[0]["crop_mode"] == "center_crop"
    assert result["motion_effect"].iloc[0]["motion_effect"] == "shake"
    assert result["color_grade"].iloc[0]["color_grade"] == "warm_glow"
    # The tagging column must not leak into the per-dimension frames.
    assert all("dimension" not in df.columns for df in result.values())


def test_get_all_visual_benchmarks_wraps_the_union_before_ordering(offline_urdr, monkeypatch):
    """
    Regression test. A trailing ORDER BY on a bare UNION ALL binds to
    only the final SELECT, so results came back in the wrong order and
    charts read worst-to-best. The union must be wrapped in a subquery.
    """
    captured = {}

    def _capture(query):
        captured["sql"] = query
        return pd.DataFrame()

    monkeypatch.setattr(offline_urdr, "_connected", True)
    monkeypatch.setattr(ch, "run_query_df", _capture)
    offline_urdr.get_all_visual_benchmarks()

    sql = " ".join(captured["sql"].split())
    assert "UNION ALL" in sql
    order_pos = sql.upper().rindex("ORDER BY")
    close_paren = sql.rindex(")")
    assert close_paren < order_pos, "ORDER BY must sit outside the wrapped union"


def test_offline_fallback_still_serves_a_visual_benchmark(offline_urdr):
    """
    Fallback mode must still return a concrete treatment so rendering
    never blocks on ClickHouse being down.
    """
    bench = offline_urdr.get_top_visual_benchmark("shock_stat")
    assert bench is not None
    assert bench["crop_mode"] and bench["motion_effect"] and bench["color_grade"]


def test_unknown_hook_type_falls_back_rather_than_returning_none(offline_urdr):
    assert offline_urdr.get_top_visual_benchmark("not_a_real_hook_type") is not None


def test_connection_error_is_recorded_when_offline(offline_urdr):
    assert offline_urdr.is_connected() is False
    assert offline_urdr.connection_error  # a reason, not just a bare False


# --------------------------------------------------------------------------
# Unmeasurable outcomes
# --------------------------------------------------------------------------
# Some published_clip_outcomes rows point at videos that are deleted,
# private, or were never actually published. Treating those as "0 views"
# is not the same as measuring zero: it puts a fabricated miss on the
# cross-validation chart. A stale 900,000-view row of exactly this kind
# once dominated the panel, so both directions matter.

def test_logging_an_outcome_defaults_to_available(monkeypatch, offline_urdr):
    captured = {}
    monkeypatch.setattr(offline_urdr, "_connected", True)
    monkeypatch.setattr(ch, "run_query", lambda q: captured.setdefault("sql", q))
    offline_urdr.log_published_outcome(
        clip_id="c", youtube_video_id="v", youtube_url="u", hook_type="h",
        predicted_virality_score=1.0, predicted_3s_retention_pct=2.0)
    assert "video_unavailable" in captured["sql"]
    # sql_literal renders False as 0.
    assert captured["sql"].rstrip().endswith(")")


def test_an_outcome_can_be_logged_as_unavailable(monkeypatch, offline_urdr):
    captured = {}
    monkeypatch.setattr(offline_urdr, "_connected", True)
    monkeypatch.setattr(ch, "run_query", lambda q: captured.setdefault("sql", q))
    offline_urdr.log_published_outcome(
        clip_id="c", youtube_video_id="v", youtube_url="u", hook_type="h",
        predicted_virality_score=1.0, predicted_3s_retention_pct=2.0,
        video_unavailable=True)
    assert "video_unavailable" in captured["sql"]


def test_sync_carries_the_forecast_forward(monkeypatch, offline_urdr):
    """
    sync_actual_stats appends rather than updating in place, so any column
    it fails to carry forward is silently reset — the forecast would be
    wiped on the first stat sync after publishing.
    """
    existing = pd.DataFrame([{
        "clip_id": "c", "youtube_url": "u", "hook_type": "h",
        "predicted_virality_score": 80.0, "predicted_3s_retention_pct": 90.0,
        "forecast_views_p50": 2455.0, "forecast_views_p90": 14989.0,
        "published_at": pd.Timestamp("2026-08-22 18:30:20"),
    }])
    captured = {}
    monkeypatch.setattr(offline_urdr, "_connected", True)
    monkeypatch.setattr(ch, "run_query_df", lambda q: existing)
    monkeypatch.setattr(ch, "run_query", lambda q: captured.setdefault("sql", q))
    monkeypatch.setattr(offline_urdr, "log_actual_outcome_to_benchmarks",
                        lambda **kw: True)

    assert offline_urdr.sync_actual_stats("v", 338, 5, 1) is True
    assert "2455.0" in captured["sql"], "forecast p50 was dropped by the sync"
    assert "14989.0" in captured["sql"], "forecast p90 was dropped by the sync"
    assert "338" in captured["sql"]


# --------------------------------------------------------------------------
# Global hook grounding in the prompt payload
# --------------------------------------------------------------------------
# get_retention_intelligence_summary is the payload Verðandi reasons over,
# so what is true here decides what the pipeline actually produces.

import agent.global_benchmarks as _gb


_GLOBAL_HOOKS = pd.DataFrame([
    {"dimension": "hook_pattern", "bucket": b, "size_band": "0-100",
     "median_views": mv, "sample_videos": n}
    for b, mv, n in [
        ("problem_agitation", 4721.0, 310),     # highest median, far too thin
        ("curiosity_gap", 3965.0, 9100),
        ("plain", 3457.0, 160751),
        ("shock_stat", 3223.0, 3998),           # below the plain baseline
    ]
])

_SEEDED = [
    {"hook_type": "shock_stat", "avg_3s_retention_value": 93.0},
    {"hook_type": "curiosity_gap", "avg_3s_retention_value": 91.0},
    {"hook_type": "problem_agitation", "avg_3s_retention_value": 88.0},
    {"hook_type": "visual_disruption", "avg_3s_retention_value": 90.0},
]


@pytest.fixture
def with_global_hooks(monkeypatch):
    monkeypatch.setattr(_gb, "load_facts", lambda dimension=None: _GLOBAL_HOOKS)


def test_measured_hooks_are_ranked_above_the_seeded_order(offline_urdr, with_global_hooks):
    """
    The seeded benchmarks rank shock_stat first. The measured data says it
    underperforms a plain title for a small channel. The measured one wins.
    """
    ordered, note, top = offline_urdr._apply_global_hooks(list(_SEEDED), 0)
    assert top == "curiosity_gap"
    assert ordered[0]["hook_type"] == "curiosity_gap"
    assert "measured, not assumed" in note


def test_a_thin_bucket_cannot_take_rank_one(offline_urdr, with_global_hooks):
    """
    problem_agitation has the highest median on 310 videos. Ranking on
    median alone would put it first while top_performing_hook_type named
    curiosity_gap — a contradiction, and hook_rank derives from this order,
    so the clip record would also mark it top-tier.
    """
    ordered, _, top = offline_urdr._apply_global_hooks(list(_SEEDED), 0)
    names = [e["hook_type"] for e in ordered]
    assert names.index("curiosity_gap") < names.index("problem_agitation")
    assert ordered[0]["hook_type"] == top


def test_unmeasured_hooks_are_kept_and_marked(offline_urdr, with_global_hooks):
    """
    visual_disruption is not inferable from a title, so it has no global
    figure. It must stay available to the model rather than be dropped, and
    must not be ranked as though it had been measured.
    """
    ordered, _, _ = offline_urdr._apply_global_hooks(list(_SEEDED), 0)
    vd = next(e for e in ordered if e["hook_type"] == "visual_disruption")
    assert vd["global_measured"] is False
    assert "global_median_views" not in vd
    assert ordered[-1]["hook_type"] == "visual_disruption"


def test_lift_is_measured_against_the_plain_baseline(offline_urdr, with_global_hooks):
    ordered, _, _ = offline_urdr._apply_global_hooks(list(_SEEDED), 0)
    gap = next(e for e in ordered if e["hook_type"] == "curiosity_gap")
    assert gap["global_lift_vs_plain_pct"] == pytest.approx(14.7, abs=0.2)
    shock = next(e for e in ordered if e["hook_type"] == "shock_stat")
    assert shock["global_lift_vs_plain_pct"] < 0


def test_missing_global_data_leaves_the_seeded_ranking_untouched(offline_urdr, monkeypatch):
    """Grounding is an improvement, not a dependency — no data, no crash."""
    monkeypatch.setattr(_gb, "load_facts", lambda dimension=None: pd.DataFrame())
    ordered, note, top = offline_urdr._apply_global_hooks(list(_SEEDED), 0)
    assert [e["hook_type"] for e in ordered] == [e["hook_type"] for e in _SEEDED]
    assert note is None and top is None


def test_a_clickhouse_failure_degrades_rather_than_raising(offline_urdr, monkeypatch):
    def _boom(dimension=None):
        raise RuntimeError("clickhouse unreachable")
    monkeypatch.setattr(_gb, "load_facts", _boom)
    ordered, note, top = offline_urdr._apply_global_hooks(list(_SEEDED), 0)
    assert ordered and note is None and top is None


def test_insights_are_derived_not_hardcoded(offline_urdr, with_global_hooks):
    """
    The old payload asserted "Shock Stats and Curiosity Gaps generate >92%
    3-second hold rate" to the model. The measured data contradicts the
    shock_stat half of that, so it was teaching the model something false.
    """
    ordered, _, _ = offline_urdr._apply_global_hooks(list(_SEEDED), 0)
    insights = offline_urdr._derive_insights(ordered, pd.DataFrame())
    joined = " ".join(insights)
    assert "shock_stat" in joined and "underperforms" in joined
    assert ">92%" not in joined


def test_sync_carries_the_publication_date_forward(monkeypatch, offline_urdr):
    """
    Regression guard. sync_actual_stats appends a row and published_at
    defaults to now(), so every sync silently restamped the publication
    date as the sync time. Every clip therefore read as zero days old, and
    any age-aware judgement of its performance was impossible — a clip
    published yesterday looked freshly posted forever.
    """
    published = pd.Timestamp("2026-08-22 18:30:20")
    existing = pd.DataFrame([{
        "clip_id": "clip_1", "youtube_url": "u", "hook_type": "curiosity_gap",
        "predicted_virality_score": 72.5, "predicted_3s_retention_pct": 90.0,
        "forecast_views_p50": 2455.0, "forecast_views_p90": 14989.0,
        "published_at": published,
    }])
    captured = {}
    monkeypatch.setattr(offline_urdr, "_connected", True)
    monkeypatch.setattr(ch, "run_query_df", lambda q: existing)
    monkeypatch.setattr(ch, "run_query", lambda q: captured.setdefault("sql", q))
    monkeypatch.setattr(offline_urdr, "log_actual_outcome_to_benchmarks", lambda **kw: True)

    assert offline_urdr.sync_actual_stats("vid", 338, 5, 1) is True
    assert "published_at" in captured["sql"]
    assert "2026-08-22 18:30:20" in captured["sql"]
