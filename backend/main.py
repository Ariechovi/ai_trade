"""FastAPI 应用入口（对齐 ai_meeting 的 main.py 写法）。

只做「组装」：建 app、挂中间件、装错误处理、注册路由。业务逻辑一概不在这里 ——
main.py 一旦超过 60 行，就说明有东西该挪出去了。
（V3 的 `app/main.py` 长到 1100 行，就是因为每加一个功能都往入口塞一点。）
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import api_router
from common.deps import get_settings
from common.exception_handler import install_error_handlers
from data.service import DEFAULT_CACHE_DIR
from services.task_runner import TaskRunner
from services.task_store import TaskStore
from settings import APP_VERSION, Settings


@dataclass
class ApiContext:
    """应用级上下文：配置 + 任务存储 + 执行器（执行器内含事件总线）。"""

    settings: Settings
    store: TaskStore
    runner: TaskRunner


def resolve_db_path(settings: Settings) -> Path:
    """任务库位置：显式配置优先，否则落在用户级缓存目录（不写进项目目录）。"""
    if settings.run_db_path:
        return Path(settings.run_db_path)
    return DEFAULT_CACHE_DIR / "runs.sqlite3"


def create_app(
    settings: Optional[Settings] = None, *, db_path: Optional[Path] = None
) -> FastAPI:
    settings = settings or get_settings()
    store = TaskStore(db_path or resolve_db_path(settings))
    runner = TaskRunner(store, settings, max_concurrent=settings.max_concurrent_runs)
    ctx = ApiContext(settings=settings, store=store, runner=runner)
    # 启动即自愈：把上次残留的 running 任务标记为 interrupted（进程被杀时没人标记）
    store.sweep_interrupted()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        runner.shutdown()
        store.close()

    app = FastAPI(
        title="mini-agents",
        version=APP_VERSION,
        description="最小多智能体投研系统：工作流控制拓扑，Agent 控制内容",
        lifespan=lifespan,
    )
    app.state.ctx = ctx
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(api_router)
    return app


# uvicorn 直接用这个实例：uvicorn main:app
app = create_app()


if __name__ == "__main__":
    import socket
    import sys

    import uvicorn

    _settings = get_settings()
    _host, _port = _settings.api_host, _settings.api_port

    # 端口被占用时给一句人话，而不是甩一个 WinError 10013 出来
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _probe:
        if _probe.connect_ex((_host, _port)) == 0:
            print(f"[mini-agents] 端口 {_port} 已被占用 —— 多半是上一次的后端还没退出。")
            print(f"              查看占用者：netstat -ano | findstr :{_port}")
            print("              或换个端口：设置环境变量 API_PORT 后重跑")
            sys.exit(1)

    print(f"[mini-agents] 后端启动中：http://{_host}:{_port}   文档：http://{_host}:{_port}/docs")
    uvicorn.run("main:app", host=_host, port=_port, reload=True)
