/**
 * ModelSelector - 智能初始模型选择器
 *
 * 根据数据特征（季节强度、趋势方向、波动性、历史长度）打分，
 * 为当前任务选择最合适的初始模型，避免从 idx=0 顺序浪费迭代。
 */

// ================================================================
// 模型池：30+ 种预测模型，按复杂度/类型排列
// ================================================================
export const MODEL_POOL = [
  { id: "auto", label: "LLM 自动选择" },
  { id: "exp_smoothing", label: "Holt-Winters / ETS" },
  { id: "sarima", label: "SARIMA（自动定阶）" },
  { id: "prophet", label: "Facebook Prophet" },
  { id: "xgboost", label: "XGBoost（时间特征）" },
  { id: "lightgbm", label: "LightGBM（时间特征）" },
  { id: "ensemble", label: "集成模型（ETS+ARIMA+XGBoost）" },
  { id: "linear_regression", label: "线性回归（季节+趋势虚拟变量）" },
  { id: "stl_ets", label: "STL 分解 + ETS" },
  { id: "naive_seasonal", label: "Naive 季节性 + 增长率" },
  { id: "moving_average", label: "移动平均 + 季节性调整" },
  { id: "theta", label: "Theta / SES + 飘移" },
  { id: "garch", label: "GARCH（波动率建模）" },
  { id: "hw_mul", label: "Holt-Winters 乘性季节" },
  { id: "hw_damp", label: "Holt-Winters 阻尼趋势" },
  { id: "croston", label: "Croston 间歇需求" },
  { id: "random_forest", label: "随机森林（滞后特征）" },
  { id: "catboost", label: "CatBoost（时序特征）" },
  { id: "mlp", label: "MLP 神经网络（sklearn）" },
  { id: "bats", label: "TBATS（多季节）" },
  { id: "arima_xgb", label: "ARIMA + XGBoost 混合" },
  { id: "ets_xgb", label: "ETS + XGBoost 混合" },
  { id: "weighted_ensemble", label: "加权集成（自动寻优权重）" },
  { id: "lgb_ets_arima", label: "三模型投票集成" },
  { id: "seasonal_decompose", label: "确定性分解 + ARIMA" },
  { id: "holt_linear", label: "Holt 线性趋势" },
  { id: "ses", label: "简单指数平滑" },
  { id: "arima111", label: "ARIMA(1,1,1) 基线" },
  { id: "arima_seasonal", label: "SARIMA(1,1,1)(1,1,1,12)" },
  { id: "winters_damped", label: "阻尼 Holt-Winters" },
  { id: "dynamic_regression", label: "动态回归（外部变量）" },
]

// ================================================================
// 模型评分定义 — 每个模型在各维度的能力评分
// ================================================================

interface ModelProfile {
  id: string
  /** 捕捉强季节性的能力 0-10 */
  seasonality: number
  /** 捕捉趋势的能力 0-10 */
  trend: number
  /** 对数据量大小的要求（值越大越适合小样本）0-10 */
  smallSample: number
  /** 对波动性/复杂度的适应能力 0-10 */
  volatility: number
  /** 对新数据的快速适应能力 0-10 */
  adaptability: number
  /** 预测稳定性（方差小）0-10 */
  stability: number
  /** 特别适合区民用电（expectType=1）0-5 */
  fitResidential: number
  /** 特别适合煤改电（expectType=2）0-5 */
  fitHeating: number
}

