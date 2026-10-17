"""配置层：所有参数来自环境变量（.env）。

对齐 ai_meeting 的 settings.py：配置集中在应用根的这一层。
区别是这里的值来自 .env（而不是硬编码），因为 mini-agents 要在
"有 Key / 没 Key"两种模式下都能跑。

设计原则：没有 API Key 也能跑。检测不到 Key 时自动降级为离线桩模型，
这样"克隆即可运行"和"自动化验证"都不需要任何外部依赖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv

APP_VERSION = "0.1.0"

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent

# .env 优先取仓库根（产品级配置），其次取 backend/ 下的那份
for _candidate in (_REPO_ROOT / ".env", _BACKEND_DIR / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate)
        break


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    llm_temperature: float
    data_provider: str
    max_debate_rounds: int
    max_tool_iterations: int
    max_retry_attempts: int
    retry_base_delay: float
    offline: bool

    # ---- API 层（FastAPI）。不启用 API 时这些值不会被读到 ----
    api_key: str = ""
    api_host: str = "127.0.0.1"
    api_port: int = 8100
    cors_origins: Tuple[str, ...] = ()
    max_concurrent_runs: int = 2
    sse_heartbeat_seconds: int = 10
    sse_max_idle_seconds: int = 300

    # ---- 存储层（PostgreSQL，唯一实现，没有 SQLite 回退）----
    pg_host: str = ""                    # SSH 隧道场景下填 127.0.0.1
    pg_port: int = 5432                  # SSH 隧道场景下填**本地转发端口**
    pg_db: str = ""
    pg_user: str = ""
    pg_password: str = ""
    pg_sslmode: str = "prefer"           # 走隧道时 disable 也行；prefer 更稳
    pg_connect_timeout: int = 5          # 远程连接不能无限等
    pg_statement_timeout_ms: int = 10000 # 单条语句上限，防止拖死应用
    pg_pool_min: int = 1                 # 连接池下限
    pg_pool_max: int = 4                 # 连接池上限：远程连接是稀缺资源

    @property
    def has_api_key(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def pg_configured(self) -> bool:
        """是否填了 PG 连接信息（填了才算启用）。"""
        return bool(self.pg_host and self.pg_db)


def load_settings(
    *,
    offline: Optional[bool] = None,
    rounds: Optional[int] = None,
) -> Settings:
    """读取配置。

    offline: 显式指定是否离线；None 表示按 .env / 是否有 Key 自动判断。
    rounds:  显式覆盖辩论轮数（评测做消融实验时会用到）。
    """
    api_key = _env("DEEPSEEK_API_KEY") or _env("OPENAI_API_KEY")
    env_offline = _env("OFFLINE", "0").lower() in {"1", "true", "yes"}
    resolved_offline = env_offline if offline is None else offline
    # 关键：没有 Key 就自动离线，保证项目永远能跑起来
    resolved_offline = resolved_offline or not api_key

    default_rounds = _env_int("MAX_DEBATE_ROUNDS", 2)
    cors_raw = _env("API_CORS_ORIGINS", "http://localhost:3100,http://localhost:8501")
    return Settings(
        llm_model=_env("LLM_MODEL", "deepseek-chat"),
        llm_base_url=_env("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        llm_api_key=api_key,
        llm_temperature=_env_float("LLM_TEMPERATURE", 0.3),
        data_provider=_env("DATA_PROVIDER", "mock").lower(),
        max_debate_rounds=default_rounds if rounds is None else rounds,
        max_tool_iterations=_env_int("MAX_TOOL_ITERATIONS", 3),
        max_retry_attempts=_env_int("MAX_RETRY_ATTEMPTS", 3),
        retry_base_delay=_env_float("RETRY_BASE_DELAY", 0.5),
        offline=resolved_offline,
        api_key=_env("API_KEY"),
        api_host=_env("API_HOST", "127.0.0.1"),
        api_port=_env_int("API_PORT", 8100),
        cors_origins=tuple(p.strip() for p in cors_raw.split(",") if p.strip()),
        max_concurrent_runs=max(1, _env_int("MAX_CONCURRENT_RUNS", 2)),
        sse_heartbeat_seconds=max(1, _env_int("SSE_HEARTBEAT_SECONDS", 10)),
        sse_max_idle_seconds=max(5, _env_int("SSE_MAX_IDLE_SECONDS", 300)),
        pg_host=_env("PG_HOST"),
        pg_port=_env_int("PG_PORT", 5432),
        pg_db=_env("PG_DB"),
        pg_user=_env("PG_USER"),
        pg_password=_env("PG_PASSWORD"),
        pg_sslmode=_env("PG_SSLMODE", "prefer"),
        pg_connect_timeout=_env_int("PG_CONNECT_TIMEOUT", 5),
        pg_statement_timeout_ms=_env_int("PG_STATEMENT_TIMEOUT_MS", 10000),
        pg_pool_min=max(1, _env_int("PG_POOL_MIN", 1)),
        pg_pool_max=max(1, _env_int("PG_POOL_MAX", 4)),
    )
