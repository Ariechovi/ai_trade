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
        """价格概览：最新价、区间涨跌幅、区间高低位置、年化波动率。

何时使用：任何技术分析的**第一步**，先建立「当前处于什么位置」的基准；
后续的指标、信号、支撑压力都要放在这个区间位置里解读。

何时不要用：不要用它替代具体指标（用 compute_indicators）；
不要用它判断趋势方向（用 detect_crossover_signals）。

返回：最新收盘、区间涨跌%、区间最高/最低、当前在区间中的百分位、年化波动率。
不包含：均线、MACD、RSI 等指标数值。

局限：区间位置只反映近 days 日的相对位置，不代表长期估值高低。
"""
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
        """按需计算技术指标。wanted 为逗号分隔，可选：MA、RSI、MACD、BOLL、ATR、VOL。

何时使用：需要某个（或某几个）指标的具体数值时。**优先用它而不是一次性拿全部指标**——
只取真正需要的，报告更聚焦也更省 token。

何时不要用：需要交叉信号的时间点（用 detect_crossover_signals）；
需要支撑压力位（用 find_support_resistance）。

返回：所请求指标的当前值（均线族、RSI、MACD 的 DIF/DEA/柱、布林三轨、ATR、成交量）。
不包含：指标的信号含义判断（不告诉你是金叉还是死叉）。

局限：只给当前值，不给历史序列；判断信号「刚发生」还是「已持续」需配合
detect_crossover_signals。指标名写错时返回可选清单。
"""
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
        """检测最近出现的均线金叉/死叉与 MACD 金叉/死叉，并给出距今交易日数。

何时使用：判断「信号是新出现的还是已持续很久」时。这是把静态指标变成
动态信号的关键一步——「今天金叉」和「15 天前金叉」含义完全不同。

何时不要用：需要指标的具体数值（用 compute_indicators）；
需要 K 线形态（用 detect_kline_patterns）。

返回：MA5/MA20、MA10/MA60 与 MACD 各自最近一次金叉或死叉，以及距今交易日数。
不包含：DIF/DEA 的绝对位置。

局限：**必须自行检查零轴位置**——DIF/DEA 同为负时的金叉往往是弱势反弹而非趋势反转；
均线交叉本身滞后，不能单独作为买卖依据。
"""
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
        """识别当前价上方的压力位与下方的支撑位（近端极值 + 布林带）。

何时使用：需要给出具体**观察价位**时——「突破哪个价算有效」「跌破哪个价算失守」。
结论落到价位上才有可操作性。

何时不要用：不要把它当预测（支撑压力是参考区间，不是承诺点位）。

返回：近 60 日高点构成的压力位（升序）、近 60 日低点构成的支撑位（降序）、布林三轨。
不包含：成交密集区、缺口位、筹码峰。

局限：只用价格极值，未考虑成交量分布；点位数量有限（各 3 个）；
强势股常直接穿过压力位，必须结合量能判断有效性。
"""
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
        """分析量能：量比、放量缩量、量价配合关系、近期量能趋势。

何时使用：**验证其他技术信号时**。任何突破或金叉，没有量能配合都不可信——
「放量突破」与「缩量反弹」有本质区别。

何时不要用：不要用成交量推断资金流向（工具不提供主力/北向数据）。

返回：最新成交量与 20 日均量之比、量价配合判定、近 5 日均量相对前期的趋势。
不包含：资金流向、龙虎榜、大单明细。

局限：单日量比噪声高，应看近 5 日均量趋势；解禁、指数调仓会造成异常放量。
"""
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

    @tool
    def detect_kline_patterns(ticker: str, days: int = 60) -> str:
        """识别近端 K 线形态：长上影/长下影、十字星、三连阳/三连阴、跳空缺口。

何时使用：需要判断「最近几根 K 线的多空力量对比」时，用于补充指标类信号
（如 MACD 金叉之后，是否有长上影线显示上方抛压）。

何时不要用：不要用它做长期趋势判断（用 detect_crossover_signals）；
不要用单根 K 线形态推翻均线结构的结论。

返回：近 10 个交易日内的形态清单，以及最近 3 根 K 线的阴阳数量与跳空情况。
不包含：经典的多根组合形态库（头肩顶、三角形等）。

局限：单根 K 线形态的预测力有限，必须结合位置（支撑压力）与量能才有意义；
未做形态的严格确认（如上下影线长度的行业差异）。
"""
        df = frame(ticker, int(days))
        recent = df.tail(10)
        hits: List[str] = []
        for _, row in recent.iterrows():
            open_p = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            span = high - low
            if span <= 0:
                continue
            body = abs(close - open_p)
            upper = high - max(open_p, close)
            lower = min(open_p, close) - low
            date = str(row["date"])[:10]
            if body / span < 0.1:
                hits.append(f"{date} 十字星（多空胶着，变盘信号）")
            elif upper > 2 * body and upper / span > 0.5:
                hits.append(f"{date} 长上影线（上方抛压明显）")
            elif lower > 2 * body and lower / span > 0.5:
                hits.append(f"{date} 长下影线（下方承接较强）")

        tail3 = df.tail(3)
        bullish = [float(r["close"]) > float(r["open"]) for _, r in tail3.iterrows()]
        if all(bullish):
            hits.append("最近 3 根 K 线连阳（短线走强）")
        elif not any(bullish):
            hits.append("最近 3 根 K 线连阴（短线走弱）")

        if len(df) >= 2:
            last, prev = df.iloc[-1], df.iloc[-2]
            if float(last["low"]) > float(prev["high"]):
                hits.append(
                    f"向上跳空缺口（{float(prev['high']):.2f} → {float(last['low']):.2f}）"
                )
            elif float(last["high"]) < float(prev["low"]):
                hits.append(
                    f"向下跳空缺口（{float(prev['low']):.2f} → {float(last['high']):.2f}）"
                )
        if not hits:
            hits.append("近 10 个交易日未见典型单根形态")
        return (
            f"[K线形态{service.source_label(ticker)}] {ticker}\n"
            + "\n".join(f"- {item}" for item in hits)
        )

    return [
        get_price_overview,
        compute_indicators,
        detect_crossover_signals,
        find_support_resistance,
        analyze_volume,
        detect_kline_patterns,
    ]
