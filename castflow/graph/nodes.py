import json

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from castflow.llm.client import make_orchestrator_llm
from castflow.llm.router import BudgetExhaustedError, get_total_tokens, log_budget_summary
from castflow.memory.episodic import get_episodic
from castflow.prompts import ORCHESTRATOR_SYSTEM
from castflow.state import ForecastState
from castflow.tools import finalize as finalize_tool, get_core_tools
from castflow.tools.versioning import should_accept_candidate, summarize_diagnostics
from castflow.graph.edges import _compute_improvement_rate

CORE_TOOLS = get_core_tools()

_llm = make_orchestrator_llm().bind_tools(CORE_TOOLS)
# 不使用 tool_choice 参数：qwen3 系列 thinking mode 不兼容该参数
# 通过 prompt 强制引导 LLM 调用 finalize
_finalize_llm = make_orchestrator_llm().bind_tools([finalize_tool])


# --- Sub-agent context isolation: ToolMessage 瘦身 ---

_HEAVY_TOOLS = {"delegate_to_coder", "delegate_to_verifier", "delegate_to_reflector"}
_MAX_TOOL_MSG_CHARS = 1200
_MAX_MESSAGES_FOR_LLM = 40

# 上下文工程：工具重要性权重（高权重的消息在截断时优先保留）
_TOOL_IMPORTANCE = {
    "delegate_to_coder": 3,      # 核心产出
    "delegate_to_verifier": 2,   # 审查结论
    "delegate_to_reflector": 3,  # 策略建议
    "evaluate_mape": 3,          # 评估结果
    "submit_candidate": 2,       # 提交记录
    "load_history": 1,           # 数据加载（一次性）
    "list_orgs": 0,              # 低价值，可丢弃
    "recall_similar_runs": 1,    # 历史参考
    "recall_lessons": 1,         # 经验参考
    "save_lesson": 0,            # 写入操作，结果不重要
    "save_predictions": 0,       # 写入操作
    "search_knowledge_docs": 1,  # 知识检索
    "run_python": 2,             # 代码执行结果
    "load_actual": 1,            # 真实值
}


def _truncate_messages(messages: list, max_count: int = _MAX_MESSAGES_FOR_LLM) -> list:
    """智能截断消息列表：优先保留高重要性消息和最近消息。
    确保不会从 ToolMessage 开头（需要前面有对应的 AI tool_calls 消息）。"""
    if len(messages) <= max_count:
        return messages

    # 始终保留前 4 条（系统消息 + 初始任务描述）和最近 max_count//2 条
    head_keep = min(4, len(messages))
    tail_keep = max_count * 2 // 3
    middle = messages[head_keep:-tail_keep] if len(messages) > head_keep + tail_keep else []

    if not middle:
        truncated = messages[-max_count:]
    else:
        # 对中间消息按重要性排序，保留高重要性的
        middle_budget = max_count - head_keep - tail_keep
        scored_middle = []
        for i, msg in enumerate(middle):
            importance = 1
            if getattr(msg, "type", None) == "tool":
                name = getattr(msg, "name", "")
                importance = _TOOL_IMPORTANCE.get(name, 1)
            elif getattr(msg, "type", None) == "ai" and getattr(msg, "tool_calls", None):
                importance = 2  # AI 的工具调用决策有参考价值
            scored_middle.append((importance, i, msg))

        scored_middle.sort(key=lambda x: (-x[0], x[1]))
        kept_middle = sorted(scored_middle[:middle_budget], key=lambda x: x[1])
        truncated = messages[:head_keep] + [m[2] for m in kept_middle] + messages[-tail_keep:]

    # 确保不从 ToolMessage 开头
    while truncated and getattr(truncated[0], "type", None) == "tool":
        truncated = truncated[1:]
    if not truncated:
        return messages[-max_count:]
    return truncated


