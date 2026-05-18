import random
from datetime import datetime, timedelta


class MockDB:
    """演示/MVP 用 mock 数据库。USE_REAL_DB=true 时切换到 mysql.py。"""

    def load_history(self, org: str, months: int = 36) -> list[dict]:
        random.seed(hash(org) % 1000)
        base = 1000 + random.randint(-200, 200)
        out = []
        for i in range(months):
            d = datetime(2023, 1, 1) + timedelta(days=30 * i)
            seasonal = 200 * (1 + 0.3 * (d.month - 6.5) / 6)
            noise = random.uniform(-50, 50)
            out.append({
                "month": d.strftime("%Y-%m"),
                "value": round(base + seasonal + noise, 2),
            })
        return out

    def load_actual(self, org: str, month: str) -> float | None:
        random.seed(hash(org + month) % 1000)
        return round(1100 + random.uniform(-100, 100), 2)


db = MockDB()
