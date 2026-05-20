from __future__ import annotations

import argparse
import hashlib
import random
import uuid
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean, pstdev

from sqlalchemy import bindparam, create_engine, text

from castflow.config import settings


@dataclass
class MonthlyPoint:
    month: str
    value: float


@dataclass
class WriteStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0


def month_range(start: str, end: str) -> list[str]:
    start_year, start_month = parse_month(start)
    end_year, end_month = parse_month(end)
    months: list[str] = []
    year = start_year
    month = start_month
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def parse_month(month: str) -> tuple[int, int]:
    year, mon = month.split("-")
    return int(year), int(mon)


def add_months(month: str, n: int) -> str:
    year, mon = parse_month(month)
    total = year * 12 + mon - 1 + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def stable_noise(key: str) -> float:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    seed = int(digest[:16], 16)
    return random.Random(seed).uniform(-1.0, 1.0)


def make_engine():
    url = (
        f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
        f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_db}"
        "?charset=utf8mb4"
    )
    return create_engine(url, pool_pre_ping=True, future=True)


def list_orgs(engine, expect_type: int) -> list[str]:
    sql = text(
        "SELECT DISTINCT org_name FROM tb_dlyc_area_qx_month "
        "WHERE expect_type = :t ORDER BY org_name"
    )
    with engine.connect() as conn:
        return [r.org_name for r in conn.execute(sql, {"t": expect_type}).fetchall()]


def load_history(engine, org: str, expect_type: int) -> list[MonthlyPoint]:
    sql = text(
        "SELECT power_year, power_month, power_num "
        "FROM tb_dlyc_area_qx_month "
        "WHERE org_name = :org AND expect_type = :t "
        "AND CONCAT(power_year, '-', LPAD(power_month, 2, '0')) <= '2025-12' "
        "ORDER BY power_year, CAST(power_month AS UNSIGNED)"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"org": org, "t": expect_type}).fetchall()
    out: list[MonthlyPoint] = []
    for row in rows:
        out.append(
            MonthlyPoint(
                month=f"{row.power_year}-{str(row.power_month).zfill(2)}",
                value=float(row.power_num),
            )
        )
    return out


def estimate_trend(points: list[MonthlyPoint]) -> float:
    values = [p.value for p in points[-36:]]
    if len(values) < 2:
        return 0.0
    xs = list(range(len(values)))
    x_mean = mean(xs)
    y_mean = mean(values)
    denom = sum((x - x_mean) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values)) / denom
    cap = max(y_mean * 0.012, 1.0)
    return max(min(slope, cap), -cap)


def monthly_factors(points: list[MonthlyPoint]) -> dict[int, float]:
    by_month: dict[int, list[float]] = defaultdict(list)
    for point in points:
        _, mon = parse_month(point.month)
        by_month[mon].append(point.value)
    overall = mean(p.value for p in points)
    factors = {mon: mean(vals) / overall for mon, vals in by_month.items() if vals and overall}
    for mon in range(1, 13):
        factors.setdefault(mon, 1.0)
    return factors


def residual_volatility(points: list[MonthlyPoint], factors: dict[int, float]) -> float:
    values = [p.value for p in points]
    if len(values) < 6:
        return max(mean(values) * 0.015, 1.0)
    base = mean(values[-12:]) if len(values) >= 12 else mean(values)
    residuals: list[float] = []
    for point in points[-36:]:
        _, mon = parse_month(point.month)
        expected = base * factors.get(mon, 1.0)
        residuals.append(point.value - expected)
    return max(pstdev(residuals), base * 0.008, 1.0)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def generate_future(points: list[MonthlyPoint], org: str, months: list[str], expect_type: int, noise_scale: float) -> list[MonthlyPoint]:
    if len(points) < 24:
        raise ValueError(f"{org} 历史不足 24 个月，无法可靠生成未来数据")

    factors = monthly_factors(points)
    slope = estimate_trend(points)
    volatility = residual_volatility(points, factors)
    recent_level = mean(p.value for p in points[-12:])
    last_month = points[-1].month
    history_by_month = {p.month: p.value for p in points}

    generated: list[MonthlyPoint] = []
    previous = points[-1].value
    for month in months:
        _, mon = parse_month(month)
        steps = (parse_month(month)[0] * 12 + parse_month(month)[1]) - (parse_month(last_month)[0] * 12 + parse_month(last_month)[1])
        base = recent_level + slope * steps
        value = base * factors.get(mon, 1.0)

        previous_year = add_months(month, -12)
        if previous_year in history_by_month:
            value = value * 0.65 + (history_by_month[previous_year] + slope * 12) * 0.35
        elif generated and len(generated) >= 12:
            value = value * 0.7 + (generated[-12].value + slope * 12) * 0.3

        noise = stable_noise(f"{org}:{month}:{expect_type}") * volatility * noise_scale
        value += noise
        value = clamp(value, previous * 0.86, previous * 1.14)
        value = max(value, recent_level * 0.35, 1.0)
        value = round(value, 2)
        generated.append(MonthlyPoint(month=month, value=value))
        history_by_month[month] = value
        previous = value

    return generated


