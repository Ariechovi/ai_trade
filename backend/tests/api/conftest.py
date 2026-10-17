"""API 用例的公共设施。

四条约定：
1. 用 `TestClient(create_app(...))` —— **不启真实服务器**；
2. 任务库是**独立的测试库**（默认 `ai_trade_test`），绝不碰生产库；
3. 每个用例前 `TRUNCATE` 业务表，用例之间互不影响；
4. **连不上 PG 就 skip 而不是红** —— 没配数据库的人看到的是 skip，
   「clone 下来能跑」这个卖点要保住。

为什么测试库名写死在代码里、不做成配置项：
测试**永远不该写进生产库**。把这条决定固化在代码里，
比在 .env 里加一个可能被改错的开关安全。
（要改测试库名用环境变量 `PG_TEST_DB`，那是显式的。）
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from typing import Any, Callable, Dict, List

import pytest
from fastapi.testclient import TestClient

from common.pg import connect, ping
from main import create_app
from migrations.runner import apply, missing_core_tables
from settings import load_settings

# 测试库：**不使用** .env 里的 PG_DB（那是生产库）
TEST_PG_DB = os.getenv("PG_TEST_DB", "ai_trade_test")


@pytest.fixture(scope="session")
def pg_settings():
    """测试库配置；连不上就跳过所有依赖数据库的用例。"""
    base = load_settings()
    if not base.pg_host:
        pytest.skip("未配置 PG_HOST —— 跳过依赖数据库的用例")

    settings = replace(base, pg_db=TEST_PG_DB, pg_pool_min=1, pg_pool_max=2)
    try:
        ping(settings)
    except Exception as exc:  # noqa: BLE001 —— 连不上就 skip，不是失败
        pytest.skip(
            f"PostgreSQL 不可用（{type(exc).__name__}: {exc}）—— 跳过依赖数据库的用例"
        )

    apply(settings)  # 测试库自己保证 schema 是最新的
    missing = missing_core_tables(settings)
    if missing:
        pytest.fail(f"测试库缺少核心表：{missing} —— 迁移没生效")
    return settings


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """把默认缓存目录指到临时目录：冷缓存 + 不写用户目录。"""
    monkeypatch.setattr("data.service.DEFAULT_CACHE_DIR", tmp_path / "cache")


@pytest.fixture
def clean_db(pg_settings):
    """每个用例前清空业务表。

    `TRUNCATE runs CASCADE` 会连带清掉所有**外键引用 runs 的表**
    （run_events / run_nodes / run_llm_calls / run_tool_calls），
    但不会碰 schema_migrations —— 它没有外键。
    """
    with connect(pg_settings, autocommit=True) as conn:
        conn.execute("TRUNCATE runs CASCADE")
    yield


@pytest.fixture
def api_settings(pg_settings):
    return replace(
        pg_settings,
        offline=True,
        max_debate_rounds=0,
        max_concurrent_runs=2,
        sse_heartbeat_seconds=1,
        sse_max_idle_seconds=10,
        api_key="",
    )


@pytest.fixture
def api_client(api_settings, clean_db):
    app = create_app(api_settings)
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
