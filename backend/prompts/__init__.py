"""提示词层：按角色组织，长文本与模板分离。

对应 TradingAgents-CN 的 `core/prompts/`（loader / manager / template 三层）
以及 `app/services/system_prompt_seed_service.py`（把职责契约沉淀成提示词模板）。

    analysts.py   三个分析师的职责契约（长文本）
    debaters.py   多空研究员的职责契约
    manager.py    研究经理的职责契约
    templates.py  用户消息模板（与角色无关的拼装函数）

为什么从 prompts.py 拆成包？——**抽象的边界应该跟着文本长度走**。
分析师提示词从 3 句变成 40 行后，塞在 specs.py 里会让那张「分析师表」没法读。
所以 specs.py 只保留短标识，长文本放这里。

位置
----
上游（谁调用我）：agents/specs.py、agents/analysts.py、agents/researchers.py、agents/manager.py
下游（我调用谁）：无（纯字符串）
"""

from __future__ import annotations

from .analysts import ANALYST_PROMPTS
from .debaters import BEAR_SYSTEM, BULL_SYSTEM
from .manager import MANAGER_SYSTEM
from .templates import analyst_user_prompt, debate_user_prompt, manager_user_prompt

__all__ = [
    "ANALYST_PROMPTS",
    "BEAR_SYSTEM",
    "BULL_SYSTEM",
    "MANAGER_SYSTEM",
    "analyst_user_prompt",
    "debate_user_prompt",
    "manager_user_prompt",
]
