"""命令行入口：python -m src.runner --ticker 600519"""

from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any, Dict

from .config import load_settings
from .graph import build_graph
from .llm import build_llm
from .tools import build_tools


def run_case(ticker: str, trade_date: str, settings, provider) -> Dict[str, Any]:
    """跑一次完整分析，返回最终状态。"""
    tool_map = build_tools(provider)

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
    return app.invoke(initial)


def main() -> None:
    from data.provider import get_provider

    parser = argparse.ArgumentParser(description="mini-agents：多智能体投研系统（最小实现）")
    parser.add_argument("--ticker", default="600519", help="股票代码，例如 600519")
    parser.add_argument("--date", default=date.today().isoformat(), help="分析日期 YYYY-MM-DD")
    parser.add_argument("--rounds", type=int, default=None, help="辩论轮数（覆盖 .env）")
    parser.add_argument("--provider", default=None, help="数据源 mock | akshare")
    parser.add_argument("--offline", action="store_true", help="强制使用离线桩模型")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出完整状态")
    args = parser.parse_args()

    settings = load_settings(offline=True if args.offline else None, rounds=args.rounds)
    provider = get_provider(args.provider or settings.data_provider)

    print(
        f"[mini-agents] ticker={args.ticker} rounds={settings.max_debate_rounds} "
        f"offline={settings.offline} provider={provider.name}"
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
    print("\n【最终结论】\n" + str(state.get("final_decision", "")))


if __name__ == "__main__":
    main()
