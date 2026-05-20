from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class ForecastRequest(BaseModel):
    org: str
    target_month: str
    target_mape: Optional[float] = None
    max_iterations: Optional[int] = None
    session_id: Optional[str] = None
    prompt: Optional[str] = None
    require_approval: bool = False


class ApprovalRequest(BaseModel):
    decision_id: Optional[str] = None
    approved: bool = True
    feedback: str = ""
    edited_args: Optional[Dict[str, Any]] = None


class FeedbackRequest(BaseModel):
    message: str = Field(min_length=1)
    action: Literal["note", "pause", "resume", "replan", "stop"] = "note"


class RunResponse(BaseModel):
    thread_id: str
    status: str
    stream_url: str


class ApiMessage(BaseModel):
    status: str
    thread_id: Optional[str] = None
    message: str = ""