def _slim_messages(messages: list) -> list:
    """对 ToolMessage 做瘦身，避免 LLM context 膨胀。
    - 子 agent 返回：只保留 summary/next_action/顶层指标
    - 低重要性工具：激进截断
    - 高重要性工具：保留关键字段
    - 其他工具：截断超长内容"""
    out = []
    for msg in messages:
        if getattr(msg, "type", None) != "tool":
            out.append(msg)
            continue
        name = getattr(msg, "name", "")
        content = getattr(msg, "content", "")
        importance = _TOOL_IMPORTANCE.get(name, 1)

        # 低重要性工具（list_orgs, save_lesson 等）：极度压缩
        if importance == 0 and isinstance(content, str) and len(content) > 200:
            tool_call_id = getattr(msg, "tool_call_id", None) or ""
            out.append(ToolMessage(
                content=content[:200] + "\n...[low-priority, truncated]",
                tool_call_id=tool_call_id,
                name=name,
            ))
            continue

        # 子 agent 返回：提取关键指标
        if name in _HEAVY_TOOLS and isinstance(content, str) and len(content) > _MAX_TOOL_MSG_CHARS:
            try:
                parsed = json.loads(content)
                slim = {k: v for k, v in parsed.items() if k != "data"}
                slim.pop("saved", None)
                slim.pop("code", None)
                slim_str = json.dumps(slim, ensure_ascii=False)
                tool_call_id = getattr(msg, "tool_call_id", None) or ""
                out.append(ToolMessage(
                    content=slim_str[:_MAX_TOOL_MSG_CHARS],
                    tool_call_id=tool_call_id,
                    name=name,
                ))
                continue
            except (json.JSONDecodeError, TypeError):
                pass

        # 高重要性工具（evaluate_mape, run_python）：保留更多内容
        max_chars = 2000 if importance >= 2 else 1200
        if isinstance(content, str) and len(content) > max_chars:
            tool_call_id = getattr(msg, "tool_call_id", None) or ""
            out.append(ToolMessage(
                content=content[:max_chars] + "\n...[truncated]",
                tool_call_id=tool_call_id,
                name=name,
            ))
            continue
        out.append(msg)
    return out


def _parse_tool_content(content) -> dict:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return {}
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _tool_payload(msg) -> dict:
    payload = _parse_tool_content(getattr(msg, "content", ""))
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        merged = {**payload, **data}
        return merged
    return payload


