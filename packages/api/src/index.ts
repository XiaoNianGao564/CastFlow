/**
 * CastFlow API 服务器 - Hono
 *
 * 借鉴 power_forecast_platform 的启动前杀旧进程技巧。
 */

import { Hono } from "hono"
import { cors } from "hono/cors"
import { stream } from "hono/streaming"
import { join } from "path"
import { execSync } from "child_process"
import { CoordinatorAgent, type SystemConfig } from "@castflow/core"
import type { StatusUpdate } from "@castflow/core"

// ================================================================
// 启动前杀旧进程（借鉴 power_forecast_platform 的核心技巧）
// ================================================================
const PORT = Number(process.env.PORT) || 3001

function killOldProcess() {
  try {
    const out = execSync(
      `netstat -ano | findstr /c:":${PORT} " | findstr LISTENING`,
      { encoding: "utf-8", timeout: 5000, windowsHide: true }
    )
    const currentPid = process.pid
    let killed = false
    for (const line of out.trim().split("\n")) {
      const pid = parseInt(line.trim().split(/\s+/).pop() || "0", 10)
      if (pid > 0 && pid !== currentPid) {
        try {
          execSync(`taskkill /F /PID ${pid}`, { windowsHide: true })
          console.log(`  ✅ 已清理旧进程 PID: ${pid}`)
          killed = true
        } catch {} // 进程已死，忽略
      }
    }
    // 等端口真正释放（最多 3 秒）
    if (killed) {
      for (let i = 0; i < 15; i++) {
        try {
          execSync(`netstat -ano | findstr /c:":${PORT} " | findstr LISTENING`, {
            encoding: "utf-8", timeout: 2000, windowsHide: true
          })
          // 还有输出 = 端口还在占用
        } catch {
          return // 端口已释放
        }
        execSync(`ping -n 1 127.0.0.1 >nul`, { windowsHide: true }) // ~0.5s delay
      }
    }
  } catch {}
}

// ================================================================
// 配置
// ================================================================
const DB_CONFIG = {
  host: process.env.MYSQL_HOST || "127.0.0.1",
  port: Number(process.env.MYSQL_PORT) || 3306,
  user: process.env.MYSQL_USER || "root",
  password: process.env.MYSQL_PASSWORD || "root",
  database: process.env.MYSQL_DATABASE || "sxfhyc11",
}

// 通义千问 API Key 自动读取（与 power_forecast_platform 共用）
import { readFileSync, existsSync } from "fs"
function getApiKey(): string {
  const envKey = process.env.DASHSCOPE_API_KEY || process.env.LLM_API_KEY || ""
  if (envKey) return envKey
  // 尝试从当前目录的 .api_key 读取
  const localKey = join(process.cwd(), ".api_key")
  if (existsSync(localKey)) return readFileSync(localKey, "utf-8").trim()
  // 尝试从 power_forecast_platform 的 .api_key 读取
  const pfKey = "C:\\Users\\17166\\Desktop\\power_forecast_platform\\.api_key"
  if (existsSync(pfKey)) return readFileSync(pfKey, "utf-8").trim()
  return ""
}

const SYSTEM_CONFIG: SystemConfig = {
  db: DB_CONFIG,
  llm: {
    apiKey: getApiKey(),
    baseURL: process.env.LLM_API_BASE || "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: process.env.LLM_MODEL || "qwen-plus",
  },
  optimizer: {
    maxIterations: 10,
    accuracyTarget: 98,
    predictSteps: 12,
    minHistory: 12,
    dataPollInterval: 60000,
    dataPollMaxRetries: 720,
  },
  defaults: { year: 2026, expectType: 1 },
}

// ================================================================
// App
// ================================================================
const app = new Hono()
app.use("*", cors())

// SSE 连接管理
const sseClients = new Set<(update: StatusUpdate) => void>()

// 日志内存缓冲区：最多保留 200 条，用于 HTTP 轮询兜底
const logBuffer: string[] = []

function broadcast(update: StatusUpdate) {
  // 写入日志缓冲区（SSE 降级兜底）
  if (update.message) {
    const entry = `[${new Date().toLocaleTimeString()}] ${update.message}`
    logBuffer.push(entry)
    if (logBuffer.length > 200) logBuffer.splice(0, logBuffer.length - 200)
  }

  // 复制一份避免迭代时删除导致漏发
  for (const send of [...sseClients]) {
    try {
      send(update)
    } catch (err) {
      // 该客户端已断开，移除
      sseClients.delete(send)
    }
  }
}

