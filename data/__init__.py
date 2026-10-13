"""数据层：数据源适配 + 指标计算 + 缓存 + 服务编排。

分层（对应 TradingAgents-CN 的 `tradingagents/dataflows/`）：

    sources/       一个来源一个文件        ≈ providers/
    indicators.py  指标计算（纯函数）      ≈ technical/
    cache.py       文件缓存                ≈ cache/
    service.py     多源编排 + 降级 + 缓存   ≈ data_source_manager.py
    本文件          对外工厂                ≈ interface.py
"""

from __future__ import annotations

import logging
from typing import List

from .service import DataService
from .sources.akshare_source import AkshareDataSource
from .sources.base import BaseDataSource, DataSourceError
from .sources.mock_source import MockDataSource

logger = logging.getLogger(__name__)

__all__ = [
    "DataService",
    "BaseDataSource",
    "DataSourceError",
    "get_data_service",
]


def get_data_service(mode: str = "auto", *, with_mock_fallback: bool = False) -> DataService:
    """按名称组装数据服务。

    mode:
        "auto"    → akshare 优先（真实数据）
        "akshare" → 只用 akshare
        "mock"    → 只用模拟数据（离线开发）

    with_mock_fallback:
        默认 **False** —— 真实数据失败时**不**偷偷用模拟数据顶替。
        宁可报错，也不要让假数据伪装成真的。
        （仅当 akshare 根本没装时才会退回 mock，并在日志里明确告警。）
    """
    mode = (mode or "auto").lower()
    if mode == "mock":
        return DataService([MockDataSource()])

    sources: List[BaseDataSource] = []
    try:
        sources.append(AkshareDataSource())
    except DataSourceError as exc:
        logger.warning("akshare 不可用（%s）", exc)

    if with_mock_fallback or not sources:
        logger.warning("⚠️ 数据源退回离线模拟数据（输出会带「离线模拟数据」标记）")
        sources.append(MockDataSource())
    return DataService(sources)
