"""健康检查、版本信息、路由注册校验。"""


def test_health(api_client):
    r = api_client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_version_reports_offline_mode(api_client):
    body = api_client.get("/api/version").json()
    assert body["offline"] is True
    assert body["max_debate_rounds"] == 0


def test_openapi_exposes_expected_paths(api_client):
    """路由注册校验放在测试里 —— 比运行时遍历 app.routes 刷日志更省事。

    V3 的 router_registry 会在启动时遍历 app.routes 校验关键路径；
    同样的目的，用测试断言更干净。
    """
    paths = api_client.get("/openapi.json").json()["paths"]
    assert "/api/health" in paths
    assert "/api/analysis" in paths
    assert "/api/analysis/sync" in paths
    assert "/api/analysis/{run_id}" in paths
    assert "/api/analysis/{run_id}/events" in paths
    assert "/api/tasks" in paths


def test_sync_endpoints_are_not_coroutines():
    """铁律一的可执行版本。

    langgraph 与 akshare 都是同步阻塞的。把分析端点写成 `async def` 会卡住
    整个事件循环 —— 症状是「服务随机假死」，不像崩溃那样显眼。
    这个断言在有人把它改成 async 的那一刻就会红。
    """
    import inspect

    from api import analysis

    assert not inspect.iscoroutinefunction(analysis.create_run_sync)
    assert not inspect.iscoroutinefunction(analysis.create_run)
    assert not inspect.iscoroutinefunction(analysis.get_run)