// Coordinator 实例（单例）
let coordinator: CoordinatorAgent | null = null

function getCoordinator(): CoordinatorAgent {
  if (!coordinator) {
    coordinator = new CoordinatorAgent(SYSTEM_CONFIG, broadcast)
  }
  return coordinator
}

// ================================================================
// SSE 端点 - 状态实时推送
// ================================================================
app.get("/api/events", (c) => {
  return stream(c, async (stream) => {
    let alive = true
    const send = (update: StatusUpdate) => {
      if (!alive || stream.aborted || stream.closed) {
        throw new Error("client disconnected")
      }
      stream.write(`data: ${JSON.stringify(update)}\n\n`)
    }
    sseClients.add(send)

    // 客户端断开监听 —— 关键修复：之前没有这一步，导致 sseClients 越积越多
    stream.onAbort(() => {
      alive = false
      sseClients.delete(send)
    })

    try {
      // 发送初始状态（含 retry 指令，浏览器等待 5 秒再重连）
      await stream.write("retry: 5000\n")
      send({ status: "ready", message: "已连接", timestamp: new Date().toISOString() })

      // 保持连接（5 秒间隔，Vite proxy 超时通常为 60 秒，足够）
      while (alive && !stream.aborted) {
        await stream.write(": keepalive\n\n")
        await new Promise(r => setTimeout(r, 5000))
      }
    } catch {
      // 连接断开 - 退出循环
    } finally {
      alive = false
      sseClients.delete(send)
    }
  })
})

// ================================================================
// API 端点
// ================================================================

// 启动完整闭环（支持启动前参数覆盖）
app.post("/api/forecast/start", async (c) => {
  const body = await c.req.json().catch(() => ({}))
  const year = body.year || 2026
  const expectType = body.expectType || 1
  const overrides: { modelIndex?: number; maxIterations?: number; accuracyTarget?: number } = {}

  // 启动前审核节点：允许用户在启动前覆盖参数
  if (body.modelIndex != null) overrides.modelIndex = body.modelIndex
  if (body.maxIterations != null) overrides.maxIterations = body.maxIterations
  if (body.accuracyTarget != null) overrides.accuracyTarget = body.accuracyTarget

  const coord = getCoordinator()
  coord.startFullLoop(year, expectType, overrides)

  return c.json({
    status: "started",
    message: "闭环已启动",
    config: {
      year,
      expectType,
      ...(overrides.modelIndex != null && { modelIndex: overrides.modelIndex }),
      ...(overrides.maxIterations != null && { maxIterations: overrides.maxIterations }),
      ...(overrides.accuracyTarget != null && { accuracyTarget: overrides.accuracyTarget }),
    },
  })
})

// 停止闭环
app.post("/api/forecast/stop", async (c) => {
  const coord = getCoordinator()
  coord.stop()
  return c.json({ status: "stopped", message: "停止指令已发送" })
})

// 单次预测（不迭代）
app.post("/api/forecast/predict", async (c) => {
  const body = await c.req.json().catch(() => ({}))
  const year = body.year || 2026
  const expectType = body.expectType || 1

  const coord = getCoordinator()
  const predictions = await coord.predictOnce(year, expectType)
  return c.json({ status: "completed", predictions, count: predictions.length })
})

// 更新配置
app.post("/api/config/update", async (c) => {
  const body = await c.req.json().catch(() => ({}))
  const config = SYSTEM_CONFIG
  if (body.accuracyTarget != null) config.optimizer.accuracyTarget = body.accuracyTarget
  if (body.maxIterations != null) config.optimizer.maxIterations = body.maxIterations
  if (body.model) config.llm.model = body.model
  broadcast({ status: "config_updated", message: `配置已更新`, timestamp: new Date().toISOString() })
  return c.json({
    status: "ok",
    message: "配置已更新（部分设置需重启生效）",
    config: {
      llm: { model: config.llm.model },
      optimizer: config.optimizer,
    },
  })
})

// 获取状态
app.get("/api/status", (c) => {
  const coord = getCoordinator()
  return c.json(coord.status)
})