def _state_updates_from_tools(state: ForecastState) -> dict:
    updates: dict = {}
    candidate_info = None
    evaluation_info = None
    coder_info = None
    reflector_info = None

    for msg in reversed(state.get("messages", [])):
        if getattr(msg, "type", None) != "tool":
            break
        name = getattr(msg, "name", "")
        payload = _tool_payload(msg)
        if name == "submit_candidate" and candidate_info is None:
            candidate_info = payload
        elif name == "evaluate_mape" and evaluation_info is None:
            evaluation_info = payload
        elif name == "delegate_to_coder" and coder_info is None:
            coder_info = payload
        elif name == "delegate_to_reflector" and reflector_info is None:
            reflector_info = payload

    # Reflector 触发 replan
    if reflector_info:
        updates["needs_replan"] = True
        updates["reflector_strategy"] = {
            "root_cause": reflector_info.get("root_cause", ""),
            "next_strategy": reflector_info.get("next_strategy", ""),
            "avoid_models": reflector_info.get("avoid_models", []),
        }

    # Coder 产物 → state + artifacts
    if coder_info:
        if coder_info.get("candidate_summaries") is not None:
            updates["candidate_summaries"] = coder_info.get("candidate_summaries")
        if coder_info.get("selection_reason"):
            updates["selection_reason"] = coder_info.get("selection_reason")
        if coder_info.get("code"):
            updates["candidate_code"] = coder_info.get("code", "")
            updates["best_code"] = coder_info.get("code", "")
            # 存入 artifacts（大字段不留在 messages 里）
            version = len(state.get("iterations", [])) + 1
            artifacts = dict(state.get("artifacts") or {})
            artifacts[f"coder.v{version}.code"] = {
                "kind": "code",
                "content": coder_info["code"],
                "created_at": "",
                "source": "coder",
            }
            updates["artifacts"] = artifacts
        if coder_info.get("best_model"):
            updates["best_model"] = coder_info.get("best_model")
        if coder_info.get("best_validation_mape") is not None:
            updates["best_validation_mape"] = coder_info.get("best_validation_mape")
        if coder_info.get("validation_diagnostics"):
            updates["validation_diagnostics"] = coder_info.get("validation_diagnostics")

    if evaluation_info and not evaluation_info.get("error"):
        updates["last_diagnostics"] = summarize_diagnostics(evaluation_info)
        if evaluation_info.get("mape") is not None:
            updates["candidate_mape"] = float(evaluation_info["mape"])

    if not candidate_info or not candidate_info.get("submitted"):
        return updates

    candidate_mape = float(candidate_info.get("mape", 999.0))
    best_mape = float(state.get("best_mape", 999.0))
    candidate_code = candidate_info.get("code") or state.get("candidate_code", "")
    diagnostics = summarize_diagnostics(candidate_info.get("diagnostics") or evaluation_info or {})
    model = candidate_info.get("model") or (coder_info or {}).get("best_model") or "unknown"
    validation_mape = candidate_info.get("validation_mape")
    if validation_mape is None and coder_info:
        validation_mape = coder_info.get("best_validation_mape")

    accepted, acceptance_reason = should_accept_candidate(
        candidate_mape=candidate_mape,
        best_mape=best_mape,
        has_best_code=bool(state.get("best_code")),
    )
    record = {
        "version": len(state.get("iterations", [])) + 1,
        "code": candidate_code,
        "mape": candidate_mape,
        "insight": acceptance_reason if accepted else f"{acceptance_reason}; keep previous best_code",
        "model": model,
        "validation_mape": validation_mape,
        "accepted": accepted,
        "reason": candidate_info.get("reason", ""),
        "diagnostics": diagnostics,
        "candidate_model": model,
    }
    updates["iterations"] = [record]
    updates["last_diagnostics"] = diagnostics
    updates["candidate_mape"] = candidate_mape
    updates["candidate_code"] = candidate_code

    # 早停策略：追踪 MAPE 历史和改善速率
    mape_history = list(state.get("mape_history") or [])
    mape_history.append(candidate_mape)
    updates["mape_history"] = mape_history

    if accepted:
        updates.update(
            {
                "best_mape": candidate_mape,
                "best_code": candidate_code,
                "best_model": model,
                "best_validation_mape": validation_mape,
                "stagnation_count": 0,
            }
        )
    else:
        rejected = list(state.get("rejected_candidates", []))
        rejected.append(
            {
                "mape": candidate_mape,
                "model": model,
                "validation_mape": validation_mape,
                "reason": candidate_info.get("reason", ""),
                "acceptance_reason": acceptance_reason,
                "diagnostics": diagnostics,
            }
        )
        updates["rejected_candidates"] = rejected[-10:]
        updates["stagnation_count"] = int(state.get("stagnation_count", 0) or 0) + 1

    return updates


def _format_plan_status(state: ForecastState) -> str:
    """把当前 plan 格式化为简短状态注入 prompt。"""
    plan = state.get("plan")
    if not plan:
        return ""
    lines = [f"当前策略：{plan.get('strategy_note', '')}"]
    for step in plan.get("steps", []):
        mark = {"done": "[x]", "in_progress": "[>]", "skipped": "[-]"}.get(step["status"], "[ ]")
        line = f"  {mark} {step['id']}: {step['action']}"
        if step.get("outcome"):
            line += f" → {step['outcome'][:60]}"
        lines.append(line)
    return "\n".join(lines)


