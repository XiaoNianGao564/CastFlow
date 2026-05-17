/**
 * CastFlow 前端主应用
 * 借鉴 CastClaw 的 app/src/ 结构
 */

import { Component, createSignal, onMount, onCleanup } from "solid-js"
import Dashboard from "./routes/Dashboard"
import History from "./routes/History"
import Settings from "./routes/Settings"

const App: Component = () => {
  const [currentRoute, setCurrentRoute] = createSignal("dashboard")
  const [status, setStatus] = createSignal({ status: "idle", currentVersion: 0, bestAccuracy: 0, totalIterations: 0 })
  const [logs, setLogs] = createSignal<string[]>([])
  const [running, setRunning] = createSignal(false)
  const [connected, setConnected] = createSignal(false)
  const [progress, setProgress] = createSignal({ iteration: 0, maxIterations: 10, elapsed: 0 })
  const [hitlPending, setHitlPending] = createSignal(false)
  const [hitlReason, setHitlReason] = createSignal("")
  const [hitlOptions, setHitlOptions] = createSignal<string[]>([])

  // SSE 连接（全局，切换页面不会断）
  onMount(() => {
    let evtSource: EventSource | null = null

    // --- SSE 用于实时推送（静默重连，不控制连接状态）---
    const connectSSE = () => {
      if (evtSource) {
        try { evtSource.close() } catch {}
      }
      evtSource = new EventSource("/api/events")

      evtSource.onopen = () => {
        // SSE 连通是 bonus，不影响连接指示器
      }

      evtSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.status) {
            if (data.status === "stopped") {
              setRunning(false)
              setProgress({ iteration: 0, maxIterations: 10, elapsed: 0 })
            }
            // SSE 推送的 accuracy 映射到 bestAccuracy，确保实时显示
            const mapped = { ...data }
            if (data.accuracy != null && data.bestAccuracy == null) {
              mapped.bestAccuracy = data.accuracy
            }
            setStatus((s) => ({ ...s, ...mapped }))
          }
          if (data.message) {
            setLogs((prev) => [...prev.slice(-99), `[${new Date().toLocaleTimeString()}] ${data.message}`])
          }
          if (data.iteration != null && data.maxIterations != null) {
            setProgress({
              iteration: data.iteration,
              maxIterations: data.maxIterations,
              elapsed: data.elapsed || progress().elapsed,
            })
          }
          // 运行/终止判断
          if (data.status === "completed" || data.status === "failed" || data.status === "idle" || data.status === "stopped") {
            setRunning(false)
            setHitlPending(false)
          } else if (data.status === "stuck") {
            // HITL: 暂停等待人类决策
            setRunning(false)  // 按钮变灰，不显示停止按钮
            setHitlPending(true)
            // 轮询获取 HITL 详情
            fetch("/api/hitl-status").then(r => r.json()).then(info => {
              if (info.paused) {
                setHitlReason(info.reason)
                setHitlOptions(info.options)
              }
            }).catch(() => {})
          } else if (data.status !== "ready" && data.status !== "config_updated") {
            setRunning(true)
          }
        } catch {}
      }

      // SSE 自动重连，onerror 静默忽略
      // 连接状态由 HTTP 轮询控制
      evtSource.onerror = () => {}
    }

    // --- HTTP 轮询：连接状态的唯一来源 ---
    const fetchStatus = async () => {
      try {
        const res = await fetch("/api/status")
        const data = await res.json()
        setConnected(true)  // HTTP 响应成功 = 后端可达
        setStatus((s) => ({ ...s, ...data }))  // 合并更新，避免覆盖 SSE 推送的字段（如 currentModel）
        if (data.status === "completed" || data.status === "failed" || data.status === "idle" || data.status === "stopped") {
          setRunning(false)
          setHitlPending(false)
        } else if (data.status === "stuck") {
          setRunning(false)
          setHitlPending(true)
        } else if (data.status !== "ready") {
          setRunning(true)
        }
      } catch {
        setConnected(false) // HTTP 失败 = 后端真的挂了
      }
    }

    // --- HTTP 拉取日志（SSE 降级保底）---
    const fetchLogs = async () => {
      try {
        const res = await fetch("/api/logs")
        if (!res.ok) return
        const data = await res.json()
        if (Array.isArray(data) && data.length > 0) {
          setLogs((prev) => {
            const lastPrev = prev[prev.length - 1]
            const newEntries = lastPrev ? data.slice(data.indexOf(lastPrev) + 1) : data
            if (newEntries.length === 0) return prev
            return [...prev, ...newEntries].slice(-99)
          })
        }
      } catch {}
    }

    // 先连 SSE，无所谓成败
    connectSSE()
    // 立即拉一次状态和日志
    fetchStatus()
    fetchLogs()
    // 每 5 秒 HTTP 轮询保底
    const pollTimer = setInterval(() => {
      fetchStatus()
      fetchLogs()
    }, 5000)
    // 每 30 秒尝试重建 SSE（如果之前失败了，给个机会重连）
    const sseRetryTimer = setInterval(() => {
      if (evtSource && evtSource.readyState === EventSource.CLOSED) {
        connectSSE()
      }
    }, 30000)

    onCleanup(() => {
      if (evtSource) {
        try { evtSource.close() } catch {}
      }
      clearInterval(pollTimer)
      clearInterval(sseRetryTimer)
    })
  })

  const navClass = (route: string) =>
    `px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
      currentRoute() === route
        ? "bg-blue-600 text-white"
        : "text-gray-600 hover:bg-gray-100"
    }`

  return (
    <div class="min-h-screen bg-gray-50">
      {/* 顶部导航 - 类似 CastClaw 的 CLI/TUI UI 风格 */}
      <header class="bg-white border-b border-gray-200 shadow-sm">
        <div class="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div class="flex items-center gap-3">
            <span class="text-2xl">⚡</span>
            <h1 class="text-xl font-bold text-gray-900">CastFlow</h1>
            <span class="text-sm text-gray-400 font-mono">v0.1</span>
          </div>
          <nav class="flex gap-2">
            <button class={navClass("dashboard")} onClick={() => setCurrentRoute("dashboard")}>仪表盘</button>
            <button class={navClass("history")} onClick={() => setCurrentRoute("history")}>迭代历史</button>
            <button class={navClass("settings")} onClick={() => setCurrentRoute("settings")}>设置</button>
          </nav>
          {/* 状态指示器 */}
          <div class="flex items-center gap-2">
            <span class={`w-2 h-2 rounded-full ${
              !connected() ? "bg-red-500 animate-pulse" :
              status().status === "completed" ? "bg-green-500" :
              status().status === "failed" || status().status === "stopped" ? "bg-red-500" :
              status().status === "idle" ? "bg-gray-400" :
              "bg-blue-500 animate-pulse"
            }`} />
            <span class="text-xs text-gray-500 font-mono">
              {!connected() ? "disconnected" : status().status}
            </span>
          </div>
        </div>
      </header>

      {/* 主内容区 */}
      <main class="max-w-7xl mx-auto px-4 py-6">
        {currentRoute() === "dashboard" && (
          <Dashboard
            status={status}
            logs={logs()}
            running={running()}
            connected={connected()}
            progress={progress()}
            hitlPending={hitlPending()}
            hitlReason={hitlReason()}
            hitlOptions={hitlOptions()}
            onStart={() => setRunning(true)}
            onStop={() => setRunning(false)}
            onHitlDecision={() => { setHitlPending(false); setHitlReason(""); setHitlOptions([]) }}
          />
        )}
        {currentRoute() === "history" && <History />}
        {currentRoute() === "settings" && <Settings />}
      </main>

      {/* 底部状态栏 */}
      <footer class="fixed bottom-0 w-full bg-white border-t border-gray-200 text-xs text-gray-400">
        <div class="max-w-7xl mx-auto px-4 py-2 flex justify-between">
          <span>
            {!connected() ? "⚠️ 未连接" : `最佳准确率: ${status().bestAccuracy.toFixed(2)}%`}
          </span>
          <span>{status().currentModel ? `模型: ${status().currentModel}` : ""}</span>
          <span>迭代次数: {status().totalIterations}</span>
          <span>当前版本: v{status().currentVersion}</span>
        </div>
      </footer>
    </div>
  )
}

export default App
