import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "qwen-max")
    llm_model_subagent: str = os.getenv("LLM_MODEL_SUBAGENT", "qwen-plus")

    target_mape: float = float(os.getenv("TARGET_MAPE", "5.0"))
    max_iterations: int = int(os.getenv("MAX_ITERATIONS", "8"))
    python_timeout_sec: int = int(os.getenv("PYTHON_TIMEOUT_SEC", "60"))

    use_real_db: bool = os.getenv("USE_REAL_DB", "false").lower() == "true"
    mysql_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "root")
    mysql_db: str = os.getenv("MYSQL_DB", "sxfhyc11")


settings = Settings()
