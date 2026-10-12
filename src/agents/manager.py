"""研究经理节点：综合三份分析师报告与多空辩论，产出最终结论。"""

from __future__ import annotations

from typing import Any, Callable, Dict

from .. import prompts
from ..config import Settings
from .base import run_agent


def make_manager_node(llm_factory: Callable[[str], Any], settings: Settings):
    llm = llm_factory("manager")

    def node(state: Dict[str, Any]) -> Dict[str, Any]:
        text = run_agent(
            llm,
            {},
            prompts.MANAGER_SYSTEM,
            prompts.manager_user_prompt(state),
            settings.max_tool_iterations,
        )
        return {"final_decision": text, "trace": ["manager"]}

    node.__name__ = "manager_node"
    return node
