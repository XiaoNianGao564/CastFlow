from __future__ import annotations

from typing import Any, Optional


def tool_response(
    *,
    success: bool,
    summary: str,
    data: Any = None,
    error: Optional[str] = None,
    next_action: Optional[str] = None,
    **legacy: Any,
) -> dict:
    payload = {
        "success": success,
        "summary": summary,
        "data": data,
        "error": error,
        "next_action": next_action,
    }
    payload.update(legacy)
    return payload


def tool_success(summary: str, data: Any = None, next_action: Optional[str] = None, **legacy: Any) -> dict:
    return tool_response(
        success=True,
        summary=summary,
        data=data,
        error=None,
        next_action=next_action,
        **legacy,
    )


def tool_error(summary: str, error: str, data: Any = None, next_action: Optional[str] = None, **legacy: Any) -> dict:
    return tool_response(
        success=False,
        summary=summary,
        data=data,
        error=error,
        next_action=next_action,
        **legacy,
    )
