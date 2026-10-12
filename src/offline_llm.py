"""离线桩模型：不联网、不花钱，也能把整张图完整跑一遍。

它是一个确定性的假 LLM：
- 绑定了工具、且还没拿到工具结果 → 返回一个 tool_call，要求调用第一个工具；
- 已经拿到工具结果，或本身没绑定工具 → 返回一段最终文本。

两个用途：
1. 任何人 clone 下来就能看到完整流程，不需要 API Key；
2. 自动化自检可以在离线环境验证图拓扑、reducer、辩论轮数是否正确。
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

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
        tool_result_seen = any(isinstance(m, ToolMessage) for m in messages)
        if self.bound_tools and not tool_result_seen:
            tool = self.bound_tools[0]
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool.name,
                        "args": {"ticker": self.ticker or "600519"},
                        "id": "call_offline_1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content=self._final_text(messages))

    def _final_text(self, messages: List[BaseMessage]) -> str:
        title = _ROLE_TITLES.get(self.role, self.role)
        ticker = self.ticker or "600519"
        tool_output = ""
        for m in messages:
            if isinstance(m, ToolMessage):
                tool_output = str(m.content)

        if self.role in {"technical", "fundamental", "news"}:
            return (
                f"## {title}分析（离线桩模型 · {ticker}）\n\n"
                f"工具返回：{tool_output}\n\n"
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
