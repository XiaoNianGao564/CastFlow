# 电力负荷预测业务规则

## 数据口径

- 预测对象是区县级月度电力负荷。
- 当前业务默认使用 `expect_type=1`，表示区民用电。
- 区县名称必须以数据库真实中文名称为准，例如「耀州」「印王」「宜君」「客服」「新区」。
- 不允许使用 `TC01`、`TC02` 这类臆造区县名替代真实区县。

## 防数据泄漏

- 预测目标月时，训练历史必须严格早于目标月。
- 调用 `load_history` 时必须传入 `before_month=target_month`。
- `load_actual` 只能在 Verifier 审查通过后，用于最终 MAPE 评估。
- Coder 内部 validation 只能使用目标月之前的 holdout 月份，不能使用目标月真实值。

## 评估规则

- 主指标为 MAPE。
- 单月预测输出必须包含 JSON 字段 `predictions`。
- `evaluate_mape` 的输入必须来自模型预测值和 `load_actual` 返回的真实值。
- 达到目标 MAPE 或到达最大迭代次数后必须调用 `finalize` 收尾。
