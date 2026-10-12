"""图装配：把节点与边组装成一张可执行的 LangGraph。

整张图的骨架：
    3 个分析师并行 → 合并 → 多空辩论循环 → 研究经理 → 结束

两个设计要点：
1. workflow 控拓扑：谁先谁后、辩论几轮，都在这里写死，与 LLM 无关；
2. agent 控内容：每个节点内部是 LLM 自主的工具调用循环（见 agents/base.py）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from langgraph.graph import END, START, StateGraph

from .agents.analysts import ANALYST_ROLES, make_analyst_node
from .agents.manager import make_manager_node
from .agents.researchers import make_bear_node, make_bull_node
from .config import Settings
from .state import AgentState


def _merge_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """汇合点：三个分析师全部完成后才会执行（LangGraph 的扇入屏障）。"""
    return {"trace": ["merge"]}


def make_router(threshold: int, after_bull: bool) -> Callable[[Dict[str, Any]], str]:
    """辩论路由器：用累加计数器决定「继续辩论」还是「交给经理」。"""

    def router(state: Dict[str, Any]) -> str:
        if int(state.get("debate_count", 0)) >= threshold:
            return "manager"
        return "bear" if after_bull else "bull"

    return router


def build_graph(*, llm_factory, tool_map: Dict[str, Any], settings: Settings):
    """构建并编译整张图。"""
    rounds = max(0, int(settings.max_debate_rounds))
    threshold = rounds * 2  # 一轮 = 多头 + 空头各发言一次

    graph = StateGraph(AgentState)

    # ---------------- 节点 ----------------
    for role in ANALYST_ROLES:
        graph.add_node(role, make_analyst_node(role, llm_factory, tool_map, settings))
    graph.add_node("merge", _merge_node)
    graph.add_node("manager", make_manager_node(llm_factory, settings))
    if rounds > 0:
        graph.add_node("bull", make_bull_node(llm_factory, settings))
        graph.add_node("bear", make_bear_node(llm_factory, settings))

    # ---------------- 边：并行扇出 + 扇入 ----------------
    for role in ANALYST_ROLES:
        graph.add_edge(START, role)
        graph.add_edge(role, "merge")

    # ---------------- 边：辩论循环（条件边 + 计数器）----------------
    if rounds > 0:
        graph.add_edge("merge", "bull")
        graph.add_conditional_edges("bull", make_router(threshold, True), ["bear", "manager"])
        graph.add_conditional_edges("bear", make_router(threshold, False), ["bull", "manager"])
    else:
        graph.add_edge("merge", "manager")  # 轮数为 0：跳过辩论，直接给结论

    graph.add_edge("manager", END)
    return graph.compile()
