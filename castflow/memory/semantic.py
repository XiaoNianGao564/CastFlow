"""Semantic memory - 提炼后的可复用 lesson 库。

由 Agent 主动调用 save_lesson() 写入；
下次任务前主动调 recall_lessons(query) 取出相关教训。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import chromadb

from castflow.memory.embedding import DashScopeEmbedding

_DATA_DIR = Path("data/chroma")


class SemanticMemory:
    def __init__(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(_DATA_DIR))
        self._col = self._client.get_or_create_collection(
            name="semantic",
            embedding_function=DashScopeEmbedding(),
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, topic: str, lesson: str, tags: list[str] | None = None) -> str:
        rid = str(uuid.uuid4())
        doc = f"主题: {topic}\n经验: {lesson}"
        self._col.add(
            ids=[rid],
            documents=[doc],
            metadatas=[{"topic": topic, "tags": ",".join(tags or [])}],
        )
        return rid

    def recall(self, query: str, top_k: int = 3) -> list[dict]:
        if self._col.count() == 0:
            return []
        n = min(top_k, self._col.count())
        res = self._col.query(query_texts=[query], n_results=n)
        out: list[dict] = []
        for i, mid in enumerate(res["ids"][0]):
            meta = res["metadatas"][0][i] if res.get("metadatas") else {}
            doc = res["documents"][0][i] if res.get("documents") else ""
            out.append(
                {
                    "id": mid,
                    "topic": meta.get("topic"),
                    "tags": meta.get("tags"),
                    "lesson": doc,
                }
            )
        return out

    def count(self) -> int:
        return self._col.count()


_semantic: SemanticMemory | None = None


def get_semantic() -> SemanticMemory:
    global _semantic
    if _semantic is None:
        _semantic = SemanticMemory()
    return _semantic
