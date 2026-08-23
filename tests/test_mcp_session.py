"""
Unit tests for the persistent ClickHouse MCP session.

No subprocess is spawned here: the session object is replaced with a stub,
so what's under test is the dispatch logic around it — when the persistent
path is used, when it is restarted, and how it degrades. Those are the
parts that fail quietly. A session that silently stops being reused turns
into a 9x slowdown nobody notices; one that doesn't restart on a config
change keeps talking to the old host with the old credentials, because the
subprocess reads its settings once, at spawn.
"""

import concurrent.futures

import pytest

import agent.clickhouse_mcp_client as ch


@pytest.fixture(autouse=True)
def _clean_session(monkeypatch):
    monkeypatch.setattr(ch, "_session", None)
    monkeypatch.setattr(ch, "_PERSISTENT_ENABLED", True)
    monkeypatch.setattr(ch, "resolve_mcp_command", lambda: "/fake/mcp-clickhouse")
    yield
    ch._session = None


class _StubSession:
    """Stands in for a live session without spawning anything."""
    instances: list = []

    def __init__(self, params, fingerprint):
        self.fingerprint = fingerprint
        self._params = params
        self.alive = False
        self.calls = []
        self.stopped = 0
        self.start_error = None
        _StubSession.instances.append(self)

    def start(self, timeout=60.0):
        if self.start_error:
            raise self.start_error
        self.alive = True

    def call(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return '{"columns": ["1"], "rows": [[1]]}'

    def stop(self):
        self.stopped += 1
        self.alive = False


@pytest.fixture
def stub(monkeypatch):
    _StubSession.instances = []
    monkeypatch.setattr(ch, "_PersistentSession", _StubSession)
    return _StubSession


# --------------------------------------------------------------------------
# Reuse — the entire point
# --------------------------------------------------------------------------

def test_one_session_serves_many_calls(stub):
    for _ in range(5):
        ch._call_tool("run_query", {"query": "SELECT 1"})
    assert len(stub.instances) == 1, "a new session per call defeats the optimisation"
    assert len(stub.instances[0].calls) == 5


def test_a_dead_session_is_replaced(stub):
    ch._call_tool("run_query", {"query": "SELECT 1"})
    stub.instances[0].alive = False          # the subprocess exited
    ch._call_tool("run_query", {"query": "SELECT 2"})
    assert len(stub.instances) == 2
    assert stub.instances[1].alive


def test_reset_session_stops_and_clears(stub):
    ch._call_tool("run_query", {"query": "SELECT 1"})
    first = stub.instances[0]
    ch.reset_session()
    assert first.stopped == 1
    assert ch._session is None


# --------------------------------------------------------------------------
# Configuration changes
# --------------------------------------------------------------------------

def test_a_config_change_restarts_the_session(stub, monkeypatch):
    """
    The subprocess reads CLICKHOUSE_* once, at spawn. Reusing it after a
    settings change means querying the old host with the old credentials.
    """
    monkeypatch.setenv("CLICKHOUSE_HOST", "first.example.invalid")
    ch._call_tool("run_query", {"query": "SELECT 1"})
    monkeypatch.setenv("CLICKHOUSE_HOST", "second.example.invalid")
    ch._call_tool("run_query", {"query": "SELECT 1"})

    assert len(stub.instances) == 2
    assert stub.instances[0].stopped == 1, "the stale session must be torn down"
    assert "second.example.invalid" in stub.instances[1].fingerprint


def test_an_unchanged_config_does_not_restart(stub, monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_HOST", "same.example.invalid")
    ch._call_tool("run_query", {"query": "SELECT 1"})
    ch._call_tool("run_query", {"query": "SELECT 2"})
    assert len(stub.instances) == 1


def test_fingerprint_covers_credentials_and_timeout(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "one")
    first = ch._fingerprint(ch._server_params())
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "two")
    assert ch._fingerprint(ch._server_params()) != first

    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "one")
    monkeypatch.setenv("CLICKHOUSE_MCP_QUERY_TIMEOUT", "180")
    assert ch._fingerprint(ch._server_params()) != first


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------

