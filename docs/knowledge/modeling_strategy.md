# 建模策略知识

## 候选模型优先级

月度区县负荷样本通常较短，应优先使用稳定模型：

1. seasonal naive / 去年同月基线
2. trend seasonal blend / 近期趋势与季节值融合
3. SARIMAX / ARIMA / ETS 等统计模型
4. LightGBM 等机器学习模型只作为补充，不应默认优先

## 选择原则

- 每个候选必须先在 pre-target 历史上做 validation split。
- 如果复杂模型 validation MAPE 没有明显优于 baseline，应优先选择 baseline。
- 高阶 SARIMAX 在短序列上容易收敛失败或过拟合，失败时应降低阶数或回退到稳定基线。
- 不要为了追求复杂度牺牲稳定性，最终目标是降低真实目标月 MAPE。

## 失败恢复

- 同一类错误重复出现时，应调用 Reflector 总结根因。
- 如果候选模型连续 MAPE 过高，应把失败模型族加入 avoid_models。
- 发现稳定经验后应调用 `save_lesson` 写入 semantic memory。
