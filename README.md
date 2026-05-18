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

# 4. 运行
python run.py              # 默认 TC01 / 2026-01
python run.py TC02 2026-02 # 指定区县/月份
```

## Project Layout

```
castflow/
├── config.py            集中配置
├── state.py             ForecastState (TypedDict + reducer)
├── prompts.py           Orchestrator system prompt
├── graph/
│   ├── orchestrator.py  build_graph() 主图
│   ├── nodes.py         orchestrator_node
│   └── edges.py         路由：should_continue / after_tools
├── tools/
│   ├── data.py          load_history / load_actual
│   ├── python_exec.py   run_python (subprocess + timeout)
│   ├── eval.py          evaluate_mape
│   └── finalize.py      finalize
├── llm/client.py        ChatTongyi 包装
└── db/
    ├── mock.py          MockDB（默认）
    └── mysql.py         真 MySQL（USE_REAL_DB=true 启用）
```

## Roadmap

- [x] **Stage A · MVP** — 主图 + 5 个工具 + MemorySaver + CLI
- [ ] **Stage B1** — 接真 MySQL sxfhyc11
- [ ] **Stage B2** — Chroma 三层记忆 + memory tools
- [ ] **Stage B3** — Reflexion 反思子 Agent
- [ ] **Stage B4** — Coder 子 Agent（subagent-as-tool）
- [ ] **Stage B5** — Langfuse 全链路 trace
- [ ] **Stage B6** — DeepEval 10 case + LLM-as-Judge
- [ ] **Stage B7** — MCP Server (Anthropic Model Context Protocol)
- [ ] **Stage B8** — FastAPI SSE
- [ ] **Stage B9** — Streamlit 演示页

## License

MIT
