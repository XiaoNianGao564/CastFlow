"""Episodic memory - 历次 run 的向量库。

每次任务结束（finalize 或上限）时把这次的关键信息存下来：
  org / target_month / data_profile / chosen_model / final_mape / best_code

下次任务前 Agent 主动调 recall_similar_runs(profile) 看历史经验。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import chromadb

from castflow.memory.embedding import DashScopeEmbedding

_DATA_DIR = Path("data/chroma")


class EpisodicMemory:
    def __init__(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(_DATA_DIR))
        self._col = self._client.get_or_create_collection(
            name="episodic",
            embedding_function=DashScopeEmbedding(),
            metadata={"hnsw:space": "cosine"},
        )

    def add(
        self,
        org: str,
        target_month: str,
        data_profile: str,
        model: str,
        mape: float,
        best_code: str,
    ) -> str:
        rid = str(uuid.uuid4())
        # 用于检索的文本：组合 org + profile + model 让相似数据特征能召回
        doc = (
            f"区县={org}; 目标月={target_month}; 模型={model}; "
            f"MAPE={mape:.2f}; 数据特征={data_profile}"
        )
        self._col.add(
            ids=[rid],
            documents=[doc],
            metadatas=[
                {
                    "org": org,
                    "target_month": target_month,
                    "model": model,
                    "mape": float(mape),
                    "best_code_len": len(best_code or ""),
                    "best_code_preview": (best_code or "")[:500],
                }
            ],
        )
        return rid

    def recall(self, query: str, top_k: int = 5) -> list[dict]:
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
                    "summary": doc,
                    "org": meta.get("org"),
                    "model": meta.get("model"),
                    "mape": meta.get("mape"),
                    "code_preview": meta.get("best_code_preview"),
                }
            )
        return out

    def count(self) -> int:
        return self._col.count()


_episodic: EpisodicMemory | None = None


def get_episodic() -> EpisodicMemory:
    global _episodic
    if _episodic is None:
        _episodic = EpisodicMemory()
    return _episodic
