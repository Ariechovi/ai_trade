"""PostgreSQL 连接层。

只做三件事：**拼连接串、建连接、做健康检查**。
「存什么、怎么查」留给迁移脚本和 `services/pg_store.py` —— 连接与业务分开。

SSH 隧道场景下的两个注意点：
  · `PG_HOST` 填的是**隧道本地端口**（例如 127.0.0.1:15432），不是服务器真实地址；
  · sslmode 默认 `prefer`：隧道本身已经加密，`disable` 也能用；
    但 `prefer` 更稳 —— 服务端强制 SSL 时也能连上。

位置
----
上游（谁调用我）：migrations/runner.py、scripts/*
下游（我调用谁）：psycopg（延迟导入，未装驱动时不影响其他功能）
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator


def pg_enabled(settings: Any) -> bool:
    """配置了 host 与 db 才算启用；留空 = 继续用 SQLite。"""
    return bool(getattr(settings, "pg_host", "") and getattr(settings, "pg_db", ""))


def pg_dsn(settings: Any) -> str:
    """拼连接串。

    用 `make_conninfo` 而不是手拼字符串 —— 密码里带 `@` `:` `/` 时
    手拼会直接拼坏，而且报错极难看懂。
    """
    from psycopg.conninfo import make_conninfo

    return make_conninfo(
        host=str(settings.pg_host),
        port=int(settings.pg_port),
        dbname=str(settings.pg_db),
        user=str(settings.pg_user),
        password=str(settings.pg_password),
        sslmode=str(settings.pg_sslmode),
        connect_timeout=int(settings.pg_connect_timeout),
        options=f"-c statement_timeout={int(settings.pg_statement_timeout_ms)}",
    )


def safe_dsn(settings: Any) -> str:
    """打日志/打印用的连接串（密码打码）。"""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    info = conninfo_to_dict(pg_dsn(settings))
    if info.get("password"):
        info["password"] = "***"
    return make_conninfo(**info)


def make_pool(settings: Any, *, min_size=None, max_size=None):
    """建连接池。

    远程库每次建连都有网络往返，业务侧**必须**用池。
    池子开小一点：远程连接是稀缺资源，而我们的写入是低频的。

    `timeout` 用 connect_timeout —— 池里拿不到连接就快速失败，
    不要让请求挂在那儿等 30 秒。
    """
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    return ConnectionPool(
        pg_dsn(settings),
        min_size=int(min_size if min_size is not None else settings.pg_pool_min),
        max_size=int(max_size if max_size is not None else settings.pg_pool_max),
        timeout=float(settings.pg_connect_timeout),
        kwargs={"row_factory": dict_row},   # 让查询返回 dict，代码更好读
        open=False,
        name="mini-agents",
    )


@contextmanager
def connect(settings: Any, *, autocommit: bool = False) -> Iterator[Any]:
    """建一个**单连接**（迁移脚本、健康检查用）。

    业务侧应该用连接池（见 `make_pool`）—— 远程库每次建连都有网络往返成本。

    `autocommit=True` 时每条语句自成事务。**迁移器必须用这个模式**：
    它要自己用 `conn.transaction()` 控制「每个文件一个事务」，
    而 psycopg 默认的隐式事务会让 `conn.transaction()` 退化成 SAVEPOINT ——
    最后没有任何人 COMMIT，关连接时全部回滚。

    （这个坑真踩过：CLI 报「应用成功、22 条语句」，表却一张都没建出来。）
    """
    import psycopg

    conn = psycopg.connect(pg_dsn(settings), autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()


def ping(settings: Any) -> Dict[str, Any]:
    """健康检查：版本 / 库 / 用户 / 扩展 / 表清单。

    返回结构可以直接给 CLI 和后续的健康端点用。
    """
    result: Dict[str, Any] = {"dsn": safe_dsn(settings), "ok": False}
    with connect(settings, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            result["server_version"] = str(cur.fetchone()[0]).split(" on ")[0]

            cur.execute("SELECT current_database(), current_user")
            database, user = cur.fetchone()
            result["database"], result["user"] = database, user

            # 表到底建在哪个 schema —— PG 15+ 之后 public 不再默认可写，
            # 非超级用户经常落在 $user schema 里，不显示出来会让人找不着表
            cur.execute("SELECT current_schema(), current_setting('search_path')")
            schema, search_path = cur.fetchone()
            result["schema"], result["search_path"] = schema, search_path

            cur.execute("SELECT extname FROM pg_extension ORDER BY extname")
            result["extensions"] = [row[0] for row in cur.fetchall()]

            cur.execute(
                "SELECT tablename FROM pg_tables"
                " WHERE schemaname = current_schema() ORDER BY tablename"
            )
            result["tables"] = [row[0] for row in cur.fetchall()]
    result["ok"] = True
    return result
