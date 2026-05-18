from langchain_core.tools import tool


@tool
def finalize(best_code: str, best_mape: float, reason: str) -> dict:
    """达标或迭代上限时调用此工具显式收尾。

    Args:
        best_code: 本次任务中产出的最佳预测代码
        best_mape: 最佳 MAPE 数值
        reason: 收尾原因（达标 / 迭代上限 / 其他）
    """
    return {
        "finalized": True,
        "best_mape": best_mape,
        "reason": reason,
        "code_length": len(best_code or ""),
    }
