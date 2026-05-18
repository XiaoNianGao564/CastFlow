from langchain_core.tools import tool

from castflow.db import db


@tool
def load_history(org: str, months: int = 36) -> dict:
    """从数据库加载某区县月度负荷历史。返回统计特征 + 末 12 个月明细。

    Args:
        org: 区县代码，例如 TC01 / TC02 / TC03 / TC04 / TC05
        months: 加载多少个月，建议 24-60
    """
    data = db.load_history(org, months)
    if not data:
        return {"error": f"no data for {org}"}
    vals = [d["value"] for d in data]
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return {
        "count": n,
        "range": [data[0]["month"], data[-1]["month"]],
        "mean": round(mean, 2),
        "std": round(var ** 0.5, 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "tail_12": data[-12:],
    }


@tool
def load_actual(org: str, month: str) -> dict:
    """加载某月真实负荷用于评估预测准确率。

    Args:
        org: 区县代码
        month: 月份字符串，格式 YYYY-MM
    """
    v = db.load_actual(org, month)
    if v is None:
        return {"found": False, "message": f"{month} 真实数据尚未上报"}
    return {"found": True, "value": v}
