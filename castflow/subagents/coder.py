"""Coder subagent - 代码生成专员（subagent-as-tool 模式）。

为什么独立成子 Agent：
- 主 Agent 关心"流程"（取数据→评估→反思→收尾），不该被代码细节淹没
- Coder 在自己的 context 里 ReAct，写完一遍跑通才回主 Agent
- 失败的中间代码 / stderr 留在 Coder context，主 Agent 只看到最终成品摘要
- 用 qwen-plus + 高温度（探索性强），主 Agent 用 qwen-max + 低温度（决策稳）

实现：先用确定性 baseline 候选快速给出保底，再让 LLM 在独立子图里产出更强候选，
统一在 validation split 上比较，最后只把最优代码交回主 Agent。
"""
from __future__ import annotations

import json
from statistics import mean

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from castflow.llm.client import make_subagent_llm
from castflow.tools.schema import tool_error, tool_success

CODER_SYSTEM = """你是预测代码工程师 (Coder)。

# 你的目标
拿到任务描述后，独立完成：load_history → 生成多个候选方案 → run_python 跑通 → 用 validation MAPE 选最佳 → 返回目标月最终代码。

# 工具
- load_history(org, months, before_month): 取历史月度负荷
- search_knowledge_docs(query, top_k): 检索业务文档中的数据口径、建模规则和评估规范
- run_python(code): 执行 Python，检查 returncode 和 parsed 字段

# 工作方式
1. 先用 search_knowledge_docs 查业务口径和建模规范，再用 load_history 拿训练数据（必须传 before_month 防数据泄漏）
2. 把 pre-target 历史拆成：训练段 + 最后 1 个月 validation holdout
3. 至少比较以下候选：
   - baseline（seasonal naive / recent trend blend）
   - 统计模型（ARIMA / SARIMAX / ETS）
4. 每个候选都要写完整 Python 代码并 run_python 跑通
5. 候选代码末尾必须 print 一行 JSON，格式至少包含：
   {"validation_predictions": [...], "validation_actuals": [...], "final_predictions": [...], "model_name": "..."}
6. 用 validation MAPE 选最佳；如果复杂模型没有明显优于 baseline，就保留 baseline
7. 最后一条 AI 回复必须给出：最佳模型诊断 + 最终代码（```python```）

# 重要原则
- 历史数据必须严格来自 load_history 的真实返回，**绝不允许凭空编造数字**
- 文档知识库只提供业务规则和建模约束，不允许替代真实历史数据
- 绝不允许使用目标月真实值做候选选择
- validation 只允许使用目标月之前最后 1 个月的真实值
- 代码必须 returncode==0 且 parsed 不为 None 才算跑通
- patch 模式下必须基于 base_code 做最小改进，保留已跑通的数据边界、输出 JSON 协议和有效特征
- patch 模式优先根据 diagnostics 修正系统性 bias、季节/趋势权重、异常点处理，不要无理由推翻当前最佳模型
- pandas 新版用 series.ffill() / .bfill()，不要 fillna(method=)
- Prophet 包名是 prophet 不是 fbprophet（且当前环境未装，优先 ARIMA/SARIMAX/ETS）
- 月度小样本优先稳定模型；LightGBM 只作补充候选
- 不要嵌套函数、不要写 main()，直接平铺，最后一行 print

# 输出格式
最后一条 AI 回复要包含：
1. 一段简短诊断（比较了哪些候选、validation 最优是谁、为什么选它）
2. 跑通的最终代码（用 ```python ``` 包起来）

主 Agent 拿到这两块就能继续后续的评估流程。
"""


def _get_tools():
    from castflow.tools.data import load_history
    from castflow.tools.knowledge import search_knowledge_docs
    from castflow.tools.python_exec import run_python

    return load_history, run_python, search_knowledge_docs


def _build_coder_agent():
    """懒构建子图，避免 import 阶段就调起 LLM。"""
    llm = make_subagent_llm()
    load_history, run_python, search_knowledge_docs = _get_tools()
    return create_react_agent(
        llm,
        tools=[load_history, search_knowledge_docs, run_python],
        prompt=CODER_SYSTEM,
    )


_coder = None


def _get_coder():
    global _coder
    if _coder is None:
        _coder = _build_coder_agent()
    return _coder


def _extract_code(text: str) -> str:
    """从 LLM 回复里抽取 ```python ... ``` 块。"""
    if not text:
        return ""
    if "```python" in text:
        chunk = text.split("```python", 1)[1]
        if "```" in chunk:
            return chunk.split("```", 1)[0].strip()
    if "```" in text:
        chunk = text.split("```", 1)[1]
        if "```" in chunk:
            return chunk.split("```", 1)[0].strip()
    return ""


