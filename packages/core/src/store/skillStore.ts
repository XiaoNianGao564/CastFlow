/**
 * SkillStore - 结构化技能沉淀系统
 *
 * 升级 ExperienceStore 为跨项目可复用的 Skill 系统。
 * 每个 Skill 包含：
 * - 数据特征画像（季节强度/趋势/波动性/区县数）
 * - 有效/无效策略列表
 * - 最佳准确率 + 适用场景标签
 *
 * 支持：跨项目存储、数据特征匹配检索、导出导入
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync, readdirSync, unlinkSync } from "fs"
import { join } from "path"

export interface SkillDataProfile {
  seasonalityStrength: number
  trendDirection: "up" | "down" | "flat"
  trendStrength: number
  volatility: number
  orgCount: number
  yearsCount: number
}

export interface SkillStrategy {
  description: string
  accuracyBefore: number
  accuracyAfter: number
  effective: boolean
  hadSideEffect: boolean
  changedSection?: string
}

export interface Skill {
  skillId: string
  modelId: string
  modelLabel: string
  expectType: number

  /** 数据特征画像（用于匹配相似任务） */
  dataProfile: SkillDataProfile

  /** 有效策略列表 */
  effectiveStrategies: SkillStrategy[]
  /** 无效策略列表 */
  failedStrategies: SkillStrategy[]

  /** 最佳准确率 */
  bestAccuracy: number
  /** 最后一次准确率 */
  lastAccuracy: number

  /** 适用用电类型 */
  applicableTypes: number[]
  /** 标签（用于搜索） */
  tags: string[]

  /** 使用次数 */
  usageCount: number
  /** 项目名称/路径 */
  projectName: string

  createdAt: string
  updatedAt: string
}

export class SkillStore {
  private skillsDir: string
  private indexFile: string

