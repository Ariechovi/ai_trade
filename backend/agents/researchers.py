"""研究员节点：多头 / 空头交替发言。

「谁会接话」由 graph.py 的条件边决定，节点本身只负责说一轮。
研究员不调用工具，所以只用 complete() 做一次调用。

两个 reducer 字段是关键：
  - debate_history：追加一条发言
  - debate_count  ：累加 1，供路由器判断是否达到轮数上限

位置
----
上游（谁调用我）：graph.py
下游（我调用谁）：agents/base.py（complete）、prompts.py（模板）、resilience.py（降级记录）
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict

import prompts
from common.observability import emit_event, llm_call_record
from common.resilience import error_record
from .base import complete_message, extract_text


class DebaterNode:
    """一个辩论参与者的「单次发言」节点。"""

    def __init__(self, side: str, system_prompt: str, llm: Any) -> None:
        self.side = side
        self.system_prompt = system_prompt
        self.llm = llm

    @property
    def name(self) -> str:
        return f"debate:{self.side}"

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        emit_event(
            f"{'多头' if self.side == 'bull' else '空头'}研究员：模型思考中",
            role=self.side,
        )
        started = time.perf_counter()
        response = complete_message(
            self.llm,
            self.system_prompt,
            prompts.debate_user_prompt(state, self.side),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "debate_history": [
                {"speaker": self.side, "content": extract_text(response)}
            ],
            "debate_count": 1,
            "last_speaker": self.side,
            "llm_calls": [
                llm_call_record(
                    role=self.side,
                    stage="debate",
                    duration_ms=elapsed_ms,
                    message=response,
                )
            ],
            "trace": [f"debate:{self.side}"],
        }

    def degrade(self, state: Dict[str, Any], exc: BaseException) -> Dict[str, Any]:
        """发言失败 → 用一段占位发言顶替。

        ⚠️ 计数器必须照常推进。否则路由器会一直把控制权交回本节点，
        形成死循环——图里的死循环比函数里的死循环更难察觉。
        """
        return {
            "debate_history": [{"speaker": self.side, "content": f"[{self.side} 发言失败]"}],
            "debate_count": 1,
            "last_speaker": self.side,
            "_node_errors": {f"debate.{self.side}": error_record("debate", self.name, exc)},
            "trace": [f"degraded:{self.name}"],
        }


def make_bull_node(llm_factory: Callable[[str], Any]) -> DebaterNode:
    return DebaterNode("bull", prompts.BULL_SYSTEM, llm_factory("bull"))


def make_bear_node(llm_factory: Callable[[str], Any]) -> DebaterNode:
    return DebaterNode("bear", prompts.BEAR_SYSTEM, llm_factory("bear"))