VALIDATION_HORIZON = 3


def _calc_mape(predictions: list[float], actuals: list[float]) -> float | None:
    if not predictions or not actuals or len(predictions) != len(actuals):
        return None
    valid = [(p, a) for p, a in zip(predictions, actuals) if a != 0]
    if not valid:
        return None
    return round(sum(abs((a - p) / a) for p, a in valid) / len(valid) * 100, 3)


def _calc_validation_diagnostics(predictions: list[float], actuals: list[float]) -> dict:
    """计算 validation 集上的诊断信息，用于指导 patch 方向。"""
    if not predictions or not actuals or len(predictions) != len(actuals):
        return {}
    n = len(predictions)
    errors = [p - a for p, a in zip(predictions, actuals)]
    abs_errors = [abs(e) for e in errors]
    pct_errors = [(p - a) / a * 100 for p, a in zip(predictions, actuals) if a != 0]
    mape = _calc_mape(predictions, actuals)
    bias = sum(errors) / n if n else 0
    mae = sum(abs_errors) / n if n else 0
    rmse = (sum(e ** 2 for e in errors) / n) ** 0.5 if n else 0
    mpe = sum(pct_errors) / len(pct_errors) if pct_errors else 0
    per_point = [
        {"idx": i, "actual": a, "predicted": p, "error": p - a, "abs_error": abs(p - a),
         "pct_error": round((p - a) / a * 100, 2) if a != 0 else None}
        for i, (p, a) in enumerate(zip(predictions, actuals))
    ]
    return {
        "mape": mape,
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        "bias": round(bias, 3),
        "mpe": round(mpe, 3),
        "per_point_errors": per_point,
        "source": "validation_holdout",
    }


def _make_candidate_summary(
    name: str,
    family: str,
    success: bool,
    validation_mape: float | None,
    notes: str,
) -> dict:
    return {
        "name": name,
        "family": family,
        "success": success,
        "validation_mape": validation_mape,
        "notes": notes[:240],
    }


def _history_profile(rows: list[dict]) -> str:
    values = [float(r["value"]) for r in rows]
    tail = values[-12:]
    recent_mean = mean(values[-3:]) if len(values) >= 3 else mean(values)
    yearly_mean = mean(tail) if tail else mean(values)
    seasonality_ratio = yearly_mean / recent_mean if recent_mean else 1.0
    trend = values[-1] - values[-4] if len(values) >= 4 else values[-1] - values[0]
    return (
        f"样本={len(values)}; 最近值={values[-1]:.3f}; 最近3月均值={recent_mean:.3f}; "
        f"近12月均值={yearly_mean:.3f}; seasonality_ratio={seasonality_ratio:.3f}; trend={trend:.3f}"
    )


def _make_seasonal_naive_candidate(rows: list[dict], validation_horizon: int = VALIDATION_HORIZON) -> dict:
    values = [float(r["value"]) for r in rows]
    months = [r["month"] for r in rows]
    train = values[:-validation_horizon]
    val_actuals = values[-validation_horizon:]
    target_final = values

    if len(train) >= 12:
        validation_predictions = train[-12:][:validation_horizon]
    else:
        validation_predictions = [train[-1]] * validation_horizon

    if len(target_final) >= 12:
        final_predictions = [target_final[-12]]
    else:
        final_predictions = [target_final[-1]]

    mape = _calc_mape(validation_predictions, val_actuals)
    return {
        "name": "seasonal_naive_12",
        "family": "baseline",
        "success": True,
        "validation_mape": mape,
        "validation_predictions": validation_predictions,
        "validation_actuals": val_actuals,
        "final_predictions": final_predictions,
        "code": (
            "import json\n"
            f"validation_predictions = {json.dumps(validation_predictions, ensure_ascii=False)}\n"
            f"validation_actuals = {json.dumps(val_actuals, ensure_ascii=False)}\n"
            f"final_predictions = {json.dumps(final_predictions, ensure_ascii=False)}\n"
            'print(json.dumps({"validation_predictions": validation_predictions, '
            '"validation_actuals": validation_actuals, '
            '"final_predictions": final_predictions, '
            '"predictions": final_predictions, '
            '"model_name": "seasonal_naive_12"}))\n'
        ),
        "notes": f"使用去年同月/最接近季节值做保底；months={months[-min(len(months), 12):]}",
    }


