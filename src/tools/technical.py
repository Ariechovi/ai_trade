"""技术面工具集（5 个）。

对应 TradingAgents-CN 的 `core/tools/implementations/technical/`——
V3 把技术域拆成 indicators / candlestick_patterns / chart_patterns /
crossover_signals / support_resistance / volume_analysis 等多个文件。
这里按同样的思路，覆盖「概览 → 指标 → 信号 → 位置 → 量能」五个角度。

设计要点：**指标原语在 data/indicators.py，业务判断在工具里**。
（V3 也是这么分的：dataflows 出指标数列，tools 出分析结论。）
"""

from __future__ import annotations

from typing import Any, List

import numpy as np
import pandas as pd
from langchain_core.tools import tool

from data import indicators


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if number != number:  # NaN
        return "N/A"
    return f"{number:.{digits}f}"


def build_technical_tools(service) -> List[Any]:
    """构建技术面工具集。"""

    def frame(ticker: str, days: int) -> pd.DataFrame:
        return service.get_price_history(ticker, days)

    @tool
    def get_price_overview(ticker: str, days: int = 60) -> str:
        """获取价格概览：最新价、区间涨跌幅、区间高低位置、年化波动率。"""
        df = frame(ticker, int(days))
        close = df["close"]
        last = float(close.iloc[-1])
        change = (last / float(close.iloc[0]) - 1.0) * 100.0
        high, low = float(df["high"].max()), float(df["low"].min())
        position = (last - low) / (high - low) * 100.0 if high > low else 0.0
        annual_vol = float(close.pct_change().dropna().std() * np.sqrt(250) * 100.0)
        return (
            f"[价格概览{service.source_label(ticker)}] {ticker} 近 {len(df)} 个交易日：\n"
            f"- 最新收盘 {last:.2f}，区间涨跌 {change:+.2f}%\n"
            f"- 区间最高 {high:.2f} / 最低 {low:.2f}，当前处于区间 {position:.1f}% 位置\n"
            f"- 年化波动率 {annual_vol:.1f}%"
        )

    @tool
    def compute_indicators(ticker: str, days: int = 120, wanted: str = "MA,RSI,MACD") -> str:
        """按需计算技术指标。wanted 为逗号分隔，可选：MA、RSI、MACD、BOLL、ATR、VOL。"""
        df = frame(ticker, int(days))
        data = indicators.compute(df)
        last = data.iloc[-1]
        picked = [w.strip().upper() for w in str(wanted).replace("，", ",").split(",") if w.strip()]
        lines = [f"[技术指标{service.source_label(ticker)}] {ticker}（{len(data)} 根 K 线）"]

        if "MA" in picked or "MA5" in picked:
            lines.append(
                f"- 均线：MA5={_fmt(last.get('ma5'))} MA10={_fmt(last.get('ma10'))} "
                f"MA20={_fmt(last.get('ma20'))} MA60={_fmt(last.get('ma60'))}"
            )
        if "RSI" in picked:
            rsi_value = _fmt(last.get("rsi14"), 1)
            lines.append(f"- RSI(14)={rsi_value}")
        if "MACD" in picked:
            lines.append(
                f"- MACD：DIF={_fmt(last.get('dif'), 3)} DEA={_fmt(last.get('dea'), 3)} "
                f"柱={_fmt(last.get('macd_hist'), 3)}"
            )
        if "BOLL" in picked:
            lines.append(
                f"- 布林带：上轨 {_fmt(last.get('boll_upper'))} / 中轨 {_fmt(last.get('boll_mid'))} "
                f"/ 下轨 {_fmt(last.get('boll_lower'))}"
            )
        if "ATR" in picked:
            lines.append(f"- ATR(14)={_fmt(last.get('atr14'))}")
        if "VOL" in picked:
            lines.append(f"- 成交量={_fmt(last.get('volume'), 0)}，20 日均量={_fmt(last.get('vol_ma20'), 0)}")
        if len(lines) == 1:
            lines.append("- 未识别到有效指标名，可选：MA、RSI、MACD、BOLL、ATR、VOL")
        return "\n".join(lines)

    @tool
    def detect_crossover_signals(ticker: str, days: int = 120) -> str:
        """检测最近出现的均线金叉/死叉与 MACD 金叉/死叉，并给出距今交易日数。"""
        df = frame(ticker, int(days))
        data = indicators.compute(df)

        def last_cross(up: pd.Series, down: pd.Series, label: str) -> str:
            for i in range(len(up) - 1, -1, -1):
                if i < 1:
                    break
                if bool(up.iloc[i]):
                    return f"{label} 金叉（{len(up) - 1 - i} 个交易日前）"
                if bool(down.iloc[i]):
                    return f"{label} 死叉（{len(down) - 1 - i} 个交易日前）"
            return f"{label} 近期无交叉"

        signals = []
        for fast, slow in ((5, 20), (10, 60)):
            f, s = data[f"ma{fast}"], data[f"ma{slow}"]
            up = (f.shift(1) <= s.shift(1)) & (f > s)
            down = (f.shift(1) >= s.shift(1)) & (f < s)
            signals.append(last_cross(up, down, f"MA{fast}/MA{slow}"))
        dif, dea = data["dif"], data["dea"]
        up = (dif.shift(1) <= dea.shift(1)) & (dif > dea)
        down = (dif.shift(1) >= dea.shift(1)) & (dif < dea)
        signals.append(last_cross(up, down, "MACD"))
        return f"[交叉信号{service.source_label(ticker)}] {ticker}：\n" + "\n".join(
            f"- {item}" for item in signals
        )

    @tool
    def find_support_resistance(ticker: str, days: int = 120) -> str:
        """识别当前价上方的压力位与下方的支撑位（近端极值 + 布林带）。"""
        df = frame(ticker, int(days))
        data = indicators.compute(df)
        close = float(data["close"].iloc[-1])
        recent = data.tail(60)
        resistance = sorted({round(float(x), 2) for x in recent["high"] if float(x) > close})[:3]
        support = sorted({round(float(x), 2) for x in recent["low"] if float(x) < close}, reverse=True)[:3]
        upper = float(data["boll_upper"].iloc[-1])
        lower = float(data["boll_lower"].iloc[-1])
        return (
            f"[支撑压力{service.source_label(ticker)}] {ticker} 当前 {close:.2f}\n"
            f"- 上方压力位（近 60 日高点）：{resistance if resistance else '已在区间新高附近'}\n"
            f"- 下方支撑位（近 60 日低点）：{support if support else '已在区间新低附近'}\n"
            f"- 布林带边界：上轨 {upper:.2f} / 下轨 {lower:.2f}"
        )

    @tool
    def analyze_volume(ticker: str, days: int = 60) -> str:
        """分析量能：量比、放量缩量、量价配合关系、近期量能趋势。"""
        df = frame(ticker, int(days))
        volume = df["volume"]
        close = df["close"]
        last_vol = float(volume.iloc[-1])
        vol_ma20 = float(volume.tail(20).mean())
        ratio = last_vol / vol_ma20 if vol_ma20 else 0.0
        price_change = float(close.pct_change().iloc[-1] * 100.0)
        if ratio >= 1.2 and price_change > 0:
            relation = "放量上涨（量价齐升，资金介入）"
        elif ratio >= 1.2 and price_change < 0:
            relation = "放量下跌（抛压较重）"
        elif ratio <= 0.8 and price_change > 0:
            relation = "缩量上涨（跟风不足）"
        elif ratio <= 0.8 and price_change < 0:
            relation = "缩量下跌（抛压减轻）"
        else:
            relation = "量价平稳"
        recent5 = float(volume.tail(5).mean())
        prev20 = float(volume.iloc[-25:-5].mean()) if len(volume) > 25 else vol_ma20
        trend = "温和放大" if recent5 > prev20 * 1.1 else ("明显萎缩" if recent5 < prev20 * 0.9 else "基本持平")
        return (
            f"[量能分析{service.source_label(ticker)}] {ticker}\n"
            f"- 最新成交量 {last_vol:,.0f}，为 20 日均量的 {ratio:.2f} 倍\n"
            f"- 量价配合：{relation}\n"
            f"- 近 5 日均量相对前期：{trend}"
        )

    return [
        get_price_overview,
        compute_indicators,
        detect_crossover_signals,
        find_support_resistance,
        analyze_volume,
    ]
