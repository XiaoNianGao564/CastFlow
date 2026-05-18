# CastFlow

**自迭代多 Agent 电力负荷预测系统** · 基于 LangGraph + 通义千问

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2-1C3C3C)](https://langchain-ai.github.io/langgraph/)
[![Qwen](https://img.shields.io/badge/LLM-qwen--max-FF6A00)](https://dashscope.aliyun.com/)

---

## What is CastFlow

CastFlow 是一个**真正意义上的 Agent 系统**：LLM 通过原生 tool-calling 自主决策每一步，
替代传统硬编码的 ML 流水线。给定区县和目标月份，Agent 会自主完成：

```
取数据 → 选模型 → 写 Python → 跑代码 → 评估 MAPE → 反思 → 迭代 → 达标收尾
```

业务场景：铜川 5 区电力负荷预测，目标 MAPE ≤ 5%。

## Architecture (Stage A)

```
         ┌──────────────────────┐
         │   Orchestrator       │  qwen-max + tool-calling
         │   (LangGraph 节点)    │
         └──────────┬───────────┘
                    │
            ┌───────▼────────┐
            │   ToolNode     │
            └───────┬────────┘
                    │
   ┌────────┬───────┼───────┬──────────┬──────────┐
   ▼        ▼       ▼       ▼          ▼          ▼
load_   load_   run_    evaluate_  finalize   (Stage B:
history actual  python  mape                  memory/MCP)
```

## Quick Start

```bash
# 1. 装 uv（推荐）或用 pip
pip install uv

# 2. 创建虚拟环境并安装依赖
uv venv
uv pip install -e .

# 3. 配置 API key
cp .env.example .env
# 编辑 .env 填入 DASHSCOPE_API_KEY

# 4.（推荐）启动 Chroma HTTP server（解决嵌入式模式在多线程下的兼容问题）
docker compose up -d chromadb

# 5. 运行
python run.py              # 默认 耀州 / 2026-12
python run.py 印王 2026-12 # 指定区县/月份
```

> 不想用 Docker 也可以：保持 `.env` 里 `CHROMA_HTTP_HOST` 留空，会自动 fallback
> 到本地 PersistentClient（在主 Agent 单线程场景下工作正常；多线程 eval 场景
> 推荐 HTTP 模式，详见下面「Memory: Chroma HTTP server」一节）。

## Memory: Chroma HTTP server

CastFlow 的三层记忆（episodic / semantic）默认连 Docker 部署的 chromadb HTTP server，
原因和真实踩过的坑：

- chromadb 1.5.x 嵌入式模式在 LangGraph ToolNode 多线程 + 重复 `PersistentClient(path=...)`
  场景下不稳，会抛 `AttributeError: 'RustBindingsAPI' object has no attribute 'bindings'`
  或 `Could not connect to tenant default_tenant`，导致 eval 跑批时 memory 失败
- chromadb 0.5.x 依赖 `chroma-hnswlib`，在 Windows 需要 MSVC 编译，开箱即用麻烦
- HTTP 模式下 client 是纯 REST 封装，没有 Rust bindings 也没有 tenant bootstrap，
  所有兼容 bug 直接绕过

启动 / 关闭：

```bash
# 启动（首次会拉 chromadb/chroma:1.0.5 镜像，约 200MB）
docker compose up -d chromadb

# 状态 / 心跳
docker compose ps
curl http://127.0.0.1:8001/api/v2/heartbeat

# 关闭（数据保留在 ./data/chroma_server/，下次启动自动恢复）
docker compose down
```

`castflow/memory/chroma_compat.py` 同时保留了对嵌入式模式的兼容性 patch
（`safe_persistent_client` 自动 ensure tenant + retry + clear cache），
所以 `CHROMA_HTTP_HOST` 留空时仍可工作。


## Project Layout

```
castflow/
├── config.py            集中配置
├── state.py             ForecastState (TypedDict + reducer)
├── prompts.py           Orchestrator system prompt
├── graph/
│   ├── orchestrator.py  build_graph() 主图
│   ├── nodes.py         orchestrator_node + force_finalize_node
│   └── edges.py         路由：should_continue / after_tools
├── tools/
│   ├── data.py          list_orgs / load_history / load_actual
│   ├── python_exec.py   run_python (subprocess + timeout)
│   ├── eval.py          evaluate_mape
│   ├── memory.py        recall_similar_runs / recall_lessons / save_lesson
│   ├── mcp_loader.py    通过 langchain-mcp-adapters 加载 MCP 工具
│   └── finalize.py      finalize
├── memory/
│   ├── episodic.py      历次 run 向量库（Chroma）
│   ├── semantic.py      lesson 向量库（Chroma）
│   └── embedding.py     DashScope text-embedding-v2
├── subagents/
│   └── reflector.py     Reflexion 反思子 Agent
├── llm/client.py        ChatOpenAI 走 Qwen 兼容协议
└── db/
    ├── mock.py          MockDB（默认）
    └── mysql.py         真 MySQL（USE_REAL_DB=true 启用）

mcp_servers/
└── castflow_data.py     MCP Server (FastMCP, stdio)
```

## MCP Server (Stage B7)

CastFlow 把电力数据查询能力包装成符合 **Anthropic Model Context Protocol** 的
独立服务，任何 MCP 客户端（Claude Desktop / Cursor / Continue / 自建 Agent）
都能复用同一套工具。

### 暴露的能力

| 类型 | 名称 | 作用 |
|---|---|---|
| Tool | `list_orgs` | 列出所有可用区县 |
| Tool | `load_history(org, months, before_month)` | 取月度历史，支持 before_month 防数据泄漏 |
| Tool | `load_actual(org, month)` | 取真实值用于评估 |
| Resource | `castflow://orgs` | 区县名 JSON 数组 |
| Resource | `castflow://org/{org}/profile` | 单个区县完整画像 |
| Prompt | `forecast_task(org, target_month)` | 复用的预测任务 prompt 模板 |

### 独立运行（stdio 传输）

```bash
.venv\Scripts\activate
python -m mcp_servers.castflow_data
```

### 端到端 smoke test

```bash
python -m scripts.test_mcp
# 期望输出:
#   ===> 加载到 3 个 MCP 工具
#   ===> 远程调用 list_orgs() 返回 5 个区县
#   ===> MCP 端到端 smoke test 通过 ✅
```

### 接入 Claude Desktop

把以下内容加入 `%APPDATA%\Claude\claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "castflow-data": {
      "command": "C:/Users/17166/Desktop/CastFlow/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_servers.castflow_data"],
      "cwd": "C:/Users/17166/Desktop/CastFlow",
      "env": {
        "DASHSCOPE_API_KEY": "sk-...",
        "USE_REAL_DB": "true",
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_USER": "root",
        "MYSQL_PASSWORD": "root",
        "MYSQL_DB": "sxfhyc11"
      }
    }
  }
}
```

重启 Claude Desktop 即可在对话框右下角看到 castflow-data 工具，
可以直接说「用 castflow-data 查耀州 2026-12 的负荷」。

## Roadmap

- [x] **Stage A · MVP** — LangGraph 主图 + 5 个工具 + MemorySaver + CLI
- [x] **Stage B1** — 接真 MySQL sxfhyc11，修复数据泄漏
- [x] **Stage B2** — Chroma 三层记忆 (working/episodic/semantic) + force_finalize
- [x] **Stage B3** — Reflexion 反思子 Agent (subagent-as-tool)
- [x] **Stage B4** — Coder 子 Agent（subagent-as-tool）
- [x] **Stage B5** — Langfuse 全链路 trace（graceful 降级）
- [x] **Stage B6** — DeepEval eval 框架 + Memory graceful 降级
- [x] **Stage B7** — MCP Server (Anthropic Model Context Protocol)
- [x] **Stage B8** — FastAPI SSE 流式 API
- [x] **Stage B9** — Streamlit 演示 UI

## Run the demo (B8 + B9)

两个终端：

```bash
# Terminal 1：起 FastAPI 后端
.venv\Scripts\activate
uvicorn api.server:app --host 0.0.0.0 --port 8000

# Terminal 2：起 Streamlit UI
.venv\Scripts\activate
streamlit run streamlit_app.py
```

浏览器打开 http://localhost:8501，左侧选区县和月份，点「运行 Agent」
就能实时看到 Agent 每一步工具调用和结果。

API 文档（Swagger UI）：http://localhost:8000/docs

主要端点：

| 端点 | 方法 | 用途 |
|---|---|---|
| `/health` | GET | 健康检查 |
| `/forecast/run` | POST | 阻塞式跑完返回最终摘要（CI/自动化） |
| `/forecast/stream` | POST | SSE 流式，每个工具调用/返回都推一条事件 |

## License

MIT
