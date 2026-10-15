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


def _latest(series: Dict[str, Dict[str, Any]], key: str):
    """取某个财务指标最新报告期的值（报告期标签形如 20260630，字符串排序即时间序）。"""
    values = series.get(key) or {}
    if not values:
        return None
    return values[max(values)]


def build_fundamental_tools(service) -> List[Any]:
    """构建基本面工具集。"""

    @tool
    def get_financial_statements(ticker: str) -> str:
        """最新报告期的核心财务指标（ROE、营收、净利、负债率、毛利率、净利率、EPS）。

何时使用：基本面分析的**第一步**，建立财务画像。

何时不要用：不要只凭这张表下结论——成长性用 analyze_growth、
利润质量用 check_cashflow_quality、安全性用 analyze_solvency。

返回：公司名、行业、报告期、ROE、营业总收入、归母净利润、同比增速、资产负债率、
毛利率、净利率、EPS。
不包含：现金流、周转率、杜邦拆解、估值。

局限：**未标注口径**——返回的净利率为含少数股东损益口径，与
「归母净利润 ÷ 营收」可能不同，需自行交叉验算；数值均为报告期累计口径，非单季值。
"""
        return service.get_fundamentals(ticker)

    @tool
    def analyze_growth(ticker: str, periods: int = 8) -> str:
        """分析成长性：营收与归母净利润的同比增速，以及最近几期的变化趋势。

何时使用：判断「增长是否失速」时。单期同比可能失真，要多期一起看。

何时不要用：不要用它解释估值（用 estimate_valuation）；
不要用增速判断盈利质量（用 check_cashflow_quality）。

返回：营收与归母净利润的逐期同比（同报告期对比去年同期）与最新两期金额。
不包含：环比（单季）增速、未来预测。

局限：**低基数下百分比严重失真**——净利率接近 0 时，同比 +84% 可能只是从极低基数反弹，
必须结合净利率绝对值判断，不要把百分比本身当作成长性证据。
"""
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
        """评估财务健康度：盈利能力、杠杆水平、利润率质量，并给出粗评分。

何时使用：快速建立「财务是否稳健」的总体印象。

何时不要用：评分只是粗筛，**不要替代分项分析**——杠杆看 analyze_solvency，
利润质量看 check_cashflow_quality。

返回：满分 6 的评分与分项说明（ROE、资产负债率、毛利率、净利率）。
不包含：现金流质量、周转率。

局限：**杠杆打分未区分行业**——银行/地产天然高负债会被误判为「弱」；
缺失指标会直接扣分，需区分「数据缺失」与「真的差」。
"""
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
        """估算估值：用最新报告期 EPS 年化后与现价计算 PE，并由 EPS/ROE 推导 PB。

何时使用：需要判断「当前价格对应什么估值水平」时。

何时不要用：**不要跨行业比较 PE/PB**；不要在利润接近 0 时用 PE 下结论
（此时 PE 严重失真，应改用 PB）。

返回：现价、报告期、按年化系数估算的 PE、由 EPS/ROE 反推的每股净资产与 PB、
以及估算假设说明。
不包含：历史估值分位、同行相对估值、股息率。

局限：**PE 是报告期口径估算，不是 TTM**；年化系数按报告期简单外推，
对强周期公司误差很大；净利润极小时 PE 无意义，应显式说明而非给出数字。
"""
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
        """与同业对比核心财务指标。peers 为逗号分隔的股票代码，建议不超过 4 个。

何时使用：判断「公司在行业中处于什么位置」，也是区分
「行业性问题 vs 公司自身问题」的关键一步。

何时不要用：不要跨行业对比（指标不可比）；不要用绝对数值判断好坏，要看相对位次。

返回：基准股与各同业公司的 ROE、毛利率、净利率、资产负债率。
不包含：估值对比、市占率、产品结构。

局限：**同业选择由你决定**，选错参照系会得出错误结论；
应确认同业报告期一致；任一标的取数失败会单独标注。
"""
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

    @tool
    def analyze_dupont(ticker: str) -> str:
        """杜邦分析：把 ROE 拆成「销售净利率 × 总资产周转率 × 权益乘数」。

何时使用：需要解释「ROE 为什么高/低」时。它回答的是回报来源问题——
高利润率驱动（品牌/消费）、高周转驱动（贸易/制造）、还是高杠杆驱动（金融/地产）。

何时不要用：不要用它判断公司好坏——高 ROE 可能来自高杠杆。判断质量请配合
check_cashflow_quality；判断行业位置请用 compare_peers。

返回：三个拆解因子 + 乘积累计的 ROE + 与工具返回 ROE 的对照 + 归因判断。
不包含：成长性、估值、现金流质量。

局限：报告期口径，与单季/TTM 口径会有差异；不同数据源的因子定义可能不同，
乘积累计值与直接返回的 ROE 允许存在小差异。
"""
        series = service.get_financial_series(ticker, 1)
        net_margin = _num(_latest(series, "net_margin"))
        turnover = _num(_latest(series, "total_asset_turnover"))
        multiplier = _num(_latest(series, "equity_multiplier"))
        roe = _num(_latest(series, "roe"))
        lines = [f"[杜邦分析{service.source_label(ticker)}] {ticker}"]
        if None not in (net_margin, turnover, multiplier):
            product = net_margin * turnover * multiplier
            lines.append(
                f"- ROE ≈ 销售净利率 {net_margin:.2f}% × 总资产周转率 {turnover:.3f} "
                f"× 权益乘数 {multiplier:.3f} ≈ {product:.2f}%"
            )
            if roe is not None:
                lines.append(f"- 与工具返回的 ROE {roe:.2f}% 对照（存在口径差异属正常）")
        else:
            lines.append("- 缺少拆解所需字段（销售净利率 / 总资产周转率 / 权益乘数）中至少一项")
        if net_margin is not None and turnover is not None:
            if net_margin >= 15 and turnover < 1:
                lines.append("- 归因：**高利润率驱动型**（典型消费品/品牌型），资产周转较慢")
            elif net_margin < 10 and turnover >= 1:
                lines.append("- 归因：**高周转型**（典型贸易/制造），利润率薄靠周转拉动")
            elif (multiplier or 0) > 2:
                lines.append("- 归因：利润率与周转率均不突出，**杠杆是回报的主要来源**")
            else:
                lines.append("- 归因：利润率与周转率均不突出，且杠杆不高，ROE 缺乏支撑")
        lines.append("- 口径说明：以上为报告期指标快照，非单季或 TTM 口径")
        return "\n".join(lines)

    @tool
    def check_cashflow_quality(ticker: str) -> str:
        """检验利润含金量：经营现金流与净利润的匹配度，识别「纸面利润」。

何时使用：判断「利润是不是真的」时。净利润高但经营现金流长期跟不上，
通常意味着利润停留在应收账款或存货里，兑现质量存疑。

何时不要用：不要用它替代偿债能力判断（用 analyze_solvency）；不要用它判断成长性
（用 analyze_growth）。

返回：经营现金流量净额、归母净利润、两者比值、现金流与利润的差额、质量判定。
不包含：投资/筹资现金流、有息负债结构。

局限：报告期为累计口径（半年报即半年度累计），不能当作单季数据；
不同行业现金流季节性差异很大（如零售 Q4 集中回款），跨行业比较需谨慎。
"""
        series = service.get_financial_series(ticker, 1)
        ocf = _num(_latest(series, "operating_cashflow"))
        profit = _num(_latest(series, "net_profit"))
        ratio = _num(_latest(series, "ocf_to_profit"))
        if ratio is None and None not in (ocf, profit) and profit:
            ratio = ocf / profit
        lines = [f"[利润含金量{service.source_label(ticker)}] {ticker}"]
        if None not in (ocf, profit):
            lines.append(f"- 经营现金流量净额 {_yi(ocf)}，归母净利润 {_yi(profit)}")
            lines.append(f"- 两者差额 {_yi(ocf - profit)}")
        if ratio is not None:
            if ratio >= 1.2:
                verdict = "优秀——现金回款强于账面利润"
            elif ratio >= 1.0:
                verdict = "良好——现金流与利润基本匹配"
            elif ratio >= 0.5:
                verdict = "一般——利润兑现质量待观察"
            else:
                verdict = "偏弱——存在「纸面利润」风险，需核查应收账款与存货"
            lines.append(f"- 经营现金流 / 归母净利润 = {ratio:.2f}，判定：{verdict}")
        if len(lines) == 1:
            lines.append("- 工具未返回经营现金流或净利润字段，该维度仍待验证")
        lines.append("- 口径说明：报告期为累计口径（如半年报为半年度累计），非单季度值")
        return "\n".join(lines)

    @tool
    def analyze_solvency(ticker: str) -> str:
        """偿债能力与资本结构：流动比率、速动比率、资产负债率、权益乘数。

何时使用：评估财务安全性、判断是否存在偿债压力或高杠杆风险时。
对周期股尤为重要——周期底部时高杠杆会放大风险。

何时不要用：不要据此判断盈利质量（用 check_cashflow_quality）；
不要跨行业直接比较杠杆水平。

返回：四项指标及各自的分档判断。
不包含：有息负债明细、到期结构、担保与表外负债。

局限：**不同行业杠杆天然不同**（银行/地产显著高于制造业），横向比较必须同行业；
流动比率/速动比率对金融类企业基本不适用，缺失时不应写成风险。
"""
        series = service.get_financial_series(ticker, 1)
        current = _num(_latest(series, "current_ratio"))
        quick = _num(_latest(series, "quick_ratio"))
        debt = _num(_latest(series, "debt_ratio"))
        multiplier = _num(_latest(series, "equity_multiplier"))
        lines = [f"[偿债能力{service.source_label(ticker)}] {ticker}"]
        if current is not None:
            verdict = "充裕" if current >= 2 else ("尚可" if current >= 1 else "偏紧")
            lines.append(f"- 流动比率 {current:.2f}（{verdict}）")
        if quick is not None:
            verdict = "充裕" if quick >= 1 else "偏紧（存货占比较高）"
            lines.append(f"- 速动比率 {quick:.2f}（{verdict}）")
        if debt is not None:
            lines.append(f"- 资产负债率 {debt:.2f}%")
        if multiplier is not None:
            lines.append(f"- 权益乘数 {multiplier:.3f}（越大杠杆越高）")
        if len(lines) == 1:
            lines.append("- 工具未返回偿债能力字段，该维度仍待验证")
        lines.append("- 注意：杠杆水平须与同行业比较才有意义（银行/地产天然高负债）")
        return "\n".join(lines)

    return [
        get_financial_statements,
        analyze_growth,
        assess_financial_health,
        estimate_valuation,
        compare_peers,
        analyze_dupont,
        check_cashflow_quality,
        analyze_solvency,
    ]
