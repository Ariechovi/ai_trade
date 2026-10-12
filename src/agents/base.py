"""Agent 基座：LLM 调用与工具执行的最小原子操作。

设计原则：**Agent 内部不做循环。**

    循环是图的责任——「调 LLM」和「执行工具」各自是图上的一个节点，
    由条件边把它们串起来（见 src/graph.py）。这样每一次工具调用都能
    被观测、中断、单独重试，也才有可能插入人工审批。

三个原子操作：
    complete()           —— 无工具场景：一次调用直接拿文本（研究员 / 经理用）
    call_llm_once()      —— 调一次 LLM，返回 AIMessage（可能带 tool_calls）
    execute_tool_calls() —— 执行 tool_calls，返回 ToolMessage 列表
"""

from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """兼容 dict 与对象两种形式的 tool_call 字段访问。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def extract_text(message: Any) -> str:
    """从模型响应里取出纯文本（兼容 content 为字符串或分块列表）。"""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    return str(content).strip()


def complete(llm: Any, system_prompt: str, user_prompt: str) -> str:
    """无工具场景下的一次性调用：直接返回文本。

    研究员与经理不需要工具，所以它们只需要这一步，不需要任何循环。
    """
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return extract_text(response)


def call_llm_once(llm: Any, tool_map: Dict[str, Any], messages: List[Any]) -> Any:
    """只调用一次 LLM（绑定工具），返回 AIMessage。不做任何循环。"""
    tools = list(tool_map.values())
    model = llm.bind_tools(tools) if tools else llm
    return model.invoke(messages)


def execute_tool_calls(tool_map: Dict[str, Any], tool_calls: List[Any]) -> List[ToolMessage]:
    """执行 AIMessage 里的全部 tool_calls，返回对应的 ToolMessage 列表。

    tool_call_id 必须与请求严格配对，否则下一次调用 LLM 会直接报错。
    （LangGraph 预置的 ToolNode 做的就是这件事，这里手写一遍是为了看清机制。）
    """
    results: List[ToolMessage] = []
    for call in tool_calls or []:
        name = _field(call, "name", "")
        args = _field(call, "args", {}) or {}
        call_id = _field(call, "id", "call_0") or "call_0"
        if name not in tool_map:
            results.append(ToolMessage(content=f"未知工具：{name}", tool_call_id=call_id))
            continue
        try:
            output = tool_map[name].invoke(args)
        except Exception as exc:  # 单个工具失败不应中断整个流程
            output = f"工具执行失败：{exc}"
        results.append(ToolMessage(content=str(output), tool_call_id=call_id))
    return results
