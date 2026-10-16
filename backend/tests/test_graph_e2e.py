"""端到端：拓扑、工具循环、扇入屏障（自检 [3][5][6][7][18]）"""

from langchain_core.messages import AIMessage, ToolMessage

from agents.specs import ANALYSTS, ROLES
from services.graph import build_graph
from services.offline_llm import ScriptedChatModel
from services.runner import run_case

DATE = "2026-10-11"


def _trace(state):
    return [str(t) for t in (state.get("trace") or [])]


def test_reports_and_decision_are_produced(e2e_state):
    assert e2e_state.get("technical_report")
    assert e2e_state.get("fundamental_report")
    assert e2e_state.get("news_report")
    assert e2e_state.get("final_decision")


def test_debate_counters_match_rounds(e2e_state):
    assert int(e2e_state.get("debate_count", 0)) == 4
    assert len(e2e_state.get("debate_history") or []) == 4


def test_analysts_all_ran_and_reported(e2e_state):
    trace = _trace(e2e_state)
    # 每个分析师的 LLM 节点会执行多次（先要工具、再出结论），所以按角色去重
    assert len({t for t in trace if t.startswith("llm:")}) == 3
    assert sum(1 for t in trace if t.startswith("report:")) == 3
    assert "merge" in trace
    assert "manager" in trace


def test_tool_loop_steps_are_visible(e2e_state):
    trace = _trace(e2e_state)
    assert "llm:technical" in trace
    assert any(t.startswith("tools:technical(") for t in trace)
    assert "report:technical" in trace
    assert all(any(t.startswith(f"tools:{role}(") for t in trace) for role in ROLES)
    assert trace.index("llm:technical") < next(
        i for i, t in enumerate(trace) if t.startswith("tools:technical(")
    )


def test_tool_rounds_land_in_state(e2e_state, offline_settings):
    round_cap = max(1, int(offline_settings.max_tool_iterations))
    for spec in ANALYSTS:
        used = int(e2e_state.get(spec.tool_calls_field, 0))
        assert used == min(len(spec.tool_names), round_cap), f"{spec.role} used={used}"
        assert e2e_state.get(spec.messages_field), spec.messages_field


def test_different_tickers_do_not_cross_contaminate(offline_settings, mock_provider):
    s1 = run_case("600519", DATE, offline_settings, mock_provider)
    s2 = run_case("300750", DATE, offline_settings, mock_provider)
    assert s1.get("technical_report") != s2.get("technical_report")


def test_fanin_barrier_holds_for_even_branches(e2e_state):
    trace = _trace(e2e_state)
    merge_at = trace.index("merge")
    assert all(trace.index(f"report:{role}") < merge_at for role in ROLES)


def test_fanin_barrier_holds_for_uneven_branches(offline_settings, tool_map):
    """回归：三个分析师循环长度不同时，merge 仍只触发一次。

    LangGraph 的 add_node 默认 defer=False，语义是「任一入边到达就执行」。
    真实 LLM 一次可能返回多个 tool_calls，导致三条支路长度不同，merge 会被
    触发多次 —— 于是辩论在报告还没到齐时就开始了。

    等长的测试会掩盖不等长的现实，所以这里专门造一个不等长的分支。
    """
    uneven_limits = {"technical": 1, "fundamental": 3, "news": 2}

    class UnevenModel(ScriptedChatModel):
        def _respond(self, messages):
            ticker = self.ticker or "600519"
            done = sum(1 for m in messages if isinstance(m, ToolMessage))
            limit = uneven_limits.get(self.role, 1)
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

    app = build_graph(
        llm_factory=lambda role: UnevenModel(role=role, ticker="600519"),
        tool_map=tool_map,
        settings=offline_settings,
    )
    state = app.invoke(
        {
            "ticker": "600519",
            "trade_date": DATE,
            "max_rounds": offline_settings.max_debate_rounds,
            "debate_history": [],
            "debate_count": 0,
            "trace": [],
        }
    )
    trace = _trace(state)

    assert trace.count("merge") == 1, f"merge 触发了 {trace.count('merge')} 次"
    assert trace.count("manager") == 1
    assert int(state.get("debate_count", 0)) == 4
    merge_at = trace.index("merge")
    assert all(trace.index(f"report:{role}") < merge_at for role in ROLES)
