"""研究经理节点：综合三份分析师报告与多空辩论，产出最终结论。

经理不需要调用工具，所以只用 complete() 做一次调用——没有任何循环。

位置
----
上游（谁调用我）：graph.py
下游（我调用谁）：agents/base.py（complete）、prompts.py（模板）、resilience.py（降级记录）
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from .. import prompts
from ..resilience import error_record
from .base import complete

DEGRADED_DECISION = (
    "## 最终结论（降级）\n\n"
    "研究经理节点执行失败，本次未能生成完整结论。\n"
    "请参考上方各分析师的独立报告。\n"
)


class ManagerNode:
    """研究经理：综合分析师报告与多空辩论，产出最终结论。"""

    def __init__(self, llm: Any) -> None:
        self.llm = llm

    @property
    def name(self) -> str:
        return "manager"

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        text = complete(self.llm, prompts.MANAGER_SYSTEM, prompts.manager_user_prompt(state))
        return {"final_decision": text, "trace": ["manager"]}

    def degrade(self, state: Dict[str, Any], exc: BaseException) -> Dict[str, Any]:
        return {
            "final_decision": DEGRADED_DECISION,
            "_node_errors": {"manager": error_record("manager", self.name, exc)},
            "trace": ["degraded:manager"],
        }


def make_manager_node(llm_factory: Callable[[str], Any]) -> ManagerNode:
    return ManagerNode(llm_factory("manager"))
