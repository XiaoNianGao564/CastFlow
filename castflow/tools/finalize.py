from __future__ import annotations

from typing import List

from langchain_core.tools import tool

from castflow.db import db
from castflow.tools.schema import tool_error, tool_success


@tool
def finalize(best_code: str, best_mape: float, reason: str) -> dict:
    """达标或迭代上限时调用此工具显式收尾。

    Args:
        best_code: 本次任务中产出的最佳预测代码
        best_mape: 最佳 MAPE 数值
        reason: 收尾原因（达标 / 迭代上限 / 其他）
    """
    payload = {
        "finalized": True,
        "best_mape": best_mape,
        "reason": reason,
        "code_length": len(best_code or ""),
    }
    return tool_success(
        "任务已显式收尾。",
        data=payload,
        next_action="结束本次预测任务。",
        **payload,
    )


@tool
def save_predictions(
    org: str, months: List[str], predictions: List[float], expect_type: int = 1
) -> dict:
    """将预测值写入数据库预测值表 tb_dlyc_area_qx_month_pred。
    在 finalize 之前必须调用此工具保存预测结果。

    Args:
        org: 区县名称（中文，如「耀州」）
        months: 月份列表，格式 YYYY-MM（如 ["2026-01","2026-02",...]）
        predictions: 预测值列表，与 months 等长
        expect_type: 1=区民, 2=煤改电
    """
    if not months or not predictions:
        return tool_error("月份和预测值不能为空。", "months 和 predictions 不能为空")
    if len(months) != len(predictions):
        return tool_error(
            f"月份({len(months)})与预测值({len(predictions)})长度不一致。",
            f"months({len(months)}) != predictions({len(predictions)})",
        )
    try:
        count = db.save_predictions(org, months, predictions, expect_type)
        return tool_success(
            f"已写入 {org} 的 {count} 条预测值到 tb_dlyc_area_qx_month_pred。",
            data={"org": org, "written": count, "expect_type": expect_type},
            next_action="调用 finalize 结束任务。",
            org=org,
            written=count,
            expect_type=expect_type,
        )
    except Exception as e:
        return tool_error("写入预测值失败。", str(e))
