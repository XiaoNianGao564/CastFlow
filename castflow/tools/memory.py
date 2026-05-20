"""Memory tools - 暴露三层记忆给 LLM。
所有工具都做 try/except 兜底：chromadb 内部失败时返回空结果而不是把主流程拉崩。"""
from __future__ import annotations

import os

from langchain_core.tools import tool

from castflow.memory.episodic import get_episodic
from castflow.memory.semantic import get_semantic
from castflow.tools.schema import tool_error, tool_success


@tool
def recall_similar_runs(query: str, top_k: int = 5) -> dict:
    """从历史 run 库（episodic memory）召回与当前任务相似的过去 run。
    输入应包含区县、数据特征、目标月等关键词。**任务开始前先调用此工具看历史经验**。

    Args:
        query: 查询文本，如 "耀州 2026-12 季节性强 趋势上升"
        top_k: 返回前几条
    """
    try:
        if os.environ.get("CASTFLOW_EVAL_MEMORY_ABLATION") == "1":
            payload = {"found": 0, "total_in_memory": 0, "runs": [], "note": "memory ablation enabled"}
            return tool_success(
                "记忆系统已关闭。",
                data=payload,
                next_action="内存恢复后再重试。",
                **payload,
            )
        eps = get_episodic()
        if not eps.available:
            payload = {"found": 0, "total_in_memory": 0, "runs": [], "note": "memory disabled"}
            return tool_success(
                "记忆不可用，返回空历史 run。",
                data=payload,
                next_action="继续使用当前数据预测。",
                **payload,
            )
        results = eps.recall(query, top_k=top_k)
        payload = {
            "found": len(results),
            "total_in_memory": eps.count(),
            "runs": results,
        }
        return tool_success(
            f"召回 {len(results)} 条相似历史 run。",
            data=payload,
            next_action="参考历史 run 的模型和误差，继续生成候选。",
            **payload,
        )
    except Exception as e:  # noqa: BLE001
        payload = {"found": 0, "total_in_memory": 0, "runs": []}
        return tool_error("召回历史 run 失败，返回空结果。", str(e), data=payload, **payload)


@tool
def recall_lessons(query: str, top_k: int = 3) -> dict:
    """从经验库（semantic memory）召回与当前问题相关的可复用教训。
    用于：选模型前看哪些坑、写代码前看哪些常见错。

    Args:
        query: 主题描述，如 "数据泄漏" / "SARIMAX 收敛失败" / "短序列季节性"
        top_k: 返回前几条
    """
    try:
        if os.environ.get("CASTFLOW_EVAL_MEMORY_ABLATION") == "1":
            payload = {"found": 0, "total_in_memory": 0, "lessons": [], "note": "memory ablation enabled"}
            return tool_success(
                "记忆系统已关闭。",
                data=payload,
                next_action="内存恢复后再重试。",
                **payload,
            )
        sem = get_semantic()
        if not sem.available:
            payload = {"found": 0, "total_in_memory": 0, "lessons": [], "note": "memory disabled"}
            return tool_success(
                "记忆不可用，返回空经验教训。",
                data=payload,
                next_action="继续使用当前数据预测。",
                **payload,
            )
        results = sem.recall(query, top_k=top_k)
        payload = {
            "found": len(results),
            "total_in_memory": sem.count(),
            "lessons": results,
        }
        return tool_success(
            f"召回 {len(results)} 条经验教训。",
            data=payload,
            next_action="把 lesson 应用到当前预测代码或避免已知坑。",
            **payload,
        )
    except Exception as e:  # noqa: BLE001
        payload = {"found": 0, "total_in_memory": 0, "lessons": []}
        return tool_error("召回经验教训失败，返回空结果。", str(e), data=payload, **payload)


@tool
def save_lesson(topic: str, lesson: str, tags: list[str] | None = None) -> dict:
    """把本次发现的可复用经验存入语义记忆，供未来任务召回。
    适用场景：踩了一个坑、找到一个有效技巧、发现一个数据规律。

    Args:
        topic: 主题，简短 5-15 字，如 "SARIMAX 短序列季节性收敛失败"
        lesson: 详细描述，包含场景、原因、解决方法
        tags: 标签列表，如 ["sarimax", "convergence", "短序列"]
    """
    try:
        sem = get_semantic()
        if not sem.available:
            payload = {"saved": False, "note": "memory disabled"}
            return tool_success(
                "语义记忆不可用，未保存 lesson。",
                data=payload,
                next_action="继续当前流程，不阻塞预测。",
                **payload,
            )
        rid = sem.add(topic=topic, lesson=lesson, tags=tags)
        payload = {"saved": bool(rid), "id": rid, "total": sem.count()}
        return tool_success(
            "lesson 已保存。" if rid else "lesson 未保存。",
            data=payload,
            next_action="未来相似任务可通过 recall_lessons 召回。",
            **payload,
        )
    except Exception as e:  # noqa: BLE001
        payload = {"saved": False}
        return tool_error("保存 lesson 失败。", str(e), data=payload, **payload)
