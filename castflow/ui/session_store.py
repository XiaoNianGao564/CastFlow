from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RunSession:
    thread_id: str
    config: dict[str, Any]
    initial_state: dict[str, Any]
    status: str = "created"
    state: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    feedback: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    final_result: Optional[dict[str, Any]] = None
    cancelled: bool = False
    waiting_for_user: bool = False
    pending_decision: Optional[dict[str, Any]] = None
    lock: threading.RLock = field(default_factory=threading.RLock)
    cond: threading.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.cond = threading.Condition(self.lock)

    def append_event(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.events.append(event)
            self.cond.notify_all()

    def append_message(self, message: dict[str, Any]) -> None:
        with self.lock:
            self.messages.append(message)
            self.cond.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "thread_id": self.thread_id,
                "status": self.status,
                "state": self.state,
                "messages": list(self.messages),
                "events": list(self.events),
                "approvals": list(self.approvals),
                "feedback": list(self.feedback),
                "error": self.error,
                "final_result": self.final_result,
                "pending_decision": self.pending_decision,
                "waiting_for_user": self.waiting_for_user,
                "cancelled": self.cancelled,
            }

    def mark_waiting(self, decision: dict[str, Any]) -> None:
        with self.lock:
            self.waiting_for_user = True
            self.pending_decision = decision
            self.status = "waiting_for_user"
            self.cond.notify_all()

    def clear_waiting(self) -> None:
        with self.lock:
            self.waiting_for_user = False
            self.pending_decision = None
            if self.status == "waiting_for_user":
                self.status = "running"
            self.cond.notify_all()


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, RunSession] = {}
        self._lock = threading.RLock()

    def create(self, thread_id: str, config: dict[str, Any], initial_state: dict[str, Any]) -> RunSession:
        with self._lock:
            session = RunSession(thread_id=thread_id, config=config, initial_state=initial_state)
            session.state = dict(initial_state)
            self._sessions[thread_id] = session
            return session

    def get(self, thread_id: str) -> Optional[RunSession]:
        with self._lock:
            return self._sessions.get(thread_id)

    def get_or_none(self, thread_id: str) -> Optional[RunSession]:
        return self.get(thread_id)

    def ensure(self, thread_id: str) -> RunSession:
        session = self.get(thread_id)
        if session is None:
            raise KeyError(thread_id)
        return session

    def all_sessions(self) -> list[RunSession]:
        with self._lock:
            return list(self._sessions.values())

    def remove(self, thread_id: str) -> None:
        with self._lock:
            self._sessions.pop(thread_id, None)


STORE = SessionStore()
