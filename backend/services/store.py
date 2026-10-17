"""存储层接口。

业务层（`api/`、`task_runner.py`）只依赖这个协议，不 import 具体实现 ——
好处有两个：换存储只改一处；测试可以注入替身。

**关于「双实现」**：曾经考虑过 SQLite + PG 两份实现做迁移对拍，后来放弃了。
理由：存储面只有十来个方法，对拍的收益是一次性的；
而长期维护两份的代价是实打实的（而且 SQLite 与 PG 的时间、并发、JSON 语义
本来就不同，强行对齐反而扭曲设计）。
**抽象的价值在「一个接口」，不在「两个实现」。**

位置
----
上游（谁依赖我）：main.py、services/task_runner.py
下游：services/pg_store.py 实现它
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class StoreProtocol(Protocol):
    """任务存储：状态机 + 事件落库。"""

    # ---------------- 写入 ----------------

    def create(
        self, run_id: str, *, ticker: str, trade_date: str, params: Dict[str, Any]
    ) -> None:
        """登记一个新任务，初始状态 queued。"""

    def mark_running(self, run_id: str) -> None: ...

    def mark_succeeded(self, run_id: str, result: Dict[str, Any]) -> None:
        """写终态 + 结果。result 的形状由 runner.to_result() 决定。"""

    def mark_failed(self, run_id: str, exc: BaseException) -> None: ...

    def mark_cancelled(self, run_id: str) -> None: ...

    def sweep_interrupted(self) -> int:
        """启动自愈：把残留的 queued/running 标成 interrupted，返回影响行数。"""

    def append_event(self, run_id: str, event: str, data: Dict[str, Any]) -> int:
        """追加一条事件，返回它的 seq。**必须先落库再推送** —— 断点续传靠它。"""

    def close(self) -> None: ...

    # ---------------- 读取 ----------------

    def status(self, run_id: str) -> str: ...

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        """详情（含 result 与 params）；不存在返回 None。"""

    def read_events(self, run_id: str, after_seq: int = 0) -> List[Dict[str, Any]]: ...

    def list(
        self,
        *,
        status: Optional[str] = None,
        ticker: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """返回 (摘要列表, 总数)。摘要不含 result —— 列表页不该拖回 20KB 的正文。"""
