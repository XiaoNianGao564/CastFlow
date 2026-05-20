"""Planner node — 首轮生成结构化 Plan，replan 时重写。

Plan 不强制路由（orchestrator 仍可偏离），但每轮 prompt 注入 plan 状态，
使 trace 可读、replan 可审计。

集成策略自动进化：首轮规划时自动参考历史运行中的成功模式。
"""
from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from castflow.llm.client import make_orchestrator_llm
from castflow.prompts import PLANNER_SYSTEM
from castflow.state import ForecastState, Plan, PlanStep


def _parse_plan_response(text: str) -> Plan | None:
    """从 LLM 回复中提取 JSON Plan。"""
    if not text:
        return None
    try:
        obj = json.loads(text)
        if "steps" in obj:
            return _validate_plan(obj)
    except json.JSONDecodeError:
        pass
    # 尝试从 markdown code block 中提取
    import re
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if match:
        try:
            obj = json.loads(match.group(1))
            if "steps" in obj:
                return _validate_plan(obj)
        except json.JSONDecodeError:
            pass
    # 尝试找最外层 {}
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            obj = json.loads(match.group(0))
            if "steps" in obj:
                return _validate_plan(obj)
        except json.JSONDecodeError:
            pass
    return None


def _validate_plan(obj: dict) -> Plan:
    steps: list[PlanStep] = []
    for i, s in enumerate(obj.get("steps", [])[:10]):
        steps.append(PlanStep(
            id=s.get("id", f"s{i+1}"),
            action=s.get("action", ""),
            tool_hint=s.get("tool_hint", ""),
            status="pending",
        ))
    return Plan(
        version=int(obj.get("version", 1)),
        steps=steps,
        strategy_note=str(obj.get("strategy_note", ""))[:200],
    )


def _default_plan(state: ForecastState) -> Plan:
    """LLM 未能产出有效 JSON 时的兜底 plan。"""
    return Plan(
        version=1,
        steps=[
            PlanStep(id="s1", action="查文档知识库", tool_hint="search_knowledge_docs", status="pending"),
            PlanStep(id="s2", action="召回历史经验", tool_hint="recall_similar_runs", status="pending"),
            PlanStep(id="s3", action="获取区县列表", tool_hint="list_orgs", status="pending"),
            PlanStep(id="s4", action="加载历史数据", tool_hint="load_history", status="pending"),
            PlanStep(id="s5", action="生成候选代码", tool_hint="delegate_to_coder", status="pending"),
            PlanStep(id="s6", action="审查候选代码", tool_hint="delegate_to_verifier", status="pending"),
            PlanStep(id="s7", action="评估并提交", tool_hint="evaluate_mape", status="pending"),
            PlanStep(id="s8", action="保存预测并收尾", tool_hint="finalize", status="pending"),
        ],
        strategy_note=f"预测 {state.get('org', '?')} {state.get('target_month', '?')} 月度负荷",
    )


def planner_node(state: ForecastState) -> dict:
    """生成或重写结构化 Plan。首轮自动参考策略进化推荐。"""
    is_replan = bool(state.get("needs_replan"))
    replan_count = int(state.get("replan_count", 0) or 0)
    existing_plan = state.get("plan")

    context_parts = [
        f"区县={state.get('org', '?')}",
        f"目标月={state.get('target_month', '?')}",
        f"目标MAPE≤{state.get('target_mape', 5.0)}%",
        f"当前best_mape={state.get('best_mape', 999.0)}",
        f"已迭代={state.get('iteration_count', 0)}次",
    ]

    # 策略自动进化：从历史运行中获取推荐
    strategy_hint = ""
    try:
        from castflow.memory.strategy import get_strategy_evolution
        evolution = get_strategy_evolution()
        analysis = evolution.analyze()
        # 尝试获取推荐（此时可能还没有数据特征，用默认值）
        rec = evolution.get_recommendation(
            state.get("org", ""),
            {"sample_size": 24, "seasonality_ratio": 1.0, "trend": 0},
        )
        if rec.get("recommended_model"):
            source = rec.get("source", "history")
            source_label = "规则推荐" if source == "rule_based" else "历史推荐"
            strategy_hint = (
                f"{source_label}：{rec['recommended_model']} "
                f"(原因={rec.get('reason', '')})"
            )
            if source != "rule_based":
                avoid = evolution.get_avoid_list(
                    state.get("org", ""),
                    {"sample_size": 24, "seasonality_ratio": 1.0, "trend": 0},
                )
                if avoid:
                    strategy_hint += f"；建议避免：{avoid}"
            ranking = analysis.get("model_ranking", [])[:3]
            if ranking:
                model_strs = [f"{r['model']}({r['avg_mape']}%)" for r in ranking]
                strategy_hint += f"；全局模型排名：{model_strs}"
    except Exception:
        pass

    if strategy_hint:
        context_parts.append(f"策略进化建议: {strategy_hint}")

    if is_replan:
        reflector = state.get("reflector_strategy") or {}
        context_parts.append(f"replan原因: {reflector.get('root_cause', '未知')}")
        context_parts.append(f"建议策略: {reflector.get('next_strategy', '')}")
        context_parts.append(f"避免模型: {reflector.get('avoid_models', [])}")
        if existing_plan:
            context_parts.append(f"上一版plan: {json.dumps(existing_plan, ensure_ascii=False)[:800]}")

        # replan 时也参考早停信息
        mape_history = state.get("mape_history") or []
        if mape_history:
            context_parts.append(f"MAPE历史: {[round(m, 2) for m in mape_history[-6:]]}")
        stagnation = int(state.get("stagnation_count", 0) or 0)
        if stagnation >= 2:
            context_parts.append(f"⚠️ 已连续{stagnation}轮无改善，需要显著改变策略")

    llm = make_orchestrator_llm()
    response = llm.invoke([
        SystemMessage(content=PLANNER_SYSTEM),
        HumanMessage(content="\n".join(context_parts)),
    ])

    plan = _parse_plan_response(response.content or "")
    if plan is None:
        plan = _default_plan(state)

    if is_replan:
        plan["version"] = (existing_plan or {}).get("version", 0) + 1

    updates: dict = {
        "plan": plan,
        "needs_replan": False,
        "replan_count": replan_count + (1 if is_replan else 0),
    }
    # 存档旧 plan
    if is_replan and existing_plan:
        updates["plan_history"] = [existing_plan]

    return updates
