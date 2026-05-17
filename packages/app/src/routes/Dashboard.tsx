/**
 * 仪表盘页面 - 主操作界面
 *
 * 功能：
 * - 启动/停止自优化闭环
 * - 单次预测
 * - 实时显示系统状态
 * - 准确率变化图表
 * - 当前代码版本查看
 * - 进度条可视化
 */

import { Component, createSignal, createMemo, createEffect, onMount, onCleanup, For, Show } from "solid-js"

const API_BASE = "/api"

// 准确率 SVG 图表（独立组件，确保 SolidJS 跟踪信号变化）
const ChartView: Component<{ data: any[] }> = (props) => {
  const points = props.data
  if (!points.length) return null

  const width = 600, height = 200
  const maxAcc = 100
  const minAcc = Math.max(0, Math.min(...points.map(p => p.accuracy)) - 5)
  const range = maxAcc - minAcc
  const xStep = width / Math.max(points.length - 1, 1)
  const linePath = points.map((p, i) => {
    const x = i * xStep
    const y = height - ((p.accuracy - minAcc) / range) * height
    return `${i === 0 ? "M" : "L"}${x},${y}`
  }).join(" ")
  const areaPath = linePath + ` L${(points.length - 1) * xStep},${height} L0,${height} Z`

  return (
    <svg viewBox={`0 0 ${width} ${height}`} class="w-full h-48">
      <defs>
        <linearGradient id="accGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#2563eb" stop-opacity="0.2" />
          <stop offset="100%" stop-color="#2563eb" stop-opacity="0.02" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill="url(#accGrad)" />
      <path d={linePath} fill="none" stroke="#2563eb" stroke-width="2" />
      <For each={points}>
        {(p, i) => (
          <g>
            <circle cx={i() * xStep} cy={height - ((p.accuracy - minAcc) / range) * height}
                    r="3" fill={p.improved ? "#16a34a" : "#d97706"} />
            <title>{`v${p.version}: ${p.accuracy.toFixed(2)}%`}</title>
          </g>
        )}
      </For>
      <text x="-8" y="12" fill="#9ca3af" font-size="10">{maxAcc}%</text>
      <text x="-8" y={height} fill="#9ca3af" font-size="10">{minAcc.toFixed(0)}%</text>
      <line x1="0" y1={height - ((98 - minAcc) / range) * height} x2={width}
            y2={height - ((98 - minAcc) / range) * height}
            stroke="#16a34a" stroke-dasharray="5,5" stroke-width="1" />
    </svg>
  )
}

// 区县级准确率趋势图（多线图）
const COLORS = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed"]
const PerOrgTrendChart: Component<{ data: Record<string, Array<{ version: number; accuracy: number; mape: number }>> }> = (props) => {
  const orgs = Object.keys(props.data)
  if (!orgs.length) return <div class="text-center text-gray-400 py-8">暂无数据</div>

  const width = 600, height = 180
  const maxAcc = 100
  const allAccs = orgs.flatMap(o => props.data[o].map(p => p.accuracy))
  const minAcc = Math.max(0, Math.min(...allAccs) - 3)
  const range = maxAcc - minAcc
  
  // Get all unique version numbers
  const versions = [...new Set(orgs.flatMap(o => props.data[o].map(p => p.version)))].sort((a, b) => a - b)
  const xStep = width / Math.max(versions.length - 1, 1)

  const chartPadding = { top: 10, right: 10, bottom: 35, left: 40 }
  const chartWidth = width - chartPadding.left - chartPadding.right
  const chartHeight = height - chartPadding.top - chartPadding.bottom
  const chartXStep = chartWidth / Math.max(versions.length - 1, 1)

  return (
    <svg viewBox={`0 0 ${width} ${height}`} class="w-full">
      <g transform={`translate(${chartPadding.left}, ${chartPadding.top})`}>
        {/* 网格线 - 水平 */}
        <line x1="0" y1="0" x2={chartWidth} y2="0" stroke="#f3f4f6" stroke-width="1" />
        <line x1="0" y1={chartHeight/2} x2={chartWidth} y2={chartHeight/2} stroke="#f3f4f6" stroke-width="1" />
        <line x1="0" y1={chartHeight} x2={chartWidth} y2={chartHeight} stroke="#f3f4f6" stroke-width="1" />
        {/* 各折线 */}
        <For each={orgs}>
          {(org, oi) => {
            const points = props.data[org].sort((a, b) => a.version - b.version)
            const path = points.map((p, i) => {
              const x = versions.indexOf(p.version) * chartXStep
              const y = chartHeight - ((p.accuracy - minAcc) / range) * chartHeight
              return `${i === 0 ? "M" : "L"}${x},${y}`
            }).join(" ")
            return (
              <g>
                <path d={path} fill="none" stroke={COLORS[oi() % COLORS.length]} stroke-width="2" />
                <For each={points}>
                  {(p, pi) => {
                    const x = versions.indexOf(p.version) * chartXStep
                    const y = chartHeight - ((p.accuracy - minAcc) / range) * chartHeight
                    return (
                      <circle cx={x} cy={y} r="3" fill={COLORS[oi() % COLORS.length]} stroke="white" stroke-width="1.5">
                        <title>{`${org} v${p.version}: ${p.accuracy.toFixed(2)}%`}</title>
                      </circle>
                    )
                  }}
                </For>
              </g>
            )
          }}
        </For>
        {/* Y 轴刻度 */}
        <text x="-8" y="5" fill="#6b7280" font-size="10" text-anchor="end">{maxAcc}%</text>
        <text x="-8" y={chartHeight/2 + 4} fill="#6b7280" font-size="10" text-anchor="end">{((maxAcc + minAcc) / 2).toFixed(0)}%</text>
        <text x="-8" y={chartHeight + 4} fill="#6b7280" font-size="10" text-anchor="end">{minAcc.toFixed(0)}%</text>
        {/* X 轴刻度（版本号） */}
        <For each={versions}>
          {(v, i) => (
            <text x={i() * chartXStep} y={chartHeight + 18} fill="#6b7280" font-size="9" text-anchor="middle">v{v}</text>
          )}
        </For>
      </g>
      {/* 图例 - 放在底部中央 */}
      <g transform={`translate(${width / 2 - (orgs.length * 50) / 2}, ${height - 5})`}>
        <For each={orgs}>
          {(org, i) => (
            <g transform={`translate(${i() * 55}, 0)`}>
              <rect width="10" height="3" rx="1" fill={COLORS[i() % COLORS.length]} />
              <text x="14" y="3" fill="#374151" font-size="11" font-weight="500">{org}</text>
            </g>
          )}
        </For>
      </g>
    </svg>
  )
}

