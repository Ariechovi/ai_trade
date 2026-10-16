"""韧性层：重试与降级。

分工（这是关键的设计判断）：
  - **重试**是横切关注点 → 放在 GuardedNode 包装器里，每个会 I/O 的节点都套一层
  - **降级**是业务语义   → 放在各节点自己的 degrade() 方法里（每个节点降级方式不同）

原则：只有会做 I/O、可能失败的节点才需要护栏。
      纯函数节点（merge、报告提取）不包——它们没有失败的理由。

位置
----
上游（谁调用我）：graph.py
下游（我调用谁）：无（只依赖标准库）
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# 可重试的错误特征：限流、超时、连接问题、服务端 5xx。
# 对应 TradingAgents-CN 的 core/agents/base.py:is_retryable_llm_error()
RETRYABLE_MARKERS = (
    "429",
    "rate limit",
    "rate_limit",
    "timeout",
    "timed out",
    "connection",
    "overloaded",
    "temporarily",
    "500",
    "502",
    "503",
    "504",
)


def is_retryable(exc: BaseException) -> bool:
    """判断一个异常是否值得重试。

    只重试「等一下可能就好」的错误；参数错误、鉴权失败这类重试多少次也没用。
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in RETRYABLE_MARKERS)


def backoff_delay(attempt: int, base_delay: float) -> float:
    """指数退避：第 1 次失败等 base，第 2 次等 2×base，第 3 次等 4×base……"""
    return base_delay * (2 ** (attempt - 1))


def error_record(stage: str, node: str, exc: BaseException) -> Dict[str, Any]:
    """把一次失败整理成结构化记录，汇入 state["_node_errors"]。"""
    return {
        "stage": stage,  # llm / tools / debate / manager
        "node": node,
        "type": type(exc).__name__,
        "message": str(exc)[:300],
    }


class GuardedNode:
    """给节点套上「重试 + 降级」护栏。

    执行流程：
        调用节点 → 失败且可重试且还有预算 → 退避后重试
                 → 失败但不可重试 / 重试次数用尽 → 调用 node.degrade() 产出降级结果

    也就是说：**异常永远不会穿透到图外**。最坏情况是一个降级结果，
    工作流的其余部分照常跑完。
    """

    def __init__(
        self,
        node: Any,
        *,
        max_attempts: int = 3,
        base_delay: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.node = node
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay = max(0.0, float(base_delay))
        self.sleeper = sleeper

    @property
    def name(self) -> str:
        return str(getattr(self.node, "name", type(self.node).__name__))

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.node(state)
            except Exception as exc:  # noqa: BLE001 —— 这里就是要兜住所有异常
                last_exc = exc
                can_retry = is_retryable(exc) and attempt < self.max_attempts
                if not can_retry:
                    break
                delay = backoff_delay(attempt, self.base_delay)
                logger.warning(
                    "[重试] %s 第 %d 次失败(%s)，%.2fs 后重试", self.name, attempt, exc, delay
                )
                self.sleeper(delay)
        logger.error("[降级] %s 重试无果，转入降级路径：%s", self.name, last_exc)
        return self.node.degrade(state, last_exc)
