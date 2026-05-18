/**
 * PlannerAgent — 规划智能体 (temperature: 0.3)
 *
 * 职责：
 * - 数据诊断与完整性检查
 * - 智能模型选择（基于数据特征）
 * - 初始预测代码生成
 * - 网络调研注入行业知识
 *
 * Prompt 风格：精确、分析型、低探索
 */

import { z } from "zod"
import { LLMClient } from "../llm/client"
import { DataAgent } from "./data"
import { CodeGenAgent } from "./codeGen"
import { WebSearchEngine } from "../store/webSearch"
import { MODEL_POOL, selectInitialModel, extractCharacteristics } from "./modelSelector"
import type { DBConfig, DataPoint } from "../types"

const PLANNER_SYSTEM_PROMPT = `你是一位资深的时序预测规划专家 (Planner)。

## 职责
1. 分析数据质量和特征（完整性、季节性、趋势、波动性）
2. 基于数据特征推荐最合适的预测模型
3. 确保初始代码生成质量
4. 评估是否需要搜索行业知识补充

## 原则
- 精确至上：数据驱动决策，不猜测
- 先检查数据，再做决策
- 选择模型时优先考虑数据特征匹配度
- 当目标准确率高于 90% 时，考虑更复杂的模型`

export interface PlanResult {
  modelIndex: number
  modelLabel: string
  code: string
  codeSource: "llm" | "default"
  predictions: DataPoint[]
  characteristics: any
  researchContext: string
  dataSummary: string
}

export class PlannerAgent {
  name = "planner"
  private llm: LLMClient
  private dataAgent: DataAgent
  private codeGenAgent: CodeGenAgent
  private webSearch: WebSearchEngine

  constructor(llm: LLMClient, dbConfig: DBConfig) {
    // Planner 使用更低温度 (0.3) 追求精确
    this.llm = llm
    this.dataAgent = new DataAgent(dbConfig)
    this.codeGenAgent = new CodeGenAgent(llm, dbConfig)
    this.webSearch = new WebSearchEngine()
  }

  /** 完整规划流程：数据检查 → 模型选择 → 代码生成 */
  async plan(year: number, expectType: number, onMessage?: (msg: string) => void): Promise<PlanResult> {
    const log = (msg: string) => { onMessage?.(msg); console.log(`[Planner] ${msg}`) }

    log("Step 1/4: 数据完整性检查")
    const sanity = await this.dataAgent.run({ action: "sanity_check", expectType })
    if (!sanity.passed) throw new Error(`数据异常: ${sanity.errors.join("; ")}`)
    log(`数据检查通过: ${sanity.stats.orgCount} 区县, ${sanity.stats.totalMonths} 月数据`)

    log("Step 2/4: 数据特征分析 + 智能模型选择")
    const chars = await this.dataAgent.run({ action: "get_characteristics", expectType })
    const extracted = extractCharacteristics(chars, expectType)
    const modelResult = selectInitialModel(extracted)
    log(`数据特征: 季节=${(extracted.seasonalityStrength * 100).toFixed(0)}%, 趋势=${extracted.trendDirection}, 波动=${(extracted.volatility * 100).toFixed(0)}%`)
    log(`推荐模型: ${modelResult.label} (Top3: ${modelResult.reason})`)

    log("Step 3/4: 网络调研")
    let researchContext = ""
    try {
      const queries = [
        `电力负荷预测 最新方法 2025`,
        `${expectType === 1 ? "区民用电" : "煤改电"} 月度负荷特性 季节性`,
        `时间序列预测 电力数据 最佳实践 sklearn`,
      ]
      const allResults = await Promise.all(queries.map(q => this.webSearch.search(q, 3)))
      const merged = allResults.flat()
      if (merged.length > 0) {
        researchContext = merged.map((r, i) => `[${i + 1}] ${r.title}\n   ${r.snippet}`).join("\n\n")
        log(`WebSearch 完成: ${merged.length} 条结果`)
      }
    } catch {
      log("WebSearch 不可用，使用 LLM 知识回退")
    }

    log("Step 4/4: 生成初始预测代码")
    const genResult = await this.codeGenAgent.generateInitial(year, expectType, MODEL_POOL[modelResult.index].id, researchContext)
    log(`代码生成完成: ${genResult.source} (${genResult.code.length} 字符)`)

    return {
      modelIndex: modelResult.index,
      modelLabel: modelResult.label,
      code: genResult.code,
      codeSource: genResult.source,
      predictions: [],
      characteristics: extracted,
      researchContext,
      dataSummary: `${sanity.stats.orgCount}区县, ${extracted.yearsCount}年, 季节${(extracted.seasonalityStrength * 100).toFixed(0)}%, 趋势${extracted.trendDirection}`,
    }
  }
}