def test_a_failed_start_falls_back_to_a_subprocess(stub, monkeypatch):
    """A broken optimisation must not be worse than not having it."""
    calls = []
    monkeypatch.setattr(ch, "_run_sync", lambda coro: (coro.close(), calls.append(1), "{}")[-1])

    def _boom(params, fingerprint):
        session = _StubSession(params, fingerprint)
        session.start_error = RuntimeError("could not spawn")
        return session
    monkeypatch.setattr(ch, "_PersistentSession", _boom)

    assert ch._call_tool("run_query", {"query": "SELECT 1"}) == "{}"
    assert calls == [1], "should have gone down the ephemeral path"


def test_clickhouse_unavailable_is_not_swallowed_by_the_fallback(stub, monkeypatch):
    """
    A genuinely unreachable ClickHouse must surface as such. Retrying it
    through a subprocess just pays the spawn cost to fail identically, and
    hides the real reason from the connection banner.
    """
    def _unavailable(params, fingerprint):
        session = _StubSession(params, fingerprint)
        session.start_error = ch.ClickHouseUnavailable("mcp-clickhouse not found")
        return session
    monkeypatch.setattr(ch, "_PersistentSession", _unavailable)
    monkeypatch.setattr(ch, "_run_sync", lambda coro: pytest.fail("must not fall back"))

    with pytest.raises(ch.ClickHouseUnavailable, match="not found"):
        ch._call_tool("run_query", {"query": "SELECT 1"})


def test_persistence_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(ch, "_PERSISTENT_ENABLED", False)
    monkeypatch.setattr(ch, "_PersistentSession",
                        lambda *a: pytest.fail("must not build a session"))
    monkeypatch.setattr(ch, "_run_sync", lambda coro: (coro.close(), "{}")[-1])
    assert ch._call_tool("run_query", {"query": "SELECT 1"}) == "{}"


# --------------------------------------------------------------------------
# Timeouts
# --------------------------------------------------------------------------

def test_call_timeout_sits_above_the_server_timeout(monkeypatch):
    """
    The client ceiling must exceed the server's, or the server never gets
    to report its own error and every slow query looks like a hang.
    """
    monkeypatch.setenv("CLICKHOUSE_MCP_QUERY_TIMEOUT", "180")
    assert ch._call_timeout() > 180
    monkeypatch.delenv("CLICKHOUSE_MCP_QUERY_TIMEOUT")
    assert ch._call_timeout() > 30


def test_call_timeout_survives_a_garbage_env_value(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_MCP_QUERY_TIMEOUT", "not-a-number")
    assert ch._call_timeout() > 30


def test_a_timed_out_call_tears_the_session_down(monkeypatch):
    """
    The server may still be mid-response, so the stream state is unknown.
    Reusing it risks reading a stale reply as the answer to the next query.
    """
    session = ch._PersistentSession.__new__(ch._PersistentSession)
    session.alive = True
    session._loop = None

    class _Timeout:
        def result(self, timeout=None):
            raise concurrent.futures.TimeoutError()
        def cancel(self):
            self.cancelled = True

    stopped = []
    session.stop = lambda: stopped.append(1)
    # monkeypatch, not a bare assignment: leaking a stubbed
    # run_coroutine_threadsafe into the rest of the suite would be a
    # miserable thing to debug.
    import asyncio
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe",
                        lambda coro, loop: (coro.close(), _Timeout())[-1])

    with pytest.raises(ch.ClickHouseUnavailable, match="restarted"):
        session.call("run_query", {"query": "SELECT 1"})
    assert stopped == [1]


# --------------------------------------------------------------------------
# Result handling
# --------------------------------------------------------------------------

class _Block:
    def __init__(self, text): self.text = text


class _Result:
    def __init__(self, text, is_error=False):
        self.content = [_Block(text)]
        self.isError = is_error


def test_result_text_joins_blocks():
    result = _Result("")
    result.content = [_Block("abc"), _Block("def")]
    assert ch._result_text("run_query", result) == "abcdef"


def test_an_error_result_raises_with_the_server_message():
    with pytest.raises(RuntimeError, match="table does not exist"):
        ch._result_text("run_query", _Result("table does not exist", is_error=True))
