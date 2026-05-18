from langgraph.graph import END

from castflow.state import ForecastState


def should_continue(state: ForecastState) -> str:
    """orchestrator 之后：有 tool_calls → tools；否则进强制收尾"""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return "force_finalize"


def after_tools(state: ForecastState) -> str:
    """tools 之后：finalize 被调用 → 真正结束；否则回 orchestrator
    迭代上限 → 也进 force_finalize 让 LLM 显式收尾"""
    for msg in reversed(state["messages"]):
        if getattr(msg, "name", None) == "finalize":
            return END
        if getattr(msg, "type", None) == "tool":
            break
    if state["iteration_count"] >= state["max_iterations"]:
        return "force_finalize"
    return "orchestrator"