  constructor(skillsDir?: string) {
    const dir = skillsDir || join(process.cwd(), "skills")
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true })
    }
    this.skillsDir = dir
    this.indexFile = join(dir, "index.json")
    // 确保索引文件存在
    if (!existsSync(this.indexFile)) {
      writeFileSync(this.indexFile, "[]", "utf-8")
    }
  }

  /** 列出所有 Skill */
  listAll(): Skill[] {
    try {
      if (!existsSync(this.indexFile)) return []
      const raw = readFileSync(this.indexFile, "utf-8")
      return JSON.parse(raw) || []
    } catch {
      return []
    }
  }

  /** 保存单个 skill 文件 */
  private saveSkillFile(skill: Skill) {
    const filePath = join(this.skillsDir, `${skill.skillId}.json`)
    writeFileSync(filePath, JSON.stringify(skill, null, 2), "utf-8")
  }

  /** 加载单个 skill 文件 */
  loadSkill(skillId: string): Skill | null {
    try {
      const filePath = join(this.skillsDir, `${skillId}.json`)
      if (!existsSync(filePath)) return null
      return JSON.parse(readFileSync(filePath, "utf-8"))
    } catch {
      return null
    }
  }

  /** 添加或更新 Skill */
  save(skill: Skill) {
    const skills = this.listAll()
    const idx = skills.findIndex(s => s.skillId === skill.skillId)
    if (idx >= 0) {
      skills[idx] = skill
    } else {
      skills.push(skill)
    }
    writeFileSync(this.indexFile, JSON.stringify(skills, null, 2), "utf-8")
    this.saveSkillFile(skill)
  }

  /** 根据数据特征匹配最相似的 Skill（用于自动推荐初始模型） */
  match(dataProfile: Partial<SkillDataProfile>, topK: number = 3): Array<{ skill: Skill; score: number }> {
    const skills = this.listAll()
    const scored: Array<{ skill: Skill; score: number }> = []

    for (const skill of skills) {
      let score = 0
      const dp = skill.dataProfile

      // 季节强度匹配（权重 0.3）
      if (dataProfile.seasonalityStrength != null && dp.seasonalityStrength != null) {
        score += (1 - Math.abs(dataProfile.seasonalityStrength - dp.seasonalityStrength)) * 30
      }

      // 趋势方向匹配（权重 0.2）
      if (dataProfile.trendDirection && dp.trendDirection) {
        score += dataProfile.trendDirection === dp.trendDirection ? 20 : 0
      }

      // 趋势强度匹配（权重 0.15）
      if (dataProfile.trendStrength != null && dp.trendStrength != null) {
        score += (1 - Math.abs(dataProfile.trendStrength - dp.trendStrength)) * 15
      }

      // 波动性匹配（权重 0.15）
      if (dataProfile.volatility != null && dp.volatility != null) {
        score += (1 - Math.abs(dataProfile.volatility - dp.volatility)) * 15
      }

      // 准确率奖励（权重 0.2）
      score += Math.min(skill.bestAccuracy / 100, 1) * 20

      scored.push({ skill, score: Math.round(score) })
    }

    return scored.sort((a, b) => b.score - a.score).slice(0, topK)
  }

  /** 从经验条目创建/更新 Skill */
  createFromExperience(
    modelId: string,
    modelLabel: string,
    description: string,
    accuracyBefore: number,
    accuracyAfter: number,
    effective: boolean,
    hadSideEffect: boolean,
    dataProfile: SkillDataProfile,
    expectType: number,
    projectName: string = "CastFlow",
  ): Skill {
    const skills = this.listAll()
    const existing = skills.find(s => s.modelId === modelId)

    const now = new Date().toISOString()
    const strategy: SkillStrategy = {
      description,
      accuracyBefore,
      accuracyAfter,
      effective,
      hadSideEffect,
    }

    if (existing) {
      // 更新现有 Skill
      existing.updatedAt = now
      existing.usageCount++
      existing.lastAccuracy = accuracyAfter
      if (effective && !hadSideEffect) {
        existing.effectiveStrategies.push(strategy)
        if (accuracyAfter > existing.bestAccuracy) {
          existing.bestAccuracy = accuracyAfter
        }
      } else {
        existing.failedStrategies.push(strategy)
      }
      this.save(existing)
      return existing
    }

    // 创建新 Skill
    const skillId = `skill_${modelId}_${Date.now()}`
    const newSkill: Skill = {
      skillId,
      modelId,
      modelLabel,
      expectType,
      dataProfile,
      effectiveStrategies: effective && !hadSideEffect ? [strategy] : [],
      failedStrategies: !effective || hadSideEffect ? [strategy] : [],
      bestAccuracy: accuracyAfter,
      lastAccuracy: accuracyAfter,
      applicableTypes: [expectType],
      tags: [`${expectType === 1 ? "区民用电" : "煤改电"}`, modelLabel],
      usageCount: 1,
      projectName,
      createdAt: now,
      updatedAt: now,
    }
    this.save(newSkill)
    return newSkill
  }

  /** 导出 Skill 为 JSON 字符串 */
  exportSkill(skillId: string): string | null {
    const skill = this.loadSkill(skillId)
    if (!skill) return null
    return JSON.stringify(skill, null, 2)
  }

  /** 导入 Skill（从 JSON 字符串） */
  importSkill(jsonStr: string): Skill | null {
    try {
      const skill: Skill = JSON.parse(jsonStr)
      if (!skill.skillId || !skill.modelId) return null
      this.save(skill)
      return skill
    } catch {
      return null
    }
  }

  /** 删除 Skill */
  delete(skillId: string): boolean {
    const skills = this.listAll()
    const filtered = skills.filter(s => s.skillId !== skillId)
    if (filtered.length === skills.length) return false
    writeFileSync(this.indexFile, JSON.stringify(filtered, null, 2), "utf-8")
    try { unlinkSync(join(this.skillsDir, `${skillId}.json`)) } catch {}
    return true
  }

  /** 统计信息 */
  getStats() {
    const skills = this.listAll()
    return {
      totalSkills: skills.length,
      totalEffectiveStrategies: skills.reduce((s, sk) => s + sk.effectiveStrategies.length, 0),
      totalFailedStrategies: skills.reduce((s, sk) => s + sk.failedStrategies.length, 0),
      modelsWithSkill: [...new Set(skills.map(s => s.modelId))].length,
      bestAccuracy: skills.length ? Math.max(...skills.map(s => s.bestAccuracy)) : 0,
    }
  }
}
