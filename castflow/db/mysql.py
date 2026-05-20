"""真 MySQL 连接 - 对接电力负荷数据库。

三表分工：
    tb_dlyc_area_qx_month       → 历史训练数据（只读）
    tb_dlyc_area_qx_month_true  → 真实值（只读，用于评估预测精度）
    tb_dlyc_area_qx_month_pred  → 预测值（读写，存储 Agent 预测结果）

启用方式：在 .env 设置 USE_REAL_DB=true。

表结构（核心列，三表一致）:
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
        self, org: str, months: int = 36, expect_type: int = 1, before_month: str | None = None
    ) -> list[dict]:
        """取某区县最近 months 个月数据，按月份升序返回。

        before_month 支持两种格式：
            - "YYYY-MM" → 取该月之前的数据
            - "YYYY"    → 取该年全年之前的数据（等价于 YYYY-01）
        """
        if before_month:
            try:
                parts = before_month.split("-")
                if len(parts) == 1 and parts[0].isdigit():
                    # 纯年份模式：视为 YYYY-01（取该年1月之前的数据）
                    by = int(parts[0])
                    bm = 1
                elif len(parts) == 2:
                    by = int(parts[0])
                    bm = int(parts[1])
                else:
                    return []
            except ValueError:
                return []
            sql = text(
                "SELECT power_year, power_month, power_num "
                "FROM tb_dlyc_area_qx_month "
                "WHERE org_name = :org AND expect_type = :t "
                "AND (CAST(power_year AS UNSIGNED) < :by OR "
                "(CAST(power_year AS UNSIGNED) = :by AND CAST(power_month AS UNSIGNED) < :bm)) "
                "ORDER BY CAST(power_year AS UNSIGNED) DESC, CAST(power_month AS UNSIGNED) DESC LIMIT :n"
            )
            params = {"org": org, "t": expect_type, "by": by, "bm": bm, "n": months}
        else:
            sql = text(
                "SELECT power_year, power_month, power_num "
                "FROM tb_dlyc_area_qx_month "
                "WHERE org_name = :org AND expect_type = :t "
                "ORDER BY CAST(power_year AS UNSIGNED) DESC, CAST(power_month AS UNSIGNED) DESC LIMIT :n"
            )
            params = {"org": org, "t": expect_type, "n": months}
        with self.engine.connect() as c:
            rows = c.execute(sql, params).fetchall()
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
        """从真实值表 tb_dlyc_area_qx_month_true 加载真实值。"""
        try:
            year, mon = month.split("-")
        except ValueError:
            return None
        sql = text(
            "SELECT power_num FROM tb_dlyc_area_qx_month_true "
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

    def save_predictions(
        self, org: str, months: list[str], predictions: list[float], expect_type: int = 1
    ) -> int:
        """将预测值写入 tb_dlyc_area_qx_month_pred。

        Args:
            org: 区县名称
            months: 月份列表，格式 YYYY-MM
            predictions: 预测值列表，与 months 等长
            expect_type: 1=区民, 2=煤改电

        Returns:
            成功更新的行数
        """
        if len(months) != len(predictions):
            raise ValueError(f"months({len(months)}) 与 predictions({len(predictions)}) 长度不一致")
        updated = 0
        sql = text(
            "UPDATE tb_dlyc_area_qx_month_pred SET power_num = :v "
            "WHERE org_name = :org AND power_year = :y AND power_month = :m "
            "AND expect_type = :t"
        )
        with self.engine.connect() as c:
            for month, pred in zip(months, predictions):
                try:
                    year, mon = month.split("-")
                except ValueError:
                    continue
                val_str = str(round(pred, 4))
                params_base = {"v": val_str, "org": org, "y": year, "m": mon, "t": expect_type}
                # 先试原始月份格式，再试 lstrip("0") 格式
                result = c.execute(sql, params_base)
                if result.rowcount == 0:
                    params_base["m"] = mon.lstrip("0") or "0"
                    result = c.execute(sql, params_base)
                updated += result.rowcount
            c.commit()
        return updated


db = MySQLDB()
