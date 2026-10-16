"""可观测性：节点耗时与 LLM 调用都被记录（自检 [20]）"""

from agents.specs import ROLES
from services.runner import summarize


def test_timings_and_calls_are_recorded(e2e_state):
    timings = e2e_state.get("node_timings") or []
    calls = e2e_state.get("llm_calls") or []
    trace = [str(t) for t in (e2e_state.get("trace") or [])]

    assert timings
    assert calls
    assert len(timings) >= len(trace)

    names = {t.get("node") for t in timings}
    assert {"llm:technical", "tools:technical", "report:technical", "merge", "manager"} <= names

    assert all(
        {"input_tokens", "output_tokens", "total_tokens", "duration_ms"} <= set(c) for c in calls
    )


def test_summarize_is_the_single_aggregation_point(e2e_state):
    """CLI / Streamlit / API 必须共用同一份聚合口径。"""
    calls = e2e_state.get("llm_calls") or []
    summary = summarize(e2e_state)

    assert set(summary["by_role"]) >= set(ROLES)
    assert summary["total_tokens"] == sum(int(c.get("total_tokens") or 0) for c in calls)
    assert summary["llm_call_count"] == len(calls)
