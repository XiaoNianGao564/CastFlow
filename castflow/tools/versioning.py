from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from castflow.tools.schema import tool_error, tool_success

MIN_ACCEPT_IMPROVEMENT = 0.1
RELATIVE_ACCEPT_IMPROVEMENT = 0.005


def summarize_diagnostics(diagnostics: Optional[Dict[str, Any]], max_points: int = 5) -> dict:
    if not diagnostics:
        return {}
    points = diagnostics.get("per_point_errors") or []
    worst_points = sorted(points, key=lambda p: p.get("abs_error", 0), reverse=True)[:max_points]
    return {
        "mape": diagnostics.get("mape"),
        "mae": diagnostics.get("mae"),
        "rmse": diagnostics.get("rmse"),
        "bias": diagnostics.get("bias"),
        "mpe": diagnostics.get("mpe"),
        "zero_actual_count": diagnostics.get("zero_actual_count", 0),
        "worst_points": worst_points,
    }


def should_accept_candidate(candidate_mape: float, best_mape: float, has_best_code: bool) -> tuple[bool, str]:
    if not has_best_code or best_mape >= 999.0:
        return True, "first valid candidate"
    absolute_gain = best_mape - candidate_mape
    relative_gain = absolute_gain / best_mape if best_mape else 0.0
    if absolute_gain >= MIN_ACCEPT_IMPROVEMENT or relative_gain > RELATIVE_ACCEPT_IMPROVEMENT + 1e-12:
        return True, f"improved by {absolute_gain:.3f} MAPE ({relative_gain:.2%})"
    return False, f"improvement {absolute_gain:.3f} MAPE ({relative_gain:.2%}) below threshold"


def _parse_mape(value: Any) -> float:
    """宽容解析 mape 值：支持 float、int、带%的字符串、纯数字字符串。"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().rstrip("%").strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            pass
    return 999.0


@tool
def submit_candidate(
    code: str,
    mape: Any = None,
    model: str = "",
    validation_mape: Any = None,
    diagnostics: Any = None,
    reason: str = "",
) -> dict:
    """提交最终评估后的候选版本，交给图节点决定接受或回滚。

    Args:
        code: 候选预测代码完整字符串
        mape: 候选在目标月真实值上的最终 MAPE（数字）
        model: 候选模型名称
        validation_mape: 候选在 validation holdout 上的 MAPE
        diagnostics: evaluate_mape 返回的诊断信息（dict）
        reason: 本轮候选说明或改进原因
    """
    if mape is None:
        return tool_error(
            "submit_candidate 失败：必须传入 mape 参数（数字类型，如 9.917）。",
            error="mape is required. 请从 evaluate_mape 的返回值中获取 mape 字段后重新调用。",
            data={"submitted": False},
            next_action="请重新调用 submit_candidate，确保传入 mape 参数（float 数字）。",
            submitted=False,
        )

    parsed_mape = _parse_mape(mape)
    parsed_val_mape = _parse_mape(validation_mape) if validation_mape is not None else None

    if isinstance(diagnostics, str):
        import json as _json
        try:
            diagnostics = _json.loads(diagnostics)
        except (ValueError, TypeError):
            diagnostics = {}
    if not isinstance(diagnostics, dict):
        diagnostics = {}

    next_action = "图节点会与 best_mape 比较；若未接受，下一轮应基于 best_code 和 diagnostics 做 patch。"
    payload = {
        "submitted": True,
        "code": code or "",
        "mape": parsed_mape,
        "model": model or "unknown",
        "validation_mape": parsed_val_mape,
        "diagnostics": diagnostics,
        "reason": reason,
        "decision": "pending_state_comparison",
    }
    return tool_success(
        "候选已提交，等待图节点判定是否接受。",
        data={**payload, "next_action": next_action},
        next_action=next_action,
        **payload,
    )
