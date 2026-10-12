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

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # ---------- 输入 ----------
    ticker: str
    trade_date: str
    max_rounds: int

    # ---------- 分析师产出（三个节点并行写入，各写各的字段，互不覆盖）----------
    technical_report: str
    fundamental_report: str
    news_report: str

    # ---------- 图级工具循环：每个分析师私有的消息历史 ----------
    # 为什么必须"私有"？三个分析师在同一个超级步里并行执行，
    # 如果都往共享的 messages 通道写，消息会交错串味（V3 里对应 _market_messages 等字段）。
    # add_messages 是 LangGraph 官方 reducer：按消息 id 追加/去重。
    technical_messages: Annotated[List[Any], add_messages]
    fundamental_messages: Annotated[List[Any], add_messages]
    news_messages: Annotated[List[Any], add_messages]

    # ---------- 工具轮数计数（operator.add 累加，用于防止死循环）----------
    technical_tool_calls: Annotated[int, operator.add]
    fundamental_tool_calls: Annotated[int, operator.add]
    news_tool_calls: Annotated[int, operator.add]

    # ---------- 辩论：reducer 让循环可以累加 ----------
    debate_history: Annotated[List[Dict[str, Any]], operator.add]
    debate_count: Annotated[int, operator.add]
    last_speaker: str

    # ---------- 最终产出 ----------
    final_decision: str

    # ---------- 可观测性：记录走过的节点，方便演示"图跑了哪些步" ----------
    trace: Annotated[List[str], operator.add]
