from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from castflow.graph.edges import after_tools, should_continue
from castflow.graph.nodes import orchestrator_node
from castflow.state import ForecastState
from castflow.tools import CORE_TOOLS


def build_graph():
    g = StateGraph(ForecastState)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("tools", ToolNode(CORE_TOOLS))

    g.add_edge(START, "orchestrator")
    g.add_conditional_edges("orchestrator", should_continue, ["tools", END])
    g.add_conditional_edges("tools", after_tools, ["orchestrator", END])

    return g.compile(checkpointer=MemorySaver())
