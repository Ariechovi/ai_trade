"""SSE 事件流：顺序、进度、续传、取消。"""

from agents.specs import ROLES


def test_event_stream_is_ordered_and_terminates(api_client, collect_sse):
    """照项目自己的经验：断言顺序关系，不要只断言成功。"""
    run_id = api_client.post(
        "/api/analysis", json={"ticker": "600519", "provider": "mock", "rounds": 1}
    ).json()["run_id"]

    events = collect_sse(api_client, f"/api/analysis/{run_id}/events")
    names = [e["event"] for e in events]
    nodes = [e["data"]["node"] for e in events if e["event"] == "node"]

    assert names[0] == "status"
    assert "done" in names
    assert nodes

    merge_at = nodes.index("merge")
    assert all(nodes.index(f"report:{role}") < merge_at for role in ROLES), (
        "三份报告必须都出现在 merge 之前 —— 否则辩论是在缺料的情况下进行的"
    )
    assert "manager" in nodes


def test_progress_is_computed_not_hardcoded(api_client, collect_sse):
    """进度是算出来的：done / 预估节点数，而不是拍脑袋的魔数。"""
    run_id = api_client.post(
        "/api/analysis", json={"ticker": "600519", "provider": "mock"}
    ).json()["run_id"]
    events = collect_sse(api_client, f"/api/analysis/{run_id}/events")
    ratios = [e["data"]["progress"] for e in events if "progress" in e["data"]]

    assert ratios
    assert ratios == sorted(ratios)
    assert 0 < ratios[-1] <= 1.0


def test_replay_after_finish(api_client, collect_sse, wait_for_status):
    """任务已结束后再连，应该能靠落库的事件完整回放（不需要 Redis）。"""
    run_id = api_client.post(
        "/api/analysis", json={"ticker": "600519", "provider": "mock"}
    ).json()["run_id"]
    wait_for_status(api_client, run_id)

    events = collect_sse(api_client, f"/api/analysis/{run_id}/events")
    assert any(e["event"] == "done" for e in events)
    assert any(e["event"] == "node" for e in events)


def test_last_event_id_resumes_stream(api_client, collect_sse, wait_for_status):
    run_id = api_client.post(
        "/api/analysis", json={"ticker": "600519", "provider": "mock"}
    ).json()["run_id"]
    wait_for_status(api_client, run_id)

    full = collect_sse(api_client, f"/api/analysis/{run_id}/events")
    assert len(full) >= 3

    resumed = collect_sse(
        api_client,
        f"/api/analysis/{run_id}/events",
        headers={"Last-Event-ID": "2"},
    )
    assert len(resumed) == len(full) - 2


def test_unknown_run_events_return_404(api_client):
    assert api_client.get("/api/analysis/nope/events").status_code == 404
