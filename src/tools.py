"""工具层：Agent 的「手」。

用 LangChain 的 @tool 把普通函数变成 LLM 可调用的工具。
工具与 Agent 解耦——分析师并不知道数据从哪来，只知道有这些工具可用。

「哪个分析师能用哪些工具」不在这里，而在 agents/specs.py 的 tool_names。
本文件只负责「工具本身怎么定义」。

位置
----
上游（谁调用我）：runner.py、graph.py（构建 tool_map 并传给各节点）
下游（我调用谁）：data/provider.py（数据源）
"""

from __future__ import annotations

from typing import Any, Dict

from langchain_core.tools import tool


def build_tools(provider) -> Dict[str, Any]:
    """根据数据源构建工具集，返回 {工具名: 工具对象}。"""

    @tool
    def get_technicals(ticker: str, days: int = 60) -> str:
        """查询某只股票的技术指标（均线、RSI、趋势）。"""
        return provider.get_technicals(ticker, days)

    @tool
    def get_fundamentals(ticker: str) -> str:
        """查询某只股票的估值与财务指标（PE/PB/ROE/增速）。"""
        return provider.get_fundamentals(ticker)

    @tool
    def get_news(ticker: str, limit: int = 3) -> str:
        """查询某只股票的近期新闻标题。"""
        return provider.get_news(ticker, limit)

    tools = [get_technicals, get_fundamentals, get_news]
    return {t.name: t for t in tools}
