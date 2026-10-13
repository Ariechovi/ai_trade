"""图装配：把节点与边组装成一张可执行的 LangGraph。

整张图的骨架：

    3 个分析师（各自带工具循环） → 合并 → 多空辩论(N轮) → 研究经理 → 结束

    每个分析师的工具循环：
        <role> ──┬─(有 tool_calls 且未超上限)─> tools_<role> ─┐
                 │                                          │
                 └─(无 tool_calls / 超上限)──> msg_clear_<role> ─> merge

两个设计要点：
1. workflow 控拓扑：谁先谁后、辩论几轮，都在这里写死，与 LLM 无关；
2. agent 控内容：每个节点只做一件事，循环由回边实现，而不是节点里的 while。

容错：会做 I/O 的节点都套一层 GuardedNode（重试 + 降级）。
      纯函数节点（merge、报告提取）不套——它们没有失败的理由。

位置
----
上游（谁调用我）：runner.py、eval/run_eval.py、scripts/*（拿 build_graph 建图）
下游（我调用谁）：agents/specs.py（分析师表）、agents/analysts.py 等（各节点实现）

阅读建议：**从这个文件读起**。它给出整张图的形状，再顺着边去读各个节点。
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from langgraph.graph import END, START, StateGraph

from .agents.analysts import (
    AnalystLLMNode,
    AnalystReportNode,
    AnalystRouter,
    AnalystToolsNode,
)
from .agents.manager import make_manager_node
from .agents.researchers import make_bear_node, make_bull_node
from .agents.specs import ANALYSTS
from .config import Settings
from .resilience import GuardedNode
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
    max_tool_calls = max(1, int(settings.max_tool_iterations))

    def guarded(node):
        """给「会 I/O、可能失败」的节点套上重试 + 降级护栏。"""
        return GuardedNode(
            node,
            max_attempts=settings.max_retry_attempts,
            base_delay=settings.retry_base_delay,
        )

    graph = StateGraph(AgentState)

    # ---------------- 1. 分析师：LLM 节点 + 工具节点 + 报告节点 ----------------
    # 遍历 agents/specs.py 里的分析师表：加一个分析师 = 改那张表，不用动这里
    for spec in ANALYSTS:
        graph.add_node(
            spec.role,
            guarded(
                AnalystLLMNode(spec, llm_factory(spec.role), tool_map, max_tool_calls)
            ),
        )
        graph.add_node(f"tools_{spec.role}", guarded(AnalystToolsNode(spec, tool_map)))
        graph.add_node(f"msg_clear_{spec.role}", AnalystReportNode(spec))  # 纯函数，不套

    # ---------------- 2. 其余节点 ----------------
    # merge 必须 defer=True：默认情况下 LangGraph 在「任一入边到达」时就执行节点，
    # 三个分析师的循环长度往往不同（真实 LLM 一次可能返回多个 tool_calls），
    # 于是 merge 会被触发三次、辩论拿不到完整报告。defer=True 让它等齐所有入边。
    graph.add_node("merge", _merge_node, defer=True)
    graph.add_node("manager", guarded(make_manager_node(llm_factory)))
    if rounds > 0:
        graph.add_node("bull", guarded(make_bull_node(llm_factory)))
        graph.add_node("bear", guarded(make_bear_node(llm_factory)))

    # ---------------- 3. 分析师的边（含工具循环的回边）----------------
    for spec in ANALYSTS:
        role = spec.role
        graph.add_edge(START, role)
        graph.add_conditional_edges(
            role,
            AnalystRouter(spec, max_tool_calls),
            [f"tools_{role}", f"msg_clear_{role}"],
        )
        graph.add_edge(f"tools_{role}", role)         # 回边：工具执行完，回到 LLM 继续想
        graph.add_edge(f"msg_clear_{role}", "merge")  # 报告落定，进入汇合点

    # ---------------- 4. 辩论循环（条件边 + 计数器）----------------
    if rounds > 0:
        graph.add_edge("merge", "bull")
        graph.add_conditional_edges("bull", make_router(threshold, True), ["bear", "manager"])
        graph.add_conditional_edges("bear", make_router(threshold, False), ["bull", "manager"])
    else:
        graph.add_edge("merge", "manager")  # 轮数为 0：跳过辩论，直接给结论

    graph.add_edge("manager", END)
    return graph.compile()