// 预测 vs 实际对比图（多区县）
const ComparisonChart: Component<{ data: Record<string, Array<{ month: string; predicted: number; actual: number; error_pct: number }>> }> = (props) => {
  const orgs = () => Object.keys(props.data)
  if (!orgs().length) return <div class="text-center text-gray-400 py-8">暂无对比数据</div>

  const [selectedOrg, setSelectedOrg] = createSignal(orgs()[0])

  // 使用 createMemo 确保选区变更时 SVG 重新渲染
  const points = createMemo(() => {
    const p = props.data[selectedOrg()]
    if (!p) return []
    return [...p].sort((a, b) => a.month.localeCompare(b.month))
  })

  const chartSvg = createMemo(() => {
    const pts = points()
    if (!pts.length) return null

    const width = 500, height = 200
    const padding = { top: 5, right: 5, bottom: 25, left: 55 }
    const cw = width - padding.left - padding.right
    const ch = height - padding.top - padding.bottom
    const values = pts.flatMap(x => [x.predicted, x.actual])
    const maxV = Math.max(...values) * 1.1
    const minV = Math.min(...values) * 0.9
    const range = maxV - minV || 1
    const xStep = cw / Math.max(pts.length - 1, 1)

    const predPath = pts.map((pt, i) => {
      const x = i * xStep
      const y = ch - ((pt.predicted - minV) / range) * ch
      return `${i === 0 ? "M" : "L"}${x},${y}`
    }).join(" ")

    const actualPath = pts.map((pt, i) => {
      const x = i * xStep
      const y = ch - ((pt.actual - minV) / range) * ch
      return `${i === 0 ? "M" : "L"}${x},${y}`
    }).join(" ")

    // Y 轴刻度值（取 4 个等分点）
    const yTicks = [0, 0.33, 0.67, 1].map(pct => ({
      label: (minV + range * pct).toFixed(0),
      y: ch - pct * ch,
    }))

    return (
      <svg viewBox={`0 0 ${width} ${height}`} class="w-full">
        <g transform={`translate(${padding.left}, ${padding.top})`}>
          {/* 网格线 */}
          <For each={yTicks}>
            {(t) => <line x1="0" y1={t.y} x2={cw} y2={t.y} stroke="#f3f4f6" stroke-width="1" />}
          </For>
          <path d={predPath} fill="none" stroke="#2563eb" stroke-width="2" stroke-dasharray="5,3" />
          <path d={actualPath} fill="none" stroke="#16a34a" stroke-width="2" />
          {/* Y 轴刻度 */}
          <For each={yTicks}>
            {(t) => <text x="-6" y={t.y + 4} fill="#6b7280" font-size="10" text-anchor="end">{t.label}</text>}
          </For>
          {/* X 轴刻度（月份） */}
          <For each={pts}>
            {(pt, i) => {
              const monthLabel = pt.month.replace("2026-", "")
              return <text x={i() * xStep} y={ch + 16} fill="#6b7280" font-size="9" text-anchor="middle">{monthLabel}月</text>
            }}
          </For>
          {/* 数据点 */}
          <For each={pts}>
            {(pt, i) => (
              <g>
                <circle cx={i() * xStep} cy={ch - ((pt.predicted - minV) / range) * ch} r="2" fill="#2563eb" />
                <circle cx={i() * xStep} cy={ch - ((pt.actual - minV) / range) * ch} r="2" fill="#16a34a" />
                <title>{`${pt.month}\n预测: ${pt.predicted.toFixed(2)}\n实际: ${pt.actual.toFixed(2)}\n误差: ${pt.error_pct.toFixed(2)}%`}</title>
              </g>
            )}
          </For>
        </g>
      </svg>
    )
  })

  return (
    <div>
      {/* 区县选择器 */}
      <div class="flex gap-2 mb-3 flex-wrap">
        <For each={orgs()}>
          {(org) => (
            <button onClick={() => setSelectedOrg(org)}
                    class={`px-2 py-1 text-xs rounded-full transition-colors ${
                      selectedOrg() === org
                        ? "bg-blue-600 text-white"
                        : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                    }`}>
              {org}
            </button>
          )}
        </For>
      </div>
      {chartSvg()}
      <div class="flex justify-center gap-6 mt-2 text-xs">
        <span class="flex items-center gap-1"><span class="w-4 h-0.5 bg-blue-600 inline-block" /> 预测值</span>
        <span class="flex items-center gap-1"><span class="w-4 h-0.5 bg-green-600 inline-block" /> 实际值</span>
      </div>
    </div>
  )
}

