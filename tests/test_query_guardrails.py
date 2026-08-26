"""
Unit tests for the ClickHouse query guardrails.

No database. Verdandi and the dashboard put model-written SQL in front of a
warehouse holding a 4.56-billion-row table, which is the case ClickHouse
warned about in the hackathon build session: a question phrased slightly
wrong asks for a scan that never returns, or a result set that exhausts the
client.

The property that matters most is not the limits themselves but the
overflow mode. 'break' would return a silently truncated result, and a
truncated aggregate looks exactly like a real one — this project would then
publish a number nobody measured.
"""

import pytest

from agent import clickhouse_mcp_client as ch


# --- what gets limited ------------------------------------------------------

@pytest.mark.parametrize("query", [
    "SELECT 1",
    "select count() from t",
    "  SELECT * FROM video_hook_retention  ",
    "WITH x AS (SELECT 1) SELECT * FROM x",
])
def test_read_queries_carry_limits(query):
    out = ch.apply_guardrails(query)
    assert "SETTINGS" in out
    assert "max_execution_time" in out


@pytest.mark.parametrize("query", [
    "INSERT INTO t VALUES (1)",
    "CREATE TABLE t (a UInt8) ENGINE = MergeTree ORDER BY a",
    "ALTER TABLE t ADD COLUMN b UInt8",
    "SHOW TABLES",
])
def test_writes_and_ddl_are_left_alone(query):
    """
    A SETTINGS clause on an INSERT either means something else or is a
    syntax error, and the runaway risk is reading rather than writing.
    """
    assert ch.apply_guardrails(query) == query


def test_a_query_with_its_own_settings_is_not_touched():
    """
    Two SETTINGS clauses is invalid SQL, and a caller who wrote one has
    thought about limits more recently than this default has.
    """
    query = "SELECT 1 SETTINGS max_threads=2"
    assert ch.apply_guardrails(query) == query


def test_a_trailing_semicolon_does_not_strand_the_clause():
    """SETTINGS after a semicolon is a syntax error."""
    out = ch.apply_guardrails("SELECT 1;")
    assert out.count("SETTINGS") == 1
    assert ";" not in out.split("SETTINGS")[0].strip()[-1:]


# --- failing loudly ---------------------------------------------------------

@pytest.mark.parametrize("mode", [
    "result_overflow_mode='throw'",
    "read_overflow_mode='throw'",
    "timeout_overflow_mode='throw'",
])
def test_overflow_throws_rather_than_truncating(mode):
    """
    The whole point. 'break' stops early and returns a partial result with
    no indication that it did — and a truncated aggregate is indistinguishable
    from a real one, so the project would publish a number nobody measured.
    """
    assert mode in ch.apply_guardrails("SELECT 1")


def test_the_limits_are_far_above_normal_use():
    """
    Hitting one should mean something went wrong, not that a real query was
    ambitious. The largest local table holds a few hundred rows.
    """
    assert ch.MAX_RESULT_ROWS >= 10_000
    assert ch.MAX_EXECUTION_TIME_SEC >= 30


# --- the deliberate exception -----------------------------------------------

def test_guardrails_can_be_turned_off(monkeypatch):
    """
    The seeding job scans the public 4.56-billion-row dataset on purpose,
    which is precisely the shape of read the defaults exist to stop.
    """
    seen = {}
    monkeypatch.setattr(ch, "_call_tool",
                        lambda tool, args: seen.update(args) or "{}")

    ch.run_query("SELECT count() FROM remote_thing", guardrails=False)
    assert "SETTINGS" not in seen["query"]

    ch.run_query("SELECT count() FROM local_thing")
    assert "SETTINGS" in seen["query"]


def test_the_seeding_job_actually_opts_out():
    """A parameter nothing passes protects nothing."""
    import inspect
    from agent import global_benchmarks as gb
    source = inspect.getsource(gb)
    assert source.count("guardrails=False") >= 3
