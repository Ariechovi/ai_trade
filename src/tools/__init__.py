"""工具层：Agent 的「手」。

对应 TradingAgents-CN 的 `core/tools/implementations/`，按**分析师域**分文件：

    technical.py    技术面工具    ≈ implementations/technical/
    fundamental.py  基本面工具    ≈ implementations/fundamentals/
    news.py         消息面工具    ≈ implementations/news/ + social/

V3 给每个分析师的工具数是 3~8 个（market=5、news=5、fundamentals=8）。
本项目按业务需要配 4~5 个——**每个工具都是一次真实的计算或取数**，
不是「换个名字再包一层」。

工具多起来之后，图上的工具循环才会真正来回走（见 src/graph.py 的回边）。
这也是「为什么需要多个分析师」这个问题的答案：他们的工具集不同。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .fundamental import build_fundamental_tools
from .news import build_news_tools
from .technical import build_technical_tools

__all__ = ["build_tools"]


def build_tools(service) -> Dict[str, Any]:
    """按数据服务构建全部工具，返回 {工具名: 工具对象}。"""
    tools: List[Any] = []
    tools += build_technical_tools(service)
    tools += build_fundamental_tools(service)
    tools += build_news_tools(service)
    return {tool.name: tool for tool in tools}
