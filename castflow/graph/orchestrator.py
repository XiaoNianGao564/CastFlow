import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from castflow.config import settings
from castflow.graph.edges import after_tools, should_continue
from castflow.graph.nodes import force_finalize_node, orchestrator_node
from castflow.graph.planner import planner_node
from castflow.state import ForecastState
from castflow.tools import get_core_tools


def _make_checkpointer():
    db_path = settings.checkpoint_db_path
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)


def build_graph():
    tools = get_core_tools()
    g = StateGraph(ForecastState)
    g.add_node("planner", planner_node)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("tools", ToolNode(tools))
    g.add_node("force_finalize", force_finalize_node)

    # START → planner → orchestrator ⇄ tools
    g.add_edge(START, "planner")
    g.add_edge("planner", "orchestrator")
    g.add_conditional_edges(
        "orchestrator", should_continue, ["tools", "force_finalize", "orchestrator"]
    )
    g.add_conditional_edges(
        "tools", after_tools, ["orchestrator", "planner", "force_finalize", END]
    )
    # force_finalize 后还要再经一次 ToolNode 把 finalize tool_call 真正执行
    g.add_edge("force_finalize", "tools")

    return g.compile(checkpointer=_make_checkpointer())
