# agent/clickhouse_mcp_client.py
"""
⚡ NornPulse: ClickHouse MCP Bridge (clickhouse_mcp_client.py)
Norn Labs (nornlabs.ai)

Bridges NornPulse's synchronous codebase (Streamlit, google-genai) to the
official ClickHouse MCP server (mcp-clickhouse), launched as a stdio
subprocess. This satisfies the Agentic Cinema ClickHouse track requirement:
runtime ClickHouse access must go through the official MCP server, not a
direct DB client library.

Connection settings come from the same environment variables the MCP
server itself expects (CLICKHOUSE_HOST, CLICKHOUSE_USER, etc.) — set them
in .env exactly as documented at
https://github.com/ClickHouse/mcp-clickhouse.

Note on performance: the MCP server is kept alive as a persistent stdio
session rather than respawned per call. Spawning cost about 3 seconds per
query against ClickHouse Cloud, which was fine at a handful of queries and
became the dominant cost once the dashboard grounded itself in the global
benchmark tables. Set NORNPULSE_MCP_PERSISTENT=0 to fall back to the
per-call behaviour.
"""

import asyncio
import atexit
import concurrent.futures
import datetime
import json
import logging
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("nornpulse.clickhouse_mcp")


class ClickHouseUnavailable(RuntimeError):
    """
    Raised when the MCP server can't be launched or reached at all — as
    opposed to a query that ran and failed. Carries a human-readable
    reason so the UI can tell the user WHY ClickHouse is unavailable
    instead of just showing a generic fallback badge.
    """


def resolve_mcp_command() -> str:
    """
    Resolves the absolute path to the mcp-clickhouse executable.

    Why this isn't just the bare string "mcp-clickhouse": the MCP SDK
    launches the server as a subprocess whose PATH comes from
    mcp.client.stdio.get_default_environment(), which inherits the
    PARENT process's PATH. When the app is started without activating
    the venv -- `venv/bin/streamlit run app.py`, a systemd unit, a
    Docker CMD, a Cloud Run entrypoint -- venv/bin is NOT on PATH, the
    bare name doesn't resolve, and every ClickHouse call fails with
    "No such file or directory: 'mcp-clickhouse'". The app then
    silently degrades to in-memory fallback while still appearing to
    work, which for a ClickHouse-track submission is the worst possible
    failure mode. (Confirmed live: the exact same checkout connects
    fine when launched from an activated venv and fails when launched
    via venv/bin/streamlit directly.)

    Looking beside sys.executable first fixes this for every launch
    style, since console scripts are installed into the same directory
    as the interpreter running them.
    """
    candidate = Path(sys.executable).parent / "mcp-clickhouse"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)

    found = shutil.which("mcp-clickhouse")
    if found:
        return found

    raise ClickHouseUnavailable(
        "The 'mcp-clickhouse' executable could not be found next to the running "
        f"Python interpreter ({Path(sys.executable).parent}) or anywhere on PATH. "
        "Install it with `pip install -r requirements.txt` into the same "
        "environment that runs the app."
    )