const MODEL_PROFILES: ModelProfile[] = [
  // { id, seasonality, trend, smallSample, volatility, adaptability, stability, fitResidential, fitHeating }
  { id: "auto",              seasonality: 5, trend: 5, smallSample: 5, volatility: 5, adaptability: 5, stability: 5, fitResidential: 0, fitHeating: 0 },
  { id: "exp_smoothing",     seasonality: 8, trend: 7, smallSample: 7, volatility: 5, adaptability: 5, stability: 7, fitResidential: 5, fitHeating: 4 },
  { id: "sarima",            seasonality: 8, trend: 6, smallSample: 4, volatility: 6, adaptability: 4, stability: 6, fitResidential: 4, fitHeating: 3 },
  { id: "prophet",           seasonality: 9, trend: 8, smallSample: 6, volatility: 7, adaptability: 6, stability: 7, fitResidential: 5, fitHeating: 5 },
  { id: "xgboost",           seasonality: 5, trend: 5, smallSample: 3, volatility: 8, adaptability: 8, stability: 4, fitResidential: 3, fitHeating: 3 },
  { id: "lightgbm",          seasonality: 5, trend: 5, smallSample: 3, volatility: 8, adaptability: 8, stability: 4, fitResidential: 3, fitHeating: 3 },
  { id: "ensemble",          seasonality: 8, trend: 7, smallSample: 4, volatility: 7, adaptability: 6, stability: 7, fitResidential: 4, fitHeating: 4 },
  { id: "linear_regression", seasonality: 4, trend: 6, smallSample: 6, volatility: 4, adaptability: 3, stability: 8, fitResidential: 2, fitHeating: 2 },
  { id: "stl_ets",           seasonality: 9, trend: 7, smallSample: 5, volatility: 6, adaptability: 5, stability: 7, fitResidential: 5, fitHeating: 4 },
  { id: "naive_seasonal",    seasonality: 7, trend: 2, smallSample: 9, volatility: 3, adaptability: 2, stability: 8, fitResidential: 3, fitHeating: 2 },
  { id: "moving_average",    seasonality: 5, trend: 3, smallSample: 8, volatility: 3, adaptability: 4, stability: 8, fitResidential: 2, fitHeating: 2 },
  { id: "theta",             seasonality: 5, trend: 5, smallSample: 7, volatility: 4, adaptability: 4, stability: 7, fitResidential: 3, fitHeating: 3 },
  { id: "garch",             seasonality: 2, trend: 1, smallSample: 5, volatility: 10, adaptability: 6, stability: 3, fitResidential: 1, fitHeating: 1 },
  { id: "hw_mul",            seasonality: 9, trend: 6, smallSample: 6, volatility: 7, adaptability: 5, stability: 5, fitResidential: 4, fitHeating: 3 },
  { id: "hw_damp",           seasonality: 8, trend: 7, smallSample: 6, volatility: 6, adaptability: 5, stability: 6, fitResidential: 4, fitHeating: 4 },
  { id: "croston",           seasonality: 3, trend: 2, smallSample: 7, volatility: 5, adaptability: 3, stability: 6, fitResidential: 1, fitHeating: 1 },
  { id: "random_forest",     seasonality: 4, trend: 5, smallSample: 3, volatility: 7, adaptability: 7, stability: 5, fitResidential: 2, fitHeating: 3 },
  { id: "catboost",          seasonality: 4, trend: 5, smallSample: 3, volatility: 7, adaptability: 7, stability: 5, fitResidential: 2, fitHeating: 3 },
  { id: "mlp",               seasonality: 5, trend: 5, smallSample: 2, volatility: 8, adaptability: 8, stability: 3, fitResidential: 2, fitHeating: 2 },
  { id: "bats",              seasonality: 9, trend: 6, smallSample: 4, volatility: 6, adaptability: 4, stability: 6, fitResidential: 4, fitHeating: 3 },
  { id: "arima_xgb",         seasonality: 7, trend: 6, smallSample: 3, volatility: 7, adaptability: 7, stability: 6, fitResidential: 4, fitHeating: 3 },
  { id: "ets_xgb",           seasonality: 8, trend: 7, smallSample: 3, volatility: 7, adaptability: 7, stability: 6, fitResidential: 4, fitHeating: 4 },
  { id: "weighted_ensemble", seasonality: 7, trend: 7, smallSample: 4, volatility: 7, adaptability: 6, stability: 7, fitResidential: 4, fitHeating: 4 },
  { id: "lgb_ets_arima",     seasonality: 8, trend: 7, smallSample: 3, volatility: 7, adaptability: 6, stability: 7, fitResidential: 4, fitHeating: 4 },
  { id: "seasonal_decompose",seasonality: 8, trend: 6, smallSample: 5, volatility: 5, adaptability: 4, stability: 7, fitResidential: 4, fitHeating: 3 },
  { id: "holt_linear",       seasonality: 3, trend: 8, smallSample: 7, volatility: 5, adaptability: 6, stability: 7, fitResidential: 2, fitHeating: 4 },
  { id: "ses",               seasonality: 2, trend: 3, smallSample: 9, volatility: 3, adaptability: 3, stability: 8, fitResidential: 1, fitHeating: 2 },
  { id: "arima111",          seasonality: 4, trend: 4, smallSample: 8, volatility: 4, adaptability: 3, stability: 7, fitResidential: 2, fitHeating: 2 },
  { id: "arima_seasonal",    seasonality: 8, trend: 5, smallSample: 4, volatility: 5, adaptability: 3, stability: 6, fitResidential: 4, fitHeating: 3 },
  { id: "winters_damped",    seasonality: 8, trend: 7, smallSample: 6, volatility: 6, adaptability: 5, stability: 6, fitResidential: 4, fitHeating: 4 },
  { id: "dynamic_regression",seasonality: 5, trend: 6, smallSample: 4, volatility: 6, adaptability: 5, stability: 6, fitResidential: 3, fitHeating: 3 },
]

