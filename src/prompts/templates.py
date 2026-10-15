"""用户消息模板：与角色无关的拼装函数。

角色设定在 analysts.py / debaters.py / manager.py 里；这里只负责把状态拼成用户消息。
"""

from __future__ import annotations

from typing import Any, Dict


def analyst_user_prompt(label: str, state: Dict[str, Any]) -> str:
    """分析师的第一条用户消息（label 来自 agents/specs.py）。"""
    return (
        f"请分析股票 {state.get('ticker')}（分析日期 {state.get('trade_date')}）。\n"
        f"你的角色是：{label}。\n"
        "请先调用可用工具获取数据，再按职责契约里规定的输出结构给出结论。"
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
        "请按要求发言，并回应对方已经提出的具体质疑。"
    )


def manager_user_prompt(state: Dict[str, Any]) -> str:
    return (
        f"股票：{state.get('ticker')}\n\n"
        f"分析师报告：\n{_render_reports(state)}\n\n"
        f"多空辩论：\n{_render_history(state)}\n\n"
        "请按职责契约里规定的输出结构给出最终研究结论。"
    )