def _server_params() -> StdioServerParameters:
    env = {
        "CLICKHOUSE_HOST": os.getenv("CLICKHOUSE_HOST", "localhost"),
        "CLICKHOUSE_USER": os.getenv("CLICKHOUSE_USER", "default"),
        "CLICKHOUSE_PASSWORD": os.getenv("CLICKHOUSE_PASSWORD", ""),
        "CLICKHOUSE_SECURE": os.getenv("CLICKHOUSE_SECURE", "true"),
        "CLICKHOUSE_DATABASE": os.getenv("CLICKHOUSE_DATABASE", "default"),
        # Urdr needs CREATE TABLE (DDL) and INSERT (DML); it never DROPs
        # or TRUNCATEs anything, so that second safety gate stays off.
        "CLICKHOUSE_ALLOW_WRITE_ACCESS": "true",
        "CLICKHOUSE_ALLOW_DROP": "false",
    }
    port = os.getenv("CLICKHOUSE_PORT")
    if port:
        env["CLICKHOUSE_PORT"] = port
    # The MCP server caps every query at 30s by default. Aggregations over
    # the 4.5-billion-row public YouTube dataset (reached via remoteSecure)
    # legitimately run longer than that, so the ceiling has to be liftable.
    query_timeout = os.getenv("CLICKHOUSE_MCP_QUERY_TIMEOUT")
    if query_timeout:
        env["CLICKHOUSE_MCP_QUERY_TIMEOUT"] = query_timeout
        env["CLICKHOUSE_SEND_RECEIVE_TIMEOUT"] = os.getenv(
            "CLICKHOUSE_SEND_RECEIVE_TIMEOUT", query_timeout)
    return StdioServerParameters(command=resolve_mcp_command(), args=[], env=env)


def _result_text(tool_name: str, result) -> str:
    text = "".join(getattr(block, "text", "") for block in result.content)
    if result.isError:
        raise RuntimeError(f"ClickHouse MCP '{tool_name}' failed: {text}")
    return text


