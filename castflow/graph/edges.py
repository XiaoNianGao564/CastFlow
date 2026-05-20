from langgraph.graph import END

from castflow.state import ForecastState


# --- 迭代护栏：无效循环检测 ---

_MAPE_IMPROVEMENT_THRESHOLD = 0.1  # 绝对 MAPE 改善至少 0.1% 才算有效进步
_MAPE_RELATIVE_THRESHOLD = 0.005  # 相对改善至少 0.5%
_STAGNATION_WINDOW = 3  # 连续 N 轮无有效改善则触发早停
_MAX_SAME_TOOL_REPEAT = 4  # 同一工具连续调用超过此次数视为无效循环
_DIMINISHING_RETURNS_WINDOW = 4  # 用于计算改善速率的窗口
_MIN_IMPROVEMENT_RATE = 0.05  # 每轮平均改善低于此值(%)视为收益递减


def _detect_stagnation(state: ForecastState) -> bool:
    """检测 MAPE 是否陷入停滞：连续 N 轮迭代改善幅度不足。"""
    iterations = state.get("iterations", [])
    if len(iterations) < _STAGNATION_WINDOW:
        return False
    recent = iterations[-_STAGNATION_WINDOW:]
    mapes = [it["mape"] for it in recent if it.get("mape") is not None]
    if len(mapes) < _STAGNATION_WINDOW:
        return False
    best_in_window = min(mapes)
    worst_in_window = max(mapes)
    absolute_range = worst_in_window - best_in_window
    if absolute_range < _MAPE_IMPROVEMENT_THRESHOLD:
        return True
    best_mape = state.get("best_mape", 999.0)
    if best_mape < 999.0 and all(
        abs(m - best_mape) < _MAPE_IMPROVEMENT_THRESHOLD for m in mapes
    ):
        return True
    return False


def _detect_tool_loop(state: ForecastState) -> bool:
    """检测是否陷入工具调用循环：同一工具连续被调用超过阈值，
    或同一工具反复失败（Error invoking）超过 3 次。"""
    recent_tools = []
    error_tools: dict[str, int] = {}
    for msg in reversed(state["messages"][-30:]):
        if getattr(msg, "type", None) == "tool":
            name = getattr(msg, "name", "")
            content = str(getattr(msg, "content", ""))
            if name:
                recent_tools.append(name)
            if "Error invoking tool" in content or ('"success": false' in content.lower() and '"submitted": false' in content.lower()):
                error_tools[name] = error_tools.get(name, 0) + 1
        elif getattr(msg, "type", None) == "ai" and not getattr(msg, "tool_calls", None):
            break
        if len(recent_tools) >= _MAX_SAME_TOOL_REPEAT:
            break
    if len(recent_tools) >= _MAX_SAME_TOOL_REPEAT:
        if len(set(recent_tools[:_MAX_SAME_TOOL_REPEAT])) == 1:
            return True
    for name, count in error_tools.items():
        if count >= 3:
            return True
    return False


def _detect_repeated_failure(state: ForecastState) -> bool:
    """检测连续失败模式：最近的候选全部被拒绝且 MAPE 无改善趋势。"""
    rejected = state.get("rejected_candidates", [])
    if len(rejected) < 3:
        return False
    recent_rejected = rejected[-3:]
    mapes = [r.get("mape", 999) for r in recent_rejected]
    if all(m > state.get("best_mape", 999.0) for m in mapes):
        if max(mapes) - min(mapes) < _MAPE_IMPROVEMENT_THRESHOLD * 2:
            return True
    return False


def _compute_improvement_rate(state: ForecastState) -> float:
    """计算最近几轮的平均 MAPE 改善速率。返回每轮平均改善百分点。
    正值表示在改善，0或负值表示停滞/恶化。"""
    iterations = state.get("iterations", [])
    if len(iterations) < 2:
        return 999.0  # 数据不足，不触发早停
    window = iterations[-_DIMINISHING_RETURNS_WINDOW:]
    mapes = [it["mape"] for it in window if it.get("mape") is not None and it["mape"] < 900]
    if len(mapes) < 2:
        return 999.0
    improvements = [mapes[i] - mapes[i + 1] for i in range(len(mapes) - 1)]
    return sum(improvements) / len(improvements)


