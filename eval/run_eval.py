"""评测脚本：跑一批股票，产出可写进简历的量化指标。

用法：
    python eval/run_eval.py                # 固定轮数（默认 2）
    python eval/run_eval.py --rounds 1
    python eval/run_eval.py --ablation     # 轮数消融：0/1/2/3 各跑一遍
    python eval/run_eval.py --repeat 3     # 每只股票重复跑，测结论稳定性
    python eval/run_eval.py --online       # 用真实 DeepSeek（需要 .env 配 Key）

输出：控制台表格 + eval/results.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from data import get_data_service  # noqa: E402
from src.config import load_settings  # noqa: E402
from src.runner import run_case  # noqa: E402


def _load_tickers(path: Path) -> list:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("cases", payload) if isinstance(payload, dict) else payload
    out = []
    for item in raw:
        out.append(item["ticker"] if isinstance(item, dict) else str(item))
    return out


def _report_chars(state) -> int:
    return sum(
        len(str(state.get(field, "")))
        for field in ("technical_report", "fundamental_report", "news_report")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="mini-agents 评测")
    parser.add_argument("--cases", default=str(_ROOT / "eval" / "cases.json"))
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--ablation", action="store_true", help="对辩论轮数做消融")
    parser.add_argument("--repeat", type=int, default=1, help="每只股票重复次数（测稳定性）")
    parser.add_argument("--online", action="store_true", help="使用真实 LLM")
    parser.add_argument("--out", default=str(_ROOT / "eval" / "results.json"))
    args = parser.parse_args()

    tickers = _load_tickers(Path(args.cases))
    trade_date = date.today().isoformat()
    rounds_list = [0, 1, 2, 3] if args.ablation else [args.rounds]

    rows = []
    stability = {}
    for rounds in rounds_list:
        settings = load_settings(offline=not args.online, rounds=rounds)
        provider = get_data_service(settings.data_provider)
        identical = 0
        for ticker in tickers:
            decisions = set()
            elapsed_total = 0.0
            last_state = {}
            for _ in range(max(1, args.repeat)):
                t0 = time.perf_counter()
                last_state = run_case(ticker, trade_date, settings, provider)
                elapsed_total += time.perf_counter() - t0
                decisions.add(str(last_state.get("final_decision", "")))
            if len(decisions) == 1:
                identical += 1
            rows.append(
                {
                    "rounds": rounds,
                    "ticker": ticker,
                    "elapsed_s": round(elapsed_total / max(1, args.repeat), 3),
                    "debate_count": int(last_state.get("debate_count", 0)),
                    "report_chars": _report_chars(last_state),
                    "decision_chars": len(str(last_state.get("final_decision", ""))),
                }
            )
        stability[rounds] = round(identical / max(1, len(tickers)) * 100, 1)

    # ---------------- 控制台汇总 ----------------
    mode = "在线" if args.online else "离线"
    print(f"\n股票池：{len(tickers)} 只   日期：{trade_date}   模式：{mode}   重复：{args.repeat} 次")
    print("\n轮数 | 平均耗时(s) | 平均报告字数 | 平均辩论发言 | 结论稳定率")
    print("-" * 68)
    for rounds in rounds_list:
        subset = [r for r in rows if r["rounds"] == rounds]
        avg_t = sum(r["elapsed_s"] for r in subset) / len(subset)
        avg_c = sum(r["report_chars"] for r in subset) / len(subset)
        avg_d = sum(r["debate_count"] for r in subset) / len(subset)
        print(
            f"  {rounds}  |   {avg_t:6.3f}    |    {avg_c:7.1f}     |"
            f"     {avg_d:4.1f}     |  {stability[rounds]:5.1f}%"
        )

    Path(args.out).write_text(
        json.dumps(
            {"tickers": tickers, "rows": rows, "stability_pct": stability},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n明细已写入：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
