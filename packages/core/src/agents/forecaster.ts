/**
 * ForecasterAgent — 预测迭代智能体 (temperature: 0.6)
 *
 * 对应 CastClaw 的 Forecaster，负责：
 * - 执行预测代码
 * - 评估预测准确率
 * - 误差深度分析
 * - 生成并应用补丁
 * - 迭代循环优化
 *
 * Prompt 风格：探索型、实验性、高容错
 */

import { z } from "zod"
import { LLMClient } from "../llm/client"
import { ExecutionAgent } from "./execution"
import { EvaluationAgent } from "./evaluation"
import { AnalysisAgent } from "./analysis"
import { PatchAgent } from "./patch"
import { MODEL_POOL } from "./modelSelector"
import type { DBConfig, DataPoint, EvaluationMetrics } from "../types"

const FORECASTER_SYSTEM_PROMPT = `你是一位实验性的时序预测优化专家 (Forecaster)。

## 职责
1. 执行预测并评估效果
2. 分析误差根因，提出修复假设
3. 生成并应用代码补丁
4. 对比不同补丁的效果，决定是否回滚
5. 当优化停滞时，建议切换到其他模型

## 原则
- 大胆试验：多尝试不同的参数组合和模型变体
- 关键指标：准确率提升 > 0.5% 才算有效改进
- 回滚保护：任何导致准确率下降的修改立即回滚
- 连续 5 轮无提升时，考虑切换模型`

export interface ForecasterRunResult {
  bestCode: string
  bestAccuracy: number
  bestMetrics: EvaluationMetrics
  bestPredictions: DataPoint[]
  totalIterations: number
  modelIndex: number
  modelLabel: string
}

export class ForecasterAgent {
  name = "forecaster"
  private llm: LLMClient
  private executionAgent: ExecutionAgent
  private evaluationAgent: EvaluationAgent
  private analysisAgent: AnalysisAgent
  private patchAgent: PatchAgent

  constructor(llm: LLMClient, dbConfig: DBConfig) {
    // Forecaster 使用更高温度 (0.6) 鼓励探索
    this.llm = llm
    this.executionAgent = new ExecutionAgent(dbConfig)
    this.evaluationAgent = new EvaluationAgent()
    this.analysisAgent = new AnalysisAgent(llm, dbConfig)
    this.patchAgent = new PatchAgent(llm)
  }

