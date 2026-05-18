from langgraph.graph import END

from castflow.state import ForecastState


def should_continue(state: ForecastState) -> str:
    """orchestrator 之后：有 tool_calls → tools；否则结束"""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return END


def after_tools(state: ForecastState) -> str:
    """tools 之后：finalize 被调用 → 结束；超上限 → 结束；否则回 orchestrator"""
    for msg in reversed(state["messages"]):
        if getattr(msg, "name", None) == "finalize":
            return END
        if getattr(msg, "type", None) == "tool":
            break
    if state["iteration_count"] >= state["max_iterations"]:
        return END
    return "orchestrator"
