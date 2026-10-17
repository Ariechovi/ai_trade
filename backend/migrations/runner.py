"""轻量迁移：把 migrations/*.sql 按文件名顺序应用，已应用的跳过。

用法（**在 backend/ 目录下**执行）：
    python -m migrations.runner status     # 看哪些已应用、哪些待应用
    python -m migrations.runner apply      # 应用待执行的迁移

为什么不用 Alembic：
    这里的迁移就是「建表 + 建索引」，纯 SQL 足够；引入 Alembic 还要多一层
    ORM 模型定义，反而更绕。等 schema 复杂到需要 autogenerate 再换也不迟。
    （V3 用的是同思路的 `migrations/` + CLI，见它的 `migrations/cli.py`。）

位置
----
上游（谁调用我）：你（命令行）
下游（我调用谁）：common/pg.py（连接）、settings.py（配置）
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Sequence, Tuple

MIGRATIONS_DIR = Path(__file__).resolve().parent

# 迁移登记表：谁应用过、什么时候应用的
_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def migration_files() -> List[Path]:
    """按文件名排序 —— 文件名以数字开头，字符串排序即版本顺序。"""
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name[:1].isdigit())


def split_statements(sql: str) -> List[str]:
    """把 SQL 文件切成单条语句。

    psycopg 带参数时走扩展协议，一次只能执行一条语句，所以要自己切。
    规则：先去掉 `--` 行注释，再按 `;` 切。

    ⚠️ 已知限制：字符串字面量里含 `--` 或 `;` 的语句会被切错。
       我们的迁移全是 DDL，够用；将来真遇到再换正式的分词器。
    """
    cleaned: List[str] = []
    for line in sql.splitlines():
        cut = line.find("--")
        cleaned.append(line[:cut] if cut >= 0 else line)
    return [stmt.strip() for stmt in "\n".join(cleaned).split(";") if stmt.strip()]


def _ensure_registry(conn) -> None:
    conn.execute(_BOOTSTRAP)
    # 不需要 commit()：调用方用 autocommit=True，语句自成事务。
    # （早先这里手动 commit 过一次，结果只有这张表落库，成了排查线索。）


def _applied(conn) -> set:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def status(settings) -> Tuple[List[str], List[str]]:
    """返回 (已应用, 待应用)。"""
    from common.pg import connect

    names = [p.stem for p in migration_files()]
    with connect(settings, autocommit=True) as conn:
        _ensure_registry(conn)
        done = _applied(conn)
    return [n for n in names if n in done], [n for n in names if n not in done]


def apply(settings) -> List[str]:
    """应用所有待执行迁移，返回本次应用的版本列表。

    **每个文件一个事务**：失败就整文件回滚，不会留下半张表。
    """
    from common.pg import connect

    applied_now: List[str] = []
    # autocommit=True 是关键：这样 conn.transaction() 才是真的 BEGIN...COMMIT，
    # 而不是退化成一个没人提交的 SAVEPOINT。
    with connect(settings, autocommit=True) as conn:
        _ensure_registry(conn)
        done = _applied(conn)
        for path in migration_files():
            version = path.stem
            if version in done:
                continue
            statements = split_statements(path.read_text(encoding="utf-8"))
            with conn.transaction():
                for stmt in statements:
                    with conn.cursor() as cur:
                        cur.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
                )
            applied_now.append(f"{version}（{len(statements)} 条语句）")
    return applied_now


# 迁移跑完必须存在的核心表 —— 用来兜住"报成功但没建出来"这类静默失败
CORE_TABLES = ("runs", "run_nodes", "run_llm_calls", "run_tool_calls", "run_events")


def missing_core_tables(settings) -> List[str]:
    """返回缺失的核心表。

    **这个校验是必需的不是锦上添花**：迁移器曾经因为事务没提交，
    报「应用成功、22 条语句」而表一张都没建出来 —— 没有校验就只能靠人肉发现。
    """
    from common.pg import connect

    with connect(settings, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
            )
            have = {row[0] for row in cur.fetchall()}
    return [name for name in CORE_TABLES if name not in have]


def main(argv: Sequence[str]) -> int:
    from common.pg import pg_enabled, ping
    from settings import load_settings

    action = (argv[0] if argv else "status").lower()
    settings = load_settings()

    if not pg_enabled(settings):
        print("未配置 PostgreSQL（PG_HOST / PG_DB 为空）。")
        print("  请在 .env 里填 PG_* 连接信息；不填则继续用 SQLite（RUN_STORE=sqlite）。")
        return 2

    try:
        info = ping(settings)
    except Exception as exc:  # noqa: BLE001 —— 这里就是要给人话，不要抛栈
        print(f"❌ 连接失败：{type(exc).__name__}: {exc}")
        print("   排查顺序：")
        print("     1. SSH 隧道是否已建立（ssh -N -L 15432:localhost:5432 user@server）")
        print("     2. PG_HOST / PG_PORT 是否指向**隧道本地端口**")
        print("     3. PG_USER / PG_PASSWORD / PG_DB 是否正确")
        print("     4. 服务端是否强制 SSL（试 PG_SSLMODE=require）")
        return 1

    print(f"✅ 连接成功：{info['dsn']}")
    print(f"   服务端  : {info['server_version']}")
    print(f"   库 / 用户: {info['database']} / {info['user']}")
    print(f"   schema  : {info['schema']}（search_path={info['search_path']}）")
    print(f"   扩展    : {', '.join(info['extensions']) or '（无）'}")

    if action == "status":
        done, pending = status(settings)
        print(f"   已应用  : {', '.join(done) or '（无）'}")
        print(f"   待应用  : {', '.join(pending) or '（无）'}")
        missing = missing_core_tables(settings)
        print(f"   核心表  : {'✅ 齐全' if not missing else '❌ 缺少 ' + ', '.join(missing)}")
        return 0

    if action == "apply":
        applied = apply(settings)
        print(f"   本次应用: {', '.join(applied) or '（无待执行迁移）'}")

        # ★ 校验：不校验的话，"事务没提交"这类静默失败会一直瞒着你
        missing = missing_core_tables(settings)
        info_after = ping(settings)
        # 扩展状态要看**应用之后**的 —— 0002 会新建 pg_trgm
        print(f"   扩展    : {', '.join(info_after['extensions']) or '（无）'}")
        print(f"   当前表  : {', '.join(info_after['tables']) or '（无）'}")
        if missing:
            print()
            print(f"   ❌ 迁移记录已更新，但核心表缺失：{', '.join(missing)}")
            print("      最常见原因：上一次运行写入了迁移记录，但事务没有真正提交。")
            print("      清理脏记录后重跑：")
            print("        psql ... -c 'DELETE FROM schema_migrations;'")
            print("        python -m migrations.runner apply")
            return 1
        print("   ✅ 5 张核心表齐全")
        return 0

    print(f"未知动作：{action}（可用：status / apply）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
