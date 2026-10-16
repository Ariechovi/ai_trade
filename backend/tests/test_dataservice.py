"""数据服务：多源降级、模拟数据标注、akshare 进程内 memo（自检 [14][15][21]）"""

from typing import List

import pandas as pd
import pytest

from data.cache import FileCache
from data.service import DataService
from data.sources.akshare_source import AkshareDataSource
from data.sources.base import DataSourceError
from data.sources.mock_source import MockDataSource


class BrokenSource(MockDataSource):
    """主源永远失败，用来验证自动切换备用源。"""

    name = "broken"

    def get_price_history(self, ticker: str, days: int):
        raise DataSourceError("模拟主源故障")


def test_falls_back_to_second_source(tmp_path):
    svc = DataService([BrokenSource(), MockDataSource()], cache=FileCache(tmp_path, 60))
    recovered = svc.get_price_history("600519", 60)
    assert len(recovered) == 60
    assert svc.source_of("600519") == "mock"


def test_all_sources_failed_raises(tmp_path):
    svc = DataService([BrokenSource()], cache=FileCache(tmp_path, 60))
    with pytest.raises(DataSourceError):
        svc.get_price_history("600519", 60)


def test_mock_output_is_visibly_labeled(mock_provider):
    """宁可显眼地承认是假的，也不要把假数据伪装成真的。"""
    text = mock_provider.get_technicals("600519", 120)
    assert "离线模拟数据" in text
    assert "RSI(14)" in text and "MACD" in text
    assert "MA20" in text and "布林带" in text


class CountingAk:
    """假的 akshare 模块，只统计被调了几次。"""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def stock_zh_a_daily(self, symbol: str, adjust: str) -> pd.DataFrame:
        self.calls.append("hist")
        dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=200)
        return pd.DataFrame(
            {
                "date": dates,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "volume": 1_000_000.0,
            }
        )

    def stock_financial_abstract(self, symbol: str) -> pd.DataFrame:
        self.calls.append("fin")
        return pd.DataFrame(
            {
                "选项": ["常用指标", "常用指标"],
                "指标": ["归母净利润", "营业总收入"],
                "20260630": [1.0, 2.0],
            }
        )


def test_akshare_memo_fetches_each_upstream_once():
    """同一份上游数据只请求一次：不同 days 复用原始表，metrics 与 series 复用。

    新浪接口一次返回全量 6000+ 行，不 memo 的话每次不同 days 都会重下一遍。
    """
    fake = CountingAk()
    source = AkshareDataSource.__new__(AkshareDataSource)  # 跳过 __init__ 里的 import
    source._ak = fake
    source._memo = {}

    source.get_price_history("600519", 60)
    source.get_price_history("600519", 120)  # 不同 days —— 不应再打网络
    source.get_financial_metrics("600519")
    source.get_financial_series("600519", 4)  # 复用同一张原始表

    assert fake.calls.count("hist") == 1
    assert fake.calls.count("fin") == 1
