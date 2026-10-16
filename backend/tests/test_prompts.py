"""提示词与工具契约的完整性（自检 [19]）

提示词是「契约」，它的结构决定了输出质量。这些断言保证结构性段落不会
被无意间删掉（尤其是「不得写」这种靠具体反例生效的约束）。
"""

import pytest

from prompts import ANALYST_PROMPTS, BEAR_SYSTEM, BULL_SYSTEM, MANAGER_SYSTEM

TOOL_CONTRACT_FIELDS = ("何时使用", "何时不要用", "返回", "局限")


def test_analyst_contracts_have_required_sections():
    for key, text in ANALYST_PROMPTS.items():
        assert "你的任务" in text, key
        assert "不得写" in text, key
        assert "输出结构" in text, key


@pytest.mark.parametrize(
    ("needle", "role"),
    [
        ("口径", "fundamental"),
        ("交叉验算", "fundamental"),
        ("零轴", "technical"),
        ("词典规则法", "news"),
    ],
)
def test_analyst_contract_specific_rules(needle, role):
    """每条规则都对应一个真实踩过的坑，不能丢。"""
    assert needle in ANALYST_PROMPTS[role]


def test_debaters_forbid_repeating_points():
    assert "不要重复上一轮" in BULL_SYSTEM
    assert "不要重复上一轮" in BEAR_SYSTEM


def test_manager_forbids_fence_sitting():
    assert "不要和稀泥" in MANAGER_SYSTEM


def test_every_tool_docstring_has_four_part_contract(tool_map):
    missing = [
        f"{name}.{field}"
        for name, tool_obj in tool_map.items()
        for field in TOOL_CONTRACT_FIELDS
        if field not in (tool_obj.description or "")
    ]
    assert not missing, "; ".join(missing)[:220]
