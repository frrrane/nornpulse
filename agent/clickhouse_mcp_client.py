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

Note on performance: each call here spawns a fresh mcp-clickhouse
subprocess and tears it down afterward (~300-500ms overhead per call,
confirmed against ClickHouse Cloud during development). That's an
acceptable tradeoff for a hackathon demo's query volume, but a persistent
session (kept alive across a background event loop) would be the right
next step for production use, since it'd avoid subprocess startup cost on
every single query.
"""

import asyncio
import datetime
import json
import logging
import os
import shutil
import sys
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


async def _call_tool_async(tool_name: str, arguments: Dict[str, Any]) -> str:
    params = _server_params()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            text = "".join(getattr(block, "text", "") for block in result.content)
            if result.isError:
                raise RuntimeError(f"ClickHouse MCP '{tool_name}' failed: {text}")
            return text


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
    raw = _run_sync(_call_tool_async("run_query", {"query": query}))
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
    raw = _run_sync(_call_tool_async("list_databases", {}))
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