"""任务存储：SQLite 两张表。

为什么是 SQLite，而不是 MongoDB / Redis？
任务中心的本质需求是「状态机 + 事件回放」，两张表就够。
Redis 只解决跨进程 pub/sub —— 而这里至多一个进程、至多一个 worker。

事件落库是刻意的设计：它让 SSE 天然支持「刷新页面后带 Last-Event-ID 续传」，
不需要像 V3 那样维护两条路（Redis 推送 + 数据库查历史）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id        TEXT PRIMARY KEY,
  ticker        TEXT NOT NULL,
  trade_date    TEXT,
  rounds        INTEGER,
  params_json   TEXT,
  status        TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  started_at    TEXT,
  finished_at   TEXT,
  result_json   TEXT,
  error_json    TEXT,
  degraded_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status  ON runs(status);

CREATE TABLE IF NOT EXISTS run_events (
  run_id    TEXT NOT NULL,
  seq       INTEGER NOT NULL,
  ts        TEXT NOT NULL,
  event     TEXT NOT NULL,
  data_json TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class TaskStore:
    """最小任务状态机：queued → running → succeeded / failed / cancelled / interrupted。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---------------- 写入 ----------------
    def create(
        self,
        run_id: str,
        *,
        ticker: str,
        trade_date: str,
        rounds: int,
        params: Dict[str, Any],
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs"
                " (run_id, ticker, trade_date, rounds, params_json, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'queued', ?)",
                (
                    run_id,
                    ticker,
                    trade_date,
                    rounds,
                    json.dumps(params, ensure_ascii=False, default=str),
                    _now(),
                ),
            )
            self._conn.commit()

    def _update(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        clause = ", ".join(f"{key} = ?" for key in fields)  # 键名来自本文件，非外部输入
        with self._lock:
            self._conn.execute(
                f"UPDATE runs SET {clause} WHERE run_id = ?",
                (*fields.values(), run_id),
            )
            self._conn.commit()

    def mark_running(self, run_id: str) -> None:
        self._update(run_id, status="running", started_at=_now())

    def mark_succeeded(self, run_id: str, result: Dict[str, Any]) -> None:
        self._update(
            run_id,
            status="succeeded",
            finished_at=_now(),
            result_json=json.dumps(result, ensure_ascii=False, default=str),
            degraded_json=json.dumps(result.get("degraded") or [], ensure_ascii=False),
        )

    def mark_failed(self, run_id: str, exc: BaseException) -> None:
        self._update(
            run_id,
            status="failed",
            finished_at=_now(),
            error_json=json.dumps(
                {"type": type(exc).__name__, "message": str(exc)[:500]},
                ensure_ascii=False,
            ),
        )

    def mark_cancelled(self, run_id: str) -> None:
        self._update(run_id, status="cancelled", finished_at=_now())

    def sweep_interrupted(self) -> int:
        """启动即自愈：进程被杀时没人来标记失败，这些任务会永远卡在 running。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status = 'interrupted', finished_at = ?"
                " WHERE status IN ('running', 'queued')",
                (_now(),),
            )
            self._conn.commit()
        return cur.rowcount

    def append_event(self, run_id: str, event: str, data: Dict[str, Any]) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            seq = int(row["next"])
            self._conn.execute(
                "INSERT INTO run_events (run_id, seq, ts, event, data_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    run_id,
                    seq,
                    _now(),
                    event,
                    json.dumps(data, ensure_ascii=False, default=str),
                ),
            )
            self._conn.commit()
        return seq

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- 读取 ----------------
    def status(self, run_id: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return str(row["status"]) if row else "unknown"

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._to_detail(row) if row is not None else None

    def read_events(self, run_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, event, data_json FROM run_events"
                " WHERE run_id = ? AND seq > ? ORDER BY seq",
                (run_id, int(after_seq)),
            ).fetchall()
        return [
            {"seq": int(r["seq"]), "event": r["event"], "data": json.loads(r["data_json"])}
            for r in rows
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
            where.append("status = ?")
            args.append(status)
        if ticker:
            where.append("ticker = ?")
            args.append(ticker)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM runs {clause}", tuple(args)
            ).fetchone()["n"]
            rows = self._conn.execute(
                f"SELECT * FROM runs {clause}"
                " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (*args, int(limit), int(offset)),
            ).fetchall()
        return [self._to_summary(r) for r in rows], int(total)

    # ---------------- 序列化 ----------------
    @staticmethod
    def _to_summary(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "ticker": row["ticker"],
            "status": row["status"],
            "trade_date": row["trade_date"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "degraded": json.loads(row["degraded_json"] or "[]"),
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
        }

    @classmethod
    def _to_detail(cls, row: sqlite3.Row) -> Dict[str, Any]:
        detail = cls._to_summary(row)
        detail["result"] = json.loads(row["result_json"]) if row["result_json"] else None
        detail["params"] = json.loads(row["params_json"] or "{}")
        return detail
