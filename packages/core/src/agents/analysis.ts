/**
 * AnalysisAgent - 误差深度分析智能体
 *
 * 职责：调用 LLM 分析预测误差的根本原因，
 * 对比预测曲线 vs 真实曲线 vs 历史模式，输出优化方向
 */

import { LLMClient } from "../llm/client"
import type { EvaluationMetrics, AnalysisResult, DBConfig } from "../types"
import { DBReader } from "../db/mysql"

const ANALYSIS_PROMPT = `你是一位资深的电力负荷预测与时间序列分析专家。

## 评估指标
- MAPE: {mape}%
- 准确率: {accuracy}%
- 样本数: {sample_count}
- MAE: {mae}
- RMSE: {rmse}
- 偏差方向: {bias}

## 各区县准确率
{perOrgTable}

## 误差明细（按误差降序排列）
{errorPairs}

## 历史模式
{historyPatterns}

## 迭代记忆迁移（历史有效/无效策略）
{effectiveStrategies}

请特别注意"历史上有效的策略"，优先在它们的改进方向上继续深入；"未产生提升的策略"请避免重复使用。

## 当前预测代码
\`\`\`python
{code}
\`\`\`

## 分析要求
从以下维度深度分析误差原因：
1. **曲线形态偏差** - 预测曲线与历史模式的形态差异
2. **系统性偏倚** - 持续高估/低估、特定月份系统误差
3. **模型缺陷** - 参数选择是否合理，模型是否适合该数据特征
4. **数据质量** - 历史数据是否充足，异常值影响
5. **外部因素** - 温度异常、政策变化等未被建模的因素

## 输出格式（只输出 JSON）
{
    "root_causes": [{"factor": "", "category": "curve_deviation|systematic_bias|model_deficiency|data_quality|external_factor", "impact": "high|medium|low", "detail": ""}],
    "severity": "critical|warning|info",
    "suggestions": [{"type": "model_params|data_preprocessing|feature_engineering|ensemble|external_features", "priority": 1, "description": "", "expected_improvement": "high|medium|low", "rationale": ""}],
    "patch_description": "一句总结要修改什么",
    "target_modules": ["数据预处理", "模型参数", "预测逻辑"]
}`

const FALLBACK_SUGGESTIONS = [
  { type: "model_params", priority: 1, description: "自动搜索最优 ARIMA(p,d,q) 参数组合", expected_improvement: "high", rationale: "固定参数非最优，可用 AIC/BIC 选择" },
  { type: "data_preprocessing", priority: 2, description: "对序列进行异常值检测和平滑处理", expected_improvement: "medium", rationale: "异常值导致参数偏移" },
  { type: "feature_engineering", priority: 3, description: "考虑对数变换或差分处理非平稳性", expected_improvement: "medium", rationale: "电力负荷有趋势和季节性" },
]

export class AnalysisAgent {
  name = "analysis_agent"
  private llm: LLMClient

  constructor(llm: LLMClient, private dbConfig?: DBConfig) {
    this.llm = llm
  }

  async analyze(evaluation: EvaluationMetrics, code: string, year: number, expectType: number, strategyHistory = "", effectiveStrategies = ""): Promise<{
    root_causes: any[]
    severity: string
    suggestions: any[]
    patch_description: string
    target_modules: string[]
  }> {
    const pairs = evaluation.pairs || []
    const errorPairs = pairs
      .sort((a, b) => Math.abs(b.error_pct) - Math.abs(a.error_pct))
      .slice(0, 15)

    const perOrg = evaluation.per_org || {}
    const perOrgTable = Object.entries(perOrg)
      .map(([org, m]) => `${org}: 准确率=${m.accuracy}%, MAPE=${m.mape}%`)
      .join("\n")

    // 获取历史模式（用于曲线对比）
    let historyPatterns = ""
    if (this.dbConfig) {
      try {
        const reader = new DBReader(this.dbConfig)
        const chars = await reader.getCharacteristics(expectType)
        historyPatterns = JSON.stringify(chars, null, 2).slice(0, 2000)
      } catch {}
    }

    const prompt = ANALYSIS_PROMPT
      .replace("{mape}", String(evaluation.mape))
      .replace("{accuracy}", String(evaluation.accuracy))
      .replace("{sample_count}", String(evaluation.sample_count))
      .replace("{mae}", String(evaluation.mae))
      .replace("{rmse}", String(evaluation.rmse))
      .replace("{bias}", evaluation.bias || "balanced")
      .replace("{perOrgTable}", perOrgTable)
      .replace("{errorPairs}", JSON.stringify(errorPairs, null, 2))
      .replace("{historyPatterns}", historyPatterns || "（获取失败）")
      .replace("{effectiveStrategies}", effectiveStrategies || "（尚无迭代历史）")
      .replace("{code}", code.slice(0, 4000))

    if (this.llm.isAvailable) {
      try {
        const result = await this.llm.chatJSON(prompt, undefined, 0.3) as AnalysisResult
        return {
          root_causes: result.root_causes || [],
          severity: result.severity || "info",
          suggestions: result.suggestions || FALLBACK_SUGGESTIONS,
          patch_description: result.patch_description || "基于误差分析优化预测代码",
          target_modules: result.target_modules || ["模型参数", "数据预处理"],
        }
      } catch (e) {
        console.warn(`[AnalysisAgent] LLM 分析失败: ${e}`)
      }
    }

    // 规则兜底
    return this.ruleBased(evaluation)
  }

  private ruleBased(evaluation: EvaluationMetrics) {
    const causes: any[] = []
    if (evaluation.mape > 20) {
      causes.push({ factor: "整体预测误差过大", category: "model_deficiency", impact: "high", detail: `MAPE=${evaluation.mape}% 超过20%阈值` })
    }
    if (evaluation.bias === "overestimate") {
      causes.push({ factor: "系统性高估", category: "systematic_bias", impact: "medium", detail: "预测值持续高于真实值" })
    }
    if (evaluation.bias === "underestimate") {
      causes.push({ factor: "系统性低估", category: "systematic_bias", impact: "medium", detail: "预测值持续低于真实值" })
    }

    return {
      root_causes: causes,
      severity: evaluation.mape > 30 ? "critical" : evaluation.mape > 15 ? "warning" : "info",
      suggestions: FALLBACK_SUGGESTIONS,
      patch_description: "基于误差分析进行参数调优和数据预处理优化",
      target_modules: ["模型参数部分", "数据预处理部分"],
    }
  }
}
