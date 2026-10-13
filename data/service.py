"""数据服务：多源优先级 + 自动降级 + 缓存。

对应 TradingAgents-CN 的 `tradingagents/dataflows/data_source_manager.py`。

职责边界（这个分层是 V3 的骨架，照搬过来后各处互不干扰）：
    sources/      只管「怎么从某个来源拿到数据」
    indicators    只管「怎么算」
    cache.py      只管「怎么存」
    service.py    只管「用哪个源、失败了怎么办、要不要缓存」  ← 本文件
    src/tools.py  只管「暴露什么给 LLM」
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from . import indicators
from .cache import FileCache
from .sources.base import BaseDataSource, DataSourceError

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".cache"
DEFAULT_CACHE_TTL = 3600  # 1 小时

# 财务指标的格式化规则：金额转「亿」，比率保留两位并加 %。
_AMOUNT_KEYS = {"revenue", "net_profit"}
_PERCENT_KEYS = {"roe", "revenue_yoy", "profit_yoy", "debt_ratio", "gross_margin", "net_margin"}


def _format_metric(key: str, value: Any) -> str:
    """把原始财务数值格式化成人能读的形式。"""
    try:
        number = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return str(value)
    if key in _AMOUNT_KEYS:
        return f"{number / 1e8:.2f} 亿" if abs(number) >= 1e8 else f"{number:,.2f}"
    if key in _PERCENT_KEYS:
        return f"{number:.2f}%"
    return f"{number:.2f}"


class DataService:
    """对上层暴露的唯一数据入口。"""

    def __init__(
        self,
        sources: Sequence[BaseDataSource],
        cache: Optional[FileCache] = None,
    ) -> None:
        if not sources:
            raise ValueError("DataService 至少需要一个数据源")
        self.sources = list(sources)
        self.cache = cache or FileCache(DEFAULT_CACHE_DIR, DEFAULT_CACHE_TTL)
        self.last_source: Dict[str, str] = {}  # 每个标的最近一次由哪个源服务

    # ---------------- 内部：按优先级依次尝试各个源 ----------------
    def _fetch(self, method: str, ticker: str, *args: Any, frame: bool = False) -> Any:
        errors: List[str] = []
        for source in self.sources:
            cache_key = f"{source.name}:{method}:{ticker}:{args}"
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.last_source[ticker] = f"{source.name}(缓存)"
                return pd.DataFrame(cached) if frame else cached
            try:
                value = getattr(source, method)(ticker, *args)
            except Exception as exc:  # 任何失败都视为「这个源不可用」，换下一个
                errors.append(f"{source.name}: {type(exc).__name__}: {exc}")
                logger.warning("[数据源降级] %s.%s(%s) 失败：%s", source.name, method, ticker, exc)
                continue
            self.last_source[ticker] = source.name
            self.cache.set(
                cache_key,
                value.to_dict(orient="records") if isinstance(value, pd.DataFrame) else value,
            )
            return value
        raise DataSourceError(
            f"所有数据源均失败（{method} {ticker}）：" + " | ".join(errors)
        )

    def source_label(self, key: str) -> str:
        """数据来源标记。模拟数据必须显眼地标出来，避免被误当成真实结论。"""
        source = self.last_source.get(key, "")
        return "· 离线模拟数据" if source.startswith("mock") else ""

    # ---------------- 对外接口 ----------------
    def get_price_history(self, ticker: str, days: int = 120) -> pd.DataFrame:
        return self._fetch("get_price_history", ticker, int(days), frame=True)

    def get_technicals(self, ticker: str, days: int = 120) -> str:
        frame = self.get_price_history(ticker, days)
        return indicators.describe(frame, ticker, source_label=self.source_label(ticker))

    def get_index_history(self, index_code: str, days: int = 120) -> pd.DataFrame:
        return self._fetch("get_index_history", index_code, int(days), frame=True)

    def get_financial_series(self, ticker: str, periods: int = 8) -> Dict[str, Dict[str, Any]]:
        return self._fetch("get_financial_series", ticker, int(periods))

    def get_metrics(self, ticker: str) -> Dict[str, Any]:
        """原始财务指标（给需要做计算的工具用，不是格式化文本）。"""
        return self._fetch("get_financial_metrics", ticker)

    def get_profile(self, ticker: str) -> Dict[str, Any]:
        """标的基本信息。"""
        return self._fetch("get_profile", ticker)

    def get_news_items(self, ticker: str, limit: int = 10) -> List[Dict[str, str]]:
        """原始新闻列表（给需要过滤/打分的工具用）。"""
        return self._fetch("get_news", ticker, int(limit))

    def get_fundamentals(self, ticker: str) -> str:
        try:
            profile = self._fetch("get_profile", ticker)
        except DataSourceError as exc:
            # 公司名拿不到不影响财务指标——「部分降级」比整体失败好
            logger.warning("[数据源降级] %s 的基本信息不可用：%s", ticker, exc)
            profile = {"name": ticker}
        metrics = self._fetch("get_financial_metrics", ticker)
        return self._format_fundamentals(ticker, profile, metrics)

    def get_news(self, ticker: str, limit: int = 3) -> str:
        items = self._fetch("get_news", ticker, int(limit))
        return self._format_news(ticker, items)

    def source_of(self, ticker: str) -> str:
        return self.last_source.get(ticker, "未知")

    # ---------------- 格式化 ----------------
    @staticmethod
    def _format_fundamentals(ticker: str, profile: Dict[str, Any], metrics: Dict[str, Any]) -> str:
        name = profile.get("name") or ticker
        parts = [f"[基本面] {name}({ticker})"]
        if profile.get("industry"):
            parts.append(f"行业 {profile['industry']}")
        if metrics.get("period"):
            parts.append(f"报告期 {metrics['period']}")

        labels = [
            ("pe", "PE"),
            ("pb", "PB"),
            ("roe", "ROE"),
            ("revenue", "营业总收入"),
            ("net_profit", "归母净利润"),
            ("revenue_yoy", "营收同比"),
            ("profit_yoy", "净利同比"),
            ("debt_ratio", "资产负债率"),
            ("gross_margin", "毛利率"),
            ("net_margin", "净利率"),
            ("eps", "每股收益"),
        ]
        for key, label in labels:
            value = metrics.get(key)
            if value is None or str(value).strip() in ("", "nan", "None", "--"):
                continue
            parts.append(f"{label}={_format_metric(key, value)}")
        return "，".join(parts)

    @staticmethod
    def _format_news(ticker: str, items: List[Dict[str, str]]) -> str:
        if not items:
            return f"[消息面] {ticker}：暂无相关新闻。"
        lines = [f"[消息面] {ticker} 近期新闻 {len(items)} 条："]
        for item in items:
            stamp = f"（{item['published_at']}）" if item.get("published_at") else ""
            lines.append(f"- {item.get('title', '')}{stamp}")
        return "\n".join(lines)
