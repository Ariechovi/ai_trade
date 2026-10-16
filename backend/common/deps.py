"""接口层依赖注入。

铁律：`DataService` **每个请求新建**。
它内部有 `last_source` 可变字典和文件缓存，多线程共享会让报告标错数据源 ——
那是「看起来对但是错的」那类 bug，比崩溃更难发现。
并发问题最简单的解法是不共享。
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Optional

from fastapi import Header, HTTPException, Request

from settings import Settings, load_settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级配置（环境变量只读一次）。测试通过 create_app(settings=...) 注入。"""
    return load_settings()


def get_ctx(request: Request):
    """取出应用级上下文（settings / store / runner / bus）。"""
    return request.app.state.ctx


def require_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> None:
    """单租户 API Key。留空 = 不校验（仅本机开发）。

    校验的是**应用级** settings（`create_app` 注入的那份），不是进程级缓存的
    `get_settings()` —— 否则测试或调用方注入的配置会被静默忽略。
    """
    ctx = getattr(request.app.state, "ctx", None)
    expected = ctx.settings.api_key if ctx is not None else get_settings().api_key
    if not expected:
        return
    if not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(status_code=401, detail="invalid api key")
