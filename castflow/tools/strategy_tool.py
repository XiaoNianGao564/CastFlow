"""Strategy recommendation tool — 基于历史运行自动推荐最优策略。"""
from __future__ import annotations

from langchain_core.tools import tool

from castflow.tools.schema import tool_error, tool_success


@tool
def get_strategy_recommendation(
    org: str,
    sample_size: int = 0,
    seasonality_ratio: float = 1.0,
    trend: float = 0.0,
    recent_mean: float = 0.0,
) -> dict:
    """根据数据特征从历史运行中推荐最优模型和策略。
    在 load_history 之后、delegate_to_coder 之前调用，获取基于经验的策略建议。

    Args:
        org: 区县名称
        sample_size: 历史数据样本量（月数）
        seasonality_ratio: 季节性比率（近12月均值/近3月均值）
        trend: 趋势值（最近值与4月前的差）
        recent_mean: 最近3月均值
    """
    try:
        from castflow.memory.strategy import get_strategy_evolution

        evolution = get_strategy_evolution()
        data_features = {
            "sample_size": sample_size,
            "seasonality_ratio": seasonality_ratio,
            "trend": trend,
            "recent_mean": recent_mean,
        }

        recommendation = evolution.get_recommendation(org, data_features)
        avoid_list = evolution.get_avoid_list(org, data_features)
        analysis = evolution.analyze()

        payload = {
            "recommendation": recommendation,
            "avoid_models": avoid_list,
            "total_historical_runs": analysis.get("total_runs", 0),
            "model_ranking": analysis.get("model_ranking", [])[:5],
        }

        source = recommendation.get("source", "history")
        if recommendation.get("recommended_model"):
            source_label = "基于规则" if source == "rule_based" else "基于历史"
            avg_mape = recommendation.get("avg_mape")
            mape_part = f", 历史平均MAPE={avg_mape}%" if avg_mape else ""
            summary = (
                f"推荐模型：{recommendation['recommended_model']} "
                f"({source_label}{mape_part})。"
                f"原因：{recommendation.get('reason', '')}。"
            )
            if avoid_list:
                summary += f" 建议避免：{avoid_list}。"
            if recommendation.get("alternatives"):
                alt_names = [a.get("model", "") for a in recommendation["alternatives"] if a.get("model")]
                if alt_names:
                    summary += f" 备选：{', '.join(alt_names)}。"
        else:
            summary = "历史数据不足，无法生成策略推荐。请使用默认策略。"

        return tool_success(
            summary,
            data=payload,
            next_action="将推荐模型作为 delegate_to_coder 的 constraints 参数传入。",
            **payload,
        )
    except Exception as e:
        return tool_error(
            "策略推荐失败，使用默认策略。",
            str(e),
            next_action="继续使用默认策略调用 delegate_to_coder。",
        )