def _make_trend_blend_candidate(rows: list[dict], validation_horizon: int = VALIDATION_HORIZON) -> dict:
    values = [float(r["value"]) for r in rows]
    train = values[:-validation_horizon]
    val_actuals = values[-validation_horizon:]
    recent_mean = mean(train[-3:]) if len(train) >= 3 else mean(train)
    if len(train) >= 12:
        seasonal_ref = train[-12]
    else:
        seasonal_ref = train[-1]
    validation_predictions = [round((recent_mean + seasonal_ref) / 2, 6)]

    full_recent_mean = mean(values[-3:]) if len(values) >= 3 else mean(values)
    if len(values) >= 12:
        final_seasonal_ref = values[-12]
    else:
        final_seasonal_ref = values[-1]
    final_predictions = [round((full_recent_mean + final_seasonal_ref) / 2, 6)]
    mape = _calc_mape(validation_predictions, val_actuals)
    return {
        "name": "trend_seasonal_blend",
        "family": "baseline",
        "success": True,
        "validation_mape": mape,
        "validation_predictions": validation_predictions,
        "validation_actuals": val_actuals,
        "final_predictions": final_predictions,
        "code": (
            "import json\n"
            f"validation_predictions = {json.dumps(validation_predictions, ensure_ascii=False)}\n"
            f"validation_actuals = {json.dumps(val_actuals, ensure_ascii=False)}\n"
            f"final_predictions = {json.dumps(final_predictions, ensure_ascii=False)}\n"
            'print(json.dumps({"validation_predictions": validation_predictions, '
            '"validation_actuals": validation_actuals, '
            '"final_predictions": final_predictions, '
            '"predictions": final_predictions, '
            '"model_name": "trend_seasonal_blend"}))\n'
        ),
        "notes": "最近3月均值与季节性参考做 50/50 融合，作为稳健保底。",
    }


def _evaluate_candidate_from_result(name: str, family: str, result: dict) -> dict:
    if result.get("error"):
        return {
            "name": name,
            "family": family,
            "success": False,
            "validation_mape": None,
            "validation_predictions": [],
            "validation_actuals": [],
            "final_predictions": [],
            "code": "",
            "notes": result["error"],
        }

    parsed = result.get("parsed") or {}
    validation_predictions = parsed.get("validation_predictions") or []
    validation_actuals = parsed.get("validation_actuals") or []
    final_predictions = parsed.get("final_predictions") or parsed.get("predictions") or []
    validation_mape = _calc_mape(validation_predictions, validation_actuals)
    success = result.get("returncode") == 0 and bool(parsed) and validation_mape is not None and bool(final_predictions)
    stderr = (result.get("stderr") or "")[:400]
    stdout = (result.get("stdout") or "")[:400]
    notes = stderr or stdout or "candidate executed"
    return {
        "name": parsed.get("model_name") or name,
        "family": family,
        "success": success,
        "validation_mape": validation_mape,
        "validation_predictions": validation_predictions,
        "validation_actuals": validation_actuals,
        "final_predictions": final_predictions,
        "code": "",
        "notes": notes,
    }


def _select_best_candidate(candidates: list[dict]) -> dict | None:
    valid = [c for c in candidates if c.get("success") and c.get("validation_mape") is not None]
    if not valid:
        return None
    valid.sort(key=lambda c: (c["validation_mape"], 0 if c["family"] == "baseline" else 1))
    best = valid[0]
    baseline = next((c for c in valid if c["family"] == "baseline"), None)
    if baseline and best["family"] != "baseline":
        if best["validation_mape"] >= baseline["validation_mape"] - 0.2:
            return baseline
    return best


