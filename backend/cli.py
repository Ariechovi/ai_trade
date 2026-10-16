"""命令行入口：cd backend && python cli.py --ticker 600519 --offline

对齐 ai_meeting 的根目录约定 —— 入口文件（main.py / cli.py / settings.py /
models.py）都放在应用根，各目录只放被 import 的模块。

本文件只做参数解析与结果打印，**不含任何分析逻辑**：
执行全部走 services.runner，与 HTTP 接口、Streamlit 界面同一条通路。
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from agents.specs import ROLES
from data import get_data_service
from services.runner import run_case, summarize
from settings import load_settings


def main() -> None:
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
    counts = {role: int(state.get(f"{role}_tool_calls", 0)) for role in ROLES}
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
