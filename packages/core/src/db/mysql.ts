/**
 * MySQL 数据库读取器
 * 通过 Python 子进程安全执行 SQL 查询
 */

import { execSync } from "child_process"
import { writeFileSync, unlinkSync, mkdtempSync } from "fs"
import { join } from "path"
import { tmpdir } from "os"
import { type DBConfig, type DataPoint } from "../types"

export class DBReader {
  private config: DBConfig

  constructor(config: DBConfig) {
    this.config = config
  }

  async loadHistory(expectType: number = 1) {
    const [orgs, years, total] = await Promise.all([
      this.getOrgList(expectType),
      this.getDataYears(expectType),
      this.queryOne(`SELECT COUNT(*) as cnt FROM tb_dlyc_area_qx_month WHERE expect_type = ${expectType}`),
    ])
    return { orgs, years, totalRecords: Number(total?.cnt || 0) }
  }

  async loadActuals(year: number, expectType: number = 1): Promise<DataPoint[]> {
    const rows = await this.query(`
      SELECT org_name, power_year, power_month, power_num
      FROM tb_dlyc_area_qx_month WHERE power_year = ${year} AND expect_type = ${expectType}
      ORDER BY org_name, power_month
    `)
    return rows.map((r: any) => ({
      org: r.org_name,
      month: `${r.power_year}-${String(r.power_month).padStart(2, "0")}`,
      value: Number(r.power_num),
    }))
  }

  async checkDataAvailable(year: number, expectType: number = 1) {
    const row = await this.queryOne(
      `SELECT COUNT(*) as cnt FROM tb_dlyc_area_qx_month WHERE power_year = ${year} AND expect_type = ${expectType}`
    )
    const count = Number(row?.cnt || 0)
    return { available: count > 0, count }
  }

  async getOrgList(expectType: number = 1): Promise<string[]> {
    const rows = await this.query(
      `SELECT DISTINCT org_name FROM tb_dlyc_area_qx_month WHERE expect_type = ${expectType} ORDER BY org_name`
    )
    return rows.map((r: any) => r.org_name)
  }

  async getDataYears(expectType: number = 1): Promise<number[]> {
    const rows = await this.query(
      `SELECT DISTINCT power_year FROM tb_dlyc_area_qx_month WHERE expect_type = ${expectType} ORDER BY power_year`
    )
    return rows.map((r: any) => r.power_year)
  }

  async getCharacteristics(expectType: number = 1): Promise<Record<string, any>> {
    const orgs = await this.getOrgList(expectType)
    const chars: Record<string, any> = {}

    for (const org of orgs) {
      const rows = await this.query(`
        SELECT power_year, power_month, power_num FROM tb_dlyc_area_qx_month
        WHERE org_name = '${org.replace(/'/g, "''")}' AND expect_type = ${expectType}
        ORDER BY power_year, power_month
      `)
      if (!rows.length) continue

      const years = [...new Set(rows.map((r: any) => r.power_year))].sort() as number[]
      const values = rows.map((r: any) => Number(r.power_num))

      const monthlyMap: Record<number, number[]> = {}
      for (const r of rows) {
        if (!monthlyMap[r.power_month]) monthlyMap[r.power_month] = []
        monthlyMap[r.power_month].push(Number(r.power_num))
      }
      const monthlyPattern: Record<string, number> = {}
      for (const [m, vals] of Object.entries(monthlyMap)) {
        monthlyPattern[m] = Number((vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2))
      }

      chars[org] = {
        years,
        totalRecords: rows.length,
        monthlyPattern,
        min: Math.min(...values),
        max: Math.max(...values),
        avg: Number((values.reduce((a, b) => a + b, 0) / values.length).toFixed(2)),
      }
    }
    return chars
  }

  // ================================================================
  // 私有：通过 Python 子进程执行 SQL
  // ================================================================

  private async query(sql: string): Promise<any[]> {
    const script = this.buildScript(sql)
    const out = this.runPython(script)
    return JSON.parse(out.trim())
  }

  private async queryOne(sql: string): Promise<any> {
    const rows = await this.query(sql)
    return rows[0] || null
  }

  private buildScript(sql: string): string {
    return `
import json, pymysql
conn = pymysql.connect(
    host="${this.config.host}",
    port=${this.config.port},
    user="${this.config.user}",
    password="${this.config.password}",
    database="${this.config.database}",
    charset="utf8",
    cursorclass=pymysql.cursors.DictCursor,
)
try:
    with conn.cursor() as cur:
        cur.execute("""${sql.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/`/g, '\\`')}""")
        rows = cur.fetchall()
    print(json.dumps(rows, ensure_ascii=False, default=str))
finally:
    conn.close()
`.trim()
  }

  private runPython(script: string): string {
    // 写入临时文件避免 shell 转义问题
    const tmpDir = mkdtempSync(join(tmpdir(), "castflow-"))
    const tmpFile = join(tmpDir, "query.py")
    writeFileSync(tmpFile, script, "utf-8")

    const execOpts = {
      encoding: "utf-8" as const,
      timeout: 30000,
      windowsHide: true,
      maxBuffer: 50 * 1024 * 1024, // 50MB，避免大查询截断
      stdio: ["ignore", "pipe", "pipe"] as const, // 显式不继承 stdin（修复 SSE 共存导致的 spawnSync ETIMEDOUT）
      env: { ...process.env, PYTHONIOENCODING: "utf-8" }, // 修复 GBK 编码损坏
    }

    try {
      // ETIMEDOUT 时自动重试（Bun + SSE 共存时偶发性问题）
      const maxRetries = 5
      for (let attempt = 0; attempt <= maxRetries; attempt++) {
        try {
          return execSync(`python "${tmpFile}"`, execOpts).trim()
        } catch (e: any) {
          if (e.code === "ETIMEDOUT" && attempt < maxRetries) {
            // 短暂等待约 500ms 后重试
            try { execSync(`ping -n 1 127.0.0.1 >nul`, { windowsHide: true, stdio: ["ignore", "pipe", "pipe"], timeout: 2000 }) } catch {}
            continue
          }
          // 最后一次失败或非 ETIMEDOUT 错误 → 抛出详细错误
          const stderr = e.stderr ? e.stderr.toString().slice(0, 500) : ""
          const stdout = e.stdout ? e.stdout.toString().slice(0, 200) : ""
          const detail = `[runPython](try=${attempt + 1}) code=${e.code} status=${e.status} signal=${e.signal} stderr=${stderr} stdout=${stdout}`
          throw new Error(`Python 执行失败: ${e.message} | ${detail}`)
        }
      }
      throw new Error("runPython 异常退出")
    } finally {
      try { unlinkSync(tmpFile); unlinkSync(tmpDir) } catch {}
    }
  }
}
