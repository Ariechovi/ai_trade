"""SSE 事件的统一形状 —— 前端不用为不同事件写分支。"""

from __future__ import annotations

import json
from typing import Any, Dict

# 收到这两个事件之一，说明本次运行已经结束，可以关闭连接
TERMINAL_EVENTS = ("done", "error")


def sse_frame(event: str, data: Dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def frame_with_id(seq: int, event: str, data: Dict[str, Any]) -> str:
    """带 id 的帧 —— 浏览器重连时会带 Last-Event-ID，于是能接着上次的位置读。"""
    return f"id: {seq}\n" + sse_frame(event, data)
