from castflow.tools.schema import tool_error, tool_response, tool_success
from castflow.tools.data import list_orgs, load_history, load_actual
from castflow.tools.python_exec import run_python
from castflow.tools.eval import evaluate_mape
from castflow.tools.finalize import finalize, save_predictions
from castflow.tools.versioning import submit_candidate
from castflow.tools.memory import recall_similar_runs, recall_lessons, save_lesson
from castflow.tools.knowledge import ingest_knowledge_docs, search_knowledge_docs
from castflow.tools.strategy_tool import get_strategy_recommendation


def get_core_tools():
    from castflow.subagents.reflector import delegate_to_reflector
    from castflow.subagents.coder import delegate_to_coder
    from castflow.subagents.verifier import delegate_to_verifier

    core = [
        list_orgs,
        load_history,
        load_actual,
        run_python,
        evaluate_mape,
        submit_candidate,
        recall_similar_runs,
        recall_lessons,
        save_lesson,
        ingest_knowledge_docs,
        search_knowledge_docs,
        get_strategy_recommendation,
        delegate_to_coder,
        delegate_to_verifier,
        delegate_to_reflector,
        finalize,
        save_predictions,
    ]

    from castflow.config import settings
    if not settings.mcp_enabled:
        return core

    import asyncio
    try:
        from castflow.tools.mcp_loader import load_mcp_tools
        mcp_tools = asyncio.run(load_mcp_tools())
        core_names = {t.name for t in core}
        for t in mcp_tools:
            if t.name not in core_names:
                core.append(t)
        print(f"[MCP] 加载了 {len(mcp_tools)} 个 MCP 工具，合并后共 {len(core)} 个")
    except Exception as e:  # noqa: BLE001
        print(f"[MCP] 工具加载失败，降级为仅 core 工具: {e}")

    return core


__all__ = [
    "get_core_tools",
    "tool_error",
    "tool_response",
    "tool_success",
    "list_orgs",
    "load_history",
    "load_actual",
    "run_python",
    "evaluate_mape",
    "submit_candidate",
    "finalize",
    "save_predictions",
    "recall_similar_runs",
    "recall_lessons",
    "save_lesson",
    "ingest_knowledge_docs",
    "search_knowledge_docs",
    "get_strategy_recommendation",
]

