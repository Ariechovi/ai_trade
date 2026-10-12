"""分析师节点：三个角色并行执行，各写各的状态字段（互不覆盖）。

每个分析师的工具循环被拆成三个节点 + 一条条件边：
    <role>            —— 调一次 LLM（可能要求调用工具）
    tools_<role>      —— 执行这一轮的工具调用，轮数 +1
    msg_clear_<role>  —— 从私有历史里提取最终报告

为什么每个分析师要有私有的消息字段？三个分析师在同一个超级步里并行执行，
若共用一条消息通道，消息会交错串味。
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .. import prompts
from ..tools import AGENT_TOOLS
from .base import call_llm_once, execute_tool_calls, extract_text

ANALYST_ROLES = ("technical", "fundamental", "news")

# 每个角色对应的三个状态字段：私有消息历史 / 工具轮数 / 报告
ROLE_FIELDS: Dict[str, Dict[str, str]] = {
    "technical": {
        "messages": "technical_messages",
        "tool_calls": "technical_tool_calls",
        "report": "technical_report",
    },
    "fundamental": {
        "messages": "fundamental_messages",
        "tool_calls": "fundamental_tool_calls",
        "report": "fundamental_report",
    },
    "news": {
        "messages": "news_messages",
        "tool_calls": "news_tool_calls",
        "report": "news_report",
    },
}

FALLBACK_REPORT = "分析未能完成：工具调用达到上限，结论仍待验证。"


def _allowed_tools(role: str, tool_map: Dict[str, Any]) -> Dict[str, Any]:
    """只给该角色需要的工具（最小权限原则）。"""
    return {name: tool_map[name] for name in AGENT_TOOLS.get(role, []) if name in tool_map}


# ============================================================================
# 与 TradingAgents-CN 的对照关系：
#   本文件 make_analyst_llm_node    ≈ V3 builder._create_analyst_wrapper
#   本文件 make_analyst_tools_node  ≈ V3 builder._create_tool_node_wrapper
#   本文件 make_analyst_report_node ≈ V3 builder._create_msg_clear_node
#   本文件 make_analyst_router      ≈ V3 builder._create_analyst_condition_func
# ============================================================================


def make_analyst_llm_node(
    role: str,
    llm_factory: Callable[[str], Any],
    tool_map: Dict[str, Any],
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """节点一：只调一次 LLM，返回它的 AIMessage（可能带 tool_calls）。"""
    fields = ROLE_FIELDS[role]
    allowed = _allowed_tools(role, tool_map)
    llm = llm_factory(role)

    def node(state: Dict[str, Any]) -> Dict[str, Any]:
        history = list(state.get(fields["messages"]) or [])
        if history:
            # 已有历史：接着往下问
            response = call_llm_once(llm, allowed, history)
            return {fields["messages"]: [response], "trace": [f"llm:{role}"]}

        # 第一次进入：用系统提示词 + 用户问题播种私有历史
        seed = [
            SystemMessage(content=prompts.ANALYST_SYSTEM[role]),
            HumanMessage(content=prompts.analyst_user_prompt(role, state)),
        ]
        response = call_llm_once(llm, allowed, seed)
        return {fields["messages"]: seed + [response], "trace": [f"llm:{role}"]}

    node.__name__ = f"{role}_llm_node"
    return node


def make_analyst_tools_node(
    role: str,
    tool_map: Dict[str, Any],
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """节点二：执行上一条 AIMessage 里的全部 tool_calls，并把轮数 +1。"""
    fields = ROLE_FIELDS[role]
    allowed = _allowed_tools(role, tool_map)

    def node(state: Dict[str, Any]) -> Dict[str, Any]:
        history = list(state.get(fields["messages"]) or [])
        last = history[-1] if history else None
        calls = list(getattr(last, "tool_calls", None) or [])
        tool_messages = execute_tool_calls(allowed, calls)
        return {
            fields["messages"]: tool_messages,
            fields["tool_calls"]: 1,  # operator.add reducer → 累加 1
            "trace": [f"tools:{role}({len(calls)})"],
        }

    node.__name__ = f"{role}_tools_node"
    return node


def make_analyst_report_node(role: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """节点三：从私有历史里取出最终文本，写入报告字段（对应 V3 的 msg_clear 节点）。

    它承担「清场」职责：报告一旦落到独立字段，下游节点就不再需要读私有消息历史。
    """
    fields = ROLE_FIELDS[role]

    def node(state: Dict[str, Any]) -> Dict[str, Any]:
        history = list(state.get(fields["messages"]) or [])
        text = ""
        for message in reversed(history):
            if isinstance(message, AIMessage) and not (
                getattr(message, "tool_calls", None) or []
            ):
                text = extract_text(message)
                break
        if not text:
            text = FALLBACK_REPORT
        return {fields["report"]: text, "trace": [f"report:{role}"]}

    node.__name__ = f"{role}_report_node"
    return node


def make_analyst_router(role: str, max_tool_calls: int) -> Callable[[Dict[str, Any]], str]:
    """条件边：决定「继续调工具」还是「收口出报告」。

    终止条件有两个：模型不再要求调工具，或者工具轮数达到上限（防死循环）。
    """
    fields = ROLE_FIELDS[role]
    tools_node = f"tools_{role}"
    report_node = f"msg_clear_{role}"

    def route(state: Dict[str, Any]) -> str:
        history = list(state.get(fields["messages"]) or [])
        last = history[-1] if history else None
        calls = list(getattr(last, "tool_calls", None) or [])
        used = int(state.get(fields["tool_calls"], 0))
        if calls and used < max_tool_calls:
            return tools_node
        return report_node

    return route
