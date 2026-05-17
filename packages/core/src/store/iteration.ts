/**
 * 迭代记录存储器（JSON 文件持久化）
 * 对应 CastClaw 的 memory/ 目录 + session 表
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync, unlinkSync, readdirSync } from "fs"
import { join } from "path"
import type { IterationRecord, AccuracyPoint } from "../types"

export class IterationStore {
  private dir: string
  private index: { versions: number[]; latestVersion: number }

  constructor(dir?: string) {
    this.dir = dir || join(process.cwd(), "memory")
    if (!existsSync(this.dir)) mkdirSync(this.dir, { recursive: true })
    this.index = this.loadIndex()
  }

  saveIteration(version: number, record: IterationRecord): void {
    const file = join(this.dir, `iteration_${version}.json`)
    record.timestamp = new Date().toISOString()
    writeFileSync(file, JSON.stringify(record, null, 2), "utf-8")

    if (!this.index.versions.includes(version)) this.index.versions.push(version)
    this.index.latestVersion = Math.max(...this.index.versions)
    this.saveIndex()
  }

  loadIteration(version: number): IterationRecord | null {
    const file = join(this.dir, `iteration_${version}.json`)
    if (!existsSync(file)) return null
    return JSON.parse(readFileSync(file, "utf-8"))
  }

  loadAll(): IterationRecord[] {
    const records: IterationRecord[] = []
    for (const v of this.index.versions.sort((a, b) => a - b)) {
      const r = this.loadIteration(v)
      if (r) records.push(r)
    }
    return records
  }

  getLatest(): IterationRecord | null {
    if (this.index.latestVersion < 0) return null
    return this.loadIteration(this.index.latestVersion)
  }

  getAccuracyTimeline(): AccuracyPoint[] {
    const timeline: AccuracyPoint[] = []
    for (const r of this.loadAll()) {
      if (r.accuracy != null) {
        timeline.push({
          version: r.version,
          accuracy: r.accuracy,
          improved: r.improved || false,
          timestamp: r.timestamp || "",
        })
      }
    }
    return timeline
  }

  getStrategyHistory(): string {
    const records = this.loadAll()
    if (records.length <= 1) return "暂无历史优化策略"

    const lines = [
      "| 版本 | 修改摘要 | 准确率 | 效果 |",
      "|------|----------|--------|------|",
    ]
    for (const r of records) {
      const summary = r.patch?.patch_summary || "初始版本"
      const acc = r.accuracy != null ? `${r.accuracy.toFixed(2)}%` : "—"
      const mark = r.improved ? "↑" : r.version === 0 ? "—" : "→"
      lines.push(`| v${r.version} | ${summary} | ${acc} | ${mark} |`)
    }
    return lines.join("\n")
  }

  /**
   * 获取有效策略摘要（用于迁移学习）
   * 返回已证明有效/无效的策略记录，帮助 LLM 在下一轮迭代时参考
   */
  getEffectiveStrategies(): string {
    const records = this.loadAll()
    if (records.length <= 1) return "暂无历史有效策略"

    const succeeded: string[] = []
    const failed: string[] = []
    let bestAcc = 0
    let bestVersion = 0

    for (const r of records) {
      if (r.accuracy != null && r.accuracy > bestAcc) {
        bestAcc = r.accuracy
        bestVersion = r.version
      }
      if (!r.patch?.patch_summary) continue
      if (r.improved) {
        succeeded.push(`- v${r.version}: ${r.patch.patch_summary} (准确率: ${r.accuracy?.toFixed(2) || "?"}%)`)
      } else {
        failed.push(`- v${r.version}: ${r.patch.patch_summary} (准确率: ${r.accuracy?.toFixed(2) || "?"}%，未提升)`)

      }
    }

    const parts: string[] = []
    if (succeeded.length > 0) {
      parts.push(`## 历史上证明有效的策略（推荐参考）：\n${succeeded.join("\n")}`)
    }
    if (failed.length > 0) {
      parts.push(`\n## 历史上未产生提升的策略（避免重复）：\n${failed.join("\n")}`)
    }
    parts.push(`\n当前最优：v${bestVersion} ${bestAcc.toFixed(2)}%`)

    return parts.join("\n")
  }

  clear(): void {
    for (const v of this.index.versions) {
      try { unlinkSync(join(this.dir, `iteration_${v}.json`)) } catch {}
    }
    this.index = { versions: [], latestVersion: -1 }
    this.saveIndex()
  }

  get length(): number {
    return this.index.versions.length
  }

  private loadIndex(): { versions: number[]; latestVersion: number } {
    const file = join(this.dir, "index.json")
    if (!existsSync(file)) return { versions: [], latestVersion: -1 }
    try {
      return JSON.parse(readFileSync(file, "utf-8"))
    } catch {
      return { versions: [], latestVersion: -1 }
    }
  }

  private saveIndex(): void {
    writeFileSync(
      join(this.dir, "index.json"),
      JSON.stringify(this.index, null, 2),
      "utf-8"
    )
  }
}
