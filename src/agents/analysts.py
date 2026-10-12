"""分析师节点：三个角色并行执行，各写各的状态字段（互不覆盖）。"""

from __future__ import annotations

from typing import Any, Callable, Dict

from .. import prompts
from ..config import Settings
from ..tools import AGENT_TOOLS
from .base import run_agent

ANALYST_ROLES = ("technical", "fundamental", "news")

_REPORT_FIELD = {
    "technical": "technical_report",
    "fundamental": "fundamental_report",
    "news": "news_report",
}


def make_analyst_node(
    role: str,
    llm_factory: Callable[[str], Any],
    tool_map: Dict[str, Any],
    settings: Settings,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """构建一个分析师节点函数。

    注意：每个角色只拿到自己需要的工具（最小权限原则）。
    """
    allowed = {name: tool_map[name] for name in AGENT_TOOLS.get(role, []) if name in tool_map}
    field = _REPORT_FIELD[role]
    llm = llm_factory(role)

    def node(state: Dict[str, Any]) -> Dict[str, Any]:
        text = run_agent(
            llm,
            allowed,
            prompts.ANALYST_SYSTEM[role],
            prompts.analyst_user_prompt(role, state),
            settings.max_tool_iterations,
        )
        return {field: text, "trace": [f"analyst:{role}"]}

    node.__name__ = f"{role}_analyst_node"
    return node
