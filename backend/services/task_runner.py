"""任务执行与事件总线。

执行本身**不复制任何逻辑** —— 全部转调 `services.runner.stream_case`，
后者是 CLI / Streamlit / HTTP 共用的唯一通路。

为什么用「后台线程 + 事件总线」，而不是让 StreamingResponse 直接包一个同步生成器？
因为任务中心的语义是「任务在后台跑，客户端只是观察者」：用户关掉页面，
分析照常跑到 done 并落库。前者会把任务的生命周期绑死在 HTTP 连接上。
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from typing import Any, Dict, List, Tuple

from agents.specs import ROLES
from data import get_data_service
from services.graph import node_label
from services.runner import stream_case, summarize
from services.store import StoreProtocol
from settings import Settings

logger = logging.getLogger(__name__)


class TaskCancelled(Exception):
    """任务在节点边界被取消（LangGraph 的同步 stream 无法从外部硬中断）。"""


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def estimate_total_nodes(settings: Settings) -> int:
    """图上大致会经过多少个节点 —— 用规格算，不是拍脑袋的魔数。

    V3 的进度百分比是硬编码的（callback(5, ...) / callback(100, ...)），
    中间那些数字没有依据。我们的图是代码写死的，节点数可以精确估算，
    所以进度百分比是算出来的、可解释的。
    """
    rounds = max(0, int(settings.max_debate_rounds))
    per_analyst = 1 + max(1, int(settings.max_tool_iterations)) + 1  # llm×N + tools×N + report
    return len(ROLES) * per_analyst + 1 + rounds * 2 + 1  # + merge + 辩论 + manager


def to_result(run_id: str, ticker: str, trade_date: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """把 LangGraph 的最终状态收敛成对外的结果形状（复用 summarize 的口径）。"""
    return {
        "run_id": run_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "status": "succeeded",
        "final_decision": str(state.get("final_decision") or ""),
        "technical_report": str(state.get("technical_report") or ""),
        "fundamental_report": str(state.get("fundamental_report") or ""),
        "news_report": str(state.get("news_report") or ""),
        "debate_history": list(state.get("debate_history") or []),
        "trace": [str(t) for t in (state.get("trace") or [])],
        "degraded": sorted((state.get("_node_errors") or {}).keys()),
        "metrics": summarize(state),
    }


class EventBus:
    """每个 run_id 一份订阅者队列；事件**先落库、再推送**。

    先落库这一步很关键：它保证「推送时不在线的客户端」还能靠 Last-Event-ID 补回来。
    """

    def __init__(self, store: StoreProtocol) -> None:
        self._store = store
        self._subs: Dict[str, List[queue.Queue]] = {}
        self._lock = threading.Lock()

    def publish(self, run_id: str, event: str, data: Dict[str, Any]) -> None:
        seq = self._store.append_event(run_id, event, data)
        with self._lock:
            targets = list(self._subs.get(run_id, ()))
        for q in targets:
            q.put({"seq": seq, "event": event, "data": data})

    def subscribe(
        self, run_id: str, after_seq: int = 0
    ) -> Tuple[queue.Queue, List[Dict[str, Any]]]:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.setdefault(run_id, []).append(q)
        return q, self._store.read_events(run_id, after_seq)

    def unsubscribe(self, run_id: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subs.get(run_id)
            if subs and q in subs:
                subs.remove(q)


class TaskRunner:
    def __init__(
        self, store: StoreProtocol, settings: Settings, *, max_concurrent: int = 2
    ) -> None:
        self.store = store
        self.settings = settings
        self.bus = EventBus(store)
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(max_concurrent)), thread_name_prefix="run"
        )
        self._cancel: Dict[str, threading.Event] = {}
        self._progress: Dict[str, Dict[str, int]] = {}
        self._lock = threading.Lock()

    # ---------------- 对外 ----------------
    def submit(self, run_id: str, req: Any) -> None:
        trade_date = (req.trade_date or date.today()).isoformat()
        self._register(run_id, req, trade_date)
        self._pool.submit(self._background, run_id, req, trade_date)

    def run_inline(self, run_id: str, req: Any) -> Dict[str, Any]:
        """同步跑完并返回结果（给 curl / 脚本用）。异常照常抛出，交给错误处理器。"""
        trade_date = (req.trade_date or date.today()).isoformat()
        self._register(run_id, req, trade_date)
        return self._execute(run_id, req, trade_date)

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            flag = self._cancel.get(run_id)
        if flag is None:
            return False
        flag.set()
        return True

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---------------- 内部 ----------------
    def _register(self, run_id: str, req: Any, trade_date: str) -> None:
        settings = self._settings_for(req)
        self.store.create(
            run_id,
            ticker=req.ticker,
            trade_date=trade_date,
            params=req.model_dump(mode="json"),
        )
        with self._lock:
            self._cancel[run_id] = threading.Event()
            self._progress[run_id] = {
                "done": 0,
                "total": estimate_total_nodes(settings),
            }

    def _settings_for(self, req: Any) -> Settings:
        rounds = self.settings.max_debate_rounds if req.rounds is None else int(req.rounds)
        return replace(
            self.settings,
            max_debate_rounds=rounds,
            offline=bool(self.settings.offline or req.offline),
        )

    def _background(self, run_id: str, req: Any, trade_date: str) -> None:
        try:
            self._execute(run_id, req, trade_date)
        except TaskCancelled:
            pass
        except Exception:  # noqa: BLE001 —— 状态与事件已在 _execute 内落库
            logger.exception("任务执行失败: %s", run_id)

    def _execute(self, run_id: str, req: Any, trade_date: str) -> Dict[str, Any]:
        store, bus = self.store, self.bus
        settings = self._settings_for(req)
        store.mark_running(run_id)
        bus.publish(run_id, "status", {"run_id": run_id, "status": "running"})
        try:
            provider = get_data_service(req.provider or settings.data_provider)
            state = stream_case(
                req.ticker,
                trade_date,
                settings,
                provider,
                on_node=lambda node, _update: self._on_node(run_id, node),
                on_event=lambda payload: bus.publish(run_id, "progress", payload),
            )
            result = to_result(run_id, req.ticker, trade_date, state)
            store.mark_succeeded(run_id, result)
            bus.publish(run_id, "done", {"run_id": run_id, "status": "succeeded"})
            return result
        except TaskCancelled:
            store.mark_cancelled(run_id)
            bus.publish(run_id, "done", {"run_id": run_id, "status": "cancelled"})
            raise
        except Exception as exc:
            store.mark_failed(run_id, exc)
            bus.publish(
                run_id,
                "error",
                {"run_id": run_id, "type": type(exc).__name__, "message": str(exc)[:500]},
            )
            raise
        finally:
            with self._lock:
                self._cancel.pop(run_id, None)
                self._progress.pop(run_id, None)

    def _on_node(self, run_id: str, node: str) -> None:
        with self._lock:
            flag = self._cancel.get(run_id)
            prog = self._progress.get(run_id)
        if flag is not None and flag.is_set():
            # 在节点边界取消：一次分析 10~60 秒，秒级粒度对用户已经足够
            raise TaskCancelled()
        payload: Dict[str, Any] = {"run_id": run_id, "node": node_label(str(node))}
        if prog:
            prog["done"] += 1
            if prog["total"]:
                payload["progress"] = round(min(0.99, prog["done"] / prog["total"]), 3)
        self.bus.publish(run_id, "node", payload)
