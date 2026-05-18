from langchain_core.messages import SystemMessage

from castflow.llm.client import make_orchestrator_llm
from castflow.prompts import ORCHESTRATOR_SYSTEM
from castflow.state import ForecastState
from castflow.tools import CORE_TOOLS

_llm = make_orchestrator_llm().bind_tools(CORE_TOOLS)


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
