/**
 * ExperienceStore - 跨会话经验记忆存储
 *
 * 保存每次迭代的有效/无效策略，跨会话累积学习。
 * 下次启动时注入 AnalysisAgent / PatchAgent 的 prompt，
 * 让 LLM 知道"历史上什么有效/无效"，避免重复踩坑。
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync } from "fs"
import { join } from "path"

export interface ExperienceEntry {
  id: string
  /** 模型 id（如 prophet / exp_smoothing） */
  modelId: string
  /** 策略类型 */
  strategyType: "model_params" | "data_preprocessing" | "feature_engineering" | "ensemble" | "external_features"
  /** 策略描述 */
  strategy: string
  /** 修改了什么 */
  whatChanged: string
  /** 改之前的准确率 */
  accuracyBefore: number
  /** 改之后的准确率 */
  accuracyAfter: number
  /** 是否有效（提升 > 0.5%） */
  effective: boolean
  /** 是否有副作用（比如破坏了输出格式） */
  hadSideEffect: boolean
  /** 时间戳 */
  timestamp: string
}

export class ExperienceStore {
  private filePath: string
  private maxEntries = 200

  constructor(dataDir?: string) {
    const dir = dataDir || join(process.cwd(), "memory")
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true })
    }
    this.filePath = join(dir, "experience.json")
  }

  /** 加载全部经验 */
  loadAll(): ExperienceEntry[] {
    try {
      if (!existsSync(this.filePath)) return []
      const raw = readFileSync(this.filePath, "utf-8")
      return JSON.parse(raw) || []
    } catch {
      return []
    }
  }

  /** 添加一条经验 */
  addEntry(entry: Omit<ExperienceEntry, "id" | "timestamp">) {
    const entries = this.loadAll()
    entries.push({
      ...entry,
      id: `exp_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      timestamp: new Date().toISOString(),
    })
    // 只保留最近 maxEntries 条
    const trimmed = entries.slice(-this.maxEntries)
    writeFileSync(this.filePath, JSON.stringify(trimmed, null, 2), "utf-8")
  }

  /** 获取有效策略（用于注入 prompt） */
  getEffectiveStrategies(): string {
    const entries = this.loadAll()
    const effective = entries.filter(e => e.effective && !e.hadSideEffect).slice(-10)
    if (!effective.length) return "（尚无跨会话有效策略记录）"
    return effective.map(e =>
      `[${e.modelId}] ${e.strategy} → ${e.whatChanged}: ${e.accuracyBefore.toFixed(1)}% → ${e.accuracyAfter.toFixed(1)}%（+${(e.accuracyAfter - e.accuracyBefore).toFixed(1)}%）`
    ).join("\n")
  }

  /** 获取失败策略（用于注入 prompt 避免重复） */
  getFailedStrategies(): string {
    const entries = this.loadAll()
    const failed = entries.filter(e => !e.effective || e.hadSideEffect).slice(-10)
    if (!failed.length) return "（尚无跨会话失败策略记录）"
    return failed.map(e =>
      `[${e.modelId}] ${e.strategy} → ${e.whatChanged}: ${e.accuracyBefore.toFixed(1)}% → ${e.accuracyAfter.toFixed(1)}%（${e.effective ? "有效" : "无效"}${e.hadSideEffect ? ", 有副作用" : ""}）`
    ).join("\n")
  }

  /** 获取指定模型的历史最佳策略 */
  getBestStrategyForModel(modelId: string): string {
    const entries = this.loadAll()
    const modelEntries = entries.filter(e => e.modelId === modelId && e.effective && !e.hadSideEffect)
    if (!modelEntries.length) return "（无历史）"
    const best = modelEntries.reduce((a, b) => a.accuracyAfter > b.accuracyAfter ? a : b)
    return `${best.strategy}: ${best.accuracyBefore.toFixed(1)}% → ${best.accuracyAfter.toFixed(1)}%`
  }

  /** 获取结构化的技能汇总（用于前端展示） */
  getSkillsSummary(): Array<{
    modelId: string
    modelLabel: string
    effectiveCount: number
    failedCount: number
    bestAccuracy: number
    lastAccuracy: number
    strategies: string[]
  }> {
    const entries = this.loadAll()
    const byModel: Record<string, { effective: number; failed: number; best: number; last: number; strategies: string[] }> = {}

    for (const e of entries) {
      if (!byModel[e.modelId]) {
        byModel[e.modelId] = { effective: 0, failed: 0, best: 0, last: 0, strategies: [] }
      }
      if (e.effective && !e.hadSideEffect) {
        byModel[e.modelId].effective++
        byModel[e.modelId].best = Math.max(byModel[e.modelId].best, e.accuracyAfter)
        byModel[e.modelId].last = e.accuracyAfter
        byModel[e.modelId].strategies.push(`${e.whatChanged}: ${e.accuracyBefore.toFixed(1)}%→${e.accuracyAfter.toFixed(1)}%`)
      } else {
        byModel[e.modelId].failed++
      }
    }

    return Object.entries(byModel).map(([modelId, data]) => ({
      modelId,
      modelLabel: modelId,
      effectiveCount: data.effective,
      failedCount: data.failed,
      bestAccuracy: Number(data.best.toFixed(2)),
      lastAccuracy: Number(data.last.toFixed(2)),
      strategies: data.strategies.slice(-5),
    })).sort((a, b) => b.effectiveCount - a.effectiveCount)
  }
}
