"""Document knowledge base - 面向业务文档的 RAG 知识库。

与 semantic/episodic memory 不同，这里存的是稳定业务文档：数据口径、建模规则、评估规范等。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from castflow.memory.embedding import DashScopeEmbedding
from castflow.memory.episodic import _make_client

_DEFAULT_DOCS_DIR = Path("docs/knowledge")
_SUPPORTED_SUFFIXES = {".md", ".txt"}


def _normalize_path(path: str | Path) -> Path:
    return Path(path).resolve()


def _chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _stable_id(source: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{source}:{index}:{text}".encode("utf-8")).hexdigest()
    return f"kb-{digest}"


class KnowledgeBase:
    def __init__(self) -> None:
        self._client = _make_client()
        self._col = None
        if self._client is not None:
            try:
                self._col = self._client.get_or_create_collection(
                    name="knowledge_docs",
                    embedding_function=DashScopeEmbedding(),
                    metadata={"hnsw:space": "cosine"},
                )
                self.ingest_dir()
            except Exception as e:  # noqa: BLE001
                print(f"[memory] knowledge collection init failed: {e}")
                self._col = None

    @property
    def available(self) -> bool:
        return self._col is not None

    def ingest_dir(self, directory: str | Path = _DEFAULT_DOCS_DIR) -> dict:
        if not self.available:
            return {"ingested": 0, "documents": 0, "chunks": 0, "note": "knowledge disabled"}

        root = _normalize_path(directory)
        if not root.exists():
            return {"ingested": 0, "documents": 0, "chunks": 0, "note": f"directory not found: {root}"}

        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict] = []
        doc_count = 0

        for path in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _SUPPORTED_SUFFIXES):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="ignore")
            chunks = _chunk_text(text)
            if not chunks:
                continue
            doc_count += 1
            rel = str(path.relative_to(root)).replace("\\", "/")
            title = path.stem.replace("_", " ")
            for idx, chunk in enumerate(chunks):
                ids.append(_stable_id(rel, idx, chunk))
                docs.append(f"标题: {title}\n来源: {rel}\n\n{chunk}")
                metas.append(
                    {
                        "source": rel,
                        "title": title,
                        "chunk_index": idx,
                        "source_path": str(path),
                    }
                )

        if not ids:
            return {"ingested": 0, "documents": doc_count, "chunks": 0, "total": self.count()}

        try:
            self._col.upsert(ids=ids, documents=docs, metadatas=metas)
        except Exception as e:  # noqa: BLE001
            print(f"[memory] knowledge.ingest failed: {e}")
            return {"ingested": 0, "documents": doc_count, "chunks": 0, "error": str(e)}

        return {"ingested": len(ids), "documents": doc_count, "chunks": len(ids), "total": self.count()}

    def search(self, query: str, top_k: int = 4, auto_ingest: bool = True) -> list[dict]:
        if not self.available:
            return []
        try:
            if self._col.count() == 0 and auto_ingest:
                self.ingest_dir()
            if self._col.count() == 0:
                return []
            n = min(top_k, self._col.count())
            res = self._col.query(query_texts=[query], n_results=n)
        except Exception as e:  # noqa: BLE001
            print(f"[memory] knowledge.search failed: {e}")
            return []

        out: list[dict] = []
        ids = res.get("ids", [[]])[0]
        documents = res.get("documents", [[]])[0] if res.get("documents") else []
        metadatas = res.get("metadatas", [[]])[0] if res.get("metadatas") else []
        distances = res.get("distances", [[]])[0] if res.get("distances") else []
        for i, mid in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            out.append(
                {
                    "id": mid,
                    "source": meta.get("source"),
                    "title": meta.get("title"),
                    "chunk_index": meta.get("chunk_index"),
                    "content": documents[i] if i < len(documents) else "",
                    "distance": distances[i] if i < len(distances) else None,
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


_knowledge: KnowledgeBase | None = None


def get_knowledge() -> KnowledgeBase:
    global _knowledge
    if _knowledge is None or not _knowledge.available:
        _knowledge = KnowledgeBase()
    return _knowledge