// 获取实时日志（SSE 降级兜底，配合前端 HTTP 轮询）
app.get("/api/logs", (c) => {
  return c.json(logBuffer)
})

// 获取迭代历史
app.get("/api/history", (c) => {
  const coord = getCoordinator()
  return c.json(coord.getHistory())
})

// 获取准确率时间线
app.get("/api/accuracy-timeline", (c) => {
  const coord = getCoordinator()
  return c.json(coord.getAccuracyTimeline())
})

// 获取配置
app.get("/api/config", (c) => {
  return c.json({
    db: { host: DB_CONFIG.host, database: DB_CONFIG.database },
    llm: { model: SYSTEM_CONFIG.llm.model },
    optimizer: SYSTEM_CONFIG.optimizer,
    defaults: SYSTEM_CONFIG.defaults,
  })
})

// 获取最优版本的代码
app.get("/api/best-code", (c) => {
  const coord = getCoordinator()
  const best = coord.getBestCode()
  if (!best) return c.json({ status: "no_data", message: "暂无迭代记录" })
  return c.json(best)
})

// 从最优版本开始优化
app.post("/api/forecast/optimize-from-best", async (c) => {
  const coord = getCoordinator()
  coord.quickLoop()
  return c.json({ status: "started", message: "从最优版本开始优化" })
})

// 获取技能沉淀摘要（SkillStore → 前端展示）
app.get("/api/skills", (c) => {
  const coord = getCoordinator()
  return c.json(coord.getSkills())
})

// 获取 Skill 库统计
app.get("/api/skill-stats", (c) => {
  const coord = getCoordinator()
  return c.json(coord.getSkillStats())
})

// 获取 HITL 状态（前端轮询检测）
app.get("/api/hitl-status", (c) => {
  const coord = getCoordinator()
  return c.json(coord.hitlInfo)
})

// 人类决策接口
app.post("/api/hitl/decide", async (c) => {
  const body = await c.req.json().catch(() => ({}))
  const decision = body.decision as "continue" | "skip_model" | "stop"
  if (!["continue", "skip_model", "stop"].includes(decision)) {
    return c.json({ status: "error", message: "无效决策" }, 400)
  }
  const coord = getCoordinator()
  const ok = coord.resolveHITL(decision)
  return c.json({ status: ok ? "ok" : "error", message: ok ? "决策已处理" : "无待处理的 HITL 请求" })
})

// 获取各区县准确率趋势（跨版本）
app.get("/api/accuracy-per-org", (c) => {
  const coord = getCoordinator()
  const history = coord.getHistory()
  // 收集所有版本各区县的准确率
  const perOrgTimeline: Record<string, Array<{ version: number; accuracy: number; mape: number }>> = {}
  for (const record of history) {
    if (!record.evaluation?.per_org) continue
    for (const [org, metrics] of Object.entries(record.evaluation.per_org)) {
      if (!perOrgTimeline[org]) perOrgTimeline[org] = []
      perOrgTimeline[org].push({
        version: record.version,
        accuracy: metrics.accuracy,
        mape: metrics.mape,
      })
    }
  }
  return c.json(perOrgTimeline)
})

// 获取最优版本的预测 vs 实际对比
app.get("/api/best-comparison", (c) => {
  const coord = getCoordinator()
  const history = coord.getHistory()
  if (!history.length) return c.json({ status: "no_data" })

  // 找到准确率最高的版本
  let best = history[0]
  for (const r of history) {
    if ((r.accuracy || 0) > (best.accuracy || 0)) best = r
  }

  // 提取匹配的 pairs
  const pairs = best.evaluation?.pairs || []
  // 按区县分组
  const byOrg: Record<string, Array<{ month: string; predicted: number; actual: number; error_pct: number }>> = {}
  for (const p of pairs) {
    if (!byOrg[p.org]) byOrg[p.org] = []
    byOrg[p.org].push({ month: p.month, predicted: p.predicted, actual: p.actual, error_pct: p.error_pct })
  }

  return c.json({
    version: best.version,
    accuracy: best.accuracy,
    mape: best.evaluation?.mape,
    pairs, // 全量
    byOrg,
  })
})

// ================================================================
// 结构化最终报告（P1.1 功能）
// ================================================================
app.get("/api/report", (c) => {
  const coord = getCoordinator()
  const report = coord.generateForecastReport()
  return c.json(report)
})