def existing_months(engine, org: str, months: list[str], expect_type: int) -> set[str]:
    if not months:
        return set()
    years = sorted({parse_month(m)[0] for m in months})
    sql = text(
        "SELECT power_year, power_month FROM tb_dlyc_area_qx_month "
        "WHERE org_name = :org AND expect_type = :t AND power_year IN :years"
    ).bindparams(bindparam("years", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(sql, {"org": org, "t": expect_type, "years": [str(y) for y in years]}).fetchall()
    target = set(months)
    return {
        f"{r.power_year}-{str(r.power_month).zfill(2)}"
        for r in rows
        if f"{r.power_year}-{str(r.power_month).zfill(2)}" in target
    }


def org_meta(engine, org: str, expect_type: int) -> dict:
    sql = text(
        "SELECT org_code, pa_org_code, pa_org_name FROM tb_dlyc_area_qx_month "
        "WHERE org_name = :org AND expect_type = :t "
        "ORDER BY CAST(power_year AS UNSIGNED) DESC, CAST(power_month AS UNSIGNED) DESC LIMIT 1"
    )
    with engine.connect() as conn:
        row = conn.execute(sql, {"org": org, "t": expect_type}).fetchone()
    if not row:
        return {"org_code": None, "pa_org_code": None, "pa_org_name": None}
    return {
        "org_code": row.org_code,
        "pa_org_code": row.pa_org_code,
        "pa_org_name": row.pa_org_name,
    }


def write_points(engine, org: str, points: list[MonthlyPoint], expect_type: int, overwrite: bool) -> WriteStats:
    stats = WriteStats()
    meta = org_meta(engine, org, expect_type)
    select_sql = text(
        "SELECT 1 FROM tb_dlyc_area_qx_month "
        "WHERE org_name = :org AND power_year = :y AND power_month = :m AND expect_type = :t LIMIT 1"
    )
    insert_sql = text(
        "INSERT INTO tb_dlyc_area_qx_month "
        "(pid, org_code, org_name, pa_org_code, pa_org_name, power_date, power_year, power_month, power_num, expect_type) "
        "VALUES (:pid, :org_code, :org, :pa_org_code, :pa_org_name, :power_date, :y, :m2, :v, :t)"
    )
    update_sql = text(
        "UPDATE tb_dlyc_area_qx_month SET power_num = :v "
        "WHERE org_name = :org AND power_year = :y AND power_month = :m AND expect_type = :t"
    )
    with engine.begin() as conn:
        for point in points:
            year, mon = parse_month(point.month)
            month_plain = str(mon)
            month_padded = f"{mon:02d}"
            params = {
                "pid": str(uuid.uuid4()),
                "org_code": meta["org_code"],
                "org": org,
                "pa_org_code": meta["pa_org_code"],
                "pa_org_name": meta["pa_org_name"],
                "power_date": point.month,
                "y": str(year),
                "m": month_plain,
                "m2": month_padded,
                "v": str(point.value),
                "t": expect_type,
            }
            exists = conn.execute(select_sql, params).fetchone() is not None
            if not exists and month_plain != month_padded:
                exists = conn.execute(select_sql, {**params, "m": month_padded}).fetchone() is not None
            if exists and overwrite:
                conn.execute(update_sql, params)
                if month_plain != month_padded:
                    conn.execute(update_sql, {**params, "m": month_padded})
                stats.updated += 1
            elif exists:
                stats.skipped += 1
            else:
                conn.execute(insert_sql, params)
                stats.inserted += 1
    return stats


def summarize(org: str, history: list[MonthlyPoint], generated: list[MonthlyPoint]) -> str:
    hist_mean = mean(p.value for p in history[-12:])
    fut_mean = mean(p.value for p in generated)
    yoy = (fut_mean / hist_mean - 1) * 100 if hist_mean else 0.0
    preview = ", ".join(f"{p.month}={p.value:.2f}" for p in generated[:3])
    tail = ", ".join(f"{p.month}={p.value:.2f}" for p in generated[-3:])
    return f"{org}: {len(generated)} months, recent12={hist_mean:.2f}, future_avg={fut_mean:.2f}, avg_change={yoy:.2f}%, head=[{preview}], tail=[{tail}]"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate 2026/2027 simulated monthly load data for evaluation.")
    parser.add_argument("--org", action="append", help="区县名；可重复传。不传则处理全部区县。")
    parser.add_argument("--start", default="2026-01", help="开始月份，默认 2026-01。")
    parser.add_argument("--end", default="2027-12", help="结束月份，默认 2027-12。")
    parser.add_argument("--expect-type", type=int, default=1, help="业务口径，默认 1=区民用电。")
    parser.add_argument("--noise-scale", type=float, default=0.35, help="噪声强度，默认 0.35。")
    parser.add_argument("--write", action="store_true", help="实际写入数据库；默认只预览。")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在月份；默认跳过。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not settings.use_real_db:
        print("WARNING: USE_REAL_DB=false，但该脚本用于真实 MySQL 模拟数据补齐。仍会按 .env 的 MySQL 配置连接。")

    months = month_range(args.start, args.end)
    engine = make_engine()
    orgs = args.org or list_orgs(engine, args.expect_type)
    total = WriteStats()

    for org in orgs:
        history = load_history(engine, org, args.expect_type)
        if len(history) < 24:
            print(f"SKIP {org}: history rows={len(history)} < 24")
            continue
        generated = generate_future(history, org, months, args.expect_type, args.noise_scale)
        existing = existing_months(engine, org, months, args.expect_type)
        print(summarize(org, history, generated))
        if existing and not args.overwrite:
            print(f"  existing target months skipped unless --overwrite: {len(existing)}")
        if args.write:
            stats = write_points(engine, org, generated, args.expect_type, args.overwrite)
            total.inserted += stats.inserted
            total.updated += stats.updated
            total.skipped += stats.skipped
            print(f"  write: inserted={stats.inserted}, updated={stats.updated}, skipped={stats.skipped}")
        else:
            would_insert = len(generated) - len(existing)
            print(f"  dry-run: would_insert={would_insert}, would_update={len(existing) if args.overwrite else 0}, existing={len(existing)}")

    if args.write:
        print(f"DONE inserted={total.inserted}, updated={total.updated}, skipped={total.skipped}")
    else:
        print("DRY-RUN only. Add --write to insert/update database rows.")


if __name__ == "__main__":
    main()
