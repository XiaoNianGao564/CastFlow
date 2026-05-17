/**
 * CriticAgent — 评审智能体 (temperature: 0.2)
 *
 * 对应 CastClaw 的 Critic，负责：
 * - 分析预测性能，提取洞察
 * - 生成结构化最终报告
 * - 对比各区县表现
 * - 给出优化建议
 *
 * Prompt 风格：严谨、结构化、低温度保稳定
 */

import { z } from "zod"
import { LLMClient } from "../llm/client"
import { RAGStore } from "../store/ragStore"
import type { ForecastReport, IterationRecord, EvaluationMetrics } from "../types"
import { MODEL_POOL } from "./modelSelector"

const CRITIC_SYSTEM_PROMPT = `你是一位严谨的预测结果评审专家 (Critic)。

## 职责
1. 分析预测性能数据，提取关键洞察
2. 对比各区县之间的表现差异
3. 评估模型在不同数据特征下的适用性
4. 生成结构化的预测总结报告

## 原则
- 数据说话：所有结论必须有数据支撑
- 洞察优先：不只列数字，要解释"为什么"
- 建议实用：给出的优化建议要可执行`

const CriticSchema = z.object({
  overallAssessment: z.string(),
  strengths: z.array(z.string()),
  weaknesses: z.array(z.string()),
  recommendations: z.array(z.string()),
  convergenceNote: z.string(),
})

export class CriticAgent {
  name = "critic"
  private llm: LLMClient
  private ragStore: RAGStore

  constructor(llm: LLMClient) {
    // Critic 使用最低温度 (0.2) 确保输出稳定
    this.llm = llm
    this.ragStore = new RAGStore()
  }

  /** 审查预测结果，生成结构化报告 */
  async review(
    records: IterationRecord[],
    bestAccuracy: number,
    bestMetrics: EvaluationMetrics | null,
    taskInfo: { year: number; expectType: number; modelLabel: string },
    characteristics: any,
    onMessage?: (msg: string) => void,
  ): Promise<ForecastReport> {
    const log = (msg: string) => { onMessage?.(msg); console.log(`[Critic] ${msg}`) }

    log("分析预测性能...")

    // 迭代摘要
    const iterationSummary = records.map(r => ({
      version: r.version,
      accuracy: r.accuracy ?? null,
      improvement: r.improved ? `+${(r.accuracy ?? 0) - ((records.find(p => p.version === r.version - 1)?.accuracy ?? r.accuracy ?? 0))}%` : "—",
      patchSummary: r.patch?.patch_summary || r.source || (r.version === 0 ? "初始生成" : "—"),
      status: (r.version === 0 ? "initial" : r.improved ? "improved" : "no_change") as any,
    }))

    // 区县级性能
    const perOrg = bestMetrics?.per_org || {}
    const perOrgList = Object.entries(perOrg).map(([org, m]) => ({
      org,
      accuracy: m.accuracy,
      mape: m.mape,
      status: (m.accuracy >= 90 ? "优秀" : m.accuracy >= 80 ? "良好" : "待优化") as "优秀" | "良好" | "待优化",
    }))

    // 时间线
    const accuracyTimeline = records
      .filter(r => r.accuracy != null)
      .map(r => ({ version: r.version, accuracy: r.accuracy!, improved: r.improved || false, timestamp: r.timestamp || "" }))

    // 用 LLM 生成洞察
    let insights = { seasonalityImpact: "", trendAnalysis: "", volatilityAnalysis: "", convergenceAnalysis: "", bestModelReason: "" }
    if (this.llm.isAvailable) {
      try {
        const reviewData = {
          bestAccuracy: `${bestAccuracy.toFixed(2)}%`,
          mape: `${bestMetrics?.mape?.toFixed(2) || "?"}%`,
          bias: bestMetrics?.bias || "balanced",
          iterations: records.length,
          perOrg: JSON.stringify(perOrg),
        }
        const prompt = `基于以下预测结果生成一份精益评审：

最佳准确率: ${reviewData.bestAccuracy}
MAPE: ${reviewData.mape}
偏差: ${reviewData.bias}
迭代次数: ${reviewData.iterations}
各区县: ${reviewData.perOrg}

请分析：1) 整体评估 2) 优缺点 3) 优化建议 4) 收敛情况`

        const criticResult = await this.llm.generateObject(CriticSchema, prompt, CRITIC_SYSTEM_PROMPT, 0.2)
        insights = {
          seasonalityImpact: criticResult.overallAssessment,
          trendAnalysis: criticResult.weaknesses.slice(0, 2).join("; "),
          volatilityAnalysis: criticResult.strengths.slice(0, 2).join("; "),
          convergenceAnalysis: criticResult.convergenceNote,
          bestModelReason: `${taskInfo.modelLabel} 在该场景下表现最优`,
        }

        // 存入 RAG
        this.ragStore.add(JSON.stringify(reviewData), { category: "critic_review", accuracy: bestAccuracy })
      } catch {
        log("LLM 评审失败，使用规则生成")
      }
    }

    // 推荐建议
    const recommendations: string[] = []
    if (bestAccuracy < 90) recommendations.push("📈 尝试更多模型变体，如 XGBoost + ARIMA 混合模型")
    if (characteristics?.seasonalityStrength > 0.6) recommendations.push("🔄 强季节性数据，建议尝试 STL 分解 + 专用季节模型")
    if (characteristics?.volatility > 0.5) recommendations.push("⚡ 高波动数据，可探索 GARCH 波动率建模或异常值预处理")
    if (perOrgList.some(o => o.accuracy < 80)) {
      const poorOrgs = perOrgList.filter(o => o.accuracy < 80).map(o => o.org).join("、")
      recommendations.push(`🎯 ${poorOrgs} 准确率偏低，建议单独调参或增加专属特征`)
    }
    if (recommendations.length === 0) recommendations.push("✅ 当前模型表现良好，建议持续监控数据漂移")

    log(`报告生成完成: 最佳 ${bestAccuracy.toFixed(2)}%`)

    return {
      title: `CastFlow 预测总结报告`,
      generatedAt: new Date().toLocaleString("zh-CN"),
      taskInfo: {
        year: taskInfo.year,
        expectType: taskInfo.expectType === 1 ? "区民用电" : "煤改电",
        totalIterations: records.length,
        elapsed: "—",
        initialModel: taskInfo.modelLabel,
        bestModel: taskInfo.modelLabel,
        iterationsCompleted: iterationSummary.length,
      },
      bestResult: {
        accuracy: Number(bestAccuracy.toFixed(2)),
        mape: bestMetrics?.mape ?? 0,
        mae: bestMetrics?.mae ?? 0,
        rmse: bestMetrics?.rmse ?? 0,
        bias: bestMetrics?.bias || "balanced",
        level: bestMetrics?.level || "—",
      },
      perOrgPerformance: perOrgList,
      dataCharacteristics: {
        seasonalityStrength: characteristics?.seasonalityStrength ?? 0,
        trendDirection: characteristics?.trendDirection ?? "flat",
        trendStrength: characteristics?.trendStrength ?? 0,
        volatility: characteristics?.volatility ?? 0,
        orgCount: characteristics?.orgCount ?? 0,
        yearsCount: characteristics?.yearsCount ?? 0,
        description: characteristics
          ? `${characteristics.orgCount} 区县 × ${characteristics.yearsCount} 年`
          : "数据特征不足",
      },
      accuracyTimeline,
      iterationSummary,
      recommendations,
      strategyHistory: "",
      insights,
    }
  }
}
