"""Reflector subagent - 失败诊断 + 经验沉淀（Reflexion 模式）。

主 Agent 在两类场景调用：
1. 同一错误反复出现（reactive reflection）
2. MAPE 长期不达标（plateau reflection）

子 Agent 独立 context、独立 LLM，专心做一件事：
- 读失败上下文 → 找根因 → 提炼一条可复用 lesson → 自动 save_lesson 入语义记忆
- 输出下一步策略建议，供 Orchestrator replan
"""
from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from castflow.tools.schema import tool_success

from castflow.llm.client import make_subagent_llm

REFLECTOR_SYSTEM = """你是失败分析专家（Reflector）。

# 你的目标
看一段失败代码 + 错误信息 + 当前任务上下文，
找出**根本原因**（不是表面错误），
提炼一条**可复用的经验教训**（lesson），
**调用 save_lesson 工具**写入语义记忆，供未来任务召回，
并输出下一步可执行的策略建议，供主 Agent replan。

# 工作方式
1. 仔细读输入：failed_attempt（代码+stderr）、context（任务背景）、pattern（失败模式）
2. 用 1-3 句话定位根因（不要罗列表面错误，要说为什么会出错）
3. 提炼成一条简短可复用的 lesson：
   - topic: 5-15 字的主题标签
   - lesson: 详细描述（场景 → 原因 → 解决方法），50-200 字
   - tags: 3-5 个英文/中文标签便于检索
4. 输出结构化 next_strategy，给主 Agent 下一步怎么改
5. **必须**调用 save_lesson 工具（这是你的核心动作，不调就是没做事）
6. 你的文字回复只写 1-2 句根因诊断，不要长篇大论

# 输出要求
如果回复文本里有 JSON，优先包含：
{
  "root_cause": "...",
  "next_strategy": "...",
  "avoid_models": ["..."],
  "suggested_actions": ["..."],
  "lesson_topic": "..."
}

# 反例（不要这样）
- "代码报错 ImportError" → 这只是表面错，没找根因
- "建议换个模型" → 太空泛，未来不可复用
- 不调 save_lesson → 没完成任务

# 正例
- topic: "Prophet 包名 fbprophet 已废弃"
- lesson: "新版应用 `from prophet import Prophet`。fbprophet 是 2020 年旧名，pip install prophet 才能用。当前环境若 ModuleNotFoundError: fbprophet，应改导入或换 ARIMA/SARIMAX。"
- tags: ["prophet", "import", "包名"]
"""


def _extract_json(text: str) -> dict:
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 非贪婪匹配，逐个尝试找到合法 JSON 对象
    for match in re.finditer(r"\{[\s\S]*?\}", text):
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
    return {}


def _get_save_lesson_tool():
    from castflow.tools.memory import save_lesson

    return save_lesson


@tool
def delegate_to_reflector(
    failed_attempt: str,
    context: str,
    pattern: str = "unknown",
    failure_summary: str = "",
) -> dict:
    """委派给反思子 Agent。当遇到反复失败、长期不收敛、相同错误 2+ 次时调用。

    子 Agent 会独立分析根因并自动 save_lesson 写入语义记忆，
    主 Agent 收到诊断结果后应换策略，不要再重复同一错误。

    Args:
        failed_attempt: 失败的完整内容（代码 + stderr + 关键 tool result）
        context: 当前任务背景（区县、目标月、已尝试的模型等）
        pattern: 失败模式标签，如 "repeated-same-error" / "convergence-failure" / "low-mape-plateau"
        failure_summary: 失败摘要，优先传 best_mape、candidate_mape、bias、主要错误点、rejected reason
    """
    save_lesson = _get_save_lesson_tool()
    llm = make_subagent_llm().bind_tools([save_lesson])

    response = llm.invoke(
        [
            SystemMessage(content=REFLECTOR_SYSTEM),
            HumanMessage(
                content=(
                    f"## 失败模式\n{pattern}\n\n"
                    f"## 失败摘要\n{failure_summary[:1200] or '无'}\n\n"
                    f"## 任务上下文\n{context[:1200]}\n\n"
                    f"## 失败内容\n{failed_attempt[:2200]}\n\n"
                    "请定位根因 → 提炼 lesson → 调用 save_lesson。"
                )
            ),
        ]
    )

    # 执行 save_lesson 工具调用
    saved: list[dict] = []
    for tc in getattr(response, "tool_calls", None) or []:
        if tc.get("name") == "save_lesson":
            try:
                result = save_lesson.invoke(tc.get("args", {}))
                saved.append({"args": tc["args"], "result": result})
            except Exception as e:
                saved.append({"error": str(e), "args": tc["args"]})

    diagnosis = (response.content or "")[:500] if hasattr(response, "content") else ""
    parsed = _extract_json(response.content or "") if hasattr(response, "content") else {}
    saved_args = saved[0].get("args", {}) if saved and "args" in saved[0] else {}
    successful_saves = [s for s in saved if "error" not in s]
    root_cause = parsed.get("root_cause") or diagnosis or "未提取到结构化根因"
    next_strategy = parsed.get("next_strategy") or (
        "换用更稳健的 baseline/SARIMAX/ETS 候选，并显式避免重复失败模式。"
    )
    avoid_models = parsed.get("avoid_models") or []
    suggested_actions = parsed.get("suggested_actions") or [
        "将失败模式传给 delegate_to_coder 的 avoid_models/constraints",
        "优先比较 seasonal baseline、SARIMAX、ETS",
        "若 verifier 拒绝或同错复现，停止当前策略",
    ]
    payload = {
        "diagnosis": diagnosis or "(reflector 未输出文字诊断)",
        "root_cause": root_cause,
        "next_strategy": next_strategy,
        "avoid_models": avoid_models,
        "suggested_actions": suggested_actions,
        "lesson_topic": parsed.get("lesson_topic") or saved_args.get("topic", ""),
        "lessons_saved": len(successful_saves),
        "saved": saved,
    }
    # 顶层只放结论摘要，不展开完整 payload（减少 ToolMessage 体积）
    return tool_success(
        payload["diagnosis"],
        data=payload,
        next_action=next_strategy,
        root_cause=root_cause,
        next_strategy=next_strategy,
        avoid_models=avoid_models,
        lessons_saved=len(successful_saves),
    )
