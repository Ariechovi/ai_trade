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

import time
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import prompts
from common.observability import emit_event, llm_call_record, tool_call_names
from common.resilience import error_record
from .base import call_llm_once, execute_tool_calls, extract_text, tool_call_id
from .specs import AnalystSpec

FALLBACK_REPORT = "分析未能完成：工具调用达到上限，结论仍待验证。"


def _allowed_tools(spec: AnalystSpec, tool_map: Dict[str, Any]) -> Dict[str, Any]:
    """只给该角色需要的工具（最小权限原则）。"""
    return {name: tool_map[name] for name in spec.tool_names if name in tool_map}


class AnalystLLMNode:
    """节点一：调一次 LLM，返回它的 AIMessage（可能带 tool_calls）。

    第一次进入时用「系统提示词 + 用户问题」播种私有历史，之后每次追加。

    一个关键细节：**工具预算用完后，这次调用不再绑定工具**。
    否则循环会停在一条「带未执行 tool_calls」的 AI 消息上，
    报告提取节点找不到纯文本结论，只能写兜底文案。
    （这正是最早的 run_agent 里那句「强制收口」的作用。）
    """

    def __init__(
        self,
        spec: AnalystSpec,
        llm: Any,
        tool_map: Dict[str, Any],
        max_tool_calls: int,
    ) -> None:
        self.spec = spec
        self.llm = llm
        self.tools = _allowed_tools(spec, tool_map)
        self.max_tool_calls = max(1, int(max_tool_calls))

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        history: List[Any] = list(state.get(self.spec.messages_field) or [])
        used = int(state.get(self.spec.tool_calls_field, 0))
        tools = {} if used >= self.max_tool_calls else self.tools

        # 第一次进入用「系统提示词 + 用户问题」播种；之后接着已有的历史继续问。
        seed: List[Any] = []
        messages_in = history
        if not history:
            seed = [
                SystemMessage(content=self.spec.system_prompt),
                HumanMessage(content=prompts.analyst_user_prompt(self.spec.label, state)),
            ]
            messages_in = seed

        # 先报进度再调模型：真实模型一次调用可能要等 30 秒以上
        emit_event(
            f"{self.spec.label}：模型思考中（第 {used + 1} 轮）", role=self.spec.role
        )
        started = time.perf_counter()
        response = call_llm_once(self.llm, tools, messages_in)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        return {
            self.spec.messages_field: seed + [response],
            "llm_calls": [
                llm_call_record(
                    role=self.spec.role,
                    stage="llm",
                    duration_ms=elapsed_ms,
                    message=response,
                )
            ],
            "trace": [f"llm:{self.spec.role}"],
        }

    @property
    def name(self) -> str:
        return f"llm:{self.spec.role}"

    def degrade(self, state: Dict[str, Any], exc: BaseException) -> Dict[str, Any]:
        """LLM 调用彻底失败 → 注入一条「没有工具调用」的兜底 AI 消息。

        这样路由会照常进入 msg_clear，由它把兜底文案写进报告字段——
        降级逻辑只写一处，不必在路由那里再判一遍。
        """
        fallback = AIMessage(
            content=f"[{self.spec.label}调用失败] 本段分析不可用，其余节点的结论不受影响。"
        )
        return {
            self.spec.messages_field: [fallback],
            "_node_errors": {self.spec.role: error_record("llm", self.name, exc)},
            "trace": [f"degraded:{self.name}"],
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
        names = tool_call_names(calls)
        if names:
            emit_event(
                f"{self.spec.label}：执行工具 {', '.join(names)}",
                role=self.spec.role,
                tools=names,
            )
        tool_messages = execute_tool_calls(self.tools, calls)
        return {
            self.spec.messages_field: tool_messages,
            self.spec.tool_calls_field: 1,  # operator.add reducer → 累加 1
            "trace": [f"tools:{self.spec.role}({len(calls)})"],
        }

    @property
    def name(self) -> str:
        return f"tools:{self.spec.role}"

    def degrade(self, state: Dict[str, Any], exc: BaseException) -> Dict[str, Any]:
        """工具节点失败 → 为每个待执行的调用补一条失败 ToolMessage，并把轮数 +1。

        ⚠️ 轮数必须推进：否则路由器会一直把控制权交回本节点，形成死循环。
        """
        history: List[Any] = list(state.get(self.spec.messages_field) or [])
        last = history[-1] if history else None
        calls = list(getattr(last, "tool_calls", None) or [])
        if not calls:
            calls = [{"id": "call_0"}]
        tool_messages = [
            ToolMessage(content=f"工具执行失败：{exc}", tool_call_id=tool_call_id(call))
            for call in calls
        ]
        return {
            self.spec.messages_field: tool_messages,
            self.spec.tool_calls_field: 1,
            "_node_errors": {f"{self.spec.role}.tools": error_record("tools", self.name, exc)},
            "trace": [f"degraded:{self.name}"],
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

    @property
    def name(self) -> str:
        return f"report:{self.spec.role}"


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
