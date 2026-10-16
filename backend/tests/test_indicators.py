"""指标计算：公式正确性（自检 [12]）

指标算错是最隐蔽的 bug，所以用「已知序列反推」来验：
单调上涨 → RSI 必须等于 100；单调下跌 → 0；常数序列 → MA 等于该常数。
"""

import numpy as np
import pandas as pd

from data import indicators


def test_rsi_on_monotonic_rise_is_100():
    rising = pd.Series(np.arange(1, 40, dtype=float))
    assert abs(float(indicators.rsi(rising).iloc[-1]) - 100) < 1e-6


def test_rsi_on_monotonic_fall_is_0():
    falling = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert abs(float(indicators.rsi(falling).iloc[-1])) < 1e-6


def test_moving_average_on_constant_series():
    flat = pd.Series([10.0] * 40)
    assert abs(float(indicators.moving_average(flat, 20).iloc[-1]) - 10.0) < 1e-9


def test_price_frame_has_standard_columns(mock_provider):
    """列名标准化是数据层的核心约定，指标层永远只认这一套列名。"""
    frame = mock_provider.get_price_history("600519", 120)
    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume"]


def test_latest_snapshot_is_sane(mock_provider):
    frame = mock_provider.get_price_history("600519", 120)
    snap = indicators.latest_snapshot(frame)
    assert snap.get("boll_upper") > snap.get("boll_lower")
    assert 0.0 <= float(snap.get("rsi14")) <= 100.0
    assert float(snap.get("atr_pct", 0)) > 0
    assert bool(snap.get("ma_trend"))
    assert bool(snap.get("macd_state"))
