"""对外数据模型（对齐 ai_meeting 的 models.py 位置）。

响应形状**复用 `services.runner.summarize()`**，不另立一套统计口径 ——
CLI / Streamlit / HTTP 必须看到同一组数字。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AnalysisRequest(BaseModel):
    ticker: str = Field(..., pattern=r"^\d{6}$", description="6 位股票代码")
    trade_date: Optional[date] = Field(None, description="分析日期，缺省=今天")
    provider: Optional[Literal["auto", "akshare", "mock"]] = Field(
        None, description="数据源，缺省=按 .env 的 DATA_PROVIDER"
    )
    rounds: Optional[int] = Field(None, ge=0, le=5, description="辩论轮数，覆盖 .env")
    offline: bool = Field(False, description="强制使用离线桩模型（不需要 API Key）")


class RunCreated(BaseModel):
    run_id: str
    status: str


class RunResult(BaseModel):
    run_id: str
    ticker: str
    trade_date: str
    status: str
    final_decision: str = ""
    technical_report: str = ""
    fundamental_report: str = ""
    news_report: str = ""
    debate_history: List[Dict[str, Any]] = Field(default_factory=list)
    trace: List[str] = Field(default_factory=list)
    degraded: List[str] = Field(
        default_factory=list,
        description="发生降级的节点。降级不是错误 —— HTTP 仍是 200",
    )
    metrics: Dict[str, Any] = Field(default_factory=dict)
