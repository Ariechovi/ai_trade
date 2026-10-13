"""基本面工具集（5 个）。

对应 TradingAgents-CN 的 `core/tools/implementations/fundamentals/`
（V3 有 financial_tools / standard_financial_tools / stock_fundamentals 等）。
这里覆盖「报表 → 成长 → 健康度 → 估值 → 同业对比」五个角度。

⚠️ 财务计算一定要写清假设。PE 用「报告期 EPS × 年化系数」估算，
这是近似值，不是严谨的 TTM——工具输出里必须标明，否则就是在误导使用者。
"""

from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.tools import tool


def _num(value: Any):
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _fmt(value: Any, digits: int = 2) -> str:
    number = _num(value)
    return "N/A" if number is None else f"{number:.{digits}f}"


def _yi(value: Any) -> str:
    number = _num(value)
    return "N/A" if number is None else f"{number / 1e8:.2f} 亿"


def build_fundamental_tools(service) -> List[Any]:
    """构建基本面工具集。"""

    @tool
    def get_financial_statements(ticker: str) -> str:
        """获取最新报告期的核心财务指标（ROE、营收、净利、负债率、毛利率、净利率、EPS）。"""
        return service.get_fundamentals(ticker)

    @tool
    def analyze_growth(ticker: str, periods: int = 8) -> str:
        """分析成长性：营收与归母净利润的同比增速，以及最近几期的变化趋势。"""
        series = service.get_financial_series(ticker, int(periods))

        def yoy(values: Dict[str, Any]) -> List[str]:
            lines = []
            for period in sorted(values, reverse=True):
                prev = str(int(period) - 10000)  # 去年同期（0630↔0630、1231↔1231）
                if prev in values and values[prev]:
                    growth = (values[period] / values[prev] - 1.0) * 100.0
                    lines.append(f"    {period}：{growth:+.1f}%（对比 {prev}）")
            return lines or ["    数据不足，无法计算同比"]

        revenue = series.get("revenue", {})
        profit = series.get("net_profit", {})
        latest = sorted(revenue, reverse=True)[0] if revenue else "—"
        return (
            f"[成长性{service.source_label(ticker)}] {ticker}（最新报告期 {latest}）\n"
            f"- 营收同比：\n" + "\n".join(yoy(revenue)) + "\n"
            f"- 归母净利润同比：\n" + "\n".join(yoy(profit)) + "\n"
            f"- 最新营收 {_yi(revenue.get(latest))}，最新归母净利润 {_yi(profit.get(latest))}"
        )

    @tool
    def assess_financial_health(ticker: str) -> str:
        """评估财务健康度：盈利能力、杠杆水平、利润率质量，并给出粗评分。"""
        metrics = service.get_metrics(ticker)
        roe = _num(metrics.get("roe"))
        debt = _num(metrics.get("debt_ratio"))
        gross = _num(metrics.get("gross_margin"))
        net = _num(metrics.get("net_margin"))
        score, notes = 0, []
        if roe is not None:
            score += 2 if roe >= 15 else (1 if roe >= 8 else 0)
            notes.append(f"ROE {roe:.2f}% " + ("（优秀）" if roe >= 15 else "（一般）" if roe >= 8 else "（偏低）"))
        if debt is not None:
            score += 2 if debt <= 50 else (1 if debt <= 70 else 0)
            notes.append(f"资产负债率 {debt:.2f}%")
        if gross is not None:
            score += 1 if gross >= 30 else 0
            notes.append(f"毛利率 {gross:.2f}%")
        if net is not None:
            score += 1 if net >= 10 else 0
            notes.append(f"净利率 {net:.2f}%")
        level = "强" if score >= 5 else ("中" if score >= 3 else "偏弱")
        return (
            f"[财务健康度{service.source_label(ticker)}] {ticker}（满分 6，得分 {score}，{level}）\n"
            + "\n".join(f"- {note}" for note in notes)
            + "\n- 注意：杠杆判断未区分行业，银行/地产等天然高负债行业需另行判断"
        )

    @tool
    def estimate_valuation(ticker: str) -> str:
        """估算估值：用最新报告期 EPS 年化后与现价计算 PE，并由 EPS/ROE 推导 PB。"""
        metrics = service.get_metrics(ticker)
        price = float(service.get_price_history(ticker, 5)["close"].iloc[-1])
        eps = _num(metrics.get("eps"))
        roe = _num(metrics.get("roe"))
        period = str(metrics.get("period") or "")
        factor = {"0630": 2.0, "0331": 4.0, "0930": 4.0 / 3.0, "1231": 1.0}.get(period[-4:], 1.0)
        if not eps:
            return f"[估值估算] {ticker} 缺少每股收益（EPS）数据，无法估算 PE。"
        annual_eps = eps * factor
        lines = [f"[估值估算{service.source_label(ticker)}] {ticker} 现价 {price:.2f}，报告期 {period or '—'}"]
        if annual_eps:
            lines.append(f"- PE ≈ {price / annual_eps:.1f} 倍（假设：{period} 的 EPS={eps:.2f} 按 ×{factor:g} 年化）")
        if roe:
            bps = eps / (roe / 100.0)  # 由 ROE = EPS / 每股净资产 反推
            lines.append(f"- 每股净资产 ≈ {bps:.2f}（由 EPS/ROE 反推），PB ≈ {price / bps:.2f} 倍")
        lines.append("- 说明：以上为报告期口径的近似估值，非严格 TTM；跨行业比较需谨慎")
        return "\n".join(lines)

    @tool
    def compare_peers(ticker: str, peers: str = "") -> str:
        """与同业对比核心财务指标。peers 为逗号分隔的股票代码，建议不超过 4 个。"""
        codes = [c.strip() for c in str(peers).replace("，", ",").split(",") if c.strip()][:4]
        if not codes:
            return "请提供同业股票代码，例如 peers=\"000001,600036\"。"
        targets = [ticker] + [c for c in codes if c != ticker]
        lines = [f"[同业对比] 以 {ticker} 为基准："]
        for code in targets:
            try:
                metrics = service.get_metrics(code)
            except Exception as exc:
                lines.append(f"- {code}：取数失败（{str(exc)[:60]}）")
                continue
            tag = "（基准）" if code == ticker else ""
            lines.append(
                f"- {code}{tag}：ROE={_fmt(metrics.get('roe'))}% "
                f"毛利率={_fmt(metrics.get('gross_margin'))}% "
                f"净利率={_fmt(metrics.get('net_margin'))}% "
                f"资产负债率={_fmt(metrics.get('debt_ratio'))}%"
            )
        return "\n".join(lines)

    return [
        get_financial_statements,
        analyze_growth,
        assess_financial_health,
        estimate_valuation,
        compare_peers,
    ]
