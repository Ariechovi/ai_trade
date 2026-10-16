"""分析师注册表：一个分析师的全部设定，集中成一条记录。

这个文件回答的问题是：「技术面分析师到底是什么？」
答案就在 ANALYSTS 里的一条记录中——提示词、工具、状态字段全在一屏之内。

为什么要单独有这个文件？
    之前这些设定散落在 5 个地方（角色列表、字段映射、工具映射、提示词、
    state 里的字段声明），想知道一个分析师的全貌要在 4 个文件之间跳。
    收成显式声明之后：读一个分析师 = 读一条记录；加一个分析师 = 加一条记录。

⚠️ 一个躲不掉的重复：state.py 里仍然要静态声明这 6 个字段。
   这是 LangGraph 对 TypedDict 的约束。要彻底消除它，只能像
    TradingAgents-CN 那样做动态 State 生成（core/state/builder.py）。

位置
----
上游（谁调用我）：agents/analysts.py、graph.py、runner.py、scripts/selfcheck.py
下游（我调用谁）：无（纯数据，不依赖任何模块）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from prompts import ANALYST_PROMPTS


@dataclass(frozen=True)
class AnalystSpec:
    """一个分析师的全部设定。

    提示词就近放在这里，是为了让「读一个分析师」不必再跳到 prompts.py。
    """

    role: str                    # 内部标识，如 "technical"
    label: str                   # 显示名，如 "技术面分析师"
    system_prompt: str           # 系统提示词
    tool_names: Tuple[str, ...]  # 允许使用的工具（最小权限原则）
    report_field: str            # 报告写入的状态字段
    messages_field: str          # 私有消息历史字段
    tool_calls_field: str        # 工具轮数字段


ANALYSTS: Tuple[AnalystSpec, ...] = (
    AnalystSpec(
        role="technical",
        label="技术面分析师",
        system_prompt=ANALYST_PROMPTS["technical"],
        tool_names=(
            "get_price_overview",
            "compute_indicators",
            "detect_crossover_signals",
            "find_support_resistance",
            "analyze_volume",
            "detect_kline_patterns",
        ),
        report_field="technical_report",
        messages_field="technical_messages",
        tool_calls_field="technical_tool_calls",
    ),
    AnalystSpec(
        role="fundamental",
        label="基本面分析师",
        system_prompt=ANALYST_PROMPTS["fundamental"],
        tool_names=(
            "get_financial_statements",
            "analyze_growth",
            "assess_financial_health",
            "estimate_valuation",
            "compare_peers",
            "analyze_dupont",
            "check_cashflow_quality",
            "analyze_solvency",
        ),
        report_field="fundamental_report",
        messages_field="fundamental_messages",
        tool_calls_field="fundamental_tool_calls",
    ),
    AnalystSpec(
        role="news",
        label="消息面分析师",
        system_prompt=ANALYST_PROMPTS["news"],
        tool_names=(
            "get_recent_news",
            "filter_news",
            "score_news_sentiment",
            "get_market_environment",
        ),
        report_field="news_report",
        messages_field="news_messages",
        tool_calls_field="news_tool_calls",
    ),
)

# 便捷映射：按 role 取设定
ANALYST_BY_ROLE: Dict[str, AnalystSpec] = {spec.role: spec for spec in ANALYSTS}

# 便捷元组：只需要角色名时用（如遍历、打印）
ROLES: Tuple[str, ...] = tuple(spec.role for spec in ANALYSTS)