def _format_state_digest(state: ForecastState) -> str:
    """分层上下文注入：生成始终可见的核心状态摘要，避免被消息截断丢失。

    包含目标、当前最佳、改善趋势、最近失败模式，让 LLM 即使在长对话中
    也能始终掌握关键信息。"""
    lines = ["## 当前状态摘要"]

    org = state.get("org", "未知")
    target_month = state.get("target_month", "未知")
    target_mape = state.get("target_mape", 5.0)
    best_mape = state.get("best_mape", 999.0)
    best_model = state.get("best_model", "无")
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", 8)
    completed = len(state.get("iterations", []))

    lines.append(f"- 任务对象：**{org}** 区 · 目标月={target_month}")
    lines.append(f"- 目标 MAPE ≤ {target_mape}%")
    if best_mape < 900:
        gap = best_mape - target_mape
        lines.append(f"- 当前最佳：MAPE={best_mape:.3f}% (模型={best_model}, 距目标{gap:+.3f}%)")
    else:
        lines.append("- 当前最佳：尚无有效候选")
    lines.append(f"- 进度：候选 {completed}/{max_iterations}, 总轮次 {iteration_count}")

    # 改善趋势
    mape_history = state.get("mape_history") or []
    if len(mape_history) >= 2:
        recent = mape_history[-4:]
        improvements = [recent[i] - recent[i + 1] for i in range(len(recent) - 1)]
        avg_improvement = sum(improvements) / len(improvements) if improvements else 0
        trend = "↓改善" if avg_improvement > 0.05 else ("↑恶化" if avg_improvement < -0.05 else "→停滞")
        lines.append(f"- 趋势：最近{len(recent)}轮 MAPE={[round(m, 2) for m in recent]} ({trend})")

    # 最近失败
    rejected = state.get("rejected_candidates", [])
    if rejected:
        last_reject = rejected[-1]
        lines.append(
            f"- 最近被拒：{last_reject.get('model', '?')} "
            f"MAPE={last_reject.get('mape', '?')} "
            f"原因={last_reject.get('reason', '')[:60]}"
        )

    # Reflector 策略建议（如果有）
    reflector = state.get("reflector_strategy") or {}
    if reflector.get("next_strategy"):
        lines.append(f"- Reflector建议：{reflector['next_strategy'][:120]}")
        if reflector.get("avoid_models"):
            lines.append(f"- 避免模型：{reflector['avoid_models']}")

    # Validation diagnostics（供 patch 模式参考，来自 Coder 的 validation holdout）
    val_diag = state.get("validation_diagnostics") or {}
    if val_diag and val_diag.get("mape") is not None:
        lines.append(
            f"- Validation诊断(holdout)：MAPE={val_diag['mape']}%, "
            f"bias={val_diag.get('bias', '?')}, RMSE={val_diag.get('rmse', '?')}, "
            f"MPE={val_diag.get('mpe', '?')}%"
        )
        lines.append("  ↑ patch 迭代时请将此 validation_diagnostics 传给 delegate_to_coder 的 diagnostics 参数")

    # 早停预警
    stagnation = int(state.get("stagnation_count", 0) or 0)
    if stagnation >= 2:
        lines.append(f"- ⚠️ 已连续 {stagnation} 轮无显著改善，请考虑换策略或收尾")

    return "\n".join(lines)


def orchestrator_node(state: ForecastState) -> dict:
    state_updates = _state_updates_from_tools(state)
    state_view = {**state, **state_updates}
    sys_msg = SystemMessage(
        content=ORCHESTRATOR_SYSTEM.format(
            target_mape=state["target_mape"],
            max_iter=state["max_iterations"],
        )
    )

    # 分层上下文注入：始终可见的状态摘要（不受消息截断影响）
    state_digest = _format_state_digest(state_view)
    plan_status = _format_plan_status(state_view)

    messages_for_llm = [sys_msg]
    messages_for_llm.append(SystemMessage(content=state_digest))
    if plan_status:
        messages_for_llm.append(SystemMessage(content=f"## 当前执行计划\n{plan_status}"))

    # 对子 agent ToolMessage 做瘦身后再送 LLM
    slimmed = _slim_messages(state["messages"])
    # 智能截断：按重要性保留消息
    slimmed = _truncate_messages(slimmed)
    messages_for_llm.extend(slimmed)

    try:
        response = _llm.invoke(messages_for_llm)
    except BudgetExhaustedError:
        budget_info = log_budget_summary()
        return {
            **state_updates,
            "messages": [],
            "iteration_count": state["iteration_count"] + 1,
            "early_stop_reason": f"token budget exhausted ({budget_info['total']:,} tokens)",
            "token_usage": budget_info,
            "best_mape": state_view.get("best_mape", state["best_mape"]),
            "best_code": state_view.get("best_code", state["best_code"]),
        }

    return {
        **state_updates,
        "messages": [response],
        "iteration_count": state["iteration_count"] + 1,
        "current_plan": getattr(response, "content", "")[:800],
        "token_usage": log_budget_summary(),
        "best_mape": state_view.get("best_mape", state["best_mape"]),
        "best_code": state_view.get("best_code", state["best_code"]),
    }


