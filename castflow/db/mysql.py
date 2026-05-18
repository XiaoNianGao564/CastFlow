"""真 MySQL 连接 - 对接 sxfhyc11 库 tb_dlyc_area_qx_month 表。

启用方式：在 .env 设置 USE_REAL_DB=true。

表结构（核心列）:
    org_name      varchar  区县中文名（印王 / 宜君 / 客服 / 新区 / 耀州）
    power_year    varchar  年份字符串
    power_month   varchar  月份字符串（01-12）
    power_num     varchar  负荷值（需 float 转换）
    expect_type   int      1=区民, 2=煤改电（默认 1）
"""
from __future__ import annotations

from sqlalchemy import create_engine, text

from castflow.config import settings


class MySQLDB:
    def __init__(self) -> None:
        url = (
            f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
            f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_db}"
            "?charset=utf8mb4"
        )
        self.engine = create_engine(url, pool_pre_ping=True, future=True)

    def list_orgs(self, expect_type: int = 1) -> list[str]:
        sql = text(
            "SELECT DISTINCT org_name FROM tb_dlyc_area_qx_month "
            "WHERE expect_type = :t ORDER BY org_name"
        )
        with self.engine.connect() as c:
            return [r.org_name for r in c.execute(sql, {"t": expect_type}).fetchall()]

    def load_history(
        self, org: str, months: int = 36, expect_type: int = 1
    ) -> list[dict]:
        """取某区县最近 months 个月数据，按月份升序返回。"""
        sql = text(
            "SELECT power_year, power_month, power_num "
            "FROM tb_dlyc_area_qx_month "
            "WHERE org_name = :org AND expect_type = :t "
            "ORDER BY power_year DESC, power_month DESC LIMIT :n"
        )
        with self.engine.connect() as c:
            rows = c.execute(
                sql, {"org": org, "t": expect_type, "n": months}
            ).fetchall()
        # 倒序拿出来后再 reverse 成升序
        return [
            {
                "month": f"{r.power_year}-{str(r.power_month).zfill(2)}",
                "value": float(r.power_num),
            }
            for r in reversed(rows)
        ]

    def load_actual(
        self, org: str, month: str, expect_type: int = 1
    ) -> float | None:
        """月份格式 'YYYY-MM'。"""
        try:
            year, mon = month.split("-")
        except ValueError:
            return None
        sql = text(
            "SELECT power_num FROM tb_dlyc_area_qx_month "
            "WHERE org_name = :org AND power_year = :y AND power_month = :m "
            "AND expect_type = :t LIMIT 1"
        )
        with self.engine.connect() as c:
            r = c.execute(
                sql,
                {"org": org, "y": year, "m": mon.lstrip("0") or "0", "t": expect_type},
            ).fetchone()
            if r:
                return float(r.power_num)
            # 部分库存的是 '01' 而非 '1'，再试一次
            r = c.execute(
                sql, {"org": org, "y": year, "m": mon, "t": expect_type}
            ).fetchone()
        return float(r.power_num) if r else None


db = MySQLDB()