async def _call_tool_ephemeral(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Spawn a server, make one call, tear it down. The fallback path."""
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return _result_text(tool_name, await session.call_tool(tool_name, arguments))


# ---------------------------------------------------------------------------
# Persistent session
# ---------------------------------------------------------------------------
# One mcp-clickhouse subprocess, held open across an event loop running in a
# background thread.
#
# The structure is dictated by anyio: stdio_client and ClientSession are
# async context managers backed by task groups, and a task group must be
# exited by the same task that entered it. Holding the session in one place
# and calling it from arbitrary tasks raises "Attempted to exit cancel scope
# in a different task". So a single runner task owns both context managers
# and serves calls from a queue — every await on the session happens inside
# that one task, and callers only ever touch futures.
#
# Calls are therefore serialised. That is a real constraint (a 3-minute
# materialisation blocks a dashboard read behind it) but Streamlit reruns are
# sequential anyway, and it is much cheaper than a subprocess per query.

_PERSISTENT_ENABLED = os.getenv("NORNPULSE_MCP_PERSISTENT", "1").lower() not in ("0", "false", "no")
_SHUTDOWN = object()


def _call_timeout() -> float:
    """
    Client-side ceiling, kept above the server's own query timeout so the
    server reports its own failures rather than being cut off mid-flight.
    """
    try:
        server_timeout = float(os.getenv("CLICKHOUSE_MCP_QUERY_TIMEOUT", "30"))
    except ValueError:
        server_timeout = 30.0
    return server_timeout + 60.0


class _PersistentSession:
    def __init__(self, params: StdioServerParameters, fingerprint: str):
        self._params = params
        self.fingerprint = fingerprint
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._queue: Optional[asyncio.Queue] = None
        self._ready: Optional[asyncio.Future] = None
        self.alive = False

    # -- lifecycle ---------------------------------------------------------

    def start(self, timeout: float = 60.0) -> None:
        started = threading.Event()

        def _thread_main():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._queue = asyncio.Queue()
            self._ready = self._loop.create_future()
            started.set()
            try:
                self._loop.run_until_complete(self._runner())
            finally:
                try:
                    self._loop.close()
                finally:
                    self.alive = False

        self._thread = threading.Thread(
            target=_thread_main, name="clickhouse-mcp", daemon=True)
        self._thread.start()
        if not started.wait(timeout=10):
            raise ClickHouseUnavailable("The ClickHouse MCP session thread did not start.")

        # Surfaces a startup failure (missing binary, unreachable host) to
        # the caller instead of letting the first query fail obscurely.
        future = asyncio.run_coroutine_threadsafe(self._await_ready(), self._loop)
        future.result(timeout=timeout)
        self.alive = True

    async def _await_ready(self):
        await self._ready

    async def _runner(self):
        try:
            async with stdio_client(self._params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    if not self._ready.done():
                        self._ready.set_result(True)
                    while True:
                        item = await self._queue.get()
                        if item is _SHUTDOWN:
                            break
                        tool_name, arguments, result_future = item
                        if result_future.cancelled():
                            continue
                        try:
                            result = await session.call_tool(tool_name, arguments)
                            result_future.set_result(_result_text(tool_name, result))
                        except Exception as exc:            # noqa: BLE001
                            result_future.set_exception(exc)
        except Exception as exc:                            # noqa: BLE001
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(exc)
            logger.warning(f"Persistent ClickHouse MCP session ended: {_unwrap_exception(exc)[:200]}")
        finally:
            self.alive = False
            self._drain()

    def _drain(self):
        """Fail anything still queued rather than leaving callers hanging."""
        if self._queue is None:
            return
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if item is _SHUTDOWN:
                continue
            _, _, result_future = item
            if not result_future.done():
                result_future.set_exception(
                    ClickHouseUnavailable("The ClickHouse MCP session closed before this query ran."))

    def stop(self):
        if self._loop is None or not self._loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._queue.put(_SHUTDOWN), self._loop).result(timeout=5)
        except Exception:                                   # noqa: BLE001
            pass
        self.alive = False

    # -- calling -----------------------------------------------------------

    async def _submit(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        result_future = self._loop.create_future()
        await self._queue.put((tool_name, arguments, result_future))
        return await result_future

    def call(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        if not self.alive:
            raise ClickHouseUnavailable("The ClickHouse MCP session is not running.")
        future = asyncio.run_coroutine_threadsafe(
            self._submit(tool_name, arguments), self._loop)
        try:
            return future.result(timeout=_call_timeout())
        except concurrent.futures.TimeoutError:
            # The server may still be mid-response, so the stream state is
            # unknown — tear the session down rather than reuse it and get
            # a stale reply on the next query.
            future.cancel()
            self.stop()
            raise ClickHouseUnavailable(
                f"ClickHouse MCP '{tool_name}' exceeded {_call_timeout():.0f}s; "
                f"the session was restarted.")


_session: Optional[_PersistentSession] = None
_session_lock = threading.Lock()


def _fingerprint(params: StdioServerParameters) -> str:
    """Config identity. A change (host, credentials, timeout) restarts the session."""
    return json.dumps({"command": params.command, "env": params.env or {}}, sort_keys=True)


def _get_session() -> "_PersistentSession":
    """The live session, started or restarted as needed."""
    global _session
    params = _server_params()
    fingerprint = _fingerprint(params)
    with _session_lock:
        if _session is not None and _session.alive and _session.fingerprint == fingerprint:
            return _session
        if _session is not None:
            # Either it died, or the configuration changed under it — the
            # subprocess reads its settings once, at spawn.
            _session.stop()
        session = _PersistentSession(params, fingerprint)
        session.start()
        _session = session
        logger.info("Started persistent ClickHouse MCP session.")
        return session


def reset_session() -> None:
    """Drop the persistent session; the next call starts a fresh one."""
    global _session
    with _session_lock:
        if _session is not None:
            _session.stop()
            _session = None


atexit.register(reset_session)


def _call_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    """
    One MCP tool call, over the persistent session when possible.

    A session that fails to start falls back to the per-call path rather
    than taking ClickHouse down entirely: the ephemeral route is slower but
    it is the behaviour this bridge shipped with, and a broken optimisation
    should not be worse than not having it.
    """
    if _PERSISTENT_ENABLED:
        try:
            return _get_session().call(tool_name, arguments)
        except ClickHouseUnavailable:
            raise
        except Exception as exc:                            # noqa: BLE001
            logger.warning(
                f"Persistent MCP session unusable ({_unwrap_exception(exc)[:160]}); "
                f"falling back to a per-call subprocess.")
            reset_session()
    return _run_sync(_call_tool_ephemeral(tool_name, arguments))


async def _call_tool_async(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Retained for callers that already have a loop; always ephemeral."""
    return await _call_tool_ephemeral(tool_name, arguments)


def _run_sync(coro):
    """
    Runs an async coroutine from sync call sites. Falls back to a worker
    thread if called from a context that already has an event loop
    running (asyncio.run() cannot be nested).
    """
    try:
        return asyncio.run(coro)
    except RuntimeError as e:
        if "cannot be called from a running event loop" in str(e).lower() or "already running" in str(e).lower():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(coro)).result()
        raise


