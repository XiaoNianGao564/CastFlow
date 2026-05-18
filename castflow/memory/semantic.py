"""Semantic memory - 提炼后的可复用 lesson 库。

由 Agent 主动调用 save_lesson() 写入；
下次任务前主动调 recall_lessons(query) 取出相关教训。
"""
from __future__ import annotations

import uuid

from castflow.memory.embedding import DashScopeEmbedding
from castflow.memory.episodic import _make_client


class SemanticMemory:
    def __init__(self) -> None:
        self._client = _make_client()
        self._col = None
        if self._client is not None:
            try:
                self._col = self._client.get_or_create_collection(
                    name="semantic",
                    embedding_function=DashScopeEmbedding(),
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as e:  # noqa: BLE001
                print(f"[memory] semantic collection init failed: {e}")
                self._col = None

    @property
    def available(self) -> bool:
        return self._col is not None

    def add(self, topic: str, lesson: str, tags: list[str] | None = None) -> str:
        if not self.available:
            return ""
        rid = str(uuid.uuid4())
        doc = f"主题: {topic}\n经验: {lesson}"
        try:
            self._col.add(
                ids=[rid],
                documents=[doc],
                metadatas=[{"topic": topic, "tags": ",".join(tags or [])}],
            )
        except Exception as e:  # noqa: BLE001
            print(f"[memory] semantic.add failed: {e}")
            return ""
        return rid

    def recall(self, query: str, top_k: int = 3) -> list[dict]:
        if not self.available:
            return []
        try:
            if self._col.count() == 0:
                return []
            n = min(top_k, self._col.count())
            res = self._col.query(query_texts=[query], n_results=n)
        except Exception as e:  # noqa: BLE001
            print(f"[memory] semantic.recall failed: {e}")
            return []
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
        if not self.available:
            return 0
        try:
            return self._col.count()
        except Exception:  # noqa: BLE001
            return 0


_semantic: SemanticMemory | None = None


def get_semantic() -> SemanticMemory:
    """单例；上次 init 失败则下次重试。"""
    global _semantic
    if _semantic is None or not _semantic.available:
        _semantic = SemanticMemory()
    return _semantic
