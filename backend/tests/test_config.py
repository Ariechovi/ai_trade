"""配置层：离线降级与可复现性（自检 [1]）"""

from settings import load_settings


def test_offline_can_be_forced():
    """可以显式强制离线。"""
    assert load_settings(offline=True).offline is True


def test_falls_back_to_offline_without_api_key():
    """没有 API Key 时自动降级为离线 —— 保证 clone 下来就能跑。"""
    auto = load_settings()
    assert auto.offline is True or bool(auto.llm_api_key)


def test_mock_data_is_reproducible(mock_provider):
    """同一只股票两次取数结果一致。"""
    assert mock_provider.get_technicals("600519") == mock_provider.get_technicals("600519")


def test_different_tickers_differ(mock_provider):
    """不同标的数据不同（证明数据真的按 ticker 生成，而不是常量）。"""
    assert mock_provider.get_technicals("600519") != mock_provider.get_technicals("000001")
