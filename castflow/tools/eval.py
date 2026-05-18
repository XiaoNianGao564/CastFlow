from langchain_core.tools import tool


@tool
def evaluate_mape(predictions: list[float], actuals: list[float]) -> dict:
    """计算 MAPE (Mean Absolute Percentage Error)。

    Args:
        predictions: 预测值列表
        actuals: 真实值列表（与预测值等长）
    """
    if not predictions or not actuals or len(predictions) != len(actuals):
        return {"error": "predictions 和 actuals 必须非空且等长"}
    n = len(actuals)
    total = 0.0
    valid = 0
    for p, a in zip(predictions, actuals):
        if a == 0:
            continue
        total += abs((a - p) / a)
        valid += 1
    if valid == 0:
        return {"error": "actuals 全为 0"}
    mape = total / valid * 100
    return {"mape": round(mape, 3), "n": valid, "accuracy": round(100 - mape, 3)}
