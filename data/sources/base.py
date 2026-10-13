"""数据源基类：所有数据源的统一接口。

对应 TradingAgents-CN 的 `tradingagents/dataflows/providers/base_provider.py`。

为什么要有基类？因为「换数据源」应该是**加一个文件**，而不是改上层代码。
上层（DataService、工具、Agent）只认 BaseDataSource 这几个方法。

一个关键约定：`get_price_history` 必须返回**列名标准化**的 DataFrame
（date/open/high/low/close/volume），不同数据源的原始列名差异在这一层被抹平。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

import pandas as pd


class DataSourceError(RuntimeError):
    """数据源调用失败（网络 / 限流 / 数据缺失）。

    统一成一个异常类型，是为了让上层能做统一的降级判断，
    而不用去分辨各家 SDK 五花八门的异常。
    """


class BaseDataSource(ABC):
    """数据源统一接口。"""

    name: str = "base"

    @abstractmethod
    def get_price_history(self, ticker: str, days: int) -> pd.DataFrame:
        """返回标准化日线：列固定为 date/open/high/low/close/volume。"""

    @abstractmethod
    def get_profile(self, ticker: str) -> Dict[str, Any]:
        """返回标的基本信息（名称 / 行业 / 市值等）。"""

    @abstractmethod
    def get_financial_metrics(self, ticker: str) -> Dict[str, Any]:
        """返回关键财务指标（PE / PB / ROE / 增速 / 负债率…）。"""

    @abstractmethod
    def get_news(self, ticker: str, limit: int) -> List[Dict[str, str]]:
        """返回新闻列表，每项形如 {"title": ..., "published_at": ..., "source": ...}。"""

    @abstractmethod
    def get_index_history(self, index_code: str, days: int) -> pd.DataFrame:
        """返回指数日线（同样标准化列名）。index_code 形如 sh000001 / sz399001。"""

    @abstractmethod
    def get_financial_series(self, ticker: str, periods: int) -> Dict[str, Dict[str, Any]]:
        """返回多报告期的财务指标序列：{指标key: {报告期: 值}}。

        「多期」是成长性分析的前提——只有单期的数字无法判断趋势。
        """
