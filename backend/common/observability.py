"""可观测性：给每个节点记耗时，给每次 LLM 调用记 token。

对应 TradingAgents-CN 的 `core/agents/execution_trace_recorder.py`——
V3 把 prompt、响应、工具调用、耗时都写进执行轨迹，落库到 `workflow_agent_invocations`。

这里的做法更轻：观测数据写进 state 的两个累加字段（`node_timings` / `llm_calls`），
`runner.py` 汇总打印，Streamlit 页面直接读。

位置
----
上游（谁调用我）：graph.py（TimedNode 包节点）、agents/*（llm_call_record）
下游（我调用谁）：无（只依赖标准库）
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def emit_event(text: str, **extra: Any) -> None:
    """往 LangGraph 的 `custom` 流里推一条进度事件。

    为什么要这个：`stream_mode="updates"` 只在节点**完成时**才发事件，
    而一次 akshare 取数或一次 LLM 调用可能要等 10~30 秒——
    这段时间界面上什么都没变，看起来就像"卡住了"。

    不在流式上下文里调用时（例如 selfcheck 直接 invoke）静默忽略。
    """
    payload: Dict[str, Any] = {"type": "progress", "text": text, **extra}
    try:
        from langgraph.config import get_stream_writer

        get_stream_writer()(payload)
    except Exception:
        pass


def tool_call_names(tool_calls: List[Any]) -> List[str]:
    """从 AIMessage 的 tool_calls 里取出工具名（兼容 dict 与对象两种形式）。"""
    names: List[str] = []
    for call in tool_calls or []:
        name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
        names.append(str(name or "?"))
    return names


def extract_usage(message: Any) -> Dict[str, int]:
    """从模型响应里取出 token 用量。

    不同厂商字段位置不同，这里做两级兼容：
      1. LangChain 标准的 `usage_metadata`（OpenAI / DeepSeek / Anthropic 适配层都会填）
      2. OpenAI 原始格式的 `response_metadata['token_usage']`
    取不到时返回全 0（离线桩模型就是这种情况），不要抛错。
    """
    usage = getattr(message, "usage_metadata", None) or {}
    if usage:
        return {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
    meta = getattr(message, "response_metadata", None) or {}
    raw = meta.get("token_usage") or meta.get("usage") or {}
    return {
        "input_tokens": int(raw.get("prompt_tokens") or 0),
        "output_tokens": int(raw.get("completion_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def llm_call_record(
    *, role: str, stage: str, duration_ms: float, message: Any
) -> Dict[str, Any]:
    """把一次 LLM 调用整理成一条观测记录。"""
    return {
        "role": role,
        "stage": stage,
        "duration_ms": round(float(duration_ms), 1),
        **extract_usage(message),
    }


class TimedNode:
    """给节点套一层耗时统计。

    用法：`timed(guarded(node))`——**计时在外层**，所以耗时会包含重试等待时间
    （这是有意的：如果一个节点耗时 30 秒，你希望知道是因为它重试过）。

    它必须放在 `GuardedNode` 外面：护栏需要被包节点提供 `degrade()`，
    而计时只需要 `__call__` 和 `name`。
    """

    def __init__(self, node: Any, label: Optional[str] = None) -> None:
        self.node = node
        self.label = label

    @property
    def name(self) -> str:
        return self.label or str(
            getattr(self.node, "name", type(self.node).__name__)
        )

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        result = self.node(state)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if isinstance(result, dict):
            timings = list(result.get("node_timings") or [])
            timings.append({"node": self.name, "duration_ms": round(elapsed_ms, 1)})
            result["node_timings"] = timings
        return result
