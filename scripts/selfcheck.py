"""零依赖自检脚本：不需要 pytest，也不需要网络。

用法：
    python scripts/selfcheck.py

验证内容：
1. 配置能自动降级到离线模式
2. 数据源确定性（同一股票结果可复现）
3. 工具层能正常返回
4. 图能端到端跑通，三份分析师报告齐全
5. 辩论轮数与计数器严格一致（rounds = 0/1/2/3）
6. 工具循环的每一步都出现在图里（llm / tools / report 三类节点）
7. 工具轮数被正确计数，并落到状态里
8. 不同股票产出不同结果（证明数据真的流过了）
9. 容错：可重试错误会重试、重试耗尽会降级、不可重试错误立即降级
10. 容错：降级不会阻断流程，也不会让辩论死循环
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from pydantic import Field  # noqa: E402

from data.provider import MockProvider  # noqa: E402
from src.agents.specs import ROLES  # noqa: E402
from src.config import load_settings  # noqa: E402
from src.graph import build_graph  # noqa: E402
from src.offline_llm import ScriptedChatModel  # noqa: E402
from src.runner import run_case  # noqa: E402
from src.tools import build_tools  # noqa: E402

DATE = "2026-10-11"


# ---------------------------------------------------------------------------
# 故障注入：用来验证重试与降级
# ---------------------------------------------------------------------------
class FlakyChatModel(ScriptedChatModel):
    """按剧本失败的桩模型：前 fail_first_n 次调用抛异常。"""

    fail_first_n: int = 0
    fail_message: str = "connection timeout (injected)"
    attempts: List[int] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.attempts.append(1)
        if len(self.attempts) <= self.fail_first_n:
            raise RuntimeError(self.fail_message)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def make_flaky_factory(*, ticker: str, targets: set, fail_first_n: int, message: str):
    """返回 (llm_factory, created)：只让 targets 里的角色使用故障模型。"""
    created: List[FlakyChatModel] = []

    def factory(role: str):
        if role in targets:
            model = FlakyChatModel(
                role=role, ticker=ticker, fail_first_n=fail_first_n, fail_message=message
            )
            created.append(model)
            return model
        return ScriptedChatModel(role=role, ticker=ticker)

    return factory, created


def run_with_factory(llm_factory, ticker: str, settings, provider) -> Dict[str, Any]:
    """用自定义的 llm_factory 跑一遍图（用来注入故障）。"""
    app = build_graph(llm_factory=llm_factory, tool_map=build_tools(provider), settings=settings)
    return app.invoke(
        {
            "ticker": ticker,
            "trade_date": DATE,
            "max_rounds": settings.max_debate_rounds,
            "debate_history": [],
            "debate_count": 0,
            "trace": [],
        }
    )


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            print(f"  FAIL  {name}  {detail}".rstrip())

    def summary(self) -> bool:
        total = self.passed + self.failed
        print(f"\n结果：{self.passed}/{total} 项检查通过")
        return self.failed == 0


def main() -> int:
    c = Checker()
    settings = load_settings(offline=True, rounds=2)
    provider = MockProvider()

    print("[1] 配置与数据源")
    c.check("可强制离线模式", settings.offline is True)
    auto = load_settings()
    c.check("无 Key 时自动降级为离线", auto.offline is True or bool(auto.llm_api_key))
    first = provider.get_technicals("600519")
    second = provider.get_technicals("600519")
    c.check("数据可复现（同股票同结果）", first == second)
    c.check(
        "不同股票数据不同",
        provider.get_technicals("600519") != provider.get_technicals("000001"),
    )

    print("[2] 工具层")
    tool_map = build_tools(provider)
    c.check("工具数量 = 3", len(tool_map) == 3, f"got={sorted(tool_map)}")
    out = tool_map["get_fundamentals"].invoke({"ticker": "600519"})
    c.check("工具返回非空文本", isinstance(out, str) and len(out) > 10)

    print("[3] 图：端到端（rounds=2）")
    state = run_case("600519", DATE, settings, provider)
    trace = [str(t) for t in (state.get("trace") or [])]
    c.check("技术面报告非空", bool(state.get("technical_report")))
    c.check("基本面报告非空", bool(state.get("fundamental_report")))
    c.check("消息面报告非空", bool(state.get("news_report")))
    c.check("最终结论非空", bool(state.get("final_decision")))
    c.check("辩论计数 = 4", int(state.get("debate_count", 0)) == 4, f"got={state.get('debate_count')}")
    c.check("辩论历史长度 = 4", len(state.get("debate_history") or []) == 4)
    c.check(
        "三个分析师都执行了 LLM 节点",
        # 每个分析师的 LLM 节点会执行两次（先要工具，再出结论），所以按角色去重
        len({t for t in trace if t.startswith("llm:")}) == 3,
    )
    c.check(
        "三个分析师都产出了报告",
        sum(1 for t in trace if t.startswith("report:")) == 3,
    )
    c.check("经过 merge 汇合点", "merge" in trace)
    c.check("经过 manager 节点", "manager" in trace)

    print("[4] 辩论轮数消融（计数器必须严格等于 轮数×2）")
    for rounds, expect in ((0, 0), (1, 2), (2, 4), (3, 6)):
        s = load_settings(offline=True, rounds=rounds)
        st = run_case("000001", DATE, s, provider)
        c.check(
            f"rounds={rounds} → 发言 {expect} 次",
            int(st.get("debate_count", 0)) == expect,
            f"got={st.get('debate_count')}",
        )
        if rounds == 0:
            c.check("rounds=0 时无辩论历史", not (st.get("debate_history") or []))

    print("[5] 数据确实流经不同标的")
    s1 = run_case("600519", DATE, settings, provider)
    s2 = run_case("300750", DATE, settings, provider)
    c.check("两只股票技术面报告不同", s1.get("technical_report") != s2.get("technical_report"))

    print("[6] 工具循环的每一步都出现在图里")
    c.check("trace 含 llm:technical", "llm:technical" in trace)
    c.check(
        "trace 含 tools:technical(n)",
        any(t.startswith("tools:technical(") for t in trace),
    )
    c.check("trace 含 report:technical", "report:technical" in trace)
    c.check(
        "三个分析师各执行了工具节点",
        all(any(t.startswith(f"tools:{role}(") for t in trace) for role in ROLES),
    )
    c.check(
        "工具节点排在对应 LLM 节点之后",
        trace.index("llm:technical") < next(
            i for i, t in enumerate(trace) if t.startswith("tools:technical(")
        ),
    )

    print("[7] 工具轮数落到状态里")
    for role in ROLES:
        used = int(state.get(f"{role}_tool_calls", 0))
        c.check(f"{role} 工具轮数 = 1", used == 1, f"got={used}")
        c.check(f"{role} 私有消息历史非空", bool(state.get(f"{role}_messages")))

    # 容错验证统一用「零退避」配置，避免测试真的等待
    fast = replace(
        load_settings(offline=True, rounds=2),
        retry_base_delay=0.0,
        max_retry_attempts=3,
    )

    print("[8] 容错：可重试错误 → 重试后成功")
    factory, created = make_flaky_factory(
        ticker="600519",
        targets={"technical"},
        fail_first_n=1,
        message="connection timeout (injected)",
    )
    st = run_with_factory(factory, "600519", fast, provider)
    c.check("重试后技术面报告正常", "调用失败" not in str(st.get("technical_report")))
    c.check("重试成功时没有降级记录", not (st.get("_node_errors") or {}))
    c.check("确实发生了重试（调用 ≥2 次）", len(created[0].attempts) >= 2, f"got={len(created[0].attempts)}")
    c.check("最终结论仍产出", bool(st.get("final_decision")))

    print("[9] 容错：重试耗尽 → 降级但不阻断")
    factory, created = make_flaky_factory(
        ticker="600519",
        targets={"technical"},
        fail_first_n=999,
        message="connection timeout (injected)",
    )
    st = run_with_factory(factory, "600519", fast, provider)
    errors = st.get("_node_errors") or {}
    record = errors.get("technical") or {}
    c.check("technical 被记录为降级", "technical" in errors)
    c.check("记录含 stage/type/message", bool(record.get("stage")) and bool(record.get("message")))
    c.check("尝试次数用尽（恰好 3 次）", len(created[0].attempts) == 3, f"got={len(created[0].attempts)}")
    c.check("技术面报告为兜底文案", "调用失败" in str(st.get("technical_report")))
    c.check("其余分析师不受影响", "调用失败" not in str(st.get("fundamental_report")))
    c.check("仍走到报告节点", "report:technical" in [str(t) for t in st.get("trace") or []])
    c.check("最终结论仍产出", bool(st.get("final_decision")))

    print("[10] 容错：不可重试错误 → 立即降级，不浪费重试")
    factory, created = make_flaky_factory(
        ticker="600519",
        targets={"news"},
        fail_first_n=999,
        message="bad request: invalid parameter",
    )
    st = run_with_factory(factory, "600519", fast, provider)
    c.check("news 被记录为降级", "news" in (st.get("_node_errors") or {}))
    c.check("只尝试了 1 次", len(created[0].attempts) == 1, f"got={len(created[0].attempts)}")

    print("[11] 容错：辩论节点失败也不会死循环")
    factory, _ = make_flaky_factory(
        ticker="600519",
        targets={"bull"},
        fail_first_n=999,
        message="connection timeout (injected)",
    )
    st = run_with_factory(factory, "600519", fast, provider)
    c.check("bull 被记录为降级", "debate.bull" in (st.get("_node_errors") or {}))
    c.check("辩论计数仍然推进（=4）", int(st.get("debate_count", 0)) == 4, f"got={st.get('debate_count')}")
    c.check("最终结论仍产出", bool(st.get("final_decision")))

    return 0 if c.summary() else 1


if __name__ == "__main__":
    raise SystemExit(main())
