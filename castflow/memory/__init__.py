"""三层记忆模块。

- working: LangGraph 自带 checkpointer，无需自实现
- episodic: 历次完整 run 的向量库（按数据特征召回）
- semantic: Reflexion 提炼出的可复用 lesson 库（按主题召回）
- knowledge: 稳定业务文档知识库（按规则/口径/规范召回）
"""