interface DashboardProps {
  status: any
  logs: string[]
  running: boolean
  connected: boolean
  progress: { iteration: number; maxIterations: number; elapsed: number }
  hitlPending: boolean
  hitlReason: string
  hitlOptions: string[]
  onStart?: () => void       // 乐观更新：启动后立即设置 running
  onStop?: () => void         // 乐观更新：停止后立即设置 running
  onHitlDecision?: () => void // HITL 决策后重置状态
}

const Dashboard: Component<DashboardProps> = (props) => {
  const [year, setYear] = createSignal(2026)
  const [expectType, setExpectType] = createSignal(1)
  const [timeline, setTimeline] = createSignal<any[]>([])
  const [perOrgTimeline, setPerOrgTimeline] = createSignal<Record<string, Array<{ version: number; accuracy: number; mape: number }>>>({})
  const [bestComparison, setBestComparison] = createSignal<any>(null)
  const [showStopConfirm, setShowStopConfirm] = createSignal(false)
  const [predicting, setPredicting] = createSignal(false)
  const [predictResult, setPredictResult] = createSignal<string | null>(null)
  const [showInitDialog, setShowInitDialog] = createSignal(false)
  const [initInfo, setInitInfo] = createSignal<{ model: string; characteristics: any } | null>(null)

  // 🆕 可编辑初始参数
  const [initConfig, setInitConfig] = createSignal({
    modelIndex: -1,  // -1 = 自动选择
    maxIterations: 10,
    accuracyTarget: 98,
  })

  // 🆕 结构化报告
  const [report, setReport] = createSignal<any>(null)
  const [showReport, setShowReport] = createSignal(false)

  // 🆕 并行预测
  const [parallelActive, setParallelActive] = createSignal(false)

  // 加载准确率时间线
  const fetchTimeline = async () => {
    try {
      const res = await fetch(`${API_BASE}/accuracy-timeline`)
      const data = await res.json()
      if (Array.isArray(data)) setTimeline(data)
    } catch {}
  }

  // 加载各区县准确率趋势
  const fetchPerOrgTimeline = async () => {
    try {
      const res = await fetch(`${API_BASE}/accuracy-per-org`)
      const data = await res.json()
      if (data && typeof data === "object") setPerOrgTimeline(data)
    } catch {}
  }

  // 加载最优版本对比数据
  const fetchBestComparison = async () => {
    try {
      const res = await fetch(`${API_BASE}/best-comparison`)
      const data = await res.json()
      if (data && data.status !== "no_data") setBestComparison(data)
    } catch {}
  }

  onMount(() => {
    fetchTimeline()
    fetchPerOrgTimeline()
    fetchBestComparison()
    // 定时刷新（每 5 秒）
    const timelineTimer = setInterval(() => {
      fetchTimeline()
      fetchPerOrgTimeline()
      fetchBestComparison()
    }, 5000)
    onCleanup(() => clearInterval(timelineTimer))
  })

  // logs 变化时额外刷新一次
  createEffect(() => {
    if (props.logs.length > 0) fetchTimeline()
  })

  // 🆕 状态变为 completed 时自动获取报告
  createEffect(() => {
    if (props.status().status === "completed" || props.status().status === "stopped") {
      setParallelActive(false)
      // 延迟 500ms 拉取报告，确保所有数据已写入
      setTimeout(async () => {
        try {
          const res = await fetch(`${API_BASE}/report`)
          const data = await res.json()
          if (data && data.title) {
            setReport(data)
            setShowReport(true)
          }
        } catch {}
      }, 500)
    }
  })

  const startForecast = async () => {
    try {
      // 获取当前配置作为初始值
      const configRes = await fetch(`${API_BASE}/config`)
      const configData = await configRes.json()
      setInitConfig({
        modelIndex: -1,
        maxIterations: configData.optimizer?.maxIterations || 10,
        accuracyTarget: configData.optimizer?.accuracyTarget || 98,
      })

      const statusRes = await fetch(`${API_BASE}/status`)
      const statusData = await statusRes.json()
      setInitInfo({
        model: statusData.currentModel || "待选择",
        characteristics: null,
      })
      setShowInitDialog(true)
    } catch (e) {
      console.error("获取状态失败:", e)
    }
  }

  const confirmStart = async () => {
    setShowInitDialog(false)
    try {
      const body: any = { year: year(), expectType: expectType() }
      const cfg = initConfig()
      if (cfg.modelIndex >= 0) body.modelIndex = cfg.modelIndex
      if (cfg.maxIterations !== 10) body.maxIterations = cfg.maxIterations
      if (cfg.accuracyTarget !== 98) body.accuracyTarget = cfg.accuracyTarget

      await fetch(`${API_BASE}/forecast/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      props.onStart?.()
    } catch (e) {
      console.error("启动失败:", e)
    }
  }

  const stopForecast = async () => {
    try {
      await fetch(`${API_BASE}/forecast/stop`, { method: "POST" })
      setShowStopConfirm(false)
      props.onStop?.()
    } catch (e) {
      console.error("停止失败:", e)
    }
  }

  const predictOnce = async () => {
    if (predicting() || props.running) return
    setPredicting(true)
    setPredictResult(null)
    try {
      const res = await fetch(`${API_BASE}/forecast/predict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ year: year(), expectType: expectType() }),
      })
      const data = await res.json()
      if (data.status === "completed") {
        setPredictResult(`✅ 预测完成: 共 ${data.count} 条`)
      } else {
        setPredictResult("❌ 预测失败")
      }
    } catch (e) {
      setPredictResult("❌ 请求失败: 后端未响应")
      console.error("预测失败:", e)
    } finally {
      setPredicting(false)
    }
  }

  // HITL 决策
  const hitlDecide = async (decision: string) => {
    try {
      const mapDecision: Record<string, "continue" | "skip_model" | "stop"> = {
        "继续当前模型": "continue",
        "强制规则兜底修改参数": "continue",
        "跳过当前模型，切换下一个": "skip_model",
        "跳过当前模型": "skip_model",
        "停止闭环": "stop",
        "停止整个闭环": "stop",
      }
      await fetch(`${API_BASE}/hitl/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: mapDecision[decision] || "continue" }),
      })
      props.onHitlDecision?.()
    } catch (e) {
      console.error("HITL 决策失败:", e)
    }
  }

  // 格式化运行时间
  const formatElapsed = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return m > 0 ? `${m}分${s}秒` : `${s}秒`
  }

  // 进度百分比
  const progressPct = () => {
    const p = props.progress
    if (!p || !p.maxIterations) return 0
    return Math.round((p.iteration / p.maxIterations) * 100)
  }

  // 确认停止弹窗
  const StopConfirmModal = () => (
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowStopConfirm(false)}>
      <div class="bg-white rounded-xl shadow-xl p-6 max-w-sm mx-4" onClick={(e) => e.stopPropagation()}>
        <div class="text-lg font-semibold mb-2">确认停止？</div>
        <p class="text-sm text-gray-500 mb-4">
          当前正在运行的闭环将被停止，已完成的迭代数据不会丢失。
        </p>
        <div class="flex gap-3 justify-end">
          <button onClick={() => setShowStopConfirm(false)}
                  class="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50">
            继续运行
          </button>
          <button onClick={stopForecast}
                  class="px-4 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700">
            确认停止
          </button>
        </div>
      </div>
    </div>
  )

  // 准确率图表（SVG 原生绘制，无第三方依赖）
  const AccuracyChart = () => {
    const points = timeline()
    if (!points.length) return (
      <div class="flex items-center justify-center h-48 text-gray-400">
        暂无数据，启动预测后自动生成
      </div>
    )

    const width = 600, height = 200
    const maxAcc = 100
    const minAcc = Math.max(0, Math.min(...points.map(p => p.accuracy)) - 5)
    const range = maxAcc - minAcc

    const xStep = width / Math.max(points.length - 1, 1)

    const linePath = points.map((p, i) => {
      const x = i * xStep
      const y = height - ((p.accuracy - minAcc) / range) * height
      return `${i === 0 ? "M" : "L"}${x},${y}`
    }).join(" ")

    const areaPath = linePath + ` L${(points.length - 1) * xStep},${height} L0,${height} Z`

    return (
      <svg viewBox={`0 0 ${width} ${height}`} class="w-full h-48">
        <defs>
          <linearGradient id="accGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#2563eb" stop-opacity="0.2" />
            <stop offset="100%" stop-color="#2563eb" stop-opacity="0.02" />
          </linearGradient>
        </defs>
        {/* 面积 */}
        <path d={areaPath} fill="url(#accGrad)" />
        {/* 折线 */}
        <path d={linePath} fill="none" stroke="#2563eb" stroke-width="2" />
        {/* 数据点 */}
        <For each={points}>
          {(p, i) => (
            <g>
              <circle cx={i() * xStep} cy={height - ((p.accuracy - minAcc) / range) * height}
                      r="3" fill={p.improved ? "#16a34a" : "#d97706"} class="hover:r-5" />
              <title>{`v${p.version}: ${p.accuracy.toFixed(2)}%`}</title>
            </g>
          )}
        </For>
        {/* Y 轴标签 */}
        <text x="-8" y="12" fill="#9ca3af" font-size="10">{maxAcc}%</text>
        <text x="-8" y={height} fill="#9ca3af" font-size="10">{minAcc.toFixed(0)}%</text>
        {/* 目标线 */}
        <line x1="0" y1={height - ((98 - minAcc) / range) * height} x2={width}
              y2={height - ((98 - minAcc) / range) * height}
              stroke="#16a34a" stroke-dasharray="5,5" stroke-width="1" />
      </svg>
    )
  }

  return (
    <div class="grid grid-cols-3 gap-6">
      {/* 控制面板 */}
      <div class="col-span-1 bg-white rounded-xl border border-gray-200 p-6">
        <h2 class="text-lg font-semibold mb-4">控制面板</h2>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-gray-600 mb-1">预测年份</label>
            <select value={year()} onChange={(e) => setYear(Number(e.currentTarget.value))}
                    class="w-full px-3 py-2 border border-gray-300 rounded-lg">
              <option value="2026">2026</option>
              <option value="2027">2027</option>
            </select>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-600 mb-1">用电类型</label>
            <select value={expectType()} onChange={(e) => setExpectType(Number(e.currentTarget.value))}
                    class="w-full px-3 py-2 border border-gray-300 rounded-lg">
              <option value="1">区民用电</option>
              <option value="2">煤改电</option>
            </select>
          </div>

          {/* 当前模型实时展示（运行中高亮） */}
          <Show when={props.status().currentModel}>
            <div class={`rounded-xl p-4 border-2 transition-all ${
              props.running
                ? "bg-blue-50 border-blue-400 shadow-md"
                : "bg-gray-50 border-gray-200"
            }`}>
              <div class="flex items-center gap-2">
                <Show when={props.running}>
                  <span class="w-3 h-3 rounded-full bg-blue-500 animate-pulse" />
                </Show>
                <span class="text-xs text-gray-500 font-medium uppercase tracking-wider">当前模型</span>
              </div>
              <div class="mt-1 text-lg font-bold font-mono break-all"
                   classList={{"text-blue-700": props.running, "text-gray-700": !props.running}}>
                {props.status().currentModel}
              </div>
              <Show when={props.status().currentModel !== "—"}>
                <div class="mt-1 text-xs text-gray-400">
                  {props.running ? "正在使用此模型执行预测" : "当前选中的预测模型"}
                </div>
              </Show>
            </div>
          </Show>

          {/* 主按钮区域 */}
          <div class="space-y-2">
            <Show when={!props.running}>
              <button onClick={startForecast}
                      disabled={!props.connected}
                      class={`w-full py-3 rounded-lg font-semibold text-white transition-all ${
                        props.connected
                          ? "bg-blue-600 hover:bg-blue-700 active:scale-95"
                          : "bg-gray-400 cursor-not-allowed"
                      }`}>
                {!props.connected ? "等待连接..." : "启动自优化闭环"}
              </button>
            </Show>
            <Show when={props.running}>
              <button onClick={() => setShowStopConfirm(true)}
                      class="w-full py-3 rounded-lg font-semibold text-white bg-red-600 hover:bg-red-700 active:scale-95 transition-all">
                停止闭环
              </button>
            </Show>

            {/* 仅预测按钮 */}
            <button onClick={predictOnce}
                    disabled={props.running || predicting() || !props.connected}
                    class={`w-full py-2 rounded-lg text-sm font-medium border transition-all ${
                      props.running || predicting() || !props.connected
                        ? "border-gray-200 text-gray-400 cursor-not-allowed"
                        : "border-gray-300 text-gray-600 hover:bg-gray-50 active:scale-95"
                    }`}>
              {predicting() ? "预测中..." : props.running ? "运行中，无法操作" : "仅执行预测（不迭代）"}
            </button>

            {/* 🆕 多模型并行预测按钮 */}
            <Show when={!props.running}>
              <button onClick={async () => {
                setParallelActive(true)
                try {
                  await fetch(`${API_BASE}/forecast/parallel-start`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ year: year(), expectType: expectType() }),
                  })
                  props.onStart?.()
                } catch (e) {
                  console.error("并行预测启动失败:", e)
                  setParallelActive(false)
                }
              }}
                      disabled={!props.connected || parallelActive()}
                      class={`w-full py-2 rounded-lg text-sm font-medium border transition-all ${
                        !props.connected || parallelActive()
                          ? "border-gray-200 text-gray-400 cursor-not-allowed"
                          : "border-purple-300 text-purple-600 hover:bg-purple-50 active:scale-95"
                      }`}>
                {parallelActive() ? "并行预测中..." : "⚡ 多模型并行预测（P2）"}
              </button>
            </Show>

            {/* 🆕 ReAct 多智能体模式按钮 */}
            <Show when={!props.running}>
              <button onClick={async () => {
                try {
                  await fetch(`${API_BASE}/forecast/react-start`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ year: year(), expectType: expectType() }),
                  })
                  props.onStart?.()
                } catch (e) {
                  console.error("ReAct 启动失败:", e)
                }
              }}
                      disabled={!props.connected}
                      class={`w-full py-2 rounded-lg text-sm font-medium border transition-all ${
                        !props.connected
                          ? "border-gray-200 text-gray-400 cursor-not-allowed"
                          : "border-amber-300 text-amber-600 hover:bg-amber-50 active:scale-95"
                      }`}>
                🧠 ReAct 多智能体模式（P0）
              </button>
            </Show>

            {/* 🆕 3-Agent 管道按钮 */}
            <Show when={!props.running}>
              <button onClick={async () => {
                try {
                  await fetch(`${API_BASE}/forecast/3agent-start`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ year: year(), expectType: expectType() }),
                  })
                  props.onStart?.()
                } catch (e) {
                  console.error("3-Agent 启动失败:", e)
                }
              }}
                      disabled={!props.connected}
                      class={`w-full py-2 rounded-lg text-sm font-medium border transition-all ${
                        !props.connected
                          ? "border-gray-200 text-gray-400 cursor-not-allowed"
                          : "border-teal-300 text-teal-600 hover:bg-teal-50 active:scale-95"
                      }`}>
                🧩 3-Agent 管道: Planner→Forecaster→Critic
              </button>
            </Show>
          </div>

          {/* 预测结果提示 */}
          <Show when={predictResult()}>
            <div class="text-sm text-center py-1 rounded bg-gray-50">{predictResult()}</div>
          </Show>

          {/* 进度条 */}
          <Show when={props.running && props.progress.iteration > 0}>
            <div>
              <div class="flex justify-between text-xs text-gray-500 mb-1">
                <span>迭代 {props.progress.iteration} / {props.progress.maxIterations}</span>
                <span>已运行 {formatElapsed(props.progress.elapsed)}</span>
              </div>
              <div class="w-full bg-gray-200 rounded-full h-2">
                <div class="bg-blue-600 h-2 rounded-full transition-all duration-500"
                     style={`width: ${progressPct()}%`} />
              </div>
            </div>
          </Show>

          {/* 连接状态 */}
          <div class="flex items-center gap-2 text-xs">
            <span class={`w-2 h-2 rounded-full ${props.connected ? "bg-green-500" : "bg-red-500 animate-pulse"}`} />
            <span class={props.connected ? "text-green-600" : "text-red-500"}>
              {props.connected ? "已连接" : "未连接"}
            </span>
          </div>

          {/* 状态摘要 */}
          <div class="bg-gray-50 rounded-lg p-4 space-y-2 text-sm">
            <div class="flex justify-between">
              <span class="text-gray-500">当前状态</span>
              <span class="font-mono font-medium">{props.status().status}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-500">当前模型</span>
              <span class="font-mono text-blue-600">{props.status().currentModel || "—"}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-500">最佳准确率</span>
              <span class="font-mono font-bold text-green-600">{props.status().bestAccuracy.toFixed(2)}%</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-500">迭代次数</span>
              <span class="font-mono">{props.status().totalIterations}</span>
            </div>
          </div>
        </div>
      </div>

      {/* 右侧：图表 + 日志 */}
      <div class="col-span-2 space-y-6">
        {/* 准确率趋势 */}
        <div class="bg-white rounded-xl border border-gray-200 p-6">
          <h2 class="text-lg font-semibold mb-4">准确率变化趋势</h2>
          <Show when={timeline().length > 0} fallback={
            <div class="flex items-center justify-center h-48 text-gray-400">
              暂无数据，启动预测后自动生成
            </div>
          }>
            <ChartView data={timeline()} />
          </Show>
          <div class="flex justify-between mt-2 text-xs text-gray-400">
            <span>v0</span>
            <span class="text-green-600">— 目标 98%</span>
            <span>v{timeline().length - 1 > 0 ? timeline().length - 1 : ""}</span>
          </div>
        </div>

        {/* 区县级准确率趋势图 */}
        <Show when={Object.keys(perOrgTimeline()).length > 0}>
          <div class="bg-white rounded-xl border border-gray-200 p-6">
            <h2 class="text-lg font-semibold mb-4">区县级准确率趋势</h2>
            <PerOrgTrendChart data={perOrgTimeline()} />
          </div>
        </Show>

        {/* 最优版本预测 vs 实际对比 */}
        <Show when={bestComparison()?.byOrg}>
          <div class="bg-white rounded-xl border border-gray-200 p-6">
            <h2 class="text-lg font-semibold mb-4">
              最优版本 v{bestComparison()?.version} 预测 vs 实际
              <span class="ml-2 text-sm font-normal text-gray-400">
                (准确率 {bestComparison()?.accuracy?.toFixed(2) || "?"}%)
              </span>
            </h2>
            <ComparisonChart data={bestComparison()?.byOrg || {}} />
          </div>
        </Show>

        {/* 🆕 结构化报告展示 */}
        <Show when={report() && showReport()}>
          <div class="bg-white rounded-xl border border-gray-200 p-6">
            <div class="flex items-center justify-between mb-4">
              <h2 class="text-lg font-semibold">📋 预测总结报告</h2>
              <div class="flex gap-2">
                <button onClick={() => setShowReport(false)}
                        class="text-xs text-gray-400 hover:text-gray-600">
                  收起
                </button>
              </div>
            </div>

            {/* 报告内容 */}
            <div class="space-y-4">
              {/* 基本信息 */}
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                  { label: "最佳准确率", value: `${report()?.bestResult?.accuracy?.toFixed(2) || "?"}%`, color: "text-green-600" },
                  { label: "MAPE", value: `${report()?.bestResult?.mape?.toFixed(2) || "?"}%`, color: "text-blue-600" },
                  { label: "迭代次数", value: report()?.taskInfo?.totalIterations || "?", color: "text-purple-600" },
                  { label: "运行时长", value: report()?.taskInfo?.elapsed || "?", color: "text-orange-600" },
                ].map(m => (
                  <div class="bg-gray-50 rounded-lg p-3 text-center">
                    <div class="text-xs text-gray-500">{m.label}</div>
                    <div class={`text-lg font-bold font-mono ${m.color}`}>{m.value}</div>
                  </div>
                ))}
              </div>

              {/* 数据特征 */}
              <Show when={report()?.dataCharacteristics}>
                <div class="bg-blue-50 border border-blue-100 rounded-lg p-3">
                  <div class="text-sm font-medium text-blue-800 mb-1">📊 数据特征</div>
                  <div class="text-sm text-blue-700">{report().dataCharacteristics.description}</div>
                </div>
              </Show>

              {/* 洞察分析 */}
              <Show when={report()?.insights}>
                <div class="space-y-2">
                  <For each={[
                    { icon: "🔄", label: "季节性", text: report().insights.seasonalityImpact },
                    { icon: "📈", label: "趋势分析", text: report().insights.trendAnalysis },
                    { icon: "🌊", label: "波动分析", text: report().insights.volatilityAnalysis },
                    { icon: "🎯", label: "收敛分析", text: report().insights.convergenceAnalysis },
                  ]}>
                    {(item) => (
                      <div class="flex items-start gap-2 bg-gray-50 rounded-lg p-2">
                        <span>{item.icon}</span>
                        <div>
                          <div class="text-xs text-gray-400 font-medium">{item.label}</div>
                          <div class="text-sm text-gray-700">{item.text}</div>
                        </div>
                      </div>
                    )}
                  </For>
                </div>
              </Show>

              {/* 区县级性能 */}
              <Show when={report()?.perOrgPerformance?.length > 0}>
                <div>
                  <div class="text-sm font-medium text-gray-700 mb-2">🏘 各区县表现</div>
                  <table class="w-full text-sm">
                    <thead>
                      <tr class="border-b border-gray-200 text-gray-500">
                        <th class="text-left py-2">区县</th>
                        <th class="text-right py-2">准确率</th>
                        <th class="text-right py-2">MAPE</th>
                        <th class="text-right py-2">状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      <For each={report().perOrgPerformance}>
                        {(item: any) => (
                          <tr class="border-b border-gray-100">
                            <td class="py-2 font-medium">{item.org}</td>
                            <td class="text-right font-mono">{item.accuracy.toFixed(2)}%</td>
                            <td class="text-right font-mono">{item.mape.toFixed(2)}%</td>
                            <td class="text-right">
                              <span class={`text-xs px-2 py-0.5 rounded-full ${
                                item.status === "优秀" ? "bg-green-100 text-green-700" :
                                item.status === "良好" ? "bg-blue-100 text-blue-700" :
                                "bg-yellow-100 text-yellow-700"
                              }`}>{item.status}</span>
                            </td>
                          </tr>
                        )}
                      </For>
                    </tbody>
                  </table>
                </div>
              </Show>

              {/* 推荐建议 */}
              <Show when={report()?.recommendations?.length > 0}>
                <div class="bg-amber-50 border border-amber-200 rounded-lg p-3">
                  <div class="text-sm font-medium text-amber-800 mb-2">💡 优化建议</div>
                  <ul class="space-y-1">
                    <For each={report().recommendations}>
                      {(rec: string) => (
                        <li class="text-sm text-amber-700">{rec}</li>
                      )}
                    </For>
                  </ul>
                </div>
              </Show>

              {/* 迭代摘要 */}
              <Show when={report()?.iterationSummary?.length > 0}>
                <div>
                  <div class="text-sm font-medium text-gray-700 mb-2">📝 迭代过程</div>
                  <div class="max-h-40 overflow-y-auto">
                    <table class="w-full text-xs">
                      <thead>
                        <tr class="border-b border-gray-200 text-gray-400">
                          <th class="text-left py-1">版本</th>
                          <th class="text-right py-1">准确率</th>
                          <th class="text-right py-1">变化</th>
                          <th class="text-left py-1">修改摘要</th>
                        </tr>
                      </thead>
                      <tbody>
                        <For each={report().iterationSummary}>
                          {(item: any) => (
                            <tr class="border-b border-gray-50">
                              <td class="py-1 font-medium">v{item.version}</td>
                              <td class="text-right font-mono">{item.accuracy != null ? `${item.accuracy.toFixed(2)}%` : "—"}</td>
                              <td class={`text-right font-mono ${
                                item.status === "improved" ? "text-green-600" :
                                item.status === "failed" ? "text-red-500" : "text-gray-400"
                              }`}>{item.improvement}</td>
                              <td class="text-gray-500 truncate max-w-[200px]" title={item.patchSummary}>{item.patchSummary}</td>
                            </tr>
                          )}
                        </For>
                      </tbody>
                    </table>
                  </div>
                </div>
              </Show>
            </div>
          </div>
        </Show>

        {/* 实时日志 */}
        <div class="bg-white rounded-xl border border-gray-200 p-6">
          <h2 class="text-lg font-semibold mb-4">实时日志</h2>
          <div class="bg-gray-900 text-green-400 rounded-lg p-4 h-48 overflow-y-auto font-mono text-xs space-y-1">
            <Show when={props.logs.length === 0}>
              <div class="text-gray-500">等待操作...</div>
            </Show>
            <For each={props.logs}>
              {(entry) => <div>{entry}</div>}
            </For>
          </div>
        </div>
      </div>

      {/* 确认停止弹窗 */}
      <Show when={showStopConfirm()}>
        <StopConfirmModal />
      </Show>

      {/* 🆕 HITL 人在回路弹窗 */}
      <Show when={props.hitlPending}>
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div class="bg-white rounded-xl shadow-xl p-6 max-w-md mx-4">
            <div class="flex items-center gap-2 mb-4">
              <span class="text-2xl">🛑</span>
              <h2 class="text-lg font-semibold">需要人工决策</h2>
            </div>
            <p class="text-sm text-gray-600 mb-4 leading-relaxed">{props.hitlReason}</p>
            <div class="space-y-2">
              <For each={props.hitlOptions}>
                {(option: string) => (
                  <button onClick={() => hitlDecide(option)}
                          class="w-full text-left px-4 py-3 rounded-lg border border-gray-200 hover:bg-blue-50 hover:border-blue-300 transition-all text-sm">
                    {option}
                  </button>
                )}
              </For>
            </div>
          </div>
        </div>
      </Show>

      {/* 🆕 初始化确认弹窗（可编辑） */}
      <Show when={showInitDialog()}>
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowInitDialog(false)}>
          <div class="bg-white rounded-xl shadow-xl p-6 max-w-lg mx-4 w-full" onClick={(e) => e.stopPropagation()}>
            <div class="flex items-center gap-2 mb-4">
              <span class="text-2xl">🚀</span>
              <h2 class="text-lg font-semibold">启动前审核 — 参数配置</h2>
            </div>

            <div class="space-y-4 mb-6">
              {/* 初始模型选择 */}
              <div>
                <label class="block text-sm font-medium text-gray-600 mb-1">初始预测模型</label>
                <select value={initConfig().modelIndex}
                        onChange={(e) => setInitConfig(c => ({ ...c, modelIndex: parseInt(e.currentTarget.value) }))}
                        class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
                  <option value="-1">🤖 自动选择（基于数据特征）</option>
                  <option value="0">LLM 自动选择</option>
                  <option value="1">Holt-Winters / ETS</option>
                  <option value="2">SARIMA（自动定阶）</option>
                  <option value="3">Facebook Prophet</option>
                  <option value="4">XGBoost（时间特征）</option>
                  <option value="5">LightGBM（时间特征）</option>
                  <option value="6">集成模型（ETS+ARIMA+XGBoost）</option>
                  <option value="7">线性回归（季节+趋势虚拟变量）</option>
                  <option value="8">STL 分解 + ETS</option>
                </select>
              </div>

              {/* 迭代次数 */}
              <div>
                <label class="block text-sm font-medium text-gray-600 mb-1">最大迭代次数</label>
                <input type="number" value={initConfig().maxIterations} min="1" max="50"
                       onChange={(e) => setInitConfig(c => ({ ...c, maxIterations: Math.max(1, Math.min(50, Number(e.currentTarget.value))) }))}
                       class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
                <div class="text-xs text-gray-400 mt-1">范围 1-50，建议 10-20 轮</div>
              </div>

              {/* 目标准确率 */}
              <div>
                <label class="block text-sm font-medium text-gray-600 mb-1">目标准确率 (%)</label>
                <input type="number" value={initConfig().accuracyTarget} min="50" max="100"
                       onChange={(e) => setInitConfig(c => ({ ...c, accuracyTarget: Math.max(50, Math.min(100, Number(e.currentTarget.value))) }))}
                       class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
                <div class="text-xs text-gray-400 mt-1">范围 50-100，达到目标后自动停止</div>
              </div>

              <div class="bg-blue-50 rounded-lg p-3">
                <div class="text-xs text-gray-500 mb-1">预测年份 / 用电类型</div>
                <div class="font-medium">{year()}年 · {expectType() === 1 ? "区民用电" : "煤改电"}</div>
              </div>
              <div class="bg-gray-50 rounded-lg p-3">
                <div class="text-xs text-gray-500">历史最优准确率</div>
                <div class="font-mono font-bold text-green-600 mt-1">{props.status().bestAccuracy.toFixed(2)}%</div>
              </div>
            </div>

            <div class="text-xs text-gray-400 mb-4 leading-relaxed">
              启动后系统将自动执行：数据完整性检查 → 智能模型选择 → 代码生成 → 预测执行 → 评估 → 分析 → 补丁迭代。
              最多 {initConfig().maxIterations} 轮，目标 {initConfig().accuracyTarget}%。
            </div>

            <div class="flex gap-3 justify-end">
              <button onClick={() => setShowInitDialog(false)}
                      class="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50">
                取消
              </button>
              <button onClick={confirmStart}
                      class="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 font-medium">
                确认启动
              </button>
            </div>
          </div>
        </div>
      </Show>
    </div>
  )
}

export default Dashboard
