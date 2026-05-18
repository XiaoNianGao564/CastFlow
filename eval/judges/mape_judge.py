"""客观判官 - 直接计算 MAPE 是否达到 case 期望阈值。"""
from __future__ import annotations

from typing import Optional


def mape_judge(actual_mape: Optional[float], mape_max: float) -> dict:
    """返回 score (0/1) + 解释。"""
    if actual_mape is None:
        return {"score": 0, "reason": "no MAPE produced (agent failed to evaluate)"}
    if actual_mape >= 999:
        return {"score": 0, "reason": f"agent gave up (MAPE={actual_mape})"}
    if actual_mape <= mape_max:
        return {"score": 1, "reason": f"MAPE {actual_mape:.2f}% ≤ threshold {mape_max}%"}
    return {"score": 0, "reason": f"MAPE {actual_mape:.2f}% > threshold {mape_max}%"}
