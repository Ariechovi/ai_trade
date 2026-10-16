"""命令行入口：python -m src.runner --ticker 600519"""

from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any, Callable, Dict, Optional

from .config import load_settings
from .graph import build_graph
from .llm import build_llm
from .tools import build_tools
from .agents.specs import ROLES


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


def main() -> None:
    from data import get_data_service

    parser = argparse.ArgumentParser(description="mini-agents：多智能体投研系统（最小实现）")
    parser.add_argument("--ticker", default="600519", help="股票代码，例如 600519")
    parser.add_argument("--date", default=date.today().isoformat(), help="分析日期 YYYY-MM-DD")
    parser.add_argument("--rounds", type=int, default=None, help="辩论轮数（覆盖 .env）")
    parser.add_argument("--provider", default=None, help="数据源 mock | akshare")
    parser.add_argument("--offline", action="store_true", help="强制使用离线桩模型")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出完整状态")
    args = parser.parse_args()

    settings = load_settings(offline=True if args.offline else None, rounds=args.rounds)
    provider = get_data_service(args.provider or settings.data_provider)
    sources = "+".join(source.name for source in provider.sources)

    print(
        f"[mini-agents] ticker={args.ticker} rounds={settings.max_debate_rounds} "
        f"offline={settings.offline} sources={sources}"
    )
    state = run_case(args.ticker, args.date, settings, provider)

    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2, default=str))
        return

    print("\n" + "=" * 60)
    print(f"节点执行轨迹：{state.get('trace')}")
    print("=" * 60)
    print("\n【技术面】\n" + str(state.get("technical_report", "")))
    print("\n【基本面】\n" + str(state.get("fundamental_report", "")))
    print("\n【消息面】\n" + str(state.get("news_report", "")))
    print(f"\n【辩论】共 {int(state.get('debate_count', 0))} 次发言")
    for item in state.get("debate_history") or []:
        print(f"  - {item.get('speaker')}：{item.get('content')}")
    counts = {
        role: int(state.get(f"{role}_tool_calls", 0))
        for role in ROLES
    }
    print(f"\n【工具轮数】{counts}")
    errors = state.get("_node_errors") or {}
    if errors:
        print("\n【降级节点】")
        for key, info in errors.items():
            print(f"  - {key}: [{info.get('stage')}] {info.get('type')}: {info.get('message')}")

    metrics = summarize(state)
    print(
        f"\n【LLM 调用】{metrics['llm_call_count']} 次，token 合计 {metrics['total_tokens']}"
        f"（输入 {metrics['total_input_tokens']} / 输出 {metrics['total_output_tokens']}）"
    )
    for role, bucket in metrics["by_role"].items():
        print(
            f"  - {role:<10} {bucket['calls']} 次 | token {bucket['total_tokens']:>7} "
            f"| 耗时 {bucket['duration_ms'] / 1000:.1f}s"
        )
    if not metrics["total_tokens"]:
        print("  （离线桩模型没有 token 用量，换成真实模型即可看到）")
    print(f"【累计节点耗时】{metrics['node_duration_ms'] / 1000:.1f}s（并行节点会重叠）")

    print("\n【最终结论】\n" + str(state.get("final_decision", "")))


if __name__ == "__main__":
    main()