// ================================================================
// 数据特征接口
// ================================================================

export interface DataCharacteristics {
  /** 是否有明显季节性 */
  hasSeasonality: boolean
  /** 季节强度 0-1 */
  seasonalityStrength: number
  /** 趋势方向：up / down / flat */
  trendDirection: "up" | "down" | "flat"
  /** 趋势强度 0-1 */
  trendStrength: number
  /** 数据波动性 0-1 */
  volatility: number
  /** 区县数量 */
  orgCount: number
  /** 历史年数 */
  yearsCount: number
  /** 用电类型 */
  expectType: number
  /** 月均峰值比值（最大值/平均值）—— 判断季节振幅 */
  peakRatio: number
  /** 同比增长平均值——判断趋势 */
  yoyGrowthAvg: number
}

// ================================================================
// 选择器主逻辑
// ================================================================

/**
 * 根据数据特征从 MODEL_POOL 中选出最适合的初始模型
 * @returns 模型在 MODEL_POOL 中的索引
 */
export function selectInitialModel(chars: DataCharacteristics): { index: number; label: string; reason: string } {
  const scores: { index: number; score: number; label: string }[] = []

  for (let i = 0; i < MODEL_POOL.length; i++) {
    const model = MODEL_POOL[i]
    const profile = MODEL_PROFILES.find(m => m.id === model.id)
    if (!profile) continue

    let score = 0

    // 基础能力维度
    score += profile.seasonality * chars.seasonalityStrength * 1.5
    score += profile.trend * chars.trendStrength * 1.2
    score += profile.volatility * chars.volatility * 1.0
    score += profile.stability * (1 - chars.volatility) * 0.8

    // 小样本偏好（历史数据少则优先小样本友好的模型）
    const sampleFactor = Math.max(0, 1 - chars.yearsCount / 10)
    score += profile.smallSample * sampleFactor * 1.0

    // 任务类型偏好
    if (chars.expectType === 1) {
      score += profile.fitResidential * 3.0
    } else {
      score += profile.fitHeating * 3.0
    }

    // 季节性强 + 峰值高 → 推荐乘性季节模型
    if (chars.seasonalityStrength > 0.6 && chars.peakRatio > 1.5) {
      if (model.id === "hw_mul" || model.id === "prophet" || model.id === "stl_ets") {
        score += 5.0
      }
    }

    // 强趋势 → 优先阻尼/Holt线性
    if (chars.trendStrength > 0.5) {
      if (model.id === "holt_linear" || model.id === "hw_damp" || model.id === "winters_damped") {
        score += 3.0
      }
    }

    // 波动性极大 → 优先 GARCH/树模型
    if (chars.volatility > 0.6) {
      if (model.id === "garch" || model.id === "xgboost" || model.id === "lightgbm" || model.id === "random_forest") {
        score += 3.0
      }
    }

    scores.push({ index: i, score, label: model.label })
  }

  // 按得分降序排列
  scores.sort((a, b) => b.score - a.score)

  const best = scores[0]

  // 记录 top-3 供参考
  const top3 = scores.slice(0, 3).map(s => `#${s.index + 1} ${s.label} (${s.score.toFixed(0)}分)`)

  return {
    index: best.index,
    label: best.label,
    reason: top3.join(" → "),
  }
}

