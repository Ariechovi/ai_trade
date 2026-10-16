"""技术指标计算：纯函数，输入价格序列，输出指标序列。

对应 TradingAgents-CN 的 `tradingagents/dataflows/technical/`
（V3 用 stockstats 库；这里用 pandas 手写公式——多一个依赖不如多看懂一个公式）。

这一层刻意与「取数据」分离：给什么价格序列都行，它不关心数据从哪来。
所以离线模拟数据也能跑真实的指标公式，验证因此才有意义。
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def moving_average(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window=window, min_periods=window).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI：用指数平滑（alpha = 1/period），不是简单均值。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + rs)
    return result.where(avg_loss != 0, 100.0)  # 一路上涨（无下跌）时定义为 100


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """返回 (DIF, DEA, 柱状值)。国内习惯柱状值 = (DIF - DEA) × 2。"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif, dea, (dif - dea) * 2.0


def bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0):
    """返回 (上轨, 中轨, 下轨)。"""
    mid = moving_average(close, window)
    std = close.rolling(window=window, min_periods=window).std(ddof=0)
    return mid + num_std * std, mid, mid - num_std * std


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """真实波幅均值（Wilder 平滑）。"""
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """在原始行情上追加全部指标列。"""
    out = df.copy()
    close = out["close"]
    out["ma5"] = moving_average(close, 5)
    out["ma10"] = moving_average(close, 10)
    out["ma20"] = moving_average(close, 20)
    out["ma60"] = moving_average(close, 60)
    out["rsi14"] = rsi(close, 14)
    out["dif"], out["dea"], out["macd_hist"] = macd(close)
    out["boll_upper"], out["boll_mid"], out["boll_lower"] = bollinger(close)
    out["atr14"] = atr(out["high"], out["low"], close, 14)
    out["pct_change"] = close.pct_change() * 100.0
    out["vol_ma20"] = moving_average(out["volume"], 20)
    return out


def _num(value: Any, digits: int = 2):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(result):
        return None
    return round(result, digits)


def latest_snapshot(df: pd.DataFrame) -> Dict[str, Any]:
    """取最后一根 K 线上的指标取值，并给出几个状态判断。"""
    if df is None or len(df) == 0:
        return {}
    data = compute(df)
    last = data.iloc[-1]
    prev = data.iloc[-2] if len(data) > 1 else last

    close = _num(last.get("close"))
    ma5, ma10 = _num(last.get("ma5")), _num(last.get("ma10"))
    ma20, ma60 = _num(last.get("ma20")), _num(last.get("ma60"))
    rsi14 = _num(last.get("rsi14"), 1)
    dif, dea = _num(last.get("dif"), 3), _num(last.get("dea"), 3)
    prev_dif, prev_dea = _num(prev.get("dif"), 3), _num(prev.get("dea"), 3)
    upper, lower = _num(last.get("boll_upper")), _num(last.get("boll_lower"))
    atr14 = _num(last.get("atr14"))
    volume = _num(last.get("volume"), 0)
    vol_ma20 = _num(last.get("vol_ma20"), 0)
    pct = _num(last.get("pct_change"))

    snapshot: Dict[str, Any] = {
        "close": close,
        "pct_change": pct,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "rsi14": rsi14,
        "dif": dif,
        "dea": dea,
        "boll_upper": upper,
        "boll_lower": lower,
        "atr14": atr14,
        "volume": volume,
        "volume_ma20": vol_ma20,
        "bars": int(len(data)),
    }

    # ---- 状态判断（这才是「分析」，而不是把数字堆给模型）----
    if ma5 is not None and ma20 is not None:
        snapshot["ma_trend"] = "多头排列（MA5>MA20）" if ma5 > ma20 else "空头排列（MA5<MA20）"
    if close is not None and ma60 is not None:
        snapshot["above_ma60"] = close > ma60
    if rsi14 is not None:
        if rsi14 >= 70:
            snapshot["rsi_zone"] = "超买区"
        elif rsi14 <= 30:
            snapshot["rsi_zone"] = "超卖区"
        else:
            snapshot["rsi_zone"] = "中性区"
    if None not in (dif, dea):
        if None not in (prev_dif, prev_dea) and prev_dif <= prev_dea and dif > dea:
            snapshot["macd_state"] = "刚形成金叉"
        elif None not in (prev_dif, prev_dea) and prev_dif >= prev_dea and dif < dea:
            snapshot["macd_state"] = "刚形成死叉"
        elif dif > dea:
            snapshot["macd_state"] = "DIF 在 DEA 上方（多头）"
        else:
            snapshot["macd_state"] = "DIF 在 DEA 下方（空头）"
    if None not in (close, upper, lower) and upper > lower:
        position = (close - lower) / (upper - lower) * 100.0
        snapshot["boll_position_pct"] = round(position, 1)
    if None not in (atr14, close) and close:
        snapshot["atr_pct"] = round(atr14 / close * 100.0, 2)
    if None not in (volume, vol_ma20) and vol_ma20:
        snapshot["volume_ratio"] = round(volume / vol_ma20, 2)
    return snapshot


def describe(df: pd.DataFrame, ticker: str, source_label: str = "") -> str:
    """把快照渲染成给 LLM 看的文本（这是「工具返回」的内容）。"""
    snapshot = latest_snapshot(df)
    if not snapshot:
        return f"[技术面] {ticker}：数据不足，无法计算指标。"

    prefix = f"{source_label}" if source_label else ""
    lines = [
        f"[技术面{prefix}] {ticker} 最近 {snapshot['bars']} 个交易日",
        f"- 最新收盘 {snapshot.get('close')}，当日涨跌 {snapshot.get('pct_change')}%",
        (
            f"- 均线：MA5={snapshot.get('ma5')}，MA10={snapshot.get('ma10')}，"
            f"MA20={snapshot.get('ma20')}，MA60={snapshot.get('ma60')}"
        ),
        f"- 均线形态：{snapshot.get('ma_trend', '数据不足')}",
        f"- RSI(14)={snapshot.get('rsi14')}（{snapshot.get('rsi_zone', '—')}）",
        f"- MACD：DIF={snapshot.get('dif')}，DEA={snapshot.get('dea')}，{snapshot.get('macd_state', '—')}",
    ]
    if snapshot.get("boll_upper") is not None:
        lines.append(
            f"- 布林带：上轨 {snapshot.get('boll_upper')} / 下轨 {snapshot.get('boll_lower')}"
            f"，当前位于带内 {snapshot.get('boll_position_pct')}% 位置"
        )
    if snapshot.get("atr_pct") is not None:
        lines.append(f"- 波动率：ATR(14) 占价格 {snapshot.get('atr_pct')}%")
    if snapshot.get("volume_ratio") is not None:
        lines.append(f"- 量能：最新成交量为 20 日均量的 {snapshot.get('volume_ratio')} 倍")
    return "\n".join(lines)
