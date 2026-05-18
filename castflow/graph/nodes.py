from langchain_core.messages import HumanMessage, SystemMessage

from castflow.llm.client import make_orchestrator_llm
from castflow.memory.episodic import get_episodic
from castflow.prompts import ORCHESTRATOR_SYSTEM
from castflow.state import ForecastState
from castflow.tools import CORE_TOOLS, finalize as finalize_tool

_llm = make_orchestrator_llm().bind_tools(CORE_TOOLS)
_finalize_llm = make_orchestrator_llm().bind_tools(
    [finalize_tool], tool_choice={"type": "function", "function": {"name": "finalize"}}
)


def orchestrator_node(state: ForecastState) -> dict:
    sys_msg = SystemMessage(
        content=ORCHESTRATOR_SYSTEM.format(
            target_mape=state["target_mape"],
            max_iter=state["max_iterations"],
        )
    )
    response = _llm.invoke([sys_msg, *state["messages"]])
    return {
        "messages": [response],
        "iteration_count": state["iteration_count"] + 1,
    }


def force_finalize_node(state: ForecastState) -> dict:
    """强制让 LLM 调 finalize 工具，避免 Agent 静默退出。
    收尾后顺便把这次 run 写入 episodic memory。
    """
    nudge = HumanMessage(
        content=(
            "你即将结束本次任务。请**立即且只**调用 finalize 工具显式收尾，"
            "参数填：best_code（你跑通过的最佳代码完整字符串）、"
            "best_mape（最佳 MAPE 数值，没有就填 999）、"
            "reason（结束原因：达标/迭代上限/反复同错放弃 等）。"
            "不要再调用其他工具，不要再输出文本分析。"
        )
    )
    sys_msg = SystemMessage(
        content=ORCHESTRATOR_SYSTEM.format(
            target_mape=state["target_mape"],
            max_iter=state["max_iterations"],
        )
    )
    response = _finalize_llm.invoke([sys_msg, *state["messages"], nudge])

    # 顺便把本次 run 写进 episodic memory（即使失败也存，作为反例）
    try:
        eps = get_episodic()
        # 从 finalize 的 tool_calls 提取参数
        best_mape = state.get("best_mape", 999.0)
        best_code = state.get("best_code", "")
        for tc in getattr(response, "tool_calls", None) or []:
            if tc.get("name") == "finalize":
                args = tc.get("args", {})
                best_mape = args.get("best_mape", best_mape)
                best_code = args.get("best_code", best_code)
        eps.add(
            org=state["org"],
            target_month=state["target_month"],
            data_profile=f"目标月={state['target_month']}, MAPE={best_mape}",
            model="unknown",  # B3 反思子 Agent 后再细化
            mape=float(best_mape) if best_mape else 999.0,
            best_code=best_code or "",
        )
    except Exception:
        # memory 写入失败不阻塞主流程
        pass

    return {"messages": [response]}
