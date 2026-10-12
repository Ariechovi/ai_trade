"""数据源抽象层：把「取数据」与「分析逻辑」解耦。

两个实现：
- MockProvider   ：确定性生成，完全离线，用于开发和自动化验证（默认）
- AkshareProvider：真实 A 股数据（akshare 免费、无需 token）

上层 Agent 只依赖同一套接口，所以切换数据源不需要改动任何分析代码。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

_FIXTURE = Path(__file__).parent / "fixtures" / "sample.json"


def _seed(ticker: str) -> int:
    """把股票代码映射成稳定整数，保证同一只股票每次结果一致（可复现）。"""
    return int(hashlib.md5(ticker.encode("utf-8")).hexdigest()[:8], 16)


class MockProvider:
    """离线数据源：确定性、可复现、无需网络。"""

    name = "mock"

    def __init__(self) -> None:
        self._fixtures: Dict[str, Any] = {}
        if _FIXTURE.exists():
            self._fixtures = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    def _profile(self, ticker: str) -> Dict[str, Any]:
        if ticker in self._fixtures:
            return self._fixtures[ticker]
        s = _seed(ticker)
        return {
            "name": f"示例公司{ticker}",
            "price": round(10 + (s % 900) / 10, 2),
            "pe": round(8 + (s % 400) / 10, 2),
            "pb": round(0.8 + (s % 60) / 10, 2),
            "roe": round(5 + (s % 200) / 10, 2),
            "revenue_yoy": round(-10 + (s % 500) / 10, 2),
            "profit_yoy": round(-15 + (s % 600) / 10, 2),
            "debt_ratio": round(30 + (s % 400) / 10, 2),
        }

    def get_technicals(self, ticker: str, days: int = 60) -> str:
        p = self._profile(ticker)
        s = _seed(ticker)
        ma5 = round(p["price"] * (1 + ((s % 21) - 10) / 1000), 2)
        ma20 = round(p["price"] * (1 + ((s % 41) - 20) / 1000), 2)
        rsi = round(30 + (s % 45), 1)
        trend = "多头排列" if ma5 > ma20 else "空头排列"
        return (
            f"[技术面] {ticker} 近{days}个交易日：收盘 {p['price']}，"
            f"MA5={ma5}，MA20={ma20}，RSI={rsi}，趋势={trend}"
        )

    def get_fundamentals(self, ticker: str) -> str:
        p = self._profile(ticker)
        return (
            f"[基本面] {p['name']}({ticker})：PE={p['pe']}，PB={p['pb']}，"
            f"ROE={p['roe']}%，营收同比={p['revenue_yoy']}%，"
            f"净利同比={p['profit_yoy']}%，资产负债率={p['debt_ratio']}%"
        )

    def get_news(self, ticker: str, limit: int = 3) -> str:
        p = self._profile(ticker)
        limit = max(1, min(int(limit), 5))
        pool: List[str] = [
            f"{p['name']}发布季度经营情况公告",
            f"行业景气度回升，{p['name']}获多家机构调研",
            f"{p['name']}公布股东增持与回购进展",
            f"机构上调{p['name']}盈利预测",
            f"{p['name']}新增产能项目获批复",
        ]
        s = _seed(ticker)
        rotated = pool[s % len(pool):] + pool[: s % len(pool)]
        return "[消息面] " + "；".join(rotated[:limit])


class AkshareProvider:
    """真实数据源（akshare）。任何一步失败都自动降级到 Mock，保证流程不中断。"""

    name = "akshare"

    def __init__(self) -> None:
        self._mock = MockProvider()
        self._ak = None
        try:
            import akshare as ak  # type: ignore

            self._ak = ak
        except Exception:
            self._ak = None

    def _history(self, ticker: str, days: int):
        if self._ak is None:
            return None
        try:
            df = self._ak.stock_zh_a_hist(symbol=ticker, period="daily", adjust="qfq")
            return df.tail(days)
        except Exception:
            return None

    def get_technicals(self, ticker: str, days: int = 60) -> str:
        df = self._history(ticker, days)
        if df is None or len(df) == 0:
            return self._mock.get_technicals(ticker, days) + "（真实数据不可用，已降级）"
        close = df["收盘"].astype(float)
        last = round(float(close.iloc[-1]), 2)
        ma5 = round(float(close.tail(5).mean()), 2)
        ma20 = round(float(close.tail(min(20, len(close))).mean()), 2)
        trend = "多头排列" if ma5 > ma20 else "空头排列"
        return f"[技术面·akshare] {ticker}：收盘 {last}，MA5={ma5}，MA20={ma20}，趋势={trend}"

    def get_fundamentals(self, ticker: str) -> str:
        if self._ak is not None:
            try:
                info = self._ak.stock_individual_info_em(symbol=ticker)
                kv = dict(zip(info["item"], info["value"]))
                return (
                    f"[基本面·akshare] {ticker}：名称={kv.get('股票简称')}，"
                    f"总市值={kv.get('总市值')}，流通市值={kv.get('流通市值')}，"
                    f"行业={kv.get('行业')}"
                )
            except Exception:
                pass
        return self._mock.get_fundamentals(ticker) + "（真实数据不可用，已降级）"

    def get_news(self, ticker: str, limit: int = 3) -> str:
        if self._ak is not None:
            try:
                df = self._ak.stock_news_em(symbol=ticker)
                titles = list(df["新闻标题"].head(max(1, min(limit, 5))))
                return "[消息面·akshare] " + "；".join(str(t) for t in titles)
            except Exception:
                pass
        return self._mock.get_news(ticker, limit) + "（真实数据不可用，已降级）"


def get_provider(name: str = "mock"):
    """工厂：按名称返回数据源实例。"""
    if str(name).lower() == "akshare":
        return AkshareProvider()
    return MockProvider()