def _make_candidate_prompt(
    task: str,
    org: str,
    target_month: str,
    history_rows: list[dict],
    constraints: str,
    avoid_models: str,
    history_profile: str,
    knowledge_context: str = "",
    base_code: str = "",
    previous_mape: float | None = None,
    diagnostics: dict | str = "",
    patch_mode: bool = False,
    patch_hint: str = "",
) -> str:
    values = [float(r["value"]) for r in history_rows]
    months = [r["month"] for r in history_rows]
    val_horizon = min(VALIDATION_HORIZON, len(values) - 12)
    if val_horizon < 1:
        val_horizon = 1
    validation_actuals = values[-val_horizon:]
    validation_months = months[-val_horizon:]
    train_rows = history_rows[:-val_horizon]
    final_history = history_rows
    patch_section = ""
    if patch_mode or base_code:
        diagnostics_text = diagnostics if isinstance(diagnostics, str) else json.dumps(diagnostics, ensure_ascii=False)
        patch_section = (
            "\n\n## Patch 模式\n"
            f"上一版 validation MAPE={previous_mape if previous_mape is not None else '未知'}\n"
            f"补丁提示={patch_hint or '基于 validation 诊断做最小改进'}\n"
            f"validation 诊断（来自 validation holdout，非目标月真实值）={diagnostics_text[:2000]}\n"
            "要求：以 base_code 为当前最佳基线，优先做局部修正；必须返回完整可执行 Python 代码，不要只返回 diff。\n"
            f"## base_code\n```python\n{base_code[:6000]}\n```\n"
        )
    return (
        f"## 任务\n{task}\n\n"
        f"## 区县\n{org}\n\n"
        f"## 目标月\n{target_month}\n\n"
        f"## 额外约束\n{constraints or '无'}\n\n"
        f"## 避免使用的模型\n{avoid_models or '无'}\n\n"
        f"## 文档知识库命中\n{knowledge_context or '无'}\n\n"
        f"## 历史特征\n{history_profile}\n\n"
        f"## 候选要求\n"
        f"- 使用已有历史数据，不再调用 load_actual\n"
        f"- validation holdout 固定为目标月之前最后 {val_horizon} 个月：{validation_months}，真实值={validation_actuals}\n"
        f"- 训练段只允许使用到 {months[-val_horizon - 1] if len(months) > val_horizon else months[0]}\n"
        f"- final_predictions 用完整 pre-target 历史预测 {target_month}\n"
        f"- 候选模型优先 SARIMAX / ETS / ARIMA，必要时再考虑 LightGBM\n"
        f"- 如果复杂模型没有明显优于 baseline，不要硬选复杂模型\n\n"
        f"## 训练历史\n{json.dumps(train_rows, ensure_ascii=False)}\n\n"
        f"## 完整 pre-target 历史\n{json.dumps(final_history, ensure_ascii=False)}"
        f"{patch_section}\n\n"
        "请只生成 1 个强候选，代码末尾必须打印 JSON："
        '{"validation_predictions": [...], "validation_actuals": [...], "final_predictions": [...], "predictions": [...], "model_name": "..."}'
        "。"
    )


