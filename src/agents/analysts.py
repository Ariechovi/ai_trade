"""分析师节点：三个角色并行执行，各写各的状态字段（互不覆盖）。

每个分析师的工具循环被拆成三个节点 + 一条条件边：
    <role>            —— 调一次 LLM（可能要求调用工具）
    tools_<role>      —— 执行这一轮的工具调用，轮数 +1
    msg_clear_<role>  —— 从私有历史里提取最终报告

为什么每个分析师要有私有的消息字段？三个分析师在同一个超级步里并行执行，
若共用一条消息通道，消息会交错串味。

本文件不含任何角色设定——提示词、工具、字段名都在 agents/specs.py。
这里只负责「怎么执行」，不负责「用什么执行」。

与 TradingAgents-CN 的对照：
    AnalystLLMNode    ≈ builder._create_analyst_wrapper
    AnalystToolsNode  ≈ builder._create_tool_node_wrapper
    AnalystReportNode ≈ builder._create_msg_clear_node
    AnalystRouter     ≈ builder._create_analyst_condition_func

位置
----
上游（谁调用我）：graph.py
下游（我调用谁）：agents/specs.py（角色设定）、agents/base.py（原子操作）、prompts.py（模板）
"""

from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .. import prompts
from .base import call_llm_once, execute_tool_calls, extract_text
from .specs import AnalystSpec

FALLBACK_REPORT = "分析未能完成：工具调用达到上限，结论仍待验证。"


def _allowed_tools(spec: AnalystSpec, tool_map: Dict[str, Any]) -> Dict[str, Any]:
    """只给该角色需要的工具（最小权限原则）。"""
    return {name: tool_map[name] for name in spec.tool_names if name in tool_map}


class AnalystLLMNode:
    """节点一：调一次 LLM，返回它的 AIMessage（可能带 tool_calls）。

    第一次进入时用「系统提示词 + 用户问题」播种私有历史，之后每次追加。
    """

    def __init__(self, spec: AnalystSpec, llm: Any, tool_map: Dict[str, Any]) -> None:
        self.spec = spec
        self.llm = llm
        self.tools = _allowed_tools(spec, tool_map)

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        history: List[Any] = list(state.get(self.spec.messages_field) or [])
        if history:
            response = call_llm_once(self.llm, self.tools, history)
            return {
                self.spec.messages_field: [response],
                "trace": [f"llm:{self.spec.role}"],
            }

        seed = [
            SystemMessage(content=self.spec.system_prompt),
            HumanMessage(content=prompts.analyst_user_prompt(self.spec.label, state)),
        ]
        response = call_llm_once(self.llm, self.tools, seed)
        return {
            self.spec.messages_field: seed + [response],
            "trace": [f"llm:{self.spec.role}"],
        }


class AnalystToolsNode:
    """节点二：执行上一条 AIMessage 里的全部 tool_calls，并把轮数 +1。"""

    def __init__(self, spec: AnalystSpec, tool_map: Dict[str, Any]) -> None:
        self.spec = spec
        self.tools = _allowed_tools(spec, tool_map)

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        history: List[Any] = list(state.get(self.spec.messages_field) or [])
        last = history[-1] if history else None
        calls = list(getattr(last, "tool_calls", None) or [])
        tool_messages = execute_tool_calls(self.tools, calls)
        return {
            self.spec.messages_field: tool_messages,
            self.spec.tool_calls_field: 1,  # operator.add reducer → 累加 1
            "trace": [f"tools:{self.spec.role}({len(calls)})"],
        }


class AnalystReportNode:
    """节点三：从私有历史里取出最终文本，写入报告字段（对应 V3 的 msg_clear 节点）。

    它承担「清场」职责：报告一旦落到独立字段，下游节点就不再需要读私有消息历史。
    """

    def __init__(self, spec: AnalystSpec) -> None:
        self.spec = spec

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        history: List[Any] = list(state.get(self.spec.messages_field) or [])
        text = ""
        for message in reversed(history):
            if isinstance(message, AIMessage) and not (
                getattr(message, "tool_calls", None) or []
            ):
                text = extract_text(message)
                break
        return {
            self.spec.report_field: text or FALLBACK_REPORT,
            "trace": [f"report:{self.spec.role}"],
        }


class AnalystRouter:
    """条件边：决定「继续调工具」还是「收口出报告」。

    终止条件有两个：模型不再要求调工具，或者工具轮数达到上限（防死循环）。
    """

    def __init__(self, spec: AnalystSpec, max_tool_calls: int) -> None:
        self.spec = spec
        self.max_tool_calls = max_tool_calls

    def __call__(self, state: Dict[str, Any]) -> str:
        history: List[Any] = list(state.get(self.spec.messages_field) or [])
        last = history[-1] if history else None
        calls = list(getattr(last, "tool_calls", None) or [])
        used = int(state.get(self.spec.tool_calls_field, 0))
        if calls and used < self.max_tool_calls:
            return f"tools_{self.spec.role}"
        return f"msg_clear_{self.spec.role}"
