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

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Tuple

import pandas as pd

from .base import BaseDataSource, DataSourceError

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

# 财务序列要抽取的指标（key → 匹配关键词）
_SERIES_SPECS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("roe", ("净资产收益率",)),
    ("revenue", ("营业总收入", "营业收入")),
    ("net_profit", ("归母净利润", "净利润")),
    ("debt_ratio", ("资产负债率",)),
    ("gross_margin", ("销售毛利率", "毛利率")),
    ("net_margin", ("销售净利率", "净利率")),
    ("eps", ("基本每股收益", "每股收益")),
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
        try:
            import akshare as ak  # type: ignore
        except ImportError as exc:  # pragma: no cover - 环境问题
            raise DataSourceError(f"akshare 未安装：{exc}") from exc
        self._ak = ak

    # ---------------- 行情：新浪 → 东财 ----------------
    def _history_sina(self, ticker: str, days: int) -> pd.DataFrame:
        raw = self._ak.stock_zh_a_daily(symbol=_market_prefix(ticker), adjust="qfq")
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
    def _financial_rows(self, ticker: str) -> Dict[str, Any]:
        try:
            raw = self._ak.stock_financial_abstract(symbol=ticker)
        except Exception as exc:
            raise DataSourceError(f"akshare 取 {ticker} 财务摘要失败：{exc}") from exc
        if raw is None or len(raw) == 0:
            raise DataSourceError(f"akshare 未返回 {ticker} 的财务摘要")

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
        try:
            raw = self._ak.stock_financial_abstract(symbol=ticker)
        except Exception as exc:
            raise DataSourceError(f"akshare 取 {ticker} 财务序列失败：{exc}") from exc
        if raw is None or len(raw) == 0:
            raise DataSourceError(f"akshare 未返回 {ticker} 的财务摘要")

        period_cols = sorted(
            [c for c in raw.columns if str(c).isdigit() and len(str(c)) == 8],
            reverse=True,
        )[: max(1, int(periods))]
        if not period_cols:
            raise DataSourceError(f"akshare 财务摘要缺少报告期列（实际列：{list(raw.columns)[:8]}）")

        label_col = "指标" if "指标" in raw.columns else raw.columns[1]
        rows = [(str(row.get(label_col, "")).strip(), row) for _, row in raw.iterrows()]
        series: Dict[str, Dict[str, Any]] = {}
        for key, keywords in _SERIES_SPECS:
            for label, row in rows:
                if not any(keyword in label for keyword in keywords):
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
