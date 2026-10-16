"""分析任务：创建 / 查询 / 事件流 / 取消。

所有执行都走 `services.runner.stream_case` —— 这一层是薄壳，
不允许出现任何分析逻辑。
"""

from __future__ import annotations

import asyncio
import queue
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from common.deps import get_ctx, require_api_key
from common.sse import TERMINAL_EVENTS, frame_with_id, sse_frame
from models import AnalysisRequest, RunCreated
from services.task_runner import new_run_id

router = APIRouter(
    prefix="/api/analysis",
    tags=["analysis"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", status_code=202, response_model=RunCreated, summary="创建分析任务（异步）")
def create_run(req: AnalysisRequest, ctx=Depends(get_ctx)) -> RunCreated:
    """立即返回 202 + run_id，任务在后台线程跑。

    202 而不是 200：语义上任务只是被**接受**、还没完成。这个区别让客户端知道
    应该去监听事件流，而不是以为响应体里就有结果。
    """
    run_id = new_run_id()
    ctx.runner.submit(run_id, req)
    return RunCreated(run_id=run_id, status="queued")


@router.post("/sync", summary="同步跑一次（阻塞，给 curl / 脚本用）")
def create_run_sync(req: AnalysisRequest, ctx=Depends(get_ctx)) -> Any:
    """端点写成 `def` 而不是 `async def`：FastAPI 会自动把它丢进线程池。

    langgraph 与 akshare 都是同步阻塞的，写成 `async def` 会卡住整个事件循环 ——
    症状是「服务随机假死」，连 /api/health 都不响应，而且不像崩溃那样显眼。
    """
    run_id = new_run_id()
    return ctx.runner.run_inline(run_id, req)


@router.get("/{run_id}", summary="查询任务详情")
def get_run(run_id: str, ctx=Depends(get_ctx)) -> Any:
    row = ctx.store.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return row


@router.post("/{run_id}/cancel", summary="请求取消")
def cancel_run(run_id: str, ctx=Depends(get_ctx)) -> dict:
    if ctx.store.get(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run_id": run_id, "cancel_requested": ctx.runner.cancel(run_id)}


@router.get("/{run_id}/events", summary="SSE 事件流")
def stream_events(
    run_id: str,
    request: Request,
    ctx=Depends(get_ctx),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
):
    """Server-Sent Events。

    四个容易漏的点（都是 V3 用生产事故换来的）：
      1. `X-Accel-Buffering: no` —— 否则反向代理会缓冲，看着像「卡住」
      2. 心跳 —— 空闲连接会被中间设备掐断
      3. 空闲上限 —— 避免僵尸连接攒着不放
      4. **客户端断开 ≠ 任务停止** —— 任务照常跑到 done 并落库，
         这正是选「后台线程 + 事件总线」而不是直接包同步生成器的理由
    """
    store = ctx.store
    if store.get(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")

    after = int(last_event_id or 0)
    heartbeat = ctx.settings.sse_heartbeat_seconds
    max_idle = ctx.settings.sse_max_idle_seconds

    async def gen():
        yield sse_frame("status", {"run_id": run_id, "status": store.status(run_id)})
        q, backlog = ctx.runner.bus.subscribe(run_id, after)
        try:
            for row in backlog:  # 先补历史：支持刷新页面后带 Last-Event-ID 续传
                yield frame_with_id(row["seq"], row["event"], row["data"])
                if row["event"] in TERMINAL_EVENTS:
                    return
            idle = 0.0
            while idle < max_idle:
                if await request.is_disconnected():
                    return
                try:
                    row = await asyncio.to_thread(q.get, True, heartbeat)
                except queue.Empty:
                    idle += heartbeat
                    yield sse_frame("heartbeat", {})
                    continue
                idle = 0.0
                yield frame_with_id(row["seq"], row["event"], row["data"])
                if row["event"] in TERMINAL_EVENTS:
                    return
        finally:
            ctx.runner.bus.unsubscribe(run_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
