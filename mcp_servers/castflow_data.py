"""castflow-data MCP Server.

Exposes CastFlow's power-load data as standard MCP tools + resources,
following the Anthropic Model Context Protocol (2025).

Usage:
    # Standalone (stdio transport)
    python -m mcp_servers.castflow_data

    # In Claude Desktop / Cursor / any MCP client, register:
    {
      "mcpServers": {
        "castflow-data": {
          "command": "python",
          "args": ["-m", "mcp_servers.castflow_data"],
          "cwd": "C:/Users/17166/Desktop/CastFlow",
          "env": {
            "DASHSCOPE_API_KEY": "...",
            "USE_REAL_DB": "true",
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_USER": "root",
            "MYSQL_PASSWORD": "root",
            "MYSQL_DB": "sxfhyc11"
          }
        }
      }
    }
"""
from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

from castflow.db import db

mcp = FastMCP("castflow-data")


# ============================================================
# Tools - 给 LLM 调用的能力
# ============================================================

@mcp.tool()
def list_orgs() -> str:
    """List all available org names (Chinese district names) in the database.
    Always call this first if you're unsure which org to query.
    """
    try:
        orgs = db.list_orgs() if hasattr(db, "list_orgs") else None
        if orgs is None:
            orgs = ["TC01", "TC02", "TC03", "TC04", "TC05"]
        return json.dumps({"orgs": orgs, "count": len(orgs)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def load_history(
    org: str,
    months: int = 36,
    before_month: Optional[str] = None,
) -> str:
    """Load monthly power-load history for one district.

    Args:
        org: District name (Chinese, e.g. 耀州). Use list_orgs first if unsure.
        months: How many months to load (24-60 recommended).
        before_month: YYYY-MM. Strongly recommended: pass the target forecast
            month so training data won't include the target month (prevents
            data leakage).
    """
    data = db.load_history(org, months)
    if not data:
        return json.dumps(
            {"error": f"no data for {org} (call list_orgs to confirm)"},
            ensure_ascii=False,
        )
    if before_month:
        data = [d for d in data if d["month"] < before_month]
    if not data:
        return json.dumps(
            {"error": f"no data for {org} before {before_month}"},
            ensure_ascii=False,
        )
    vals = [d["value"] for d in data]
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return json.dumps(
        {
            "count": n,
            "range": [data[0]["month"], data[-1]["month"]],
            "mean": round(mean, 2),
            "std": round(var ** 0.5, 2),
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "tail_12": data[-12:],
            "filtered_by_before_month": before_month,
        },
        ensure_ascii=False,
    )


@mcp.tool()
def load_actual(org: str, month: str) -> str:
    """Load the actual (ground-truth) power-load value for one month.
    Use this to evaluate your forecast against reality.

    Args:
        org: District name
        month: YYYY-MM
    """
    v = db.load_actual(org, month)
    if v is None:
        return json.dumps(
            {"found": False, "message": f"no actual for {org} {month}"},
            ensure_ascii=False,
        )
    return json.dumps({"found": True, "value": v}, ensure_ascii=False)


# ============================================================
# Resources - 让客户端订阅的只读视图
# ============================================================

@mcp.resource("castflow://orgs")
def orgs_resource() -> str:
    """All available district names as a JSON array."""
    try:
        orgs = db.list_orgs() if hasattr(db, "list_orgs") else []
        return json.dumps(orgs, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.resource("castflow://org/{org}/profile")
def org_profile_resource(org: str) -> str:
    """Per-district profile: range, mean, std, recent 12 months.

    URI template: castflow://org/{org}/profile (e.g. castflow://org/耀州/profile)
    """
    data = db.load_history(org, 60)
    if not data:
        return json.dumps({"error": f"no data for {org}"}, ensure_ascii=False)
    vals = [d["value"] for d in data]
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return json.dumps(
        {
            "org": org,
            "count": n,
            "range": [data[0]["month"], data[-1]["month"]],
            "mean": round(mean, 2),
            "std": round(var ** 0.5, 2),
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "tail_12": data[-12:],
        },
        ensure_ascii=False,
    )


# ============================================================
# Prompts - 复用 prompt 模板
# ============================================================

@mcp.prompt()
def forecast_task(org: str, target_month: str, target_mape: float = 5.0) -> str:
    """Generate a forecast task prompt for any LLM client (Claude Desktop, Cursor, etc.)."""
    return (
        f"使用 castflow-data MCP 提供的工具，预测 {org} 区 {target_month} 月的电力负荷。\n"
        f"目标 MAPE ≤ {target_mape}%。\n\n"
        f"建议步骤：\n"
        f"1. list_orgs 确认区县名\n"
        f"2. load_history(org={org}, before_month={target_month}) 拿训练数据\n"
        f"3. 选模型（ARIMA/SARIMAX/LightGBM）写 Python 代码并跑通\n"
        f"4. load_actual(org={org}, month={target_month}) 拿真值\n"
        f"5. 算 MAPE 并报告\n"
    )


def main() -> None:
    """stdio transport entrypoint - MCP 客户端通过 stdin/stdout 通信"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
