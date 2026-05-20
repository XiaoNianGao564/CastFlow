from __future__ import annotations

import hashlib
import math
import random


class MockDB:
    """演示/MVP 用 mock 数据库。USE_REAL_DB=true 时切换到 mysql.py。"""

    def __init__(self) -> None:
        self._orgs = ["耀州", "印王", "宜君", "客服", "新区"]
        self._cache: dict[str, dict[str, float]] = {}

    def _month_to_index(self, month: str) -> int:
        year, mon = month.split("-")
        return int(year) * 12 + int(mon) - 1

    def _index_to_month(self, index: int) -> str:
        return f"{index // 12:04d}-{index % 12 + 1:02d}"

    def _org_seed(self, org: str) -> int:
        digest = hashlib.sha256(org.encode("utf-8")).hexdigest()
        return int(digest[:12], 16) % 100_000

    def _seasonal_factor(self, month_idx: int, seed: int) -> float:
        angle = 2 * math.pi * ((month_idx % 12) / 12)
        second = 2 * math.pi * (((month_idx + 5) % 12) / 12)
        return 1.0 + 0.16 * math.sin(angle) + 0.05 * math.cos(second + seed / 10000)

    def _trend_factor(self, month_idx: int, seed: int) -> float:
        base_idx = self._month_to_index("2023-01")
        steps = month_idx - base_idx
        slope = 0.0012 + (seed % 11) * 0.00008
        return 1.0 + slope * steps

    def _noise(self, org: str, month: str) -> float:
        digest = hashlib.sha256(f"{org}:{month}".encode("utf-8")).hexdigest()
        seed = int(digest[:12], 16)
        return random.Random(seed).uniform(-0.035, 0.035)

    def _build_series(self, org: str) -> dict[str, float]:
        if org in self._cache:
            return self._cache[org]

        seed = self._org_seed(org)
        rng = random.Random(seed)
        base = 780 + (seed % 360)
        org_bias = 0.92 + (seed % 17) / 100
        series: dict[str, float] = {}
        previous = base * org_bias

        start_idx = self._month_to_index("2023-01")
        end_idx = self._month_to_index("2027-12")
        for month_idx in range(start_idx, end_idx + 1):
            month = self._index_to_month(month_idx)
            seasonal = self._seasonal_factor(month_idx, seed)
            trend = self._trend_factor(month_idx, seed)
            noise = self._noise(org, month)
            shock = 1.0 + rng.uniform(-0.015, 0.015)
            value = previous * 0.58 + base * org_bias * seasonal * trend * shock * 0.42
            value *= 1.0 + noise
            value = max(value, 120.0)
            value = round(value, 2)
            series[month] = value
            previous = value

        self._cache[org] = series
        return series

    def list_orgs(self) -> list[str]:
        return list(self._orgs)

    def load_history(self, org: str, months: int = 36, before_month: str | None = None) -> list[dict]:
        series = self._build_series(org)
        items = sorted(series.items())
        if before_month:
            items = [item for item in items if item[0] < before_month]
        items = items[-months:]
        return [{"month": month, "value": value} for month, value in items]

    def load_actual(self, org: str, month: str) -> float | None:
        series = self._build_series(org)
        return series.get(month)

    def save_predictions(self, org: str, months: list[str], predictions: list[float], expect_type: int = 1) -> int:
        """Mock 实现：仅打印日志，不实际写入。"""
        print(f"[MockDB] save_predictions: org={org}, expect_type={expect_type}, {len(months)} months")
        return len(months)


db = MockDB()
