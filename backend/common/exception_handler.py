"""统一错误语义。

一条刻意的规则：**节点降级不是 HTTP 错误**。
图里的容错设计是「异常不穿透到图外，最坏情况是一个降级结果」；
如果接口层把它报成 5xx，等于把内层的容错成果又扔掉了。
降级信息走 `AnalysisResult.degraded` 字段，由客户端决定怎么展示。

真正该报 5xx 的只有一类：**外部依赖整体不可用**（所有数据源都失败）。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from data.sources.base import DataSourceError

logger = logging.getLogger(__name__)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DataSourceError)
    async def _data_source_unavailable(
        request: Request, exc: DataSourceError
    ) -> JSONResponse:
        logger.warning("数据源不可用: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"error": "data_source_unavailable", "detail": str(exc)[:300]},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
            },
        )
