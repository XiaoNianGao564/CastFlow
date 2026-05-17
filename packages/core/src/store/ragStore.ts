/**
 * RAGStore - 基于 TF-IDF 的轻量向量存储（无需 ChromaDB）
 *
 * 使用 sklearn TfidfVectorizer + cosine similarity 实现文档检索。
 * 存储：每次迭代的分析结果/误差/策略作为文档，
 * 检索：给定查询找到最相关的历史文档。
 *
 * 如果环境安装了 chromadb 则优先使用，否则回退到 TF-IDF。
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync, readdirSync } from "fs"
import { join } from "path"
import { execSync } from "child_process"

export interface RAGDocument {
  id: string
  content: string
  metadata: Record<string, any>
  timestamp: string
}

export interface RAGQueryResult {
  document: RAGDocument
  score: number
}

export class RAGStore {
  private dir: string
  private indexFile: string
  private docs: RAGDocument[] = []
  private _useChromadb: boolean = false

  constructor(ragDir?: string) {
    const dir = ragDir || join(process.cwd(), "rag_store")
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
    this.dir = dir
    this.indexFile = join(dir, "documents.json")
    this.load()

    // 检测 chromadb 是否可用
    try {
      execSync(`python -c "import chromadb; print(chromadb.__version__)"`, {
        timeout: 5000, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
      })
      this._useChromadb = true
    } catch {
      this._useChromadb = false
    }
  }

  get useChromadb(): boolean {
    return this._useChromadb
  }

  /** 添加文档 */
  add(content: string, metadata: Record<string, any> = {}): string {
    const id = `doc_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
    const doc: RAGDocument = {
      id,
      content,
      metadata,
      timestamp: new Date().toISOString(),
    }
    this.docs.push(doc)
    this.save()
    return id
  }

  /** 批量添加 */
  addMany(entries: Array<{ content: string; metadata: Record<string, any> }>): string[] {
    return entries.map(e => this.add(e.content, e.metadata))
  }

  /** 检索最相关文档 */
  query(query: string, topK: number = 3): RAGQueryResult[] {
    if (this.docs.length === 0) return []

    if (this._useChromadb) {
      return this.queryChromadb(query, topK)
    }

    return this.queryTfidf(query, topK)
  }

  /** 获取所有文档 */
  getAll(): RAGDocument[] {
    return [...this.docs]
  }

  /** 清除所有文档 */
  clear() {
    this.docs = []
    this.save()
  }

  /** 统计信息 */
  stats() {
    const categories: Record<string, number> = {}
    for (const doc of this.docs) {
      const cat = doc.metadata.category || "other"
      categories[cat] = (categories[cat] || 0) + 1
    }
    return {
      totalDocs: this.docs.length,
      usingChromadb: this._useChromadb,
      categories,
    }
  }

  // ======= TF-IDF 检索 =======
  private queryTfidf(query: string, topK: number): RAGQueryResult[] {
    // 通过 Python 子进程执行向量化 + 相似度计算
    const pyCode = `
import json, sys
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

docs_json = ${JSON.stringify(JSON.stringify(this.docs.map(d => d.content)))}
docs = json.loads(docs_json)
query = ${JSON.stringify(query)}

if not docs:
    print(json.dumps([]))
    sys.exit(0)

# TF-IDF 向量化（使用字符 n-gram 支持中文）
vectorizer = TfidfVectorizer(max_features=2000, analyzer='char', ngram_range=(2, 4))
try:
    doc_vectors = vectorizer.fit_transform(docs)
    query_vector = vectorizer.transform([query])
    similarities = cosine_similarity(query_vector, doc_vectors).flatten()
except Exception:
    print(json.dumps([]))
    sys.exit(0)

# 取 topK
top_indices = np.argsort(similarities)[::-1][:${topK}]
results = []
for idx in top_indices:
    if similarities[idx] > 0.01:  # 忽略极低相似度
        results.append({"index": int(idx), "score": round(float(similarities[idx]), 4)})

print(json.dumps(results, ensure_ascii=False))
`

    const tmpPy = join(this.dir, "_query_tfidf.py")
    writeFileSync(tmpPy, pyCode, "utf-8")

    try {
      const output = execSync(`python "${tmpPy}"`, {
        encoding: "utf-8",
        timeout: 15000,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
        maxBuffer: 50 * 1024 * 1024,
      }).trim()

      const results: Array<{ index: number; score: number }> = JSON.parse(output)
      return results
        .filter(r => r.index >= 0 && r.index < this.docs.length)
        .map(r => ({ document: this.docs[r.index], score: r.score }))
        .slice(0, topK)
    } catch (e) {
      console.warn(`[RAGStore] TF-IDF query failed: ${e}`)
      return []
    } finally {
      try { require("fs").unlinkSync(tmpPy) } catch {}
    }
  }

  // ======= ChromaDB 检索（如果可用） =======
  private queryChromadb(query: string, topK: number): RAGQueryResult[] {
    const pyCode = `
import json, sys
import chromadb
from chromadb.config import Settings

client = chromadb.Client(Settings(
    chroma_db_impl="duckdb+parquet",
    persist_directory=r"${this.dir.replace(/\\/g, "\\\\")}\\chroma"
))

collection_name = "castflow_rag"
try:
    collection = client.get_collection(collection_name)
except:
    print(json.dumps([]))
    sys.exit(0)

results = collection.query(query_texts=[${JSON.stringify(query)}], n_results=${topK})
if not results["ids"] or not results["ids"][0]:
    print(json.dumps([]))
    sys.exit(0)

output = []
for i in range(len(results["ids"][0])):
    output.append({
        "id": results["ids"][0][i],
        "content": results["documents"][0][i] if results["documents"] else "",
        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
        "score": results["distances"][0][i] if results["distances"] else 0,
    })

print(json.dumps(output, ensure_ascii=False))
`

    const tmpPy = join(this.dir, "_query_chroma.py")
    writeFileSync(tmpPy, pyCode, "utf-8")

    try {
      const output = execSync(`python "${tmpPy}"`, {
        encoding: "utf-8",
        timeout: 30000,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
        maxBuffer: 50 * 1024 * 1024,
      }).trim()

      const raw: Array<{ id: string; content: string; metadata: Record<string, any>; score: number }> = JSON.parse(output)
      return raw
        .filter(r => r.content)
        .map(r => ({
          document: { id: r.id, content: r.content, metadata: r.metadata || {}, timestamp: "" },
          score: 1 - r.score, // ChromaDB 返回距离，转换为相似度
        }))
    } catch (e) {
      console.warn(`[RAGStore] ChromaDB query failed: ${e}`)
      return []
    } finally {
      try { require("fs").unlinkSync(tmpPy) } catch {}
    }
  }

  // ======= 持久化 =======
  private save() {
    writeFileSync(this.indexFile, JSON.stringify(this.docs, null, 2), "utf-8")
  }

  private load() {
    try {
      if (existsSync(this.indexFile)) {
        this.docs = JSON.parse(readFileSync(this.indexFile, "utf-8"))
      }
    } catch {
      this.docs = []
    }
  }
}
