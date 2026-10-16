"""任务中心：列表 / 过滤 / 分页。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from common.deps import get_ctx, require_api_key

router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", summary="任务列表")
def list_runs(
    ctx=Depends(get_ctx),
    status: Optional[str] = Query(
        None, description="queued|running|succeeded|failed|cancelled|interrupted"
    ),
    ticker: Optional[str] = Query(None, description="按股票代码过滤"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    items, total = ctx.store.list(status=status, ticker=ticker, limit=limit, offset=offset)
    return {"total": total, "items": items}
