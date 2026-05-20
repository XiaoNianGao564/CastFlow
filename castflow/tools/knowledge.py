"""Knowledge tools - 文档知识库 RAG 工具。"""
from __future__ import annotations

from langchain_core.tools import tool

from castflow.memory.knowledge import get_knowledge


@tool
def ingest_knowledge_docs(directory: str = "docs/knowledge") -> dict:
    """把本地业务文档导入 Chroma 文档知识库。

    支持 .md / .txt。用于初始化或更新文档型 RAG 知识库。

    Args:
        directory: 文档目录，默认 docs/knowledge
    """
    try:
        kb = get_knowledge()
        return kb.ingest_dir(directory)
    except Exception as e:  # noqa: BLE001
        return {"ingested": 0, "documents": 0, "chunks": 0, "error": str(e)}


@tool
def search_knowledge_docs(query: str, top_k: int = 4) -> dict:
    """从文档知识库检索与当前任务相关的业务规则、数据口径和建模规范。

    任务开始后、选模型或解释业务规则前优先调用。首次检索时如果库为空，会自动导入 docs/knowledge。

    Args:
        query: 查询文本，如 "电力负荷预测 数据口径 MAPE 防泄漏"
        top_k: 返回前几条文档片段
    """
    try:
        kb = get_knowledge()
        if not kb.available:
            return {"found": 0, "total_in_knowledge": 0, "documents": [], "note": "knowledge disabled"}
        results = kb.search(query, top_k=top_k)
        return {
            "found": len(results),
            "total_in_knowledge": kb.count(),
            "documents": results,
        }
    except Exception as e:  # noqa: BLE001
        return {"found": 0, "total_in_knowledge": 0, "documents": [], "error": str(e)}
