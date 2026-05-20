"""Strategy Evolution — 从 episodic memory 中提取成功模式，自动生成策略推荐。

核心思路（来自《智能体设计模式》第9章 学习与适应）：
- 分析历史 episodic memory 中哪些模型/策略对哪类数据特征效果好
- 自动生成"数据特征 → 推荐模型/参数"的策略推荐
- 记录失败模式，下次遇到相似场景时主动规避
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from statistics import mean, median

from castflow.memory.episodic import get_episodic


class StrategyEvolution:
    """从历史运行中提取策略模式并生成推荐。"""

    def __init__(self) -> None:
        self._cache: dict | None = None

    def _load_all_runs(self) -> list[dict]:
        """从 episodic memory 加载所有历史运行记录。"""
        eps = get_episodic()
        if not eps.available:
            return []
        try:
            count = eps.count()
            if count == 0:
                return []
            results = eps.recall("电力负荷预测", top_k=min(count, 100))
            return results
        except Exception:
            return []

    def _parse_data_features(self, summary: str) -> dict:
        """从 run summary 中提取数据特征。"""
        features = {}
        if not summary:
            return features

        sample_match = re.search(r"样本=(\d+)", summary)
        if sample_match:
            features["sample_size"] = int(sample_match.group(1))

        seasonality_match = re.search(r"seasonality_ratio=([\d.]+)", summary)
        if seasonality_match:
            features["seasonality_ratio"] = float(seasonality_match.group(1))

        trend_match = re.search(r"trend=([-\d.]+)", summary)
        if trend_match:
            features["trend"] = float(trend_match.group(1))

        recent_mean_match = re.search(r"最近3月均值=([\d.]+)", summary)
        if recent_mean_match:
            features["recent_mean"] = float(recent_mean_match.group(1))

        return features

    def _categorize_data(self, features: dict) -> str:
        """将数据特征归类为几种典型模式。"""
        sample_size = features.get("sample_size", 0)
        seasonality = features.get("seasonality_ratio", 1.0)
        trend = features.get("trend", 0)

        categories = []
        if sample_size < 18:
            categories.append("短序列")
        elif sample_size >= 36:
            categories.append("长序列")
        else:
            categories.append("中等序列")

        if abs(seasonality - 1.0) > 0.1:
            categories.append("强季节性")
        else:
            categories.append("弱季节性")

        if abs(trend) > features.get("recent_mean", 1) * 0.1:
            categories.append("有趋势")
        else:
            categories.append("平稳")

        return "+".join(categories)

    def analyze(self) -> dict:
        """分析所有历史运行，提取策略模式。"""
        runs = self._load_all_runs()
        if not runs:
            return {"total_runs": 0, "patterns": [], "recommendations": []}

        # 按模型分组统计
        model_stats: dict[str, list[float]] = defaultdict(list)
        # 按数据类别+模型分组
        category_model_stats: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        # 失败模式
        failure_patterns: list[dict] = []

        for run in runs:
            model = run.get("model") or "unknown"
            mape = run.get("mape")
            summary = run.get("summary", "")

            if mape is None or mape >= 900:
                failure_patterns.append({
                    "model": model,
                    "org": run.get("org"),
                    "summary_preview": summary[:200],
                })
                continue

            model_stats[model].append(float(mape))
            features = self._parse_data_features(summary)
            category = self._categorize_data(features)
            category_model_stats[category][model].append(float(mape))

        # 生成模型排名
        model_ranking = []
        for model, mapes in model_stats.items():
            model_ranking.append({
                "model": model,
                "avg_mape": round(mean(mapes), 3),
                "median_mape": round(median(mapes), 3),
                "count": len(mapes),
                "best": round(min(mapes), 3),
                "worst": round(max(mapes), 3),
            })
        model_ranking.sort(key=lambda x: x["avg_mape"])

        # 生成分类推荐
        recommendations = []
        for category, models in category_model_stats.items():
            best_model = None
            best_avg = 999.0
            for model, mapes in models.items():
                avg = mean(mapes)
                if avg < best_avg:
                    best_avg = avg
                    best_model = model
            if best_model:
                recommendations.append({
                    "data_category": category,
                    "recommended_model": best_model,
                    "avg_mape": round(best_avg, 3),
                    "sample_count": len(models[best_model]),
                    "alternatives": [
                        {"model": m, "avg_mape": round(mean(ms), 3)}
                        for m, ms in sorted(models.items(), key=lambda x: mean(x[1]))
                        if m != best_model
                    ][:3],
                })

        self._cache = {
            "total_runs": len(runs),
            "successful_runs": sum(len(v) for v in model_stats.values()),
            "failed_runs": len(failure_patterns),
            "model_ranking": model_ranking[:10],
            "recommendations": recommendations,
            "failure_patterns": failure_patterns[-5:],
        }
        return self._cache

    def get_recommendation(self, org: str, data_features: dict) -> dict:
        """根据数据特征获取策略推荐。无历史数据时使用基于规则的默认推荐。"""
        if self._cache is None:
            self.analyze()
        if not self._cache or not self._cache.get("recommendations"):
            return self._rule_based_recommendation(data_features)

        category = self._categorize_data(data_features)

        # 精确匹配
        for rec in self._cache["recommendations"]:
            if rec["data_category"] == category:
                return {
                    "recommended_model": rec["recommended_model"],
                    "avg_mape": rec["avg_mape"],
                    "data_category": category,
                    "alternatives": rec.get("alternatives", []),
                    "reason": f"历史{rec['sample_count']}次相似数据({category})中，{rec['recommended_model']}平均MAPE最低",
                }

        # 模糊匹配：找最接近的类别
        category_parts = set(category.split("+"))
        best_match = None
        best_overlap = 0
        for rec in self._cache["recommendations"]:
            rec_parts = set(rec["data_category"].split("+"))
            overlap = len(category_parts & rec_parts)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = rec

        if best_match:
            return {
                "recommended_model": best_match["recommended_model"],
                "avg_mape": best_match["avg_mape"],
                "data_category": category,
                "matched_category": best_match["data_category"],
                "alternatives": best_match.get("alternatives", []),
                "reason": f"近似匹配({best_match['data_category']})，推荐{best_match['recommended_model']}",
            }

        # 兜底：用全局最佳模型
        if self._cache.get("model_ranking"):
            top = self._cache["model_ranking"][0]
            return {
                "recommended_model": top["model"],
                "avg_mape": top["avg_mape"],
                "data_category": category,
                "reason": f"无精确匹配，使用全局最佳模型{top['model']}(avg MAPE={top['avg_mape']}%)",
            }

        return self._rule_based_recommendation(data_features)

    def _rule_based_recommendation(self, data_features: dict) -> dict:
        """基于规则的默认推荐（冷启动时使用）。"""
        sample_size = data_features.get("sample_size", 0)
        seasonality = data_features.get("seasonality_ratio", 1.0)
        trend = abs(data_features.get("trend", 0))
        recent_mean = data_features.get("recent_mean", 0)
        category = self._categorize_data(data_features)

        if sample_size >= 24 and abs(seasonality - 1.0) > 0.1:
            return {
                "recommended_model": "SARIMAX",
                "data_category": category,
                "alternatives": [
                    {"model": "Holt-Winters", "reason": "备选：指数平滑"},
                    {"model": "seasonal_weighted", "reason": "备选：季节性加权"},
                ],
                "reason": "规则推荐：数据量充足且有季节性，SARIMAX 通常表现最佳",
                "source": "rule_based",
            }
        elif sample_size >= 24:
            return {
                "recommended_model": "Holt-Winters",
                "data_category": category,
                "alternatives": [
                    {"model": "SARIMAX", "reason": "备选：SARIMAX"},
                    {"model": "linear_trend+seasonal", "reason": "备选：线性趋势+季节分解"},
                ],
                "reason": "规则推荐：数据量充足但季节性不明显，Holt-Winters 稳定性好",
                "source": "rule_based",
            }
        elif sample_size >= 12:
            return {
                "recommended_model": "seasonal_weighted",
                "data_category": category,
                "alternatives": [
                    {"model": "Holt-Winters", "reason": "备选：指数平滑"},
                    {"model": "moving_average", "reason": "备选：滑动平均"},
                ],
                "reason": "规则推荐：中等数据量，季节性加权方法鲁棒性好",
                "source": "rule_based",
            }
        else:
            return {
                "recommended_model": "moving_average",
                "data_category": category,
                "alternatives": [
                    {"model": "seasonal_naive", "reason": "备选：季节性朴素法"},
                ],
                "reason": "规则推荐：数据量不足，使用简单稳健方法",
                "source": "rule_based",
            }

    def get_avoid_list(self, org: str, data_features: dict) -> list[str]:
        """获取应避免的模型列表（在相似数据上表现差的模型）。"""
        if self._cache is None:
            self.analyze()
        if not self._cache:
            return []

        category = self._categorize_data(data_features)
        avoid = []

        for rec in self._cache.get("recommendations", []):
            if rec["data_category"] == category:
                for alt in rec.get("alternatives", []):
                    if alt["avg_mape"] > rec["avg_mape"] * 1.5:
                        avoid.append(alt["model"])
        return avoid


_strategy_evolution: StrategyEvolution | None = None


def get_strategy_evolution() -> StrategyEvolution:
    global _strategy_evolution
    if _strategy_evolution is None:
        _strategy_evolution = StrategyEvolution()
    return _strategy_evolution