@tool
def delegate_to_coder(
    task: str,
    org: str,
    target_month: str,
    constraints: str = "",
    avoid_models: str = "",
    base_code: str = "",
    previous_mape: float | None = None,
    diagnostics: dict | str = "",
    patch_mode: bool = False,
    patch_hint: str = "",
) -> dict:
    """委派给代码生成子 Agent。它会先比较 baseline/统计模型候选，再返回最优代码。

    Args:
        task: 任务描述
        org: 区县名
        target_month: 目标预测月
        constraints: 额外约束
        avoid_models: 避免使用的模型列表
        base_code: patch 模式下的当前最佳代码
        previous_mape: 上一版 validation MAPE（来自 Coder 返回的 best_validation_mape）
        diagnostics: **必须传 validation_diagnostics**（来自 Coder 上次返回），
            禁止传入目标月 evaluate_mape 的诊断结果，防止数据泄漏
        patch_mode: 是否为补丁模式
        patch_hint: 补丁方向提示（只基于 validation 诊断的 bias/误差模式）
    """
    load_history, run_python, search_knowledge_docs = _get_tools()
    history = load_history.invoke({"org": org, "months": 36, "before_month": target_month})
    rows = history.get("data") or []
    if len(rows) < 13:
        return tool_error(
            "历史数据不足，无法生成可靠候选。",
            f"not enough history before {target_month}: {len(rows)} rows",
            next_action="扩大历史窗口或选择有更多历史数据的区县。",
        )

    profile = _history_profile(rows)
    kb_hint = search_knowledge_docs.invoke(
        {
            "query": f"{org} {target_month} 电力负荷预测 数据口径 建模规范 MAPE 防泄漏",
            "top_k": 3,
        }
    )
    candidates: list[dict] = []

    seasonal_candidate = _make_seasonal_naive_candidate(rows)
    seasonal_result = run_python.invoke({"code": seasonal_candidate["code"]})
    seasonal_eval = _evaluate_candidate_from_result(
        seasonal_candidate["name"], seasonal_candidate["family"], seasonal_result
    )
    seasonal_eval["code"] = seasonal_candidate["code"]
    seasonal_eval["notes"] = seasonal_candidate["notes"]
    candidates.append(seasonal_eval)

    blend_candidate = _make_trend_blend_candidate(rows)
    blend_result = run_python.invoke({"code": blend_candidate["code"]})
    blend_eval = _evaluate_candidate_from_result(
        blend_candidate["name"], blend_candidate["family"], blend_result
    )
    blend_eval["code"] = blend_candidate["code"]
    blend_eval["notes"] = blend_candidate["notes"]
    candidates.append(blend_eval)

    coder = _get_coder()
    user_msg = _make_candidate_prompt(
        task=task,
        org=org,
        target_month=target_month,
        history_rows=rows,
        constraints=constraints,
        avoid_models=avoid_models,
        history_profile=profile,
        knowledge_context=str(kb_hint.get("documents", []))[:1200],
        base_code=base_code,
        previous_mape=previous_mape,
        diagnostics=diagnostics,
        patch_mode=patch_mode,
        patch_hint=patch_hint,
    )

    llm_summary = ""
    llm_code = ""
    llm_candidate = None
    internal_steps = 0
    tool_calls_count = 2
    try:
        result = coder.invoke(
            {"messages": [("user", user_msg)]},
            config={"recursion_limit": 25},
        )
        msgs = result.get("messages", [])
        internal_steps = len(msgs)
        for m in reversed(msgs):
            if getattr(m, "type", None) == "ai" and getattr(m, "content", ""):
                llm_summary = m.content
                llm_code = _extract_code(m.content)
                break
        for m in msgs:
            if getattr(m, "type", None) == "ai":
                for tc in getattr(m, "tool_calls", None) or []:
                    if tc.get("name") == "run_python":
                        tool_calls_count += 1
        if llm_code:
            llm_result = run_python.invoke({"code": llm_code})
            llm_candidate = _evaluate_candidate_from_result("llm_candidate", "llm", llm_result)
            llm_candidate["code"] = llm_code
            llm_candidate["notes"] = (llm_summary or llm_candidate["notes"])[:240]
            candidates.append(llm_candidate)
    except Exception as e:
        llm_summary = f"coder agent crashed: {e}"

    best = _select_best_candidate(candidates)
    if best is None:
        candidate_summaries = [
            _make_candidate_summary(c["name"], c["family"], c["success"], c["validation_mape"], c["notes"])
            for c in candidates
        ]
        return tool_error(
            "没有生成可用候选代码。",
            "no valid candidate produced",
            data={"candidate_summaries": candidate_summaries},
            next_action="降低模型复杂度或调用 reflector 分析失败模式。",
            candidate_summaries=candidate_summaries,
        )

    candidate_summaries = [
        _make_candidate_summary(c["name"], c["family"], c["success"], c["validation_mape"], c["notes"])
        for c in candidates
    ]
    candidate_summaries.sort(
        key=lambda c: (999.0 if c["validation_mape"] is None else c["validation_mape"], 0 if c["family"] == "baseline" else 1)
    )
    runner_up = candidate_summaries[1] if len(candidate_summaries) > 1 else None
    selection_reason = (
        f"best={best['name']} ({best['family']}) validation_mape={best['validation_mape']}"
        + (f"; runner_up={runner_up['name']}:{runner_up['validation_mape']}" if runner_up else "")
    )

    summary = (
        f"比较了 {len(candidates)} 个候选；最佳模型是 {best['name']}，"
        f"validation MAPE={best['validation_mape']}。"
        f" {selection_reason}"
    )
    if patch_mode or base_code:
        summary = "patch 模式：" + summary
    if llm_summary:
        summary += f" 子 Agent 摘要：{llm_summary[:220]}"

    # 精简 candidate_summaries：去掉 notes 字段，只保留指标
    compact_summaries = [
        {"name": c["name"], "family": c["family"], "success": c["success"], "validation_mape": c["validation_mape"]}
        for c in candidate_summaries
    ]

    # data 保留完整信息供 _state_updates_from_tools 提取；
    # 不再 **payload 展开到顶层，减少 ToolMessage 体积
    val_diag = _calc_validation_diagnostics(
        best.get("validation_predictions", []),
        best.get("validation_actuals", []),
    )
    payload = {
        "code": best["code"],
        "code_runs": tool_calls_count,
        "internal_steps": internal_steps,
        "best_model": best["name"],
        "best_validation_mape": best["validation_mape"],
        "validation_diagnostics": val_diag,
        "candidate_summaries": candidate_summaries,
        "selection_reason": selection_reason,
        "used_validation_split": f"holdout={VALIDATION_HORIZON} months before {target_month}",
        "history_profile": profile,
        "patch_mode": bool(patch_mode or base_code),
    }
    return tool_success(
        summary[:600],
        data=payload,
        next_action="将 code 交给 verifier 审查。",
        best_model=best["name"],
        best_validation_mape=best["validation_mape"],
        candidate_summaries=compact_summaries,
        selection_reason=selection_reason,
    )
