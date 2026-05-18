/**
 * DataAgent - 数据获取与监控智能体
 */

import { DBReader } from "../db/mysql"
import type { DBConfig, DataPoint } from "../types"

export interface DataContext {
  action: "load_history" | "check_data" | "get_characteristics" | "load_actuals" | "wait_for_data" | "sanity_check"
  year?: number
  expectType?: number
}

export interface SanityCheckResult {
  passed: boolean
  warnings: string[]
  errors: string[]
  stats: {
    orgCount: number
    expectedOrgs: number
    totalMonths: number
    zeroValueOrgs: string[]
    shortHistoryOrgs: string[]
  }
}

export class DataAgent {
  name = "data_agent"
  private reader: DBReader
  // 铜川五区县（数据库实际名称）
  private readonly EXPECTED_ORGS = ["新区", "耀州", "印王", "宜君", "客服"]

  constructor(dbConfig: DBConfig) {
    this.reader = new DBReader(dbConfig)
  }

  async run(ctx: DataContext): Promise<any> {
    const year = ctx.year ?? 2026
    const type = ctx.expectType ?? 1

    switch (ctx.action) {
      case "load_history":
        return this.reader.loadHistory(type)
      case "check_data":
        return this.reader.checkDataAvailable(year, type)
      case "get_characteristics":
        return this.reader.getCharacteristics(type)
      case "load_actuals":
        return this.reader.loadActuals(year, type)
      case "wait_for_data":
        return this.waitForData(year, type)
      case "sanity_check":
        return this.checkDataSanity(type)
      default:
        throw new Error(`未知操作: ${ctx.action}`)
    }
  }

  /**
   * 数据完整性检查
   * - 检查 5 区县是否齐全
   * - 检查是否有零值
   * - 检查历史数据是否足够（至少 12 个月）
   */
  async checkDataSanity(expectType: number = 1): Promise<SanityCheckResult> {
    const result: SanityCheckResult = {
      passed: true,
      warnings: [],
      errors: [],
      stats: {
        orgCount: 0,
        expectedOrgs: this.EXPECTED_ORGS.length,
        totalMonths: 0,
        zeroValueOrgs: [],
        shortHistoryOrgs: [],
      },
    }

    try {
      const chars = await this.reader.getCharacteristics(expectType)
      const orgs = Object.keys(chars)
      result.stats.orgCount = orgs.length

      // 检查区县是否齐全
      const missingOrgs = this.EXPECTED_ORGS.filter(o => !orgs.includes(o))
      if (missingOrgs.length > 0) {
        result.errors.push(`缺少区县: ${missingOrgs.join(", ")}`)
        result.passed = false
      }

      // 检查每个区县的数据
      for (const org of orgs) {
        const c = chars[org]
        if (!c) continue

        // 检查零值
        if (c.min === 0) {
          result.warnings.push(`${org}: 存在零值数据`)
          result.stats.zeroValueOrgs.push(org)
        }

        // 检查历史长度
        const monthCount = Object.keys(c.monthlyPattern || {}).length
        result.stats.totalMonths += monthCount
        if (monthCount < 12) {
          result.warnings.push(`${org}: 仅 ${monthCount} 个月数据（少于 12 个月）`)
          result.stats.shortHistoryOrgs.push(org)
        }
      }

      // 综合判断
      if (result.errors.length > 0) {
        result.passed = false
      }

      return result
    } catch (e: any) {
      result.errors.push(`数据检查失败: ${e.message}`)
      result.passed = false
      return result
    }
  }

  private async waitForData(year: number, expectType: number, maxRetries = 720, intervalMs = 60_000): Promise<{
    status: string
    actuals?: DataPoint[]
    pollAttempts?: number
  }> {
    for (let i = 1; i <= maxRetries; i++) {
      const { available, count } = await this.reader.checkDataAvailable(year, expectType)
      if (available) {
        const actuals = await this.reader.loadActuals(year, expectType)
        return { status: "data_arrived", actuals, pollAttempts: i }
      }
      if (i % 60 === 0) console.log(`[DataAgent] 等待真实数据... 已轮询 ${i} 次`)
      await new Promise(r => setTimeout(r, intervalMs))
    }
    return { status: "timeout" }
  }
}
