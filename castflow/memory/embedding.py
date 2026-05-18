"""DashScope embedding wrapper - 给 Chroma 用，避免它去拉默认的英文模型。"""
from __future__ import annotations

import os
from typing import Sequence

from chromadb import EmbeddingFunction
from openai import OpenAI

from castflow.config import settings

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    return _client


class DashScopeEmbedding(EmbeddingFunction):
    """Chroma 兼容的通义千问 embedding。"""

    def __init__(self, model: str = "text-embedding-v2") -> None:
        self.model = model

    def __call__(self, input: Sequence[str]) -> list[list[float]]:
        client = _get_client()
        # DashScope 单次最多 25 条
        out: list[list[float]] = []
        batch_size = 10
        for i in range(0, len(input), batch_size):
            batch = list(input[i : i + batch_size])
            resp = client.embeddings.create(model=self.model, input=batch)
            out.extend(d.embedding for d in resp.data)
        return out

    @staticmethod
    def name() -> str:  # Chroma 0.5+ 要求
        return "dashscope-text-embedding-v2"