def sql_literal(value: Any) -> str:
    """
    Escapes a Python value into a ClickHouse SQL literal. Used to build
    INSERT statements manually, since run_query takes a raw SQL string
    rather than parameterized queries.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime.datetime):
        return f"'{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def run_query(query: str) -> Dict[str, Any]:
    """
    Executes SQL via the official ClickHouse MCP server. Returns the
    parsed {"columns": [...], "rows": [[...], ...]} dict for SELECTs, or
    {} for statements with no result set (CREATE TABLE, INSERT).
    """
    raw = _call_tool("run_query", {"query": query})
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"run_query response wasn't JSON, returning raw text: {raw[:200]}")
        return {"raw": raw}


def run_query_df(query: str) -> pd.DataFrame:
    """Runs a SELECT via MCP and returns the result as a DataFrame."""
    data = run_query(query)
    columns = data.get("columns", [])
    rows = data.get("rows", [])
    if not columns:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=columns)


def list_databases() -> List[str]:
    raw = _call_tool("list_databases", {})
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _unwrap_exception(exc: BaseException, _depth: int = 0) -> str:
    """
    Digs the real cause out of an ExceptionGroup. The MCP stdio client
    runs its plumbing inside an asyncio TaskGroup, so any genuine error
    (bad host, refused connection, auth rejection) surfaces to callers
    as the useless string "unhandled errors in a TaskGroup (1
    sub-exception)" with the actual failure buried one or more levels
    down. Reporting that raw string to a user tells them nothing about
    what to fix.
    """
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions and _depth < 5:
        return _unwrap_exception(exc.exceptions[0], _depth + 1)
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def check_connection() -> bool:
    """
    Boolean connectivity probe, kept for callers that only need yes/no.
    Prefer describe_connection() when the reason for a failure matters —
    a silent False is what let a misconfigured deployment look healthy.
    """
    return describe_connection() is None


def describe_connection() -> Optional[str]:
    """
    Probes ClickHouse and returns None when healthy, or a human-readable
    explanation of what went wrong. The distinct failure modes get
    distinct messages, because "ClickHouse is unavailable" is not
    actionable while "the binary is missing" and "the credentials were
    rejected" point at completely different fixes.
    """
    try:
        resolve_mcp_command()
    except ClickHouseUnavailable as e:
        logger.warning(f"ClickHouse MCP unavailable: {e}")
        return str(e)

    try:
        result = run_query("SELECT 1")
    except Exception as e:
        host = os.getenv("CLICKHOUSE_HOST", "localhost")
        cause = _unwrap_exception(e)
        logger.warning(f"ClickHouse MCP connection check failed: {cause}")
        return (
            f"The mcp-clickhouse server started, but querying ClickHouse at "
            f"'{host}' failed: {cause}. Check CLICKHOUSE_HOST / CLICKHOUSE_USER / "
            f"CLICKHOUSE_PASSWORD / CLICKHOUSE_SECURE in your .env, and that the "
            f"instance is running and reachable."
        )

    if not result.get("rows"):
        return (
            "The mcp-clickhouse server responded, but the 'SELECT 1' health check "
            "returned no rows — the server is reachable but not answering queries "
            "as expected."
        )
    return None