def _should_early_stop(state: ForecastState) -> tuple[bool, str]:
    """综合早停判断，返回 (是否早停, 原因)。"""
    iterations = state.get("iterations", [])
    best_mape = state.get("best_mape", 999.0)

    # 已达标，不需要早停（会在 should_continue 中正常收尾）
    if best_mape <= state.get("target_mape", 5.0):
        return False, ""

    # 条件1：MAPE 停滞
    if _detect_stagnation(state):
        return True, f"MAPE停滞：最近{_STAGNATION_WINDOW}轮改善幅度<{_MAPE_IMPROVEMENT_THRESHOLD}%"

    # 条件2：收益递减 — 改善速率过低且已有足够迭代
    if len(iterations) >= _DIMINISHING_RETURNS_WINDOW:
        rate = _compute_improvement_rate(state)
        if rate < _MIN_IMPROVEMENT_RATE and best_mape < 900:
            return True, f"收益递减：平均改善速率{rate:.3f}%/轮<阈值{_MIN_IMPROVEMENT_RATE}%"

    # 条件3：连续失败
    if _detect_repeated_failure(state):
        return True, "连续失败：最近3个候选均被拒绝且MAPE无改善趋势"

    return False, ""


def should_continue(state: ForecastState) -> str:
    """orchestrator 之后：有 tool_calls → tools；否则按候选状态决定是否收尾。"""
    # Budget 耗尽 → 立即收尾
    if state.get("early_stop_reason", "").startswith("token budget"):
        return "force_finalize"

    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    completed_candidates = len(state.get("iterations", []))
    if completed_candidates >= state["max_iterations"]:
        return "force_finalize"
    if state.get("best_mape", 999.0) <= state["target_mape"] and state.get("best_code"):
        return "force_finalize"
    hard_turn_limit = max(state["max_iterations"] * 6, state["max_iterations"] + 12)
    if state["iteration_count"] >= hard_turn_limit:
        return "force_finalize"
    # 连续 3 轮 orchestrator 不调工具 → 强制收尾，防止空转浪费 token
    no_tool_streak = 0
    for msg in reversed(state["messages"][-6:]):
        if getattr(msg, "type", None) == "ai" and not getattr(msg, "tool_calls", None):
            no_tool_streak += 1
        else:
            break
    if no_tool_streak >= 3:
        return "force_finalize"
    # 无效循环检测：工具重复调用
    if _detect_tool_loop(state):
        return "force_finalize"
    return "orchestrator"


def after_tools(state: ForecastState) -> str:
    """tools 之后：finalize → END；needs_replan → planner；否则回 orchestrator。"""
    for msg in reversed(state["messages"]):
        if getattr(msg, "name", None) == "finalize":
            return END
        if getattr(msg, "type", None) == "tool":
            break

    # replan 分支：reflector 触发且未超上限
    if state.get("needs_replan") and int(state.get("replan_count", 0) or 0) < 3:
        return "planner"

    completed_candidates = len(state.get("iterations", []))
    if completed_candidates >= state["max_iterations"]:
        return "force_finalize"
    hard_turn_limit = max(state["max_iterations"] * 6, state["max_iterations"] + 12)
    if state["iteration_count"] >= hard_turn_limit:
        return "force_finalize"

    # 迭代护栏：综合早停判断（MAPE停滞 + 收益递减 + 连续失败）
    should_stop, stop_reason = _should_early_stop(state)
    if should_stop:
        # 已有 best_code 且 replan 次数用尽 → 直接收尾
        if state.get("best_code") and int(state.get("replan_count", 0) or 0) >= 2:
            return "force_finalize"
        # 还有 replan 机会 → 给一次 replan 尝试
        if int(state.get("replan_count", 0) or 0) < 2:
            return "planner"
        return "force_finalize"

    return "orchestrator"
