"""零依赖自检入口：内部等价于 `pytest -m "not network"`。

用法：
    python scripts/selfcheck.py              # 全部离线用例
    python scripts/selfcheck.py -k cache     # 参数原样透传给 pytest
    python scripts/selfcheck.py -m network   # 显式跑需要联网的用例

为什么这里只剩一个转发壳？
因为同一批断言有两套实现，迟早会一个绿一个红 —— 项目自己的架构文档写过
「聚合逻辑只能有一份」，断言是同一个道理。真正的用例都在 tests/ 下。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    args = [sys.executable, "-m", "pytest", "-m", "not network", *argv]
    return subprocess.call(args, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
