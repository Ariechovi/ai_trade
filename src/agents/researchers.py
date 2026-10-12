"""研究员节点：多头 / 空头交替发言。

「谁会接话」由 graph.py 的条件边决定，节点本身只负责说一轮。
两个 reducer 字段是关键：
  - debate_history：追加一条发言
  - debate_count  ：累加 1，供路由器判断是否达到轮数上限
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from .. import prompts
from ..config import Settings
from .base import run_agent


def _make_speaker(
    side: str,
    system_prompt: str,
    llm: Any,
    settings: Settings,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    def node(state: Dict[str, Any]) -> Dict[str, Any]:
        text = run_agent(
            llm,
            {},
            system_prompt,
            prompts.debate_user_prompt(state, side),
            settings.max_tool_iterations,
        )
        return {
            "debate_history": [{"speaker": side, "content": text}],
            "debate_count": 1,
            "last_speaker": side,
            "trace": [f"debate:{side}"],
        }

    node.__name__ = f"{side}_node"
    return node


def make_bull_node(llm_factory: Callable[[str], Any], settings: Settings):
    return _make_speaker("bull", prompts.BULL_SYSTEM, llm_factory("bull"), settings)


def make_bear_node(llm_factory: Callable[[str], Any], settings: Settings):
    return _make_speaker("bear", prompts.BEAR_SYSTEM, llm_factory("bear"), settings)
