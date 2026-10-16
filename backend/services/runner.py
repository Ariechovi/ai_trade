"""执行通路。

这里是 CLI / Streamlit / HTTP 共用的**唯一**执行通路：
    stream_case()   流式跑一次分析（带节点与进度回调）
    run_case()      跑一次分析（不带回调）
    summarize()     把观测数据汇总成指标（唯一的聚合口）

命令行入口在 backend/cli.py；HTTP 入口在 backend/main.py。
两者都只是调用本文件，不复制任何逻辑。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .graph import build_graph
from .llm import build_llm
from tools import build_tools


def stream_case(
    ticker: str,
    trade_date: str,
    settings,
    data_service,
    on_node: Optional[Callable[[str, Any], None]] = None,
    on_event: Optional[Callable[[Any], None]] = None,
) -> Dict[str, Any]:
    """流式跑一次分析：每完成一个节点回调 on_node(节点名, 该节点的更新)，返回最终状态。

    Streamlit 页面用它做实时进度；CLI 也走这条通路——
    这样「实时进度」和「最终结果」不会变成两套逻辑。
    """
    tool_map = build_tools(data_service)

    def llm_factory(role: str):
        return build_llm(settings, role=role, ticker=ticker)

    app = build_graph(llm_factory=llm_factory, tool_map=tool_map, settings=settings)
    initial: Dict[str, Any] = {
        "ticker": ticker,
        "trade_date": trade_date,
        "max_rounds": settings.max_debate_rounds,
        "debate_history": [],
        "debate_count": 0,
        "trace": [],
    }
    final_state: Dict[str, Any] = dict(initial)
    # 三种流模式各司其职：
    #   updates —— 节点**完成**时的事件（用来打勾）
    #   custom  —— 节点内部主动推的进度（"正在调模型 / 正在执行工具"）
    #   values  —— 完整状态快照，最后一块就是最终状态
    for mode, payload in app.stream(
        initial, stream_mode=["updates", "values", "custom"]
    ):
        if mode == "updates" and isinstance(payload, dict):
            for node, update in payload.items():
                if on_node is not None and not str(node).startswith("__"):
                    on_node(str(node), update)
        elif mode == "custom":
            if on_event is not None:
                on_event(payload)
        elif mode == "values" and isinstance(payload, dict):
            final_state = payload
    return final_state


def run_case(ticker: str, trade_date: str, settings, provider) -> Dict[str, Any]:
    """跑一次完整分析（不带进度回调），返回最终状态。"""
    return stream_case(ticker, trade_date, settings, provider)


def summarize(state: Dict[str, Any]) -> Dict[str, Any]:
    """把状态里的观测数据汇总成指标。CLI 与 Streamlit 共用，避免两套统计口径。"""
    calls = list(state.get("llm_calls") or [])
    timings = list(state.get("node_timings") or [])
    by_role: Dict[str, Dict[str, Any]] = {}
    for call in calls:
        bucket = by_role.setdefault(
            str(call.get("role") or "?"),
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "duration_ms": 0.0,
            },
        )
        bucket["calls"] += 1
        bucket["input_tokens"] += int(call.get("input_tokens") or 0)
        bucket["output_tokens"] += int(call.get("output_tokens") or 0)
        bucket["total_tokens"] += int(call.get("total_tokens") or 0)
        bucket["duration_ms"] = round(
            float(bucket["duration_ms"]) + float(call.get("duration_ms") or 0), 1
        )
    return {
        "llm_calls": calls,
        "node_timings": timings,
        "by_role": by_role,
        "llm_call_count": len(calls),
        "total_tokens": sum(int(c.get("total_tokens") or 0) for c in calls),
        "total_input_tokens": sum(int(c.get("input_tokens") or 0) for c in calls),
        "total_output_tokens": sum(int(c.get("output_tokens") or 0) for c in calls),
        "llm_duration_ms": round(sum(float(c.get("duration_ms") or 0) for c in calls), 1),
        # 注意：并行节点会重叠，所以这是「累计节点耗时」，不是墙上时间
        "node_duration_ms": round(sum(float(t.get("duration_ms") or 0) for t in timings), 1),
        "degraded": sorted((state.get("_node_errors") or {}).keys()),
    }
