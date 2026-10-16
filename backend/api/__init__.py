"""API 路由层：每个域一个文件，在这里聚合成一个 `api_router`。

对齐 ai_meeting 的写法 —— `main.py` 只写一句 `include_router(api_router)`，
路由的组织全在本文件里，一眼能看全有哪些接口。

V3 的对应物是 `app/router_registry.py`：它用一张 `(router, name, kwargs)` 清单
把 59 个路由集中注册。我们只有 3 个，保留"集中登记"这个思路就够。
"""

from __future__ import annotations

from fastapi import APIRouter

from api import analysis, health, tasks

api_router = APIRouter()

# 新增路由必须在这里登记 —— 这张表就是"项目对外暴露了什么"
api_router.include_router(health.router)
api_router.include_router(analysis.router)
api_router.include_router(tasks.router)

__all__ = ["api_router"]
