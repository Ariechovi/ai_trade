"""辩论轮数消融：计数器必须严格等于「轮数 × 2」（自检 [4]）"""

import pytest

from settings import load_settings
from services.runner import run_case

DATE = "2026-10-11"


@pytest.mark.parametrize(("rounds", "expected"), [(0, 0), (1, 2), (2, 4), (3, 6)])
def test_debate_count_equals_rounds_times_two(rounds, expected, mock_provider):
    settings = load_settings(offline=True, rounds=rounds)
    state = run_case("000001", DATE, settings, mock_provider)
    assert int(state.get("debate_count", 0)) == expected


def test_zero_rounds_skips_debate_entirely(mock_provider):
    settings = load_settings(offline=True, rounds=0)
    state = run_case("000001", DATE, settings, mock_provider)
    assert not (state.get("debate_history") or [])
