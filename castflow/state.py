from typing import Annotated, TypedDict
from operator import add

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class IterationRecord(TypedDict):
    version: int
    code: str
    mape: float
    insight: str


class ForecastState(TypedDict):
    goal: str
    org: str
    target_month: str
    target_mape: float

    messages: Annotated[list[AnyMessage], add_messages]
    iterations: Annotated[list[IterationRecord], add]

    best_mape: float
    best_code: str
    iteration_count: int
    max_iterations: int