  /** 主迭代循环：执行 → 评估 → 分析 → 补丁 → 重复 */
  async iterate(
    initialCode: string,
    initialPredictions: DataPoint[],
    actuals: DataPoint[],
    year: number,
    expectType: number,
    maxIterations: number = 10,
    accuracyTarget: number = 98,
    modelIndex: number = 0,
    onMessage?: (msg: string) => void,
  ): Promise<ForecasterRunResult> {
    const log = (msg: string) => { onMessage?.(msg); console.log(`[Forecaster] ${msg}`) }

    let currentCode = initialCode
    let currentPredictions = initialPredictions

    // 评估初始版本
    log("评估初始预测...")
    let metrics = await this.evaluationAgent.evaluate(currentPredictions, actuals)
    let bestCode = currentCode
    let bestPredictions = currentPredictions
    let bestMetrics = metrics
    let bestAccuracy = metrics.accuracy
    let roundsSinceBest = 0

    log(`初始准确率: ${bestAccuracy.toFixed(2)}%`)
    if (bestAccuracy >= accuracyTarget) {
      log(`目标达成: ${bestAccuracy.toFixed(2)}% >= ${accuracyTarget}%`)
      return this.buildResult(bestCode, bestAccuracy, bestMetrics, bestPredictions, 0, modelIndex)
    }

    // 迭代循环
    for (let i = 1; i <= maxIterations; i++) {
      if (roundsSinceBest >= 10) {
        log(`连续 10 轮无提升，停止`)
        break
      }

      log(`迭代 ${i}/${maxIterations} (最优 ${bestAccuracy.toFixed(2)}%, ${roundsSinceBest} 轮未提升)`)

      // 分析
      const analysis = await this.analysisAgent.analyze(bestMetrics, bestCode, year, expectType)

      // 补丁
      const patchResult = await this.patchAgent.generate(bestCode, analysis as any, i)
      currentCode = patchResult.patched_code
      if (currentCode === bestCode) {
        log(`补丁无实际改动，跳过`)
        roundsSinceBest++
        continue
      }

      // 语法检查 + 自动修复（优先 fallbackPatch 本地规则，毫秒级；搞不定才调 LLM）
      let syntaxCheck = await this.checkSyntax(currentCode)
      if (!syntaxCheck.ok) {
        log(`语法错误: ${syntaxCheck.error.slice(0, 150)}`)
        let fixed = false

        // 🆕 第一步：先尝试 fallbackPatch（本地规则替换，不调用 LLM，不会引入新错误）
        log(`  尝试规则补丁修复...`)
        const fallbackPatch = await this.patchAgent.generate(bestCode, analysis as any, i, "", true, "")
        if (fallbackPatch.patched_code !== bestCode) {
          const fallbackSyntax = await this.checkSyntax(fallbackPatch.patched_code)
          if (fallbackSyntax.ok) {
            currentCode = fallbackPatch.patched_code
            fixed = true
            log(`  ✅ 规则补丁修复成功（本地替换，无 LLM 调用）`)
          }
        }

        // 第二步：fallback 搞不定，才调 LLM（最多 2 次）
        if (!fixed) {
          for (let attempt = 1; attempt <= 2; attempt++) {
            log(`  LLM 自动修复第 ${attempt} 次...`)
            const retryPatch = await this.patchAgent.generate(
              bestCode, analysis as any, i, "", false, syntaxCheck.error.slice(0, 500),
            )
            if (retryPatch.patched_code !== bestCode) {
              const retrySyntax = await this.checkSyntax(retryPatch.patched_code)
              if (retrySyntax.ok) {
                currentCode = retryPatch.patched_code
                fixed = true
                log(`  ✅ LLM 自动修复成功`)
                break
              }
              syntaxCheck = retrySyntax
            } else {
              log(`  补丁未产生改动`)
              break
            }
          }
        }

        if (!fixed) {
          log(`  ❌ 语法错误无法修复，跳过`)
          roundsSinceBest++
          continue
        }
      }

      // 执行
      const execResult = await this.executionAgent.execute(currentCode, year, expectType, `iter_${i}`)
      if (!execResult.success || execResult.predictions.length === 0) {
        log(`执行失败: ${execResult.error || "空结果"}`)
        roundsSinceBest++
        continue
      }

      // 评估
      const newMetrics = await this.evaluationAgent.evaluate(execResult.predictions, actuals)
      const newAccuracy = newMetrics.accuracy

      if (newAccuracy > bestAccuracy + 0.5) {
        const improvement = newAccuracy - bestAccuracy
        bestCode = currentCode
        bestPredictions = execResult.predictions
        bestMetrics = newMetrics
        bestAccuracy = newAccuracy
        roundsSinceBest = 0
        log(`🎯 新最佳! ${newAccuracy.toFixed(2)}% (↑${improvement.toFixed(2)}%)`)

        if (newAccuracy >= accuracyTarget) {
          log(`目标达成: ${newAccuracy.toFixed(2)}% >= ${accuracyTarget}%`)
          break
        }
      } else {
        roundsSinceBest++
        log(`→ 无提升 (${newAccuracy.toFixed(2)}%), 最优 ${bestAccuracy.toFixed(2)}%`)
      }
    }

    return this.buildResult(bestCode, bestAccuracy, bestMetrics, bestPredictions, roundsSinceBest, modelIndex)
  }

  private async checkSyntax(code: string): Promise<{ ok: boolean; error: string }> {
    try {
      const { writeFileSync, unlinkSync } = require("fs")
      const { join } = require("path")
      const { tmpdir } = require("os")
      const { execSync } = require("child_process")
      const tmpFile = join(tmpdir(), `castflow_syntax_${Date.now()}.py`)
      writeFileSync(tmpFile, code, "utf-8")
      execSync(`python -m py_compile "${tmpFile}"`, {
        timeout: 10000, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      })
      unlinkSync(tmpFile)
      return { ok: true, error: "" }
    } catch (e: any) {
      const stderr = (e.stderr || e.message || "").toString()
      const lines = stderr.split("\n").filter((l: string) => l.trim())
      const errorSummary = lines.slice(-3).join("\n").slice(0, 500)
      return { ok: false, error: errorSummary }
    }
  }

  private buildResult(
    code: string, accuracy: number, metrics: EvaluationMetrics,
    predictions: DataPoint[], roundsSinceBest: number, modelIndex: number,
  ): ForecasterRunResult {
    return {
      bestCode: code,
      bestAccuracy: Number(accuracy.toFixed(2)),
      bestMetrics: metrics,
      bestPredictions: predictions,
      totalIterations: roundsSinceBest,
      modelIndex,
      modelLabel: MODEL_POOL[modelIndex]?.label || "?",
    }
  }
}
