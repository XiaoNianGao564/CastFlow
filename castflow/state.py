from __future__ import annotations

from operator import add
from typing import Any, Annotated, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class IterationRecord(TypedDict):
    version: int
    code: str
    mape: float
    insight: str
    model: NotRequired[str]
    validation_mape: NotRequired[float]
    accepted: NotRequired[bool]
    reason: NotRequired[str]
    diagnostics: NotRequired[dict[str, Any]]
    candidate_model: NotRequired[str]


class PendingDecision(TypedDict, total=False):
    id: str
    kind: str
    node: str
    tool_name: str
    tool_call_id: str
    tool_args: dict[str, Any]
    summary: str
    risk_level: str
    status: str
    created_at: str
    user_feedback: str


# --- Sub-agent context isolation: artifact store ---

class Artifact(TypedDict):
    kind: str
    content: Any
    created_at: str
    source: str


# --- Plan-Execute: structured plan ---

class PlanStep(TypedDict):
    id: str
    action: str
    tool_hint: str
    status: str
    outcome: NotRequired[str]


class Plan(TypedDict):
    version: int
    steps: list[PlanStep]
    strategy_note: str


class ForecastState(TypedDict):
    goal: str
    org: str
    target_month: str
    target_months: NotRequired[list[str]]
    target_mape: float

    messages: Annotated[list[AnyMessage], add_messages]
    iterations: Annotated[list[IterationRecord], add]

    best_mape: float
    best_code: str
    best_model: NotRequired[str]
    best_validation_mape: NotRequired[float]
    current_plan: NotRequired[str]
    replan_hint: NotRequired[str]
    verifier_summary: NotRequired[dict]
    reflector_strategy: NotRequired[dict]
    last_diagnostics: NotRequired[dict[str, Any]]
    validation_diagnostics: NotRequired[dict[str, Any]]
    candidate_summaries: NotRequired[list[dict[str, Any]]]
    selection_reason: NotRequired[str]
    stagnation_count: NotRequired[int]
    rejected_candidates: NotRequired[list[dict[str, Any]]]
    candidate_code: NotRequired[str]
    candidate_mape: NotRequired[float]
    iteration_count: int
    max_iterations: int
    run_status: NotRequired[str]
    pending_decision: NotRequired[PendingDecision]
    approval_history: NotRequired[list[PendingDecision]]
    user_feedback: NotRequired[str]
    resume_action: NotRequired[str]
    ui_events: NotRequired[list[dict[str, Any]]]

    # 早停策略
    mape_history: NotRequired[list[float]]  # 每轮迭代的 MAPE 记录
    improvement_rate: NotRequired[float]  # 最近几轮的平均改善速率
    early_stop_reason: NotRequired[str]  # 早停原因

    # Token budget
    token_usage: NotRequired[dict[str, int]]  # 各 tier 累计 token 消耗快照

    # Sub-agent context isolation
    artifacts: NotRequired[dict[str, Artifact]]

    # Plan-Execute
    plan: NotRequired[Plan]
    plan_history: Annotated[list[Plan], add]
    needs_replan: NotRequired[bool]
    replan_count: NotRequired[int]
