"""Agent 基座：一个「LLM ↔ 工具」的循环（ReAct 的最小实现）。

整个项目里「Agent」的本质就是下面这 30 行：
    调 LLM → 如果它要求调用工具 → 执行工具、把结果喂回去 → 再调 LLM → ……
           → 如果它不再调用工具 → 返回最终文本

max_iterations 是防死循环的保险丝。
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


def run_agent(
    llm: Any,
    tool_map: Dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    max_iterations: int = 3,
) -> str:
    """执行一次完整的 Agent 推理，返回最终文本。"""
    tools = list(tool_map.values())
    model = llm.bind_tools(tools) if tools else llm
    messages: List[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    for _ in range(max(1, int(max_iterations))):
        response = model.invoke(messages)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return extract_text(response)

        for call in tool_calls:
            name = _field(call, "name", "")
            args = _field(call, "args", {}) or {}
            call_id = _field(call, "id", "call_0") or "call_0"
            if name not in tool_map:
                messages.append(ToolMessage(content=f"未知工具：{name}", tool_call_id=call_id))
                continue
            try:
                result = tool_map[name].invoke(args)
            except Exception as exc:  # 单个工具失败不应中断整个流程
                result = f"工具执行失败：{exc}"
            messages.append(ToolMessage(content=str(result), tool_call_id=call_id))

    # 兜底：不再绑定工具，强制模型收口，避免无限循环
    messages.append(HumanMessage(content="请基于以上信息直接给出最终结论，不要再调用工具。"))
    return extract_text(llm.invoke(messages))
