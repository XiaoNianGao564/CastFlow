from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from castflow.graph.edges import after_tools, should_continue
from castflow.graph.nodes import force_finalize_node, orchestrator_node
from castflow.state import ForecastState
from castflow.tools import CORE_TOOLS


def build_graph():
    g = StateGraph(ForecastState)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("tools", ToolNode(CORE_TOOLS))
    g.add_node("force_finalize", force_finalize_node)

    g.add_edge(START, "orchestrator")
    g.add_conditional_edges(
        "orchestrator", should_continue, ["tools", "force_finalize"]
    )
    g.add_conditional_edges(
        "tools", after_tools, ["orchestrator", "force_finalize", END]
    )
    # force_finalize 后还要再经一次 ToolNode 把 finalize tool_call 真正执行
    g.add_edge("force_finalize", "tools")

    return g.compile(checkpointer=MemorySaver())
