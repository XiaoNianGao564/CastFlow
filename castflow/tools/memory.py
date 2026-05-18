"""Memory tools - 暴露三层记忆给 LLM。"""
from __future__ import annotations

from langchain_core.tools import tool

from castflow.memory.episodic import get_episodic
from castflow.memory.semantic import get_semantic


@tool
def recall_similar_runs(query: str, top_k: int = 5) -> dict:
    """从历史 run 库（episodic memory）召回与当前任务相似的过去 run。
    输入应包含区县、数据特征、目标月等关键词。**任务开始前先调用此工具看历史经验**。

    Args:
        query: 查询文本，如 "耀州 2026-12 季节性强 趋势上升"
        top_k: 返回前几条
    """
    eps = get_episodic()
    results = eps.recall(query, top_k=top_k)
    return {
        "found": len(results),
        "total_in_memory": eps.count(),
        "runs": results,
    }


@tool
def recall_lessons(query: str, top_k: int = 3) -> dict:
    """从经验库（semantic memory）召回与当前问题相关的可复用教训。
    用于：选模型前看哪些坑、写代码前看哪些常见错。

    Args:
        query: 主题描述，如 "数据泄漏" / "SARIMAX 收敛失败" / "短序列季节性"
        top_k: 返回前几条
    """
    sem = get_semantic()
    results = sem.recall(query, top_k=top_k)
    return {
        "found": len(results),
        "total_in_memory": sem.count(),
        "lessons": results,
    }


@tool
def save_lesson(topic: str, lesson: str, tags: list[str] | None = None) -> dict:
    """把本次发现的可复用经验存入语义记忆，供未来任务召回。
    适用场景：踩了一个坑、找到一个有效技巧、发现一个数据规律。

    Args:
        topic: 主题，简短 5-15 字，如 "SARIMAX 短序列季节性收敛失败"
        lesson: 详细描述，包含场景、原因、解决方法
        tags: 标签列表，如 ["sarimax", "convergence", "短序列"]
    """
    sem = get_semantic()
    rid = sem.add(topic=topic, lesson=lesson, tags=tags)
    return {"saved": True, "id": rid, "total": sem.count()}
