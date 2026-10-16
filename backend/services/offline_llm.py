"""离线桩模型：不联网、不花钱，也能把整张图完整跑一遍。

它是一个确定性的假 LLM：
- 绑定了工具、且还有没用过的工具 → 返回一个 tool_call，**按顺序**调用下一个工具；
- 工具都调过了，或本身没绑定工具 → 返回一段最终文本。

「按顺序调多个工具」是刻意设计的：只有这样，图上的工具循环才会真正多轮往返
（条件边 → 工具节点 → 回边 → 条件边 …），而不是一次就结束。
真实 LLM 当然不会这么机械，但它演示的路径是一样的。

两个用途：
1. 任何人 clone 下来就能看到完整流程，不需要 API Key；
2. 自动化自检可以在离线环境验证图拓扑、reducer、辩论轮数是否正确。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

_ROLE_TITLES = {
    "technical": "技术面",
    "fundamental": "基本面",
    "news": "消息面",
    "bull": "多头",
    "bear": "空头",
    "manager": "研究经理",
}


def _stub_args(name: str, ticker: str) -> Dict[str, Any]:
    """离线桩为不同工具准备的最小参数集（有些工具需要额外参数）。"""
    if name == "compare_peers":
        return {"ticker": ticker, "peers": "000001,600036"}
    if name == "filter_news":
        return {"ticker": ticker, "keyword": "公告"}
    return {"ticker": ticker}


class ScriptedChatModel(BaseChatModel):
    """确定性的假模型（离线模式使用）。"""

    role: str = "generic"
    ticker: str = ""
    bound_tools: List[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-offline"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        return self.model_copy(update={"bound_tools": list(tools)})

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=self._respond(list(messages)))]
        )

    # ---------------- 内部逻辑 ----------------
    def _respond(self, messages: List[BaseMessage]) -> AIMessage:
        ticker = self.ticker or "600519"
        done = sum(1 for m in messages if isinstance(m, ToolMessage))
        if self.bound_tools and done < len(self.bound_tools):
            tool = self.bound_tools[done]
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool.name,
                        "args": _stub_args(tool.name, ticker),
                        "id": f"call_offline_{done + 1}",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content=self._final_text(messages))

    def _final_text(self, messages: List[BaseMessage]) -> str:
        title = _ROLE_TITLES.get(self.role, self.role)
        ticker = self.ticker or "600519"
        outputs = [str(m.content).strip() for m in messages if isinstance(m, ToolMessage)]
        tool_output = "\n".join(outputs) if outputs else ""

        if self.role in {"technical", "fundamental", "news"}:
            return (
                f"## {title}分析（离线桩模型 · {ticker}）\n\n"
                f"共调用 {len(outputs)} 个工具，返回：\n{tool_output}\n\n"
                f"结论：{title}信号中性，建议结合其他维度共同判断，等待进一步验证。\n\n"
                "> 本段由离线桩模型生成，仅用于验证流程，不构成投资建议。"
            )
        if self.role == "bull":
            return (
                f"多头观点：{ticker} 估值具备安全边际，景气度与催化清晰，"
                "支持在回调中积极关注。"
            )
        if self.role == "bear":
            return (
                f"空头观点：{ticker} 的盈利增速与估值匹配度存疑，"
                "一旦景气回落将面临戴维斯双杀风险。"
            )
        if self.role == "manager":
            return (
                f"## 最终研究结论（{ticker}）\n\n"
                "倾向：中性偏多。\n\n"
                "核心逻辑：基本面未恶化，消息面中性，技术面等待方向确认。\n\n"
                "主要风险：估值与盈利增速的匹配度。\n\n"
                "可执行建议：分批建仓，并设置明确的止损位。\n\n"
                "> 离线桩模型输出，仅用于流程验证，不构成投资建议。"
            )
        return f"[{title}] {ticker} 的离线输出。"
