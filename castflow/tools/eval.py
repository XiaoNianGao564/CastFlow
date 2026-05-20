from __future__ import annotations

from math import sqrt
from typing import List

from langchain_core.tools import tool

from castflow.tools.schema import tool_error, tool_success


@tool
def evaluate_mape(predictions: List[float], actuals: List[float]) -> dict:
    """计算 MAPE (Mean Absolute Percentage Error)。

    Args:
        predictions: 预测值列表
        actuals: 真实值列表（与预测值等长）
    """
    if not predictions or not actuals or len(predictions) != len(actuals):
        return tool_error(
            "预测值和真实值必须非空且等长。",
            "predictions 和 actuals 必须非空且等长",
            next_action="检查预测输出和真实值数量是否一致。",
        )

    errors = []
    abs_errors = []
    squared_errors = []
    pct_errors = []
    per_point_errors = []
    zero_actual_count = 0

    for idx, (p, a) in enumerate(zip(predictions, actuals)):
        prediction = float(p)
        actual = float(a)
        error = prediction - actual
        abs_error = abs(error)
        errors.append(error)
        abs_errors.append(abs_error)
        squared_errors.append(error ** 2)

        pct_error = None
        if actual == 0:
            zero_actual_count += 1
        else:
            pct_error = abs_error / abs(actual) * 100
            pct_errors.append(pct_error)

        if idx < 50:
            per_point_errors.append(
                {
                    "index": idx,
                    "prediction": round(prediction, 6),
                    "actual": round(actual, 6),
                    "error": round(error, 6),
                    "abs_error": round(abs_error, 6),
                    "pct_error": round(pct_error, 6) if pct_error is not None else None,
                }
            )

    valid = len(pct_errors)
    if valid == 0:
        return tool_error(
            "所有真实值都是 0，无法计算 MAPE。",
            "all actuals are zero",
            data={"zero_actual_count": zero_actual_count},
            next_action="改用 MAE/RMSE 或检查真实值数据。",
            zero_actual_count=zero_actual_count,
        )

    n = len(actuals)
    mape = sum(pct_errors) / valid
    mae = sum(abs_errors) / n
    rmse = sqrt(sum(squared_errors) / n)
    bias = sum(errors) / n
    mpe = sum((p - a) / a for p, a in zip(predictions, actuals) if a != 0) / valid * 100

    result = {
        "mape": round(mape, 3),
        "n": valid,
        "accuracy": round(100 - mape, 3),
        "valid_n": valid,
        "total_n": n,
        "mae": round(mae, 6),
        "rmse": round(rmse, 6),
        "bias": round(bias, 6),
        "mean_error": round(bias, 6),
        "mpe": round(mpe, 6),
        "zero_actual_count": zero_actual_count,
        "per_point_errors": per_point_errors,
        "per_point_error_count": n,
    }
    return tool_success(
        "已计算 MAPE 和诊断指标。",
        data=result,
        next_action="用 MAPE 判断是否达标，然后调用 submit_candidate。注意：此诊断仅用于 pass/fail 判断，禁止传给 delegate_to_coder 的 diagnostics 参数。",
        **result,
    )
