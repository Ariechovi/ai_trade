"""提示词模板：与角色无关的模板函数，以及辩论 / 经理的固定提示词。

分析师的系统提示词**不在这里**——它跟着角色设定走，放在 agents/specs.py，
这样「读一个分析师」时不必跨文件跳转。

位置
----
上游（谁调用我）：agents/analysts.py、agents/researchers.py、agents/manager.py
下游（我调用谁）：无（纯字符串处理）
"""

from __future__ import annotations

from typing import Any, Dict

BULL_SYSTEM = (
    "你是一名乐观的多头研究员。请基于已有的分析师报告，给出支持买入的最强论据，"
    "并正面回应空头的质疑。观点鲜明，不超过 200 字。"
)

BEAR_SYSTEM = (
    "你是一名审慎的空头研究员。请基于已有的分析师报告，指出最关键的下跌风险，"
    "并反驳多头的乐观假设。观点鲜明，不超过 200 字。"
)

MANAGER_SYSTEM = (
    "你是一名研究经理。请综合三位分析师的报告与多空双方的辩论，"
    "给出最终研究结论：总体倾向、核心逻辑、主要风险、可执行建议。"
)


def analyst_user_prompt(label: str, state: Dict[str, Any]) -> str:
    """分析师的第一条用户消息（label 来自 agents/specs.py）。"""
    return (
        f"请分析股票 {state.get('ticker')}（分析日期 {state.get('trade_date')}）。\n"
        f"你的角色是：{label}。\n"
        "请先调用可用工具获取数据，再给出分析结论。"
    )


def _render_reports(state: Dict[str, Any]) -> str:
    return (
        f"【技术面】{state.get('technical_report', '（无）')}\n"
        f"【基本面】{state.get('fundamental_report', '（无）')}\n"
        f"【消息面】{state.get('news_report', '（无）')}"
    )


def _render_history(state: Dict[str, Any]) -> str:
    history = state.get("debate_history") or []
    if not history:
        return "（暂无发言）"
    return "\n".join(f"- {item.get('speaker')}：{item.get('content')}" for item in history)


def debate_user_prompt(state: Dict[str, Any], side: str) -> str:
    return (
        f"股票：{state.get('ticker')}\n\n"
        f"分析师报告：\n{_render_reports(state)}\n\n"
        f"当前辩论记录：\n{_render_history(state)}\n\n"
        f"你是【{side}】一方，这是第 {int(state.get('debate_count', 0)) + 1} 次发言。"
    )


def manager_user_prompt(state: Dict[str, Any]) -> str:
    return (
        f"股票：{state.get('ticker')}\n\n"
        f"分析师报告：\n{_render_reports(state)}\n\n"
        f"多空辩论：\n{_render_history(state)}\n\n"
        "请给出最终研究结论。"
    )
