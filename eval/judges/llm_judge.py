"""LLM-as-Judge - 用 qwen-max 评 Agent 行为质量（流程合规、反思有效、记忆使用）。

不是评 MAPE（那是客观 mape_judge 的事），是评"过程".
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from castflow.llm.client import make_orchestrator_llm

JUDGE_SYSTEM = """你是 Agent 行为评审官。
我会给你一段 LangGraph Agent 的工具调用日志，请你按 0-10 给以下三项打分（0=完全没做，10=做得非常好）：

1. workflow_compliance: 是否按 list_orgs → recall → load_history(before_month) → 写代码 → 评估 → finalize 的流程
2. recovery: 遇到失败/不达标时是否合理换策略（不重复同一错；该用 reflector 的时候用了）
3. memory_usage: 是否真正用了 recall_similar_runs / recall_lessons / save_lesson

只输出 JSON：{"workflow_compliance": <0-10>, "recovery": <0-10>, "memory_usage": <0-10>, "summary": "<≤80字总结>"}"""


def llm_judge(tool_calls_summary: str) -> dict:
    """tool_calls_summary 是从 Agent 日志压缩出的工具调用序列字符串。"""
    llm = make_orchestrator_llm()
    response = llm.invoke(
        [
            SystemMessage(content=JUDGE_SYSTEM),
            HumanMessage(content=f"## Agent 日志\n{tool_calls_summary[:6000]}"),
        ]
    )
    text = (response.content or "").strip()
    # 抽 JSON
    import json
    import re

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"error": "judge did not return JSON", "raw": text[:300]}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"error": str(e), "raw": text[:300]}
