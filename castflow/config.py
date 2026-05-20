import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "qwen3.6-flash")
    llm_model_subagent: str = os.getenv("LLM_MODEL_SUBAGENT", "qwen3.6-flash")

    target_mape: float = float(os.getenv("TARGET_MAPE", "5.0"))
    max_iterations: int = int(os.getenv("MAX_ITERATIONS", "8"))
    python_timeout_sec: int = int(os.getenv("PYTHON_TIMEOUT_SEC", "60"))

    # Sandbox: "subprocess" (default, hardened env+temp cwd) or "docker" (run in --network=none container)
    sandbox_mode: str = os.getenv("SANDBOX_MODE", "subprocess")
    sandbox_docker_image: str = os.getenv("SANDBOX_DOCKER_IMAGE", "python:3.11-slim")

    use_real_db: bool = os.getenv("USE_REAL_DB", "false").lower() == "true"
    mysql_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "root")
    mysql_db: str = os.getenv("MYSQL_DB", "castflow_db")

    # Token budget (0 = 不限制)
    token_budget_total: int = int(os.getenv("TOKEN_BUDGET_TOTAL", "500000"))

    # Chroma HTTP server (空字符串 = 用本地 PersistentClient)
    chroma_http_host: str = os.getenv("CHROMA_HTTP_HOST", "")
    chroma_http_port: int = int(os.getenv("CHROMA_HTTP_PORT", "8001"))

    # Checkpoint 持久化路径
    checkpoint_db_path: str = os.getenv("CHECKPOINT_DB_PATH", "data/checkpoints/castflow.db")

    # MCP 工具加载
    mcp_enabled: bool = os.getenv("MCP_ENABLED", "false").lower() == "true"

    # 轮询调度器
    scheduler_enabled: bool = os.getenv("SCHEDULER_ENABLED", "false").lower() == "true"
    scheduler_interval_sec: int = int(os.getenv("SCHEDULER_INTERVAL_SEC", "300"))
    scheduler_target_months: str = os.getenv("SCHEDULER_TARGET_MONTHS", "")


settings = Settings()