/**
 * 从 getCharacteristics() 返回的原始特征数据中提取聚合特征
 */
export function extractCharacteristics(
  rawChars: Record<string, any>,
  expectType: number,
): DataCharacteristics {
  const orgs = Object.keys(rawChars)
  if (!orgs.length) {
    // 无数据时的默认值
    return {
      hasSeasonality: false,
      seasonalityStrength: 0.3,
      trendDirection: "flat",
      trendStrength: 0.3,
      volatility: 0.3,
      orgCount: 0,
      yearsCount: 3,
      expectType,
      peakRatio: 1.2,
      yoyGrowthAvg: 0,
    }
  }

  let totalSeasonality = 0
  let totalTrend = 0
  let totalVolatility = 0
  let maxPeakRatio = 0
  let totalYoyGrowth = 0
  let yearsCount = 0
  let seasonalCount = 0

  for (const org of orgs) {
    const c = rawChars[org]
    if (!c?.monthlyPattern) continue

    const values = Object.values(c.monthlyPattern) as number[]
    if (values.length < 2) continue

    // 季节强度 = 月均值的变异系数
    const mean = values.reduce((a: number, b: number) => a + b, 0) / values.length
    const variance = values.reduce((a: number, b: number) => a + (b - mean) ** 2, 0) / values.length
    const cv = Math.sqrt(variance) / (mean || 1)
    totalSeasonality += Math.min(cv, 1)

    if (cv > 0.15) seasonalCount++

    // 峰值比 = 最高月 / 均值
    const peak = Math.max(...values)
    maxPeakRatio = Math.max(maxPeakRatio, peak / (mean || 1))

    // 趋势强度：对比不同年份同月数据
    if (c.years && c.years.length >= 2 && c.totalRecords > 12) {
      const sortedYears = [...c.years].sort((a: number, b: number) => a - b)
      const firstYear = sortedYears[0]
      const lastYear = sortedYears[sortedYears.length - 1]
      yearsCount = sortedYears.length

      // 使用最大值作为趋势代理
      if (c.max && c.min && firstYear !== lastYear) {
        const growth = (c.max - c.min) / (c.min || 1)
        totalTrend += Math.min(Math.abs(growth), 1)
        totalYoyGrowth += growth
      }
    }

    // 波动性
    if (c.min && c.max) {
      const rangeRatio = (c.max - c.min) / (c.min || 1)
      totalVolatility += Math.min(rangeRatio, 1)
    }
  }

  const n = orgs.length || 1

  return {
    hasSeasonality: seasonalCount > orgs.length / 2,
    seasonalityStrength: Math.min(totalSeasonality / n, 1),
    trendDirection: totalYoyGrowth > 0 ? "up" : totalYoyGrowth < 0 ? "down" : "flat",
    trendStrength: Math.min(Math.abs(totalTrend) / n, 1),
    volatility: Math.min(totalVolatility / n, 1),
    orgCount: orgs.length,
    yearsCount: yearsCount || 3,
    expectType,
    peakRatio: maxPeakRatio || 1.2,
    yoyGrowthAvg: totalYoyGrowth / n,
  }
}

/**
 * 获取模型在池中的显示标签
 */
export function getModelLabel(index: number): string {
  return `${MODEL_POOL[index]?.label || "?"} (${index + 1}/${MODEL_POOL.length})`
}

export { MODEL_PROFILES }
