"""PostgreSQL 任务存储。

对应 V3 的 MongoDB 存储层（`tradingagents/config/mongodb_storage.py`）——
职责一样：状态机 + 事件落库。区别是我们把「状态机 + 事件回放」放进关系模型，
并且用一张 `run_events` 表同时解决推送和回放（V3 要维护 Redis 推送 + 数据库历史两条路）。

三个实现要点（都踩过）：
  1. **事件序号**用 `UPDATE runs SET event_seq = event_seq + 1 ... RETURNING`，
     原子且并发安全；替换掉 `SELECT MAX(seq)+1`（多 worker 下必撞主键）
  2. **JSONB 必须显式 `Jsonb()` 包装** —— psycopg3 对 dict 没有默认适配器，
     直接传会报错
  3. **时间**统一存 UTC（TIMESTAMPTZ），出口转 ISO 字符串

位置
----
上游（谁调用我）：main.py
下游（我调用谁）：common/pg.py（连接池）、psycopg / psycopg_pool
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)


def _iso(value: Any) -> Optional[str]:
    """把 date/datetime 转成 ISO 字符串，其他原样返回。"""
    if value is None:
        return None
    if isinstance(value, (datetime, date_cls)):
        return value.isoformat()
    return str(value)


class PgTaskStore:
    """`StoreProtocol` 的 PostgreSQL 实现。"""

    def __init__(self, settings: Any, *, pool: Any = None) -> None:
        self.settings = settings
        if pool is None:
            from common.pg import make_pool

            pool = make_pool(settings)
        self._pool = pool
        self._pool.open()

    # ================= 写入 =================

    def create(
        self, run_id: str, *, ticker: str, trade_date: str, params: Dict[str, Any]
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, ticker, trade_date, status, params)"
                " VALUES (%s, %s, %s, 'queued', %s)",
                (run_id, ticker, date_cls.fromisoformat(trade_date), Jsonb(params)),
            )

    def mark_running(self, run_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE runs SET status = 'running', started_at = now()"
                " WHERE run_id = %s",
                (run_id,),
            )

    def mark_succeeded(self, run_id: str, result: Dict[str, Any]) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE runs SET status = 'succeeded', finished_at = now(),"
                "               result = %s, final_decision = %s,"
                "               metrics = %s, degraded = %s"
                " WHERE run_id = %s",
                (
                    Jsonb(result),
                    str(result.get("final_decision") or ""),
                    Jsonb(result.get("metrics") or {}),
                    [str(x) for x in (result.get("degraded") or [])],
                    run_id,
                ),
            )

    def mark_failed(self, run_id: str, exc: BaseException) -> None:
        payload = {"type": type(exc).__name__, "message": str(exc)[:500]}
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE runs SET status = 'failed', finished_at = now(), error = %s"
                " WHERE run_id = %s",
                (Jsonb(payload), run_id),
            )

    def mark_cancelled(self, run_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE runs SET status = 'cancelled', finished_at = now()"
                " WHERE run_id = %s",
                (run_id,),
            )

    def sweep_interrupted(self) -> int:
        """启动自愈：进程被杀时没人来标失败，这些任务会永远卡在 running。"""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE runs SET status = 'interrupted', finished_at = now()"
                " WHERE status IN ('queued', 'running')"
            )
            affected = cur.rowcount
        if affected:
            logger.warning("启动自愈：%d 个残留任务被标记为 interrupted", affected)
        return int(affected)

    def append_event(self, run_id: str, event: str, data: Dict[str, Any]) -> int:
        """追加事件并返回 seq。

        两步在**同一个事务**里：先原子自增计数器拿 seq，再插入事件。
        这样多 worker 并发追加同一个 run 的事件也不会撞主键。
        """
        with self._pool.connection() as conn:
            row = conn.execute(
                "UPDATE runs SET event_seq = event_seq + 1"
                " WHERE run_id = %s RETURNING event_seq",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"run not found: {run_id}")
            seq = int(row["event_seq"])
            conn.execute(
                "INSERT INTO run_events (run_id, seq, event, data)"
                " VALUES (%s, %s, %s, %s)",
                (run_id, seq, event, Jsonb(data)),
            )
        return seq

    def close(self) -> None:
        self._pool.close()

    # ================= 读取 =================

    def status(self, run_id: str) -> str:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT status FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()
        return str(row["status"]) if row else "unknown"

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()
        return self._to_detail(row) if row is not None else None

    def read_events(self, run_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT seq, event, data FROM run_events"
                " WHERE run_id = %s AND seq > %s ORDER BY seq",
                (run_id, int(after_seq)),
            ).fetchall()
        return [
            {"seq": int(r["seq"]), "event": r["event"], "data": r["data"]} for r in rows
        ]

    def list(
        self,
        *,
        status: Optional[str] = None,
        ticker: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where: List[str] = []
        args: List[Any] = []
        if status:
            where.append("status = %s")
            args.append(status)
        if ticker:
            where.append("ticker = %s")
            args.append(ticker)
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        with self._pool.connection() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS n FROM runs {clause}", tuple(args)
            ).fetchone()["n"]
            rows = conn.execute(
                f"SELECT * FROM runs {clause}"
                " ORDER BY created_at DESC, run_id DESC LIMIT %s OFFSET %s",
                (*args, int(limit), int(offset)),
            ).fetchall()
        return [self._to_summary(r) for r in rows], int(total)

    # ================= 序列化 =================

    @staticmethod
    def _to_summary(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "ticker": row["ticker"],
            "status": row["status"],
            "trade_date": _iso(row["trade_date"]),
            "created_at": _iso(row["created_at"]),
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["finished_at"]),
            "degraded": list(row["degraded"] or []),
            "error": row["error"],
        }

    @classmethod
    def _to_detail(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        detail = cls._to_summary(row)
        detail["result"] = row["result"]
        detail["params"] = row["params"] or {}
        return detail
