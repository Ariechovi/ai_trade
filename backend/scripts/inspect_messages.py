"""消息级观测：把「模型调用工具」的每一条消息完整打印出来。

用法：
    python scripts/inspect_messages.py                       # 离线桩模型
    python scripts/inspect_messages.py --online              # 用真实模型（DeepSeek）
    python scripts/inspect_messages.py --full                # 内容不截断
    python scripts/inspect_messages.py --ticker 000001 --rounds 1
    python scripts/inspect_messages.py --online --no-steps   # 只跑一遍，省一半调用

它做两件事：
  ① 逐步打印每个节点向 state 新增了哪些消息（谁写的、写了什么）；
  ② 汇总每个分析师的完整消息历史，并把 tool_call 与工具返回按 id 配对展示。

实现说明：为了拿到「最终 state」，脚本默认跑两遍图——第一遍观察过程，第二遍取结果。
离线模式下没有成本；用 --online 时可以加 --no-steps 只跑一遍。

位置
----
这是一个「阅读辅助工具」：它把图级工具循环的内部消息暴露出来，
配合 docs/architecture.md 的阅读地图使用。
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any, Dict, List

from agents.specs import ROLES
from settings import load_settings
from data import get_data_service
from services.graph import build_graph
from services.llm import build_llm
from tools import build_tools

PREVIEW = 150


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def role_of(message: Any) -> str:
    return str(getattr(message, "type", type(message).__name__))


def tool_calls_of(message: Any) -> List[Dict[str, Any]]:
    return list(getattr(message, "tool_calls", None) or [])


def clip(text: Any, full: bool) -> str:
    raw = str(text).replace("\n", "\\n")
    if full or len(raw) <= PREVIEW:
        return raw
    return raw[:PREVIEW] + f"…(共 {len(raw)} 字)"


def content_of(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):  # 有些模型把 content 拆成 block 列表
        return " ".join(str(item) for item in content)
    return str(content or "")


def call_field(call: Any, name: str, default: Any = None) -> Any:
    if isinstance(call, dict):
        return call.get(name, default)
    return getattr(call, name, default)


def describe(message: Any, index: int, full: bool, indent: str = "     ") -> List[str]:
    """把一条消息渲染成若干行文本。"""
    role = role_of(message)
    mid = getattr(message, "id", None)
    short_id = f"{str(mid)[:8]}…" if mid else "None"
    lines = [f"{indent}[{index}] {role:<7} id={short_id}"]

    for call in tool_calls_of(message):
        lines.append(
            f"{indent}      ↳ 请求调用工具: {call_field(call, 'name')}"
            f"({call_field(call, 'args')})"
        )
        lines.append(f"{indent}        期望的 tool_call_id: {call_field(call, 'id')}")

    if role == "tool":
        lines.append(f"{indent}      ↳ 应答 tool_call_id: {getattr(message, 'tool_call_id', None)}")

    body = content_of(message)
    if body.strip():
        lines.append(f"{indent}     content: {clip(body, full)}")
    return lines


def is_message_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and hasattr(value[0], "type")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="逐条观测模型调用工具的消息")
    parser.add_argument("--ticker", default="600519")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--online", action="store_true", help="使用真实 LLM（需 .env 配 Key）")
    parser.add_argument("--full", action="store_true", help="内容不截断")
    parser.add_argument("--no-steps", action="store_true", help="跳过逐节点观察（只跑一遍）")
    args = parser.parse_args()

    settings = load_settings(offline=not args.online, rounds=args.rounds)
    provider = get_data_service(settings.data_provider)
    tool_map = build_tools(provider)

    def llm_factory(role: str):
        return build_llm(settings, role=role, ticker=args.ticker)

    app = build_graph(llm_factory=llm_factory, tool_map=tool_map, settings=settings)
    initial: Dict[str, Any] = {
        "ticker": args.ticker,
        "trade_date": date.today().isoformat(),
        "max_rounds": settings.max_debate_rounds,
        "debate_history": [],
        "debate_count": 0,
        "trace": [],
    }

    print("=" * 74)
    print(
        f"模型: {'真实 LLM' if not settings.offline else '离线桩模型'}   "
        f"ticker: {args.ticker}   数据源: {provider.name}   辩论轮数: {settings.max_debate_rounds}"
    )
    print("=" * 74)

    # ---------------- ① 逐步观察 ----------------
    if not args.no_steps:
        print("\n① 逐步观察：每个节点向 state 新增了什么\n")
        for chunk in app.stream(initial, stream_mode="updates"):
            for node, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                print(f"▶ 节点 [{node}]")
                for key, value in update.items():
                    if is_message_list(value):
                        print(f"    + {key}: 新增 {len(value)} 条消息")
                        for i, message in enumerate(value):
                            for line in describe(message, i, args.full):
                                print(line)
                    elif isinstance(value, list):
                        print(f"    + {key}: {value}")
                    else:
                        print(f"    + {key}: {clip(value, args.full)}")
                print()

    # ---------------- ② 最终状态 ----------------
    state = app.invoke(initial)

    print("\n② 最终 state 里的完整消息历史\n")
    for role in ROLES:
        field = f"{role}_messages"
        history = state.get(field) or []
        header = f"### {role}（{field}，共 {len(history)} 条）"
        print(header)
        print("-" * len(header))
        if not history:
            print("  （空）\n")
            continue
        for i, message in enumerate(history):
            for line in describe(message, i, args.full):
                print(line)
        print()

    # ---------------- ③ 汇总 ----------------
    print("③ 汇总\n")
    for role in ROLES:
        history = state.get(f"{role}_messages") or []
        ai_msgs = sum(1 for m in history if role_of(m) == "ai")
        tool_msgs = sum(1 for m in history if role_of(m) == "tool")
        calls = sum(len(tool_calls_of(m)) for m in history)
        used = state.get(f"{role}_tool_calls", 0)
        print(
            f"  {role:<12} 消息 {len(history):>2} 条 | "
            f"AI {ai_msgs} | Tool {tool_msgs} | 工具调用 {calls} 次 | "
            f"计数字段 {role}_tool_calls={used}"
        )
    print(f"\n  节点轨迹：{state.get('trace')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
