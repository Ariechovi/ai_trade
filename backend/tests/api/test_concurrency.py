"""并发与隔离：跨请求不串味、分析期间健康检查仍秒回。"""

import time


def test_parallel_runs_do_not_cross_contaminate(api_client, wait_for_status):
    """铁律四的可执行版本：每个 run_id 的结果必须与自己的 ticker 对应。"""
    tickers = ["600519", "000001", "300750", "600036"]
    run_ids = [
        api_client.post("/api/analysis", json={"ticker": t, "provider": "mock"}).json()[
            "run_id"
        ]
        for t in tickers
    ]

    for ticker, run_id in zip(tickers, run_ids):
        detail = wait_for_status(api_client, run_id)
        assert detail["status"] == "succeeded"
        assert detail["result"]["ticker"] == ticker
        assert detail["result"]["final_decision"]


def test_health_stays_responsive_while_run_in_flight(api_client, monkeypatch):
    """分析在跑时，健康检查仍然秒回（事件循环没被阻塞）。"""
    from data.sources.mock_source import MockDataSource

    original = MockDataSource.get_price_history

    def slow(self, ticker: str, days: int = 120):
        time.sleep(0.3)
        return original(self, ticker, days)

    monkeypatch.setattr(MockDataSource, "get_price_history", slow)

    run_id = api_client.post(
        "/api/analysis", json={"ticker": "600519", "provider": "mock"}
    ).json()["run_id"]

    latencies = []
    saw_in_flight = False
    for _ in range(20):
        started = time.perf_counter()
        assert api_client.get("/api/health").status_code == 200
        latencies.append(time.perf_counter() - started)
        status = api_client.get(f"/api/analysis/{run_id}").json()["status"]
        if status in ("queued", "running"):
            saw_in_flight = True

    assert max(latencies) < 0.5, f"健康检查被拖慢：{latencies}"
    assert saw_in_flight, "分析结束得太快，这个用例的观察窗口无效"


def test_cancel_takes_effect_at_node_boundary(api_client, monkeypatch, wait_for_status):
    """取消是在**节点边界**生效的：LangGraph 的同步 stream 无法从外部硬中断，
    但一次分析 10~60 秒、节点粒度是秒级，用户感受不到差别。"""
    from data.sources.mock_source import MockDataSource

    original = MockDataSource.get_price_history

    def slow(self, ticker: str, days: int = 120):
        time.sleep(0.3)
        return original(self, ticker, days)

    monkeypatch.setattr(MockDataSource, "get_price_history", slow)

    run_id = api_client.post(
        "/api/analysis", json={"ticker": "600519", "provider": "mock"}
    ).json()["run_id"]
    assert api_client.post(f"/api/analysis/{run_id}/cancel").json()["cancel_requested"] is True

    detail = wait_for_status(api_client, run_id, timeout=30)
    assert detail["status"] == "cancelled"
    assert detail["result"] is None
