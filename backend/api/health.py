"""健康检查与版本信息。

刻意不校验 API Key：健康检查要能在任何情况下被探活。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from common.deps import get_ctx
from settings import APP_VERSION

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", summary="健康检查")
def health() -> dict:
    return {"status": "ok", "version": APP_VERSION}


@router.get("/version", summary="版本与运行模式")
def version(ctx=Depends(get_ctx)) -> dict:
    settings = ctx.settings
    return {
        "version": APP_VERSION,
        "offline": settings.offline,
        "data_provider": settings.data_provider,
        "max_debate_rounds": settings.max_debate_rounds,
        "max_concurrent_runs": settings.max_concurrent_runs,
    }
