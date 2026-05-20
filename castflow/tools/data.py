from typing import Optional

from langchain_core.tools import tool

from castflow.db import db
from castflow.tools.schema import tool_error, tool_success


@tool
def list_orgs() -> dict:
    """列出数据库中所有可用的区县名（中文）。
    任务开始前如果不确定 org 参数取值，先调用此工具。
    """
    try:
        orgs = db.list_orgs() if hasattr(db, "list_orgs") else None
        if orgs is None:
            # MockDB 没有 list_orgs，返回常见 mock 名
            return tool_success(
                "返回 mock 区县列表。",
                data={"orgs": ["TC01", "TC02", "TC03", "TC04", "TC05"], "count": 5},
                next_action="选择一个 org 后调用 load_history。",
                orgs=["TC01", "TC02", "TC03", "TC04", "TC05"],
                count=5,
            )
        return tool_success(
            f"找到 {len(orgs)} 个可用区县。",
            data={"orgs": orgs, "count": len(orgs)},
            next_action="选择一个 org 后调用 load_history。",
            orgs=orgs,
            count=len(orgs),
        )
    except Exception as e:
        return tool_error("列出区县失败。", str(e), orgs=[])


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
        months: 最大加载月数，**默认 36 即可，通常无需修改**。工具会自动返回数据库中 `before_month` 之前的所有可用历史数据。
        before_month: 字符串 YYYY 或 YYYY-MM。传入目标预测年/月，
            **工具会自动返回数据库中该时间点之前实际存在的全部历史数据**，
            不是精确到该月，而是以此为截止点向前取所有可用数据。
            例如要预测 2026 年，传 before_month="2026"。
    """
    data = db.load_history(org, months, before_month=before_month)
    if not data:
        msg = f"no data for {org} before {before_month}（请先调用 list_orgs 确认区县名）"
        return tool_error(
            "历史数据为空。",
            msg,
            next_action="调用 list_orgs 确认区县名，或扩大 months。",
        )
    vals = [d["value"] for d in data]
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    payload = {
        "count": n,
        "range": [data[0]["month"], data[-1]["month"]],
        "mean": round(mean, 2),
        "std": round(var ** 0.5, 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "tail_12": data[-12:],
        "data": data,
        "filtered_by_before_month": before_month,
    }
    legacy_payload = {k: v for k, v in payload.items() if k != "data"}
    return tool_success(
        f"已加载 {org} 的 {n} 条历史数据。",
        data=payload,
        next_action="基于历史数据生成预测代码。",
        **legacy_payload,
    )


@tool
def load_actual(org: str, month: str) -> dict:
    """加载某月真实负荷用于评估预测准确率。

    Args:
        org: 区县名称
        month: 月份字符串，格式 YYYY-MM
    """
    v = db.load_actual(org, month)
    if v is None:
        return tool_error(
            f"{month} 真实数据尚未上报。",
            f"{month} 真实数据尚未上报",
            data={"found": False, "message": f"{month} 真实数据尚未上报"},
            next_action="等待真实值上报后再评估。",
            found=False,
            message=f"{month} 真实数据尚未上报",
        )
    return tool_success(
        f"已加载 {org} {month} 的真实值。",
        data={"found": True, "value": v},
        next_action="将该值与预测值传给 evaluate_mape。",
        found=True,
        value=v,
    )
