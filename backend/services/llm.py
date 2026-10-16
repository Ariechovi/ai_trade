"""LLM 工厂：同一套代码既支持真实 DeepSeek，也支持离线桩模型。

DeepSeek 提供 OpenAI 兼容接口，所以真实模式直接让 ChatOpenAI 指过去即可。
"""

from __future__ import annotations

from settings import Settings


def build_llm(settings: Settings, *, role: str = "generic", ticker: str = ""):
    """按角色构建 LLM。

    离线：返回 ScriptedChatModel（无需 Key、无需网络）。
    在线：返回指向 DeepSeek 的 ChatOpenAI。
    """
    if settings.offline:
        from .offline_llm import ScriptedChatModel

        return ScriptedChatModel(role=role, ticker=ticker)

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
    )
