"""AKShare 数据源：真实 A 股数据（免费、无需 token）。

对应 TradingAgents-CN 的 `tradingagents/dataflows/providers/china/akshare.py`。
V3 的 akshare provider 内部同样是「多接口互备」（fetch_spot_data_sina /
fetch_spot_data_em），这里沿用同样的思路：

    行情    新浪优先 → 东财兜底   （新浪稳定、不依赖东财域名）
    财务    东财财务摘要
    新闻    东财新闻

为什么按能力而不是按厂商互备？因为不同端点挂的原因不同（域名被封、限流、
字段变更），多一条路径就多一次机会。这也是「部分降级」的做法：
公司名拿不到不该拖垮财务指标。

两个约定：
  1. akshare 是同步库、会发网络请求 → 所有调用都包 try/except，
     统一抛 DataSourceError，交给上层降级；
  2. 原始列名（中文或混合）在这一层被标准化成
     date/open/high/low/close/volume。
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Tuple

import pandas as pd

from .base import BaseDataSource, DataSourceError

# 底层 socket 超时（秒）。akshare 用 requests/urllib3，未显式传 timeout 时会退回
# socket 默认值——不设上限的话，网络抖动会让它**无限挂起**（这就是"卡住"的根因）。
# 可用环境变量 `AKSHARE_TIMEOUT` 覆盖。
DEFAULT_TIMEOUT = 30.0
# 东财日线接口的中文列名 → 标准列名（新浪接口本身就是英文列名）
_EM_COLUMN_MAP = {
    "日期": "date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
}

_REQUIRED = ("date", "open", "high", "low", "close", "volume")

# 国内数据源域名：必须**直连**，不能走系统代理。
#
# 为什么要在代码里显式处理：Windows 上一旦配了系统代理（WinINet 设置），
# Python 的 requests 就会读到它 —— 而且**即使 HTTP_PROXY 环境变量是空的也照样读到**，
# 因为 urllib 在 Windows 上会去读注册表。于是对东财/新浪的请求也发给代理，
# 而这些域名通常不在代理的放行规则里，表现就是：
#   - 东财 → ProxyError: Unable to connect to proxy
#   - 新浪 → 拿到空响应 → JSONDecodeError: No value to decode
# 看起来像"数据源挂了"，实际是被代理拦了。
# V3 的 AGENTS.md 里也是同一条：国内数据源域名需在 NO_PROXY 中直连。
_DIRECT_DOMAINS = (
    "eastmoney.com",   # 东财：行情 / 财务 / 新闻 / 个股信息
    "sina.com.cn",     # 新浪：日线与指数
    "sinajs.cn",       # 新浪行情脚本域
    "sse.com.cn",      # 上交所
    "szse.cn",         # 深交所
)


def _ensure_direct_connection() -> None:
    """把国内数据源域名并入 NO_PROXY，让 requests 绕过系统代理。"""
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    hosts = [h for h in current.replace(" ", "").split(",") if h]
    for domain in _DIRECT_DOMAINS:
        if domain not in hosts:
            hosts.append(domain)
    merged = ",".join(hosts)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged

# 财务序列要抽取的指标：(key, 匹配关键词, 排除关键词)
# akshare 的财务摘要按「常用指标 / 每股指标 / 盈利能力 / 收益质量 / 成长能力 /
# 财务风险 / 营运能力」分块，同一指标名会出现多次——所以先出现的先被认领，
# 并且对「净利润」这种会被「归母净利润」误匹配的名字加排除词。
_SERIES_SPECS: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    # 利润与规模
    ("revenue", ("营业总收入", "营业收入"), ()),
    ("net_profit", ("归母净利润",), ()),
    ("net_profit_total", ("净利润",), ("归母", "扣非")),
    ("deducted_profit", ("扣非净利润",), ()),
    ("operating_cashflow", ("经营现金流量净额",), ()),
    ("equity", ("股东权益合计",), ()),
    ("goodwill", ("商誉",), ()),
    # 成长能力
    ("revenue_yoy", ("营业总收入增长率", "营业收入增长率"), ()),
    ("profit_yoy", ("归属母公司净利润增长率", "归母净利润增长率"), ()),
    # 盈利能力
    ("roe", ("净资产收益率",), ()),
    ("roa", ("总资产报酬率",), ()),
    ("gross_margin", ("毛利率",), ()),
    ("net_margin", ("销售净利率",), ()),
    ("expense_ratio", ("期间费用率",), ()),
    # 收益质量（利润含金量）
    ("ocf_to_profit", ("净现金/归属", "净现金/母公司"), ()),
    ("ocf_to_revenue", ("经营性现金净流量/营业总收入", "经营活动净现金/销售收入"), ()),
    # 财务风险
    ("debt_ratio", ("资产负债率",), ()),
    ("equity_multiplier", ("权益乘数",), ("少数",)),
    ("current_ratio", ("流动比率",), ()),
    ("quick_ratio", ("速动比率",), ("保守",)),
    # 营运能力
    ("total_asset_turnover", ("总资产周转率",), ()),
    ("receivable_turnover", ("应收账款周转率",), ()),
    ("inventory_turnover", ("存货周转率",), ()),
    # 每股
    ("eps", ("基本每股收益",), ()),
    ("bps", ("每股净资产",), ("摊薄", "调整", "最新")),
    ("ocf_per_share", ("每股经营现金流",), ()),
)


def _market_prefix(ticker: str) -> str:
    """新浪/腾讯接口需要市场前缀：sh600519 / sz000001 / bj830799。"""
    code = ticker.strip().lower()
    if code.startswith(("sh", "sz", "bj")):
        return code
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def _pick(rows: Dict[str, Any], *keywords: str) -> Any:
    """在财务摘要的各行里做模糊匹配（akshare 的指标名各版本略有差异）。"""
    for key, value in rows.items():
        if any(keyword in key for keyword in keywords):
            return value
    return None


def _normalize(frame: pd.DataFrame, days: int, source: str) -> pd.DataFrame:
    missing = [col for col in _REQUIRED if col not in frame.columns]
    if missing:
        raise DataSourceError(f"{source} 日线缺少列 {missing}（实际列：{list(frame.columns)}）")
    out = frame[list(_REQUIRED)].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna().sort_values("date").tail(int(days)).reset_index(drop=True)
    if out.empty:
        raise DataSourceError(f"{source} 返回的日线为空")
    return out


class AkshareDataSource(BaseDataSource):
    """真实 A 股数据源（多接口互备）。"""

    name = "akshare"

    def __init__(self) -> None:
        # 必须赶在第一次请求之前设置 —— requests 是在发请求时读这个环境变量的
        _ensure_direct_connection()
        try:
            import akshare as ak  # type: ignore
        except ImportError as exc:  # pragma: no cover - 环境问题
            raise DataSourceError(f"akshare 未安装：{exc}") from exc
        self._ak = ak
        # 进程内 memo：同一个上游数据只拉一次（详见 _history_sina / _raw_financial）
        self._memo: Dict[str, Any] = {}
        socket.setdefaulttimeout(
            float(os.getenv("AKSHARE_TIMEOUT", str(DEFAULT_TIMEOUT)))
        )

    # ---------------- 行情：新浪 → 东财 ----------------
    def _history_sina(self, ticker: str, days: int) -> pd.DataFrame:
        # 新浪接口不支持按日期区间取数，每次都返回全量（6000+ 行）。
        # 而不同工具用的 days 不同 → 文件缓存的 key 不同 → 同一只股票会被反复全量下载。
        # 这里用进程内 memo 把「同一 ticker 的原始数据」合并成一次网络请求，本地再切片。
        key = f"hist:sina:{ticker}"
        if key not in self._memo:
            self._memo[key] = self._ak.stock_zh_a_daily(
                symbol=_market_prefix(ticker), adjust="qfq"
            )
        raw = self._memo[key]
        if raw is None or len(raw) == 0:
            raise DataSourceError("新浪接口未返回数据")
        return _normalize(raw, days, "新浪")

    def _history_em(self, ticker: str, days: int) -> pd.DataFrame:
        end = datetime.today().strftime("%Y%m%d")
        start = (datetime.today() - timedelta(days=max(days * 2, 180))).strftime("%Y%m%d")
        raw = self._ak.stock_zh_a_hist(
            symbol=ticker, period="daily", start_date=start, end_date=end, adjust="qfq"
        )
        if raw is None or len(raw) == 0:
            raise DataSourceError("东财接口未返回数据")
        return _normalize(raw.rename(columns=_EM_COLUMN_MAP), days, "东财")

    def get_price_history(self, ticker: str, days: int) -> pd.DataFrame:
        errors: List[str] = []
        for label, fetch in (("新浪", self._history_sina), ("东财", self._history_em)):
            try:
                return fetch(ticker, days)
            except Exception as exc:
                errors.append(f"{label}: {type(exc).__name__}: {exc}")
        raise DataSourceError(f"akshare 取 {ticker} 日线失败（{' | '.join(errors)}）")

    # ---------------- 基本信息 ----------------
    def get_profile(self, ticker: str) -> Dict[str, Any]:
        try:
            info = self._ak.stock_individual_info_em(symbol=ticker)
        except Exception as exc:
            raise DataSourceError(f"akshare 取 {ticker} 基本信息失败：{exc}") from exc
        if info is None or len(info) == 0:
            raise DataSourceError(f"akshare 未返回 {ticker} 的基本信息")
        kv = dict(zip(info["item"], info["value"]))
        return {
            "ticker": ticker,
            "name": str(kv.get("股票简称", "")),
            "industry": str(kv.get("行业", "")),
            "market_cap": kv.get("总市值"),
            "listing_date": kv.get("上市时间"),
        }

    # ---------------- 财务 ----------------
    def _raw_financial(self, ticker: str) -> pd.DataFrame:
        """财务摘要原始表（进程内只拉一次）。

        `get_financial_metrics` 与 `get_financial_series` 都要这张表，
        不去重的话同一份数据会被拉 2~3 次——每个都是一次网络往返。
        """
        key = f"fin:{ticker}"
        if key not in self._memo:
            try:
                raw = self._ak.stock_financial_abstract(symbol=ticker)
            except Exception as exc:
                raise DataSourceError(f"akshare 取 {ticker} 财务摘要失败：{exc}") from exc
            if raw is None or len(raw) == 0:
                raise DataSourceError(f"akshare 未返回 {ticker} 的财务摘要")
            self._memo[key] = raw
        return self._memo[key]

    def _financial_rows(self, ticker: str) -> Dict[str, Any]:
        raw = self._raw_financial(ticker)
        # 列形如：选项 | 指标 | 20260630 | 20260331 | ...
        period_cols = [c for c in raw.columns if str(c).isdigit() and len(str(c)) == 8]
        if not period_cols:
            raise DataSourceError(f"akshare 财务摘要缺少报告期列（实际列：{list(raw.columns)[:8]}）")
        latest_period = sorted(period_cols, reverse=True)[0]
        label_col = "指标" if "指标" in raw.columns else raw.columns[1]
        rows: Dict[str, Any] = {}
        for _, row in raw.iterrows():
            label = str(row.get(label_col, "")).strip()
            if label:
                rows[label] = row.get(latest_period)
        rows["_period"] = str(latest_period)
        return rows

    def get_financial_metrics(self, ticker: str) -> Dict[str, Any]:
        rows = self._financial_rows(ticker)
        return {
            "period": rows.get("_period"),
            "roe": _pick(rows, "净资产收益率"),
            "revenue": _pick(rows, "营业总收入", "营业收入"),
            "net_profit": _pick(rows, "归母净利润", "净利润"),
            "revenue_yoy": _pick(rows, "营业总收入同比增长", "营业收入同比增长"),
            "profit_yoy": _pick(rows, "归母净利润同比增长", "净利润同比增长"),
            "debt_ratio": _pick(rows, "资产负债率"),
            "gross_margin": _pick(rows, "销售毛利率", "毛利率"),
            "net_margin": _pick(rows, "销售净利率", "净利率"),
            "eps": _pick(rows, "基本每股收益", "每股收益"),
        }

    # ---------------- 新闻 ----------------
    def get_news(self, ticker: str, limit: int) -> List[Dict[str, str]]:
        try:
            raw = self._ak.stock_news_em(symbol=ticker)
        except Exception as exc:
            raise DataSourceError(f"akshare 取 {ticker} 新闻失败：{exc}") from exc
        if raw is None or len(raw) == 0:
            raise DataSourceError(f"akshare 未返回 {ticker} 的新闻")
        items: List[Dict[str, str]] = []
        for _, row in raw.head(max(1, int(limit))).iterrows():
            items.append(
                {
                    "title": str(row.get("新闻标题", "")).strip(),
                    "published_at": str(row.get("发布时间", "")).strip(),
                    "source": str(row.get("文章来源", "")).strip(),
                }
            )
        return [item for item in items if item["title"]]

    # ---------------- 指数 ----------------
    def get_index_history(self, index_code: str, days: int) -> pd.DataFrame:
        try:
            raw = self._ak.stock_zh_index_daily(symbol=index_code)
        except Exception as exc:
            raise DataSourceError(f"akshare 取指数 {index_code} 失败：{exc}") from exc
        if raw is None or len(raw) == 0:
            raise DataSourceError(f"akshare 未返回指数 {index_code} 的数据")
        return _normalize(raw, days, "新浪指数")

    # ---------------- 多期财务 ----------------
    def get_financial_series(self, ticker: str, periods: int) -> Dict[str, Dict[str, Any]]:
        raw = self._raw_financial(ticker)
        period_cols = sorted(
            [c for c in raw.columns if str(c).isdigit() and len(str(c)) == 8],
            reverse=True,
        )[: max(1, int(periods))]
        if not period_cols:
            raise DataSourceError(f"akshare 财务摘要缺少报告期列（实际列：{list(raw.columns)[:8]}）")

        label_col = "指标" if "指标" in raw.columns else raw.columns[1]
        rows = [(str(row.get(label_col, "")).strip(), row) for _, row in raw.iterrows()]
        series: Dict[str, Dict[str, Any]] = {}
        for key, keywords, excludes in _SERIES_SPECS:
            for label, row in rows:
                if not any(keyword in label for keyword in keywords):
                    continue
                if excludes and any(bad in label for bad in excludes):
                    continue
                values: Dict[str, Any] = {}
                for period in period_cols:
                    try:
                        number = float(row.get(period))
                    except (TypeError, ValueError):
                        continue
                    if number == number:  # 过滤 NaN
                        values[str(period)] = number
                if values:
                    series[key] = values
                break
        if not series:
            raise DataSourceError(f"{ticker} 的财务摘要里没有匹配到已知指标")
        return series
