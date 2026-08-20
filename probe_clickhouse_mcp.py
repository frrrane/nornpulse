# probe_clickhouse_mcp.py
"""
Run this once, locally, to see exactly what the official ClickHouse MCP
server (mcp-clickhouse) returns from run_query. We need this before
rewriting UrdrAnalytics to parse it correctly.

Usage:
    python probe_clickhouse_mcp.py
"""
import asyncio
import json
import os

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()


async def main():
    env = {
        "CLICKHOUSE_HOST": os.getenv("CLICKHOUSE_HOST", "localhost"),
        "CLICKHOUSE_USER": os.getenv("CLICKHOUSE_USER", "default"),
        "CLICKHOUSE_PASSWORD": os.getenv("CLICKHOUSE_PASSWORD", ""),
        "CLICKHOUSE_SECURE": os.getenv("CLICKHOUSE_SECURE", "true"),
        "CLICKHOUSE_DATABASE": os.getenv("CLICKHOUSE_DATABASE", "default"),
        "CLICKHOUSE_ALLOW_WRITE_ACCESS": "true",
    }
    params = StdioServerParameters(command="mcp-clickhouse", args=[], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("\n=== Tool list ===")
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"- {t.name}: {t.description}")

            print("\n=== run_query('SELECT 1 AS test_col') raw result ===")
            result = await session.call_tool("run_query", {"query": "SELECT 1 AS test_col"})
            print("isError:", result.isError)
            for i, block in enumerate(result.content):
                print(f"--- content block {i} (type={type(block).__name__}) ---")
                print(repr(block))
                if hasattr(block, "text"):
                    print("text attr:", block.text[:1000])
                    try:
                        parsed = json.loads(block.text)
                        print("Parses as JSON! Type:", type(parsed))
                        print(json.dumps(parsed, indent=2)[:1000])
                    except (json.JSONDecodeError, TypeError):
                        print("Does NOT parse as JSON — likely plain text/CSV/table format.")

            print("\n=== list_databases raw result ===")
            result2 = await session.call_tool("list_databases", {})
            for block in result2.content:
                if hasattr(block, "text"):
                    print(block.text[:500])


if __name__ == "__main__":
    asyncio.run(main())