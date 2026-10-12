"""研究经理节点：综合三份分析师报告与多空辩论，产出最终结论。

经理不需要调用工具，所以只用 complete() 做一次调用——没有任何循环。
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from .. import prompts
from .base import complete


def make_manager_node(llm_factory: Callable[[str], Any]):
    llm = llm_factory("manager")

    def node(state: Dict[str, Any]) -> Dict[str, Any]:
        text = complete(llm, prompts.MANAGER_SYSTEM, prompts.manager_user_prompt(state))
        return {"final_decision": text, "trace": ["manager"]}

    node.__name__ = "manager_node"
    return node
