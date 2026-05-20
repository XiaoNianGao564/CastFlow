# CastFlow Docker 部署

## 前置条件

- Docker Engine ≥ 24.0
- Docker Compose ≥ 2.20
- 至少 4 GB 可用内存

## 快速启动

```bash
# 1. 进入项目目录
cd /path/to/CastFlow

# 2. 构建并启动所有服务
docker compose up -d --build

# 3. 查看日志
docker compose logs -f castflow

# 4. 打开页面
#    Dashboard: http://localhost:8501
#    API:       http://localhost:8000
#    API Docs:  http://localhost:8000/docs
```

## 服务说明

| 服务 | 容器名 | 端口 | 说明 |
|------|--------|------|------|
| castflow | castflow-app | 8000 (API), 8501 (Streamlit) | 主应用 |
| chromadb | castflow-chroma | 8001 | 向量数据库（三层记忆） |
| mysql | castflow-mysql | 3307 | 业务数据库（历史负荷 + 预测） |

## 环境变量

### 宿主机 → 容器配置传递

`docker compose up` 会自动读取宿主机 `.env` 文件中的以下变量并注入容器：

| 变量 | 说明 | 必填 |
|------|------|------|
| `DASHSCOPE_API_KEY` | DashScope API Key（`sk-` 开头） | 🔴 是 |
| `LLM_MODEL` | 主 Agent 模型 | 🟢 否（默认 qwen3.6-flash） |
| `LLM_MODEL_SUBAGENT` | 子 Agent 模型 | 🟢 否（默认 qwen3.6-flash） |

### 容器内预设值 vs 需要修改的值

以下配置在 `docker-compose.yml` 中已预设为 Docker 服务发现值，**通常无需修改**：

| 变量 | 预设值 | 说明 |
|------|--------|------|
| `CHROMA_HTTP_HOST` | `chromadb` | 同网络下 chromadb 服务名 |
| `CHROMA_HTTP_PORT` | `8000` | chromadb 容器内端口 |
| `MYSQL_HOST` | `mysql` | MySQL 服务名 |
| `MYSQL_PORT` | `3306` | MySQL 容器内端口 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `root` / `root` | 与 mysql 服务配置一致 |
| `MYSQL_DB` | `castflow_db` | 与 mysql 服务配置一致 |
| `USE_REAL_DB` | `false` | 容器内默认使用模拟数据 |

### 如何覆盖

在 `docker-compose.yml` 的 `castflow` → `environment` 段中直接修改即可。也可在宿主机 `.env` 中定义同名变量（Docker Compose 会优先读取）。

## 常用命令

```bash
# 构建镜像
docker compose build

# 启动
docker compose up -d

# 停止
docker compose down

# 停止并删除数据卷
docker compose down -v

# 查看日志（实时）
docker compose logs -f

# 查看某个服务日志
docker compose logs -f castflow

# 重启某个服务
docker compose restart castflow

# 重新构建并启动（代码变更后）
docker compose up -d --build castflow
```

## 数据持久化

- MySQL 数据：`./data/mysql/`
- ChromaDB 数据：`./data/chroma_server/`
- `docker compose down` **不会**删除这些数据
- `docker compose down -v` 会删除

## 云服务器部署

```bash
# 复制项目到服务器
scp -r CastFlow/ user@host:/opt/castflow

# SSH 登录服务器
ssh user@host

# 启动
cd /opt/castflow && docker compose up -d --build

# 配置 Nginx 反向代理（可选）
# 见 docs/nginx.conf 示例
```

## 常见问题

**Q: 容器启动后页面打不开？**
A: 查看日志：`docker compose logs castflow`

**Q: MySQL 连接失败？**
A: 等待 MySQL 健康检查通过：`docker compose logs mysql`

**Q: 模型调用报 AccessDenied？**
A: 检查 `.env` 中 `DASHSCOPE_API_KEY` 是否有效且账户未欠费
