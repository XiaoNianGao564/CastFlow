"""通过 langchain-mcp-adapters 把 MCP Server 的工具加载进 LangChain。

这样 Agent 既能用本地原生工具（list_orgs/load_history 等），
也能用任何 MCP-compliant server 暴露的工具——这是 2025 Anthropic 力推的
互操作模式：工具一次定义，任何 MCP 客户端（Claude Desktop/Cursor/...）都能复用。
"""
from __future__ import annotations

import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient


def make_mcp_client() -> MultiServerMCPClient:
    """构造 MCP 客户端，连本地 castflow-data MCP server (stdio transport)。

    Returns:
        MultiServerMCPClient 实例。await client.get_tools() 拿 LangChain Tool 列表。
    """
    project_root = Path(__file__).resolve().parents[2]
    return MultiServerMCPClient(
        {
            "castflow-data": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "mcp_servers.castflow_data"],
                "cwd": str(project_root),
            }
        }
    )


async def load_mcp_tools() -> list:
    """异步加载 MCP server 的工具列表。"""
    client = make_mcp_client()
    return await client.get_tools()
