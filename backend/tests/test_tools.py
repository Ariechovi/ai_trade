"""工具层：数量、归属、可调用性、循环往返（自检 [2][16][17]）"""

from typing import Any, Dict

from agents.specs import ANALYSTS

EXPECTED_TOOL_COUNT = {"technical": 6, "fundamental": 8, "news": 4}


def test_tool_count_is_18(tool_map):
    assert len(tool_map) == 18, f"got={len(tool_map)}"


def test_tool_returns_non_empty_text(tool_map):
    out = tool_map["get_financial_statements"].invoke({"ticker": "600519"})
    assert isinstance(out, str) and len(out) > 10


def test_spec_tool_names_all_exist(tool_map):
    assert all(name in tool_map for spec in ANALYSTS for name in spec.tool_names)


def test_tools_per_analyst_match_registry():
    for spec in ANALYSTS:
        assert len(spec.tool_names) == EXPECTED_TOOL_COUNT[spec.role], spec.role


def test_tools_do_not_overlap():
    """最小权限原则：每个分析师只拿到自己的工具。"""
    names = [name for spec in ANALYSTS for name in spec.tool_names]
    assert len(set(names)) == len(names)


def _args_for(name: str) -> Dict[str, Any]:
    if name == "compare_peers":
        return {"ticker": "600519", "peers": "000001,600036"}
    if name == "filter_news":
        return {"ticker": "600519", "keyword": "公告"}
    if name == "get_market_environment":
        return {"index_code": "sh000001"}
    return {"ticker": "600519"}


def test_every_tool_is_callable(tool_map):
    failures = []
    for name, tool_obj in tool_map.items():
        try:
            result = tool_obj.invoke(_args_for(name))
            if not isinstance(result, str) or not result.strip():
                failures.append(f"{name}: 返回空")
        except Exception as exc:  # noqa: BLE001 —— 这里就是要收集所有失败
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failures, " | ".join(failures)[:250]


def test_tool_loop_really_roundtrips(e2e_state):
    """离线桩按顺序调用多个工具 → 每个分析师的工具节点都执行 >1 次。"""
    trace = [str(t) for t in (e2e_state.get("trace") or [])]
    for spec in ANALYSTS:
        steps = [t for t in trace if t.startswith(f"tools:{spec.role}(")]
        assert len(steps) > 1, f"{spec.role} 的工具节点只执行了 {len(steps)} 次"
    assert "共调用 3 个工具" in str(e2e_state.get("technical_report"))
