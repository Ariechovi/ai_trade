"""API 用例的公共设施。

三条约定：
1. 用 `TestClient(create_app(...))` —— **不启真实服务器**；
2. 任务库与数据缓存都落在 `tmp_path`，不污染用户目录；
3. 仍然走 conftest 的断网保护（任何偷偷联网的用例都会红）。
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any, Callable, Dict, List

import pytest
from fastapi.testclient import TestClient

from main import create_app
from settings import load_settings


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """把默认缓存目录指到临时目录：冷缓存 + 不写用户目录。"""
    monkeypatch.setattr("data.service.DEFAULT_CACHE_DIR", tmp_path / "cache")


@pytest.fixture
def api_settings(tmp_path):
    return replace(
        load_settings(offline=True, rounds=0),
        run_db_path=str(tmp_path / "runs.sqlite3"),
        max_concurrent_runs=2,
        sse_heartbeat_seconds=1,
        sse_max_idle_seconds=10,
        api_key="",
    )


@pytest.fixture
def api_client(api_settings, tmp_path):
    app = create_app(api_settings, db_path=tmp_path / "runs.sqlite3")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def wait_for_status() -> Callable[..., Dict[str, Any]]:
    def _wait(client: TestClient, run_id: str, timeout: float = 60.0) -> Dict[str, Any]:
        deadline = time.perf_counter() + timeout
        last: Dict[str, Any] = {}
        while time.perf_counter() < deadline:
            last = client.get(f"/api/analysis/{run_id}").json()
            if last["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
                return last
            time.sleep(0.02)
        raise AssertionError(f"任务未在 {timeout}s 内结束，最后状态：{last}")

    return _wait


@pytest.fixture
def collect_sse() -> Callable[..., List[Dict[str, Any]]]:
    """把 SSE 流读成 [{event, data}] 列表，遇到终止事件就停。"""

    def _collect(client: TestClient, url: str, headers: Dict[str, str] | None = None):
        events: List[Dict[str, Any]] = []
        with client.stream("GET", url, headers=headers or {}) as resp:
            assert resp.status_code == 200, resp.read()
            name = None
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    name = line[len("event: ") :]
                elif line.startswith("data: ") and name:
                    events.append({"event": name, "data": json.loads(line[len("data: ") :])})
                    if name in ("done", "error"):
                        return events
        return events

    return _collect
