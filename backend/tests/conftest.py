"""pytest 公共设施：离线环境、断网保护、共享 fixture。

三条约定：
1. **默认切断真实网络** —— 需要联网的用例必须显式打 `@pytest.mark.network`；
2. 全程使用离线桩模型 + mock 数据源，**不需要任何 API Key**；
3. 重活（建图、跑一次完整分析）放在 session / module 级 fixture 里，只做一次。
"""

from __future__ import annotations

import socket
from dataclasses import replace
from typing import Any, Callable, Dict, List

import pytest
from pydantic import Field

from settings import load_settings
from data.cache import FileCache
from data.service import DataService
from data.sources.mock_source import MockDataSource
from services.graph import build_graph
from services.offline_llm import ScriptedChatModel
from services.runner import run_case
from tools import build_tools

# 固定分析日期：离线结果必须逐次可复现
DATE = "2026-10-11"


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """默认切断**对外**网络 —— 这是「离线可复现」这个卖点的自动化证明。

    ⚠️ 不能简单地把 `socket.socket` 换成抛异常的桩：asyncio 在 Windows 上
    建事件循环时要用 socketpair（纯本机管道），那样做会把测试框架自己锁死
    （TestClient 连启动都启动不了）。

    真正要拦的是「往外连」这三条路：DNS 解析、create_connection、非环回的 connect。
    环回地址放行，让 asyncio / TestClient 的内部管道正常工作。
    """
    if request.node.get_closest_marker("network"):
        return

    def _blocked(what: str) -> RuntimeError:
        return RuntimeError(
            f"该用例试图联网（{what}）。"
            "若确实需要，请标记 @pytest.mark.network 并用 -m network 运行。"
        )

    def _is_local(host: Any) -> bool:
        if host is None:
            return True
        if isinstance(host, bytes):
            host = host.decode("utf-8", "ignore")
        if not isinstance(host, str):
            return False
        return (
            host in {"", "0.0.0.0", "::1", "localhost"}
            or host.startswith("127.")
            or host.startswith("::ffff:127.")
        )

    def _host_of(address: Any) -> Any:
        return address[0] if isinstance(address, tuple) and address else address

    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self, address, *args, **kwargs):
        host = _host_of(address)
        if not _is_local(host):
            raise _blocked(f"connect {host}")
        return real_connect(self, address, *args, **kwargs)

    def guarded_create_connection(address, *args, **kwargs):
        host = _host_of(address)
        if not _is_local(host):
            raise _blocked(f"create_connection {host}")
        return real_create_connection(address, *args, **kwargs)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            raise _blocked(f"DNS {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


class FlakyChatModel(ScriptedChatModel):
    """按剧本失败的桩模型：前 N 次调用抛异常（用于验证重试与降级）。"""

    fail_first_n: int = 0
    fail_message: str = "connection timeout (injected)"
    attempts: List[int] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.attempts.append(1)
        if len(self.attempts) <= self.fail_first_n:
            raise RuntimeError(self.fail_message)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture(scope="session")
def mock_provider(tmp_path_factory) -> DataService:
    """mock 数据源 + 临时缓存目录（不污染用户级缓存）。"""
    return DataService(
        [MockDataSource()],
        cache=FileCache(tmp_path_factory.mktemp("cache"), ttl_seconds=60),
    )


@pytest.fixture(scope="session")
def offline_settings():
    return load_settings(offline=True, rounds=2)


@pytest.fixture(scope="session")
def fast_settings():
    """零退避配置：容错用例不必真的等重试间隔。"""
    return replace(
        load_settings(offline=True, rounds=2),
        retry_base_delay=0.0,
        max_retry_attempts=3,
    )


@pytest.fixture(scope="session")
def tool_map(mock_provider) -> Dict[str, Any]:
    return build_tools(mock_provider)


@pytest.fixture(scope="session")
def e2e_state(offline_settings, mock_provider) -> Dict[str, Any]:
    """跑一次完整的 rounds=2 分析：多个用例共用，整个会话只跑一次。"""
    return run_case("600519", DATE, offline_settings, mock_provider)


@pytest.fixture
def make_flaky_factory() -> Callable[..., Any]:
    """构造「只让指定角色失败」的 llm_factory，并回传被创建的模型实例。"""

    def _make(*, ticker: str, targets: set, fail_first_n: int, message: str):
        created: List[FlakyChatModel] = []

        def factory(role: str):
            if role in targets:
                model = FlakyChatModel(
                    role=role,
                    ticker=ticker,
                    fail_first_n=fail_first_n,
                    fail_message=message,
                )
                created.append(model)
                return model
            return ScriptedChatModel(role=role, ticker=ticker)

        return factory, created

    return _make


@pytest.fixture
def run_graph_with(tool_map):
    """用自定义 llm_factory 跑一遍图（故障注入用）。"""

    def _run(llm_factory, settings, ticker: str = "600519") -> Dict[str, Any]:
        app = build_graph(llm_factory=llm_factory, tool_map=tool_map, settings=settings)
        return app.invoke(
            {
                "ticker": ticker,
                "trade_date": DATE,
                "max_rounds": settings.max_debate_rounds,
                "debate_history": [],
                "debate_count": 0,
                "trace": [],
            }
        )

    return _run