def force_finalize_node(state: ForecastState) -> dict:
    """强制让 LLM 调 finalize 工具，避免 Agent 静默退出。"""
    completed_candidates = len(state.get("iterations", []))
    has_best_code = bool(state.get("best_code"))
    if not has_best_code and completed_candidates == 0:
        reason_hint = (
            "没有产生有效候选代码，不能把原因归结为历史数据不足。"
            "请在 reason 中明确写：流程在候选生成/验证前被收尾，"
            "需要检查 Coder、Verifier 或工具调用链路。"
        )
    elif completed_candidates >= state["max_iterations"]:
        reason_hint = "已达到候选建模迭代上限，请用当前 best_code 和 best_mape 收尾。"
    else:
        reason_hint = "Agent 未继续提出工具调用，请用当前 best_code 和 best_mape 收尾。"

    nudge = HumanMessage(
        content=(
            "你即将结束本次任务。请**立即且只**调用 finalize 工具显式收尾，"
            "参数填：best_code（只填已经跑通过并被接受的最佳代码，没有则填空字符串）、"
            "best_mape（最佳 MAPE 数值，没有有效候选则填 999）、"
            f"reason（结束原因：{reason_hint}）。"
            "不要再调用其他工具，不要再输出文本分析。"
        )
    )
    sys_msg = SystemMessage(
        content=ORCHESTRATOR_SYSTEM.format(
            target_mape=state["target_mape"],
            max_iter=state["max_iterations"],
        )
    )
    response = _finalize_llm.invoke([sys_msg, *_truncate_messages(state["messages"], 20), nudge])

    final_reason = reason_hint
    # 顺便把本次 run 写进 episodic memory（即使失败也存，作为反例）
    try:
        eps = get_episodic()
        best_mape = state.get("best_mape", 999.0)
        best_code = state.get("best_code", "")
        best_model = state.get("best_model", "unknown")
        best_validation_mape = state.get("best_validation_mape")
        candidate_summaries = state.get("candidate_summaries") or []
        selection_reason = state.get("selection_reason", "")
        for tc in getattr(response, "tool_calls", None) or []:
            if tc.get("name") == "finalize":
                args = tc.get("args", {})
                best_mape = args.get("best_mape", best_mape)
                best_code = args.get("best_code", best_code)
                best_model = args.get("best_model", best_model)
                best_validation_mape = args.get("best_validation_mape", best_validation_mape)
        data_profile = (
            f"目标月={state['target_month']}, MAPE={best_mape}, "
            f"best_model={best_model}, best_validation_mape={best_validation_mape}, "
            f"selection={selection_reason}"
        )
        if candidate_summaries:
            data_profile += f", candidates={candidate_summaries[:3]}"
        eps.add(
            org=state["org"],
            target_month=state["target_month"],
            data_profile=data_profile,
            model=best_model or "unknown",
            mape=float(best_mape) if best_mape else 999.0,
            best_code=best_code or "",
            candidate_summaries=str(candidate_summaries)[:1000] if candidate_summaries else None,
            validation_mape=best_validation_mape,
            selection_reason=selection_reason,
        )
    except Exception:
        # memory 写入失败不阻塞主流程
        pass

    return {"messages": [response], "final_reason": final_reason}
