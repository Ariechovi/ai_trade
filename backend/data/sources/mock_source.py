"""离线模拟数据源：确定性生成，不需要网络。

用途只有一个：让你在没有网络 / 被限流时也能跑通整条链路。

⚠️ 它产生的数据**不是真实的**，所以 DataService 会在输出上打 [离线模拟数据] 标记——
避免把模拟结果误当成真实结论。这条规则很重要：宁可显眼地承认是假的，
也不要让假数据伪装成真的。

一个设计要点：`get_price_history` 生成的是**真正的价格序列**，
所以指标计算模块能在离线状态下跑真实公式。离线验证因此不再是「假逻辑验假逻辑」。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .base import BaseDataSource, DataSourceError

# 样本数据就在本文件旁边（应用目录形态，不走打包分发），直接按路径读最省事。
FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "sample.json"


def _seed(ticker: str) -> int:
    """把股票代码映射成稳定整数，保证同一只股票每次结果一致（可复现）。"""
    return int(hashlib.md5(ticker.encode("utf-8")).hexdigest()[:8], 16)


def _period_labels(count: int) -> List[str]:
    """生成最近 count 个报告期标签（半年报 / 年报交替），从新到旧。"""
    today = pd.Timestamp.today()
    year = today.year
    labels: List[str] = []
    while len(labels) < count and year > 2000:
        for suffix in ("1231", "0630"):
            stamp = pd.Timestamp(f"{year}-{suffix[:2]}-{suffix[2:]}")
            if stamp <= today:
                labels.append(f"{year}{suffix}")
        year -= 1
    return labels[:count]


class MockDataSource(BaseDataSource):
    """确定性模拟数据源（离线开发用）。"""

    name = "mock"

    def __init__(self) -> None:
        self._fixtures: Dict[str, Any] = {}
        if FIXTURE_PATH.is_file():
            self._fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    # ---------------- 内部 ----------------
    def _base(self, ticker: str) -> Dict[str, Any]:
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

    # ---------------- 接口 ----------------
    def get_price_history(self, ticker: str, days: int) -> pd.DataFrame:
        days = max(30, int(days))
        base_price = float(self._base(ticker)["price"])
        rng = np.random.default_rng(_seed(ticker))
        returns = rng.normal(0.0004, 0.018, days)
        close = base_price * np.cumprod(1.0 + returns)
        open_ = close * (1.0 + rng.normal(0.0, 0.004, days))
        high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.006, days)))
        low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.006, days)))
        volume = rng.integers(2_000_000, 20_000_000, days).astype(float)
        dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
        frame = pd.DataFrame(
            {
                "date": dates,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
        if frame.empty:
            raise DataSourceError(f"mock 数据源未能生成 {ticker} 的行情")
        return frame

    def get_profile(self, ticker: str) -> Dict[str, Any]:
        base = self._base(ticker)
        return {
            "ticker": ticker,
            "name": base["name"],
            "industry": "示例行业",
            "market_cap": None,
        }

    def get_financial_metrics(self, ticker: str) -> Dict[str, Any]:
        base = self._base(ticker)
        return {
            "pe": base["pe"],
            "pb": base["pb"],
            "roe": base["roe"],
            "revenue_yoy": base["revenue_yoy"],
            "profit_yoy": base["profit_yoy"],
            "debt_ratio": base["debt_ratio"],
        }

    def get_news(self, ticker: str, limit: int) -> List[Dict[str, str]]:
        base = self._base(ticker)
        limit = max(1, min(int(limit), 5))
        pool = [
            f"{base['name']}发布季度经营情况公告",
            f"行业景气度回升，{base['name']}获多家机构调研",
            f"{base['name']}公布股东增持与回购进展",
            f"机构上调{base['name']}盈利预测",
            f"{base['name']}新增产能项目获批复",
        ]
        offset = _seed(ticker) % len(pool)
        rotated = pool[offset:] + pool[:offset]
        return [{"title": title, "published_at": "", "source": "mock"} for title in rotated[:limit]]

    def get_index_history(self, index_code: str, days: int) -> pd.DataFrame:
        days = max(30, int(days))
        base = 2500.0 + (_seed(index_code) % 2000)
        rng = np.random.default_rng(_seed(index_code))
        returns = rng.normal(0.0002, 0.010, days)
        close = base * np.cumprod(1.0 + returns)
        open_ = close * (1.0 + rng.normal(0.0, 0.002, days))
        high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.003, days)))
        low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.003, days)))
        volume = rng.integers(1e8, 5e8, days).astype(float)
        dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
        return pd.DataFrame(
            {"date": dates, "open": open_, "high": high, "low": low, "close": close, "volume": volume}
        )

    def get_financial_series(self, ticker: str, periods: int) -> Dict[str, Dict[str, Any]]:
        base = self._base(ticker)
        labels = _period_labels(max(2, int(periods)))
        rng = np.random.default_rng(_seed(ticker))
        revenue = base["price"] * 1.2e9 / 100.0
        net_profit = base["price"] * 3.0e8 / 100.0
        anchors = {
            "revenue": revenue,
            "net_profit": net_profit,
            "net_profit_total": net_profit * 1.08,
            "deducted_profit": net_profit * 0.95,
            "operating_cashflow": net_profit * 1.15,
            "equity": revenue * 0.45,
            "goodwill": revenue * 0.02,
            "revenue_yoy": base["revenue_yoy"],
            "profit_yoy": base["profit_yoy"],
            "roe": base["roe"],
            "roa": base["roe"] * 0.4,
            "gross_margin": 30.0 + (_seed(ticker) % 400) / 10.0,
            "net_margin": 8.0 + (_seed(ticker) % 200) / 10.0,
            "expense_ratio": 15.0 + (_seed(ticker) % 150) / 10.0,
            "ocf_to_profit": 1.0 + (_seed(ticker) % 80) / 100.0,
            "ocf_to_revenue": 0.05 + (_seed(ticker) % 200) / 1000.0,
            "debt_ratio": base["debt_ratio"],
            "equity_multiplier": round(1.0 / max(0.05, 1.0 - base["debt_ratio"] / 100.0), 3),
            "current_ratio": round(1.0 + (_seed(ticker) % 200) / 100.0, 3),
            "quick_ratio": round(0.6 + (_seed(ticker) % 150) / 100.0, 3),
            "total_asset_turnover": round(0.3 + (_seed(ticker) % 120) / 100.0, 3),
            "receivable_turnover": round(3.0 + (_seed(ticker) % 300) / 10.0, 3),
            "inventory_turnover": round(1.0 + (_seed(ticker) % 500) / 10.0, 3),
            "eps": round(base["price"] / 30.0, 2),
            "bps": round(base["price"] / 3.0, 2),
            "ocf_per_share": round(base["price"] / 25.0, 2),
        }
        series: Dict[str, Dict[str, Any]] = {}
        for key, anchor in anchors.items():
            values: Dict[str, Any] = {}
            for step, label in enumerate(labels):
                # 越久远的报告期，数值按轻微漂移回推（确定性）
                drift = 1.0 + rng.normal(0.0, 0.05) - 0.02 * step
                values[label] = round(float(anchor) * drift, 4)
            series[key] = values
        return series
