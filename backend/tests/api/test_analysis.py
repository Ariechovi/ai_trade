"""分析任务：同步执行、异步执行、参数校验、错误语义。"""


def test_sync_run_returns_full_result(api_client):
    r = api_client.post("/api/analysis/sync", json={"ticker": "600519", "provider": "mock"})
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "600519"
    assert body["final_decision"]
    assert body["technical_report"]
    assert body["degraded"] == []
    assert body["metrics"]["llm_call_count"] > 0
    assert body["metrics"]["total_tokens"] == 0


def test_async_run_is_accepted_then_succeeds(api_client, wait_for_status):
    r = api_client.post("/api/analysis", json={"ticker": "000001", "provider": "mock"})
    assert r.status_code == 202, "异步任务必须返回 202 而不是 200"
    run_id = r.json()["run_id"]
    assert r.json()["status"] == "queued"

    detail = wait_for_status(api_client, run_id)
    assert detail["status"] == "succeeded"
    assert detail["result"]["ticker"] == "000001"


def test_invalid_ticker_is_rejected(api_client):
    assert api_client.post("/api/analysis", json={"ticker": "abc"}).status_code == 422
    assert api_client.post("/api/analysis", json={}).status_code == 422


def test_unknown_run_returns_404(api_client):
    assert api_client.get("/api/analysis/does-not-exist").status_code == 404
    assert api_client.post("/api/analysis/does-not-exist/cancel").status_code == 404


def test_rounds_can_override_env(api_client):
    body = api_client.post(
        "/api/analysis/sync",
        json={"ticker": "600519", "provider": "mock", "rounds": 1},
    ).json()
    assert len(body["debate_history"]) == 2


def test_api_key_is_enforced_when_configured(pg_settings, clean_db):
    """留空 = 不校验（本机开发）；配置了就严格要求。"""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from main import create_app

    settings = replace(
        pg_settings,
        offline=True,
        max_debate_rounds=0,
        api_key="secret-key",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/tasks").status_code == 401
        assert client.get("/api/tasks", headers={"X-API-Key": "wrong"}).status_code == 401
        ok = client.get("/api/tasks", headers={"X-API-Key": "secret-key"})
        assert ok.status_code == 200
        # 健康检查刻意不校验：探活要能在任何情况下工作
        assert client.get("/api/health").status_code == 200


def test_task_list_filters(api_client, wait_for_status):
    for ticker in ("600519", "000001"):
        run_id = api_client.post(
            "/api/analysis", json={"ticker": ticker, "provider": "mock"}
        ).json()["run_id"]
        wait_for_status(api_client, run_id)

    assert api_client.get("/api/tasks").json()["total"] == 2

    filtered = api_client.get("/api/tasks", params={"ticker": "600519"}).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["ticker"] == "600519"

    succeeded = api_client.get("/api/tasks", params={"status": "succeeded"}).json()
    assert succeeded["total"] == 2
    assert api_client.get("/api/tasks", params={"status": "failed"}).json()["total"] == 0
