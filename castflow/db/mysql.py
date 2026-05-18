"""真 MySQL 连接（阶段 B1 启用）。

现在留作占位 - USE_REAL_DB=true 时启用，业务表名按 sxfhyc11 实际结构补全 SQL。
"""
from sqlalchemy import create_engine, text

from castflow.config import settings


class MySQLDB:
    def __init__(self) -> None:
        url = (
            f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
            f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_db}"
        )
        self.engine = create_engine(url, pool_pre_ping=True)

    def load_history(self, org: str, months: int = 36) -> list[dict]:
        sql = text(
            "SELECT month, value FROM load_history "
            "WHERE org = :org ORDER BY month DESC LIMIT :n"
        )
        with self.engine.connect() as c:
            rows = c.execute(sql, {"org": org, "n": months}).fetchall()
        return [{"month": r.month, "value": float(r.value)} for r in reversed(rows)]

    def load_actual(self, org: str, month: str) -> float | None:
        sql = text("SELECT value FROM load_actual WHERE org = :org AND month = :m")
        with self.engine.connect() as c:
            r = c.execute(sql, {"org": org, "m": month}).fetchone()
        return float(r.value) if r else None


db = MySQLDB()
