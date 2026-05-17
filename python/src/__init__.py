"""
CastFlow Python 后端

职责：
1. 提供 MySQL 查询接口（被 TypeScript DBReader 调用）
2. 提供指标计算（被 EvaluationAgent 调用）
3. 提供安全代码执行沙箱（被 ExecutionAgent 调用）

与 CastClaw 的 python/ 包类似，作为 ML 执行的后端。
"""

__version__ = "0.1.0"