// ================================================================
// 多模型并行初始预测（P2.1 功能）
// ================================================================
app.post("/api/forecast/parallel-start", async (c) => {
  const body = await c.req.json().catch(() => ({}))
  const year = body.year || 2026
  const expectType = body.expectType || 1

  const coord = getCoordinator()

  // 在后台异步执行并行预测
  coord.parallelInitialPrediction(year, expectType).then(result => {
    // 并行预测完成后，自动进入迭代优化循环
    if (result.code && result.predictions.length > 0) {
      coord.emit({ message: `🎯 并行模型选择完成，最佳: ${result.runResults[0]?.label || "?"} (${result.accuracy.toFixed(2)}%)` })
    }
  }).catch(e => {
    if (e.message !== "用户已终止闭环") {
      console.error("[parallel-start]", e)
    }
  })

  return c.json({
    status: "started",
    message: "多模型并行预测已启动",
    config: { year, expectType, parallelModels: 3 },
  })
})

// ================================================================
// ReAct 多智能体调度
// ================================================================
app.post("/api/forecast/react-start", async (c) => {
  const body = await c.req.json().catch(() => ({}))
  const year = body.year || 2026
  const expectType = body.expectType || 1

  const coord = getCoordinator()
  coord.reactLoop(year, expectType).catch(e => {
    if (e.message !== "用户已终止闭环") {
      console.error("[react-start]", e)
    }
  })

  return c.json({
    status: "started",
    message: "ReAct 多智能体调度已启动",
    config: { year, expectType },
  })
})

// ================================================================
// 并行迭代循环（同时跑多个模型迭代优化）
// ================================================================
app.post("/api/forecast/parallel-iterate", async (c) => {
  const body = await c.req.json().catch(() => ({}))
  const year = body.year || 2026
  const expectType = body.expectType || 1
  const parallelCount = body.parallelCount || 3

  const coord = getCoordinator()

  // 获取数据，然后启动并行迭代
  const dataResult = await coord["dataAgent"].run({ action: "wait_for_data", year, expectType })
  const predictions = await coord["dataAgent"].run({ action: "load_history", expectType })

  // 在后台异步执行
  coord.runParallelIterationLoop(year, expectType, [], dataResult.actuals || [], "", 0, parallelCount)
    .catch((e: any) => {
      if (e.message !== "用户已终止闭环") {
        console.error("[parallel-iterate]", e)
      }
    })

  return c.json({
    status: "started",
    message: `并行迭代已启动: ${parallelCount} 个模型同时运行`,
    config: { year, expectType, parallelCount },
  })
})

// ================================================================
// RAG 状态查询
// ================================================================
app.get("/api/rag-stats", (c) => {
  const coord = getCoordinator()
  const ragStats = coord["_ragStore"].stats()
  return c.json(ragStats)
})

// ================================================================
// 3-Agent 管道（Planner → Forecaster → Critic）
// ================================================================
app.post("/api/forecast/3agent-start", async (c) => {
  const body = await c.req.json().catch(() => ({}))
  const year = body.year || 2026
  const expectType = body.expectType || 1

  const coord = getCoordinator()
  coord.run3AgentPipeline(year, expectType).catch(e => {
    if (e.message !== "用户已终止闭环") {
      console.error("[3agent-start]", e)
    }
  })

  return c.json({
    status: "started",
    message: "3-Agent 管道已启动 (Planner→Forecaster→Critic)",
    config: { year, expectType },
  })
})

// ================================================================
// 启动
// ================================================================
killOldProcess()

let finalPort = PORT
for (let attempt = 0; attempt < 10; attempt++) {
  try {
    Bun.serve({ port: finalPort, fetch: app.fetch, idleTimeout: 255 })
    break
  } catch (e: any) {
    if (e?.code !== "EADDRINUSE") throw e
    console.log(`  ⚠️ 端口 ${finalPort} 被占用，尝试 ${finalPort + 1}...`)
    finalPort++
  }
}

console.log(`🚀 CastFlow API 服务器: http://0.0.0.0:${finalPort}`)
if (finalPort !== PORT) {
  console.log(`   ※ 原端口 ${PORT} 被占用（TIME_WAIT），已自动切换到 ${finalPort}`)
  console.log(`   ※ 更新前端 vite.config.ts 的 proxy 目标端口后刷新即可`)
}
