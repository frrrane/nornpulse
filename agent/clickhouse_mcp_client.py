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
from typing import Any, Dict, List

import pandas as pd
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("nornpulse.clickhouse_mcp")


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
    return StdioServerParameters(command="mcp-clickhouse", args=[], env=env)


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


def check_connection() -> bool:
    try:
        result = run_query("SELECT 1")
        return bool(result.get("rows"))
    except Exception as e:
        logger.warning(f"ClickHouse MCP connection check failed: {e}")
        return False