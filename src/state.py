"""全局状态：多智能体系统里，状态就是唯一的数据总线。

最关键的是 `Annotated[类型, reducer]` 里的 reducer（归约器）——
它决定当多个节点（或循环中的同一节点被反复执行）写入同一个字段时如何合并：

  - 不写 reducer  → 后写覆盖先写（last write wins）
  - operator.add  → 新值"追加/累加"到旧值上

辩论循环能一轮轮转下去，靠的就是 debate_count 这个累加器；
辩论历史能一段段累积，靠的是 debate_history 的追加器。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    # ---------- 输入 ----------
    ticker: str
    trade_date: str
    max_rounds: int

    # ---------- 分析师产出（三个节点并行写入，各写各的字段，互不覆盖）----------
    technical_report: str
    fundamental_report: str
    news_report: str

    # ---------- 辩论：reducer 让循环可以累加 ----------
    debate_history: Annotated[List[Dict[str, Any]], operator.add]
    debate_count: Annotated[int, operator.add]
    last_speaker: str

    # ---------- 最终产出 ----------
    final_decision: str

    # ---------- 可观测性：记录走过的节点，方便演示"图跑了哪些步" ----------
    trace: Annotated[List[str], operator.add]
