"""Smoke test: 通过 langchain-mcp-adapters 启动本地 MCP server，
拿到工具列表，确认 MCP 协议端到端工作。

跑这个脚本验证 B7:
    python -m scripts.test_mcp
"""
from __future__ import annotations

import asyncio

from castflow.tools.mcp_loader import load_mcp_tools


def _safe_print(text: str) -> None:
    print(text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore"))


async def main() -> None:
    _safe_print("===> 启动 castflow-data MCP server (stdio) ...")
    tools = await load_mcp_tools()
    _safe_print(f"===> 加载到 {len(tools)} 个 MCP 工具：")
    for t in tools:
        _safe_print(f"  - {t.name}: {(t.description or '')[:80]}")

    # 试调用 list_orgs
    list_orgs = next((t for t in tools if t.name == "list_orgs"), None)
    if list_orgs is not None:
        _safe_print("\n===> 远程调用 list_orgs() ...")
        result = await list_orgs.ainvoke({})
        _safe_print(f"  结果: {result}")

    # 试调用 load_history
    load_history = next((t for t in tools if t.name == "load_history"), None)
    if load_history is not None:
        _safe_print("\n===> 远程调用 load_history(org=耀州, months=12, before_month=2026-12) ...")
        result = await load_history.ainvoke(
            {"org": "耀州", "months": 12, "before_month": "2026-12"}
        )
        _safe_print(f"  结果: {result[:300]}...")

    _safe_print("\n===> MCP 端到端 smoke test 通过")


if __name__ == "__main__":
    asyncio.run(main())
