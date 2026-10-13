"""文件缓存：把取过的数据存下来，避免反复打网络。

对应 TradingAgents-CN 的 `tradingagents/dataflows/cache/file_cache.py`。
V3 的缓存有完整元数据、按数据类型分目录、多级过期策略；这里保留核心：
cache key → 文件，附带写入时间与 TTL。

⚠️ 一个刻意的设计：**缓存写失败不影响主流程**。缓存是加速手段，不是正确性依赖。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional


class FileCache:
    """极简文件缓存。"""

    def __init__(self, root: Path, ttl_seconds: int = 3600) -> None:
        self.root = Path(root)
        self.ttl_seconds = int(ttl_seconds)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _path(self, key: str) -> Path:
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        return self.root / f"{digest}.json"

    def get(self, key: str) -> Optional[Any]:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - float(payload.get("_cached_at", 0)) > self.ttl_seconds:
            return None  # 过期
        return payload.get("value")

    def set(self, key: str, value: Any) -> None:
        payload = {"_cached_at": time.time(), "key": key, "value": value}
        try:
            self._path(key).write_text(
                json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
            )
        except (OSError, TypeError):
            pass  # 缓存写失败不该影响主流程

    def clear(self) -> int:
        removed = 0
        for path in self.root.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed
