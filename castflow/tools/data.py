from typing import Optional

from langchain_core.tools import tool

from castflow.db import db


@tool
def list_orgs() -> dict:
    """列出数据库中所有可用的区县名（中文）。
    任务开始前如果不确定 org 参数取值，先调用此工具。
    """
    try:
        orgs = db.list_orgs() if hasattr(db, "list_orgs") else None
        if orgs is None:
            # MockDB 没有 list_orgs，返回常见 mock 名
            return {"orgs": ["TC01", "TC02", "TC03", "TC04", "TC05"]}
        return {"orgs": orgs, "count": len(orgs)}
    except Exception as e:
        return {"error": str(e)}


def _filter_before(data: list[dict], before_month: Optional[str]) -> list[dict]:
    if not before_month:
        return data
    return [d for d in data if d["month"] < before_month]


@tool
def load_history(
    org: str, months: int = 36, before_month: Optional[str] = None
) -> dict:
    """从数据库加载某区县月度负荷历史。返回统计特征 + 末 12 个月明细。

    Args:
        org: 区县名称。如果不确定可用区县，先调用 list_orgs。
        months: 加载多少个月，建议 24-60
        before_month: 字符串 YYYY-MM。**强烈建议**传入目标预测月，
            返回的数据将严格只包含该月之前的数据，避免训练时数据泄漏。
            例如要预测 2026-12，传 before_month="2026-12"。
    """
    data = db.load_history(org, months)
    if not data:
        return {"error": f"no data for {org}（请先调用 list_orgs 确认区县名）"}
    data = _filter_before(data, before_month)
    if not data:
        return {"error": f"no data for {org} before {before_month}"}
    vals = [d["value"] for d in data]
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return {
        "count": n,
        "range": [data[0]["month"], data[-1]["month"]],
        "mean": round(mean, 2),
        "std": round(var ** 0.5, 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "tail_12": data[-12:],
        "filtered_by_before_month": before_month,
    }


@tool
def load_actual(org: str, month: str) -> dict:
    """加载某月真实负荷用于评估预测准确率。

    Args:
        org: 区县名称
        month: 月份字符串，格式 YYYY-MM
    """
    v = db.load_actual(org, month)
    if v is None:
        return {"found": False, "message": f"{month} 真实数据尚未上报"}
    return {"found": True, "value": v}
