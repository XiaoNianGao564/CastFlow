/**
 * CastFlow 核心类型定义
 * 用 Zod 定义所有数据结构
 */

import { z } from "zod"

// ============================================================
// 数据库配置（与 power_forecast_platform 的 sxfhyc11 一致）
// ============================================================
export const DBConfigSchema = z.object({
  host: z.string().default("127.0.0.1"),
  port: z.number().default(3306),
  user: z.string().default("root"),
  password: z.string().default("root"),
  database: z.string().default("sxfhyc11"),
})
export type DBConfig = z.infer<typeof DBConfigSchema>

// ============================================================
// 预测数据点
// ============================================================
export const DataPointSchema = z.object({
  org: z.string(),
  month: z.string(),       // "2026-01"
  value: z.number(),
  type: z.string().optional(), // "区民" | "煤改电"
})
export type DataPoint = z.infer<typeof DataPointSchema>

// ============================================================
// 评估指标
// ============================================================
export const EvaluationMetricsSchema = z.object({
  mape: z.number(),
  mae: z.number(),
  rmse: z.number(),
  mase: z.number().optional(),
  accuracy: z.number(),
  sample_count: z.number(),
  eval_error: z.boolean().optional(),
  error: z.string().optional(),
  bias: z.enum(["overestimate", "underestimate", "balanced"]).optional(),
  level: z.enum(["优秀", "良好", "一般", "较差"]).optional(),
  per_org: z.record(z.object({
    mape: z.number(),
    accuracy: z.number(),
    count: z.number(),
  })).optional(),
  pairs: z.array(z.object({
    org: z.string(),
    month: z.string(),
    predicted: z.number(),
    actual: z.number(),
    error: z.number(),
    error_pct: z.number(),
  })).optional(),
})
export type EvaluationMetrics = z.infer<typeof EvaluationMetricsSchema>

// ============================================================
// LLM 分析结果
// ============================================================
export const AnalysisResultSchema = z.object({
  root_causes: z.array(z.object({
    factor: z.string(),
    category: z.string(),
    impact: z.enum(["high", "medium", "low"]),
    detail: z.string(),
  })),
  severity: z.enum(["critical", "warning", "info"]),
  suggestions: z.array(z.object({
    type: z.string(),
    priority: z.number(),
    description: z.string(),
    expected_improvement: z.string(),
  })),
  patch_description: z.string(),
  target_modules: z.array(z.string()),
})
export type AnalysisResult = z.infer<typeof AnalysisResultSchema>

// ============================================================
// 代码补丁
// ============================================================
export const PatchResultSchema = z.object({
  patched_code: z.string(),
  diff: z.string(),
  patch_summary: z.string(),
  changed_sections: z.array(z.object({
    section: z.string(),
    old: z.string(),
    new: z.string(),
    reason: z.string(),
  })),
  version: z.number(),
})
export type PatchResult = z.infer<typeof PatchResultSchema>

// ============================================================
// 迭代记录
// ============================================================
export const IterationRecordSchema = z.object({
  version: z.number(),
  code: z.string(),
  code_path: z.string().optional(),
  predictions: z.array(DataPointSchema).optional(),
  evaluation: EvaluationMetricsSchema.optional(),
  accuracy: z.number().optional(),
  analysis: AnalysisResultSchema.optional(),
  patch: PatchResultSchema.optional(),
  execution: z.object({
    elapsed: z.number().optional(),
    success: z.boolean().optional(),
    error: z.string().optional(),
  }).optional(),
  improved: z.boolean().optional(),
  source: z.string().optional(),
  timestamp: z.string().optional(),
})
export type IterationRecord = z.infer<typeof IterationRecordSchema>

// ============================================================
// 系统状态
// ============================================================
export const SystemStatusSchema = z.object({
  status: z.enum(["idle", "running", "generating", "executing", "waiting_data", "evaluating", "analyzing", "patching", "completed", "failed", "stopped"]),
  current_version: z.number(),
  best_accuracy: z.number(),
  total_iterations: z.number(),
  year: z.number().optional(),
  expect_type: z.number().optional(),
  message: z.string().optional(),
})
export type SystemStatus = z.infer<typeof SystemStatusSchema>

// ============================================================
// 准确率时间点
// ============================================================
export interface AccuracyPoint {
  version: number
  accuracy: number
  improved: boolean
  timestamp: string
}

// ============================================================
// 系统配置
// ============================================================
export interface SystemConfig {
  db: DBConfig
  llm: { apiKey: string; baseURL: string; model: string }
  optimizer: {
    maxIterations: number
    accuracyTarget: number
    predictSteps: number
    minHistory: number
    dataPollInterval: number
    dataPollMaxRetries: number
  }
  defaults: { year: number; expectType: number }
}

// ============================================================
// 预测总结报告（闭环完成后自动生成，结构化呈现）
// ============================================================
export interface ForecastReport {
  title: string
  generatedAt: string
  taskInfo: {
    year: number
    expectType: string
    totalIterations: number
    elapsed: string
    initialModel: string
    bestModel: string
    iterationsCompleted: number
  }
  bestResult: {
    accuracy: number
    mape: number
    mae: number
    rmse: number
    bias: string
    level: string
  }
  perOrgPerformance: Array<{
    org: string
    accuracy: number
    mape: number
    status: "优秀" | "良好" | "待优化"
  }>
  dataCharacteristics: {
    seasonalityStrength: number
    trendDirection: string
    trendStrength: number
    volatility: number
    orgCount: number
    yearsCount: number
    description: string
  }
  accuracyTimeline: AccuracyPoint[]
  iterationSummary: Array<{
    version: number
    accuracy: number | null
    improvement: string
    patchSummary: string
    status: "initial" | "improved" | "no_change" | "regressed" | "failed"
  }>
  recommendations: string[]
  strategyHistory: string
  insights: {
    seasonalityImpact: string
    trendAnalysis: string
    volatilityAnalysis: string
    convergenceAnalysis: string
    bestModelReason: string
  }
}

// ============================================================
// 系统状态更新消息（用于 SSE/WebSocket 推送）
// ============================================================
export interface StatusUpdate {
  status: string
  message: string
  version?: number
  accuracy?: number
  iteration?: number
  maxIterations?: number
  elapsed?: number
  currentModel?: string
  timestamp: string
}
