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
11. 数据层：指标公式正确、缓存可用、多源降级、模拟数据有显眼标注
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from pydantic import Field  # noqa: E402

from data import get_data_service  # noqa: E402
from data import indicators  # noqa: E402
from data.cache import FileCache  # noqa: E402
from data.service import DataService  # noqa: E402
from data.sources.base import DataSourceError  # noqa: E402
from data.sources.mock_source import MockDataSource  # noqa: E402
from src.agents.specs import ANALYSTS, ROLES  # noqa: E402
from src.config import load_settings  # noqa: E402
from src.graph import build_graph  # noqa: E402
from src.offline_llm import ScriptedChatModel  # noqa: E402
from src.prompts import ANALYST_PROMPTS, BEAR_SYSTEM, BULL_SYSTEM, MANAGER_SYSTEM  # noqa: E402
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
    provider = get_data_service("mock")

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
    c.check("工具总数 = 18", len(tool_map) == 18, f"got={len(tool_map)}")
    out = tool_map["get_financial_statements"].invoke({"ticker": "600519"})
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

    print("[7] 工具轮数落到状态里（工具变多后循环真的多轮往返）")
    round_cap = max(1, int(settings.max_tool_iterations))
    for spec in ANALYSTS:
        used = int(state.get(spec.tool_calls_field, 0))
        expected = min(len(spec.tool_names), round_cap)
        c.check(
            f"{spec.role} 工具轮数 = {expected}（工具 {len(spec.tool_names)} 个，上限 {round_cap}）",
            used == expected,
            f"got={used}",
        )
        c.check(f"{spec.role} 私有消息历史非空", bool(state.get(spec.messages_field)))

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

    print("[12] 指标计算：公式正确性（用已知序列反推）")
    rising = pd.Series(np.arange(1, 40, dtype=float))
    falling = pd.Series(np.arange(40, 1, -1, dtype=float))
    c.check("单调上涨 → RSI ≈ 100", abs(float(indicators.rsi(rising).iloc[-1]) - 100) < 1e-6)
    c.check("单调下跌 → RSI ≈ 0", abs(float(indicators.rsi(falling).iloc[-1])) < 1e-6)
    flat = pd.Series([10.0] * 40)
    c.check(
        "常数序列 MA20 = 10",
        abs(float(indicators.moving_average(flat, 20).iloc[-1]) - 10.0) < 1e-9,
    )
    frame = provider.get_price_history("600519", 120)
    c.check(
        "行情列为标准列名",
        list(frame.columns) == ["date", "open", "high", "low", "close", "volume"],
    )
    snap = indicators.latest_snapshot(frame)
    c.check("布林上轨 > 下轨", snap.get("boll_upper") > snap.get("boll_lower"))
    c.check("RSI 落在 0~100", 0.0 <= float(snap.get("rsi14")) <= 100.0)
    c.check("ATR 占价格比例为正", float(snap.get("atr_pct", 0)) > 0)
    c.check("给出均线形态判断", bool(snap.get("ma_trend")))
    c.check("给出 MACD 状态判断", bool(snap.get("macd_state")))

    print("[13] 缓存：命中 / 串味 / 过期 / 清空")
    cache_dir = Path(tempfile.mkdtemp(prefix="mini_cache_"))
    cache = FileCache(cache_dir, ttl_seconds=60)
    cache.set("k1", {"a": 1})
    c.check("写入后可命中", cache.get("k1") == {"a": 1})
    c.check("不同 key 不串味", cache.get("k2") is None)
    c.check("过期后不命中", FileCache(cache_dir, ttl_seconds=-1).get("k1") is None)
    c.check("清空返回删除数量", cache.clear() == 1)

    print("[14] 多源降级：主源故障自动切备用源")

    class BrokenSource(MockDataSource):
        name = "broken"

        def get_price_history(self, ticker: str, days: int):
            raise DataSourceError("模拟主源故障")

    svc = DataService(
        [BrokenSource(), MockDataSource()],
        cache=FileCache(Path(tempfile.mkdtemp(prefix="mini_svc_")), 60),
    )
    recovered = svc.get_price_history("600519", 60)
    c.check("备用源接住请求", len(recovered) == 60)
    c.check("记录了实际服务来源", svc.source_of("600519") == "mock")

    dead = DataService(
        [BrokenSource()], cache=FileCache(Path(tempfile.mkdtemp(prefix="mini_dead_")), 60)
    )
    raised = False
    try:
        dead.get_price_history("600519", 60)
    except DataSourceError:
        raised = True
    c.check("全部源失败 → 抛 DataSourceError", raised)

    print("[15] 模拟数据必须显眼标注，且指标是算出来的")
    technical_text = provider.get_technicals("600519", 120)
    c.check("输出带「离线模拟数据」标记", "离线模拟数据" in technical_text)
    c.check("输出含真实指标名", "RSI(14)" in technical_text and "MACD" in technical_text)
    c.check("输出含均线与布林带", "MA20" in technical_text and "布林带" in technical_text)

    print("[16] 工具层：数量、归属、逐个可调用")
    c.check(
        "规格表里的工具名都存在",
        all(name in tool_map for spec in ANALYSTS for name in spec.tool_names),
    )
    expected_tools = {"technical": 6, "fundamental": 8, "news": 4}
    for spec in ANALYSTS:
        c.check(
            f"{spec.role} 工具数 = {expected_tools[spec.role]}",
            len(spec.tool_names) == expected_tools[spec.role],
            f"got={len(spec.tool_names)}",
        )
    c.check(
        "各分析师工具互不重叠",
        len({name for spec in ANALYSTS for name in spec.tool_names})
        == sum(len(spec.tool_names) for spec in ANALYSTS),
    )

    def _args_for(name: str) -> Dict[str, Any]:
        if name == "compare_peers":
            return {"ticker": "600519", "peers": "000001,600036"}
        if name == "filter_news":
            return {"ticker": "600519", "keyword": "公告"}
        if name == "get_market_environment":
            return {"index_code": "sh000001"}
        return {"ticker": "600519"}

    failures: List[str] = []
    for name, tool_obj in tool_map.items():
        try:
            result = tool_obj.invoke(_args_for(name))
            if not isinstance(result, str) or not result.strip():
                failures.append(f"{name}: 返回空")
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    c.check("每个工具都能被调用且返回非空", not failures, " | ".join(failures)[:250])

    print("[17] 工具循环真的多轮往返（离线桩按顺序调用多个工具）")
    trace_text = [str(t) for t in (state.get("trace") or [])]
    for spec in ANALYSTS:
        steps = [t for t in trace_text if t.startswith(f"tools:{spec.role}(")]
        c.check(f"{spec.role} 工具节点执行 {len(steps)} 次（>1 即证明循环往返）", len(steps) > 1)
    c.check(
        "技术面报告里出现了多个工具的输出",
        "共调用 3 个工具" in str(state.get("technical_report")),
    )

    print("[18] 回归：三分支循环长度不同时，扇入屏障仍只触发一次")
    uneven_rounds = {"technical": 1, "fundamental": 3, "news": 2}

    class UnevenModel(ScriptedChatModel):
        """让每个分析师的工具循环长度不同（真实 LLM 一次可能返回多个 tool_calls）。"""

        def _respond(self, messages):
            ticker = self.ticker or "600519"
            done = sum(1 for m in messages if isinstance(m, ToolMessage))
            limit = uneven_rounds.get(self.role, 1)
            if self.bound_tools and done < limit:
                tool = self.bound_tools[done % len(self.bound_tools)]
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": tool.name,
                            "args": {"ticker": ticker},
                            "id": f"c{done}",
                            "type": "tool_call",
                        }
                    ],
                )
            return AIMessage(content=self._final_text(messages))

    uneven_app = build_graph(
        llm_factory=lambda role: UnevenModel(role=role, ticker="600519"),
        tool_map=tool_map,
        settings=settings,
    )
    uneven_state = uneven_app.invoke(
        {
            "ticker": "600519",
            "trade_date": DATE,
            "max_rounds": settings.max_debate_rounds,
            "debate_history": [],
            "debate_count": 0,
            "trace": [],
        }
    )
    uneven_trace = [str(t) for t in (uneven_state.get("trace") or [])]
    c.check(
        "merge 只执行一次",
        uneven_trace.count("merge") == 1,
        f"got={uneven_trace.count('merge')}",
    )
    c.check(
        "manager 只执行一次",
        uneven_trace.count("manager") == 1,
        f"got={uneven_trace.count('manager')}",
    )
    c.check(
        "辩论次数仍为 4",
        int(uneven_state.get("debate_count", 0)) == 4,
        f"got={uneven_state.get('debate_count')}",
    )
    for run_label, run_state in (("等长", state), ("不等长", uneven_state)):
        run_trace = [str(t) for t in (run_state.get("trace") or [])]
        if "merge" not in run_trace:
            c.check(f"{run_label}分支：轨迹含 merge", False)
            continue
        merge_at = run_trace.index("merge")
        c.check(
            f"{run_label}分支：三份报告都在 merge 之前（辩论不会缺料）",
            all(run_trace.index(f"report:{role}") < merge_at for role in ROLES),
        )

    print("[19] 提示词与工具契约的结构完整性")
    for key, text in ANALYST_PROMPTS.items():
        c.check(f"{key} 契约含「你的任务」", "你的任务" in text)
        c.check(f"{key} 契约含「不得写」反例", "不得写" in text)
        c.check(f"{key} 契约含「输出结构」", "输出结构" in text)
    c.check("基本面契约含口径规则", "口径" in ANALYST_PROMPTS["fundamental"])
    c.check("基本面契约要求交叉验算", "交叉验算" in ANALYST_PROMPTS["fundamental"])
    c.check("技术面契约要求检查零轴位置", "零轴" in ANALYST_PROMPTS["technical"])
    c.check("消息面契约说明词典法局限", "词典规则法" in ANALYST_PROMPTS["news"])
    c.check("多头契约禁止重复论点", "不要重复上一轮" in BULL_SYSTEM)
    c.check("空头契约禁止重复论点", "不要重复上一轮" in BEAR_SYSTEM)
    c.check("经理契约要求不和稀泥", "不要和稀泥" in MANAGER_SYSTEM)

    contract_fields = ("何时使用", "何时不要用", "返回", "局限")
    missing = [
        f"{name}.{field}"
        for name, tool_obj in tool_map.items()
        for field in contract_fields
        if field not in (tool_obj.description or "")
    ]
    c.check("每个工具的描述都含四段契约", not missing, "; ".join(missing)[:220])

    return 0 if c.summary() else 1


if __name__ == "__main__":
    raise SystemExit(main())
