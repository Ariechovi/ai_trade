# mini-agents 项目进度

> **这是项目的单一进度来源。**
> 每次改动后：① 在「变更记录」追加一条（最新在上）；② 更新「当前状态」里的完成度。
> 最后更新：2026-10-17

---

## 一、项目是什么

一个 A 股多智能体投研系统的最小完备实现：

```
三位分析师并行 → 多空辩论 → 研究经理综合
```

核心设计：**工作流控拓扑，Agent 控内容**。

- 谁先谁后、辩论几轮、工具循环怎么转 —— 由 LangGraph 的图和边决定
- 每个节点内部说什么、调哪个工具 —— 由 LLM 自主决定

定位：既作为多智能体架构的学习载体，也是能写进简历的工程项目。

---

## 二、当前状态

### 2.1 能力矩阵

| 维度 | 状态 | 说明 |
|---|---|---|
| 编排 | ✅ 完整 | 3 分析师并行 → 工具循环 → 扇入屏障 → N 轮辩论 → 经理 |
| Agent | ✅ 完整 | 3 分析师 + 多/空研究员 + 经理 |
| 工具 | ✅ 18 个 | 技术面 6 / 基本面 8 / 消息面 4，全部是真实计算 |
| 提示词 | ✅ 契约化 | 分析师五段式职责契约；工具四段式描述 |
| 数据 | ✅ 真实 | akshare（新浪→东财互备）+ mock；25 个财务字段；pandas 指标 |
| 容错 | ✅ 可用 | 重试 + 降级 + `_node_errors`；节点边界取消 |
| 观测 | ✅ 完整 | trace / 节点耗时 / LLM token / 降级记录 / SSE 进度 |
| API | ✅ 完整 | FastAPI：异步任务 + 同步执行 + SSE + 任务列表 + 取消 |
| 存储 | ✅ PostgreSQL | 5 张业务表 + `schema_migrations`；连接池；原子事件序号 |
| 测试 | ✅ 70 用例 | 单元 + API；API 用例跑真实 PG，连不上自动 skip |
| 记忆 | ❌ 未开始 | 数据已落库，但还没有"召回 + 注入" |
| 评测 | ⚠️ 只有过程指标 | 耗时 / 字数 / 稳定性，**没有质量指标** |
| 并发 | ⚠️ 单进程 | 有 `max_concurrent_runs` 线程池，但还没有多 worker + 队列 |

### 2.2 技术栈

| 层 | 选型 |
|---|---|
| 编排 | LangGraph（StateGraph / 条件边 / 自定义 reducer） |
| LLM | DeepSeek（OpenAI 兼容）+ 离线确定性桩模型 |
| 数据 | akshare、pandas（指标手写）、文件缓存 |
| 后端 | FastAPI + uvicorn + SSE |
| 存储 | PostgreSQL 18.3（+ pg_trgm；vector 已装待用） |
| 前端 | Streamlit |
| 测试 | pytest（14 个文件 / 70 个用例） |

### 2.3 规模

- 后端 69 个 Python 文件 / 约 5200 行
- 3 个数据库迁移
- 70 个测试用例

---

## 三、架构一览

### 3.1 一次分析的流程

```
POST /api/analysis  →  202 + run_id  →  ThreadPool 里跑
        │
        ▼
   LangGraph StateGraph
        │
   ┌────┴──── START ────────────────┐
   ▼                ▼                ▼
技术面分析师     基本面分析师      消息面分析师
   │                │                │
   │  每个分析师的工具循环（图上的回边）：              │
   │    <role> ──有 tool_calls──► tools_<role> ──┐     │
   │       ▲                                    │     │
   │       └──────── 回边（循环在这里）──────────┘     │
   │    <role> ──无 tool_calls──► msg_clear_<role> ──► merge
   └──────────────────────────────────────────────────┘
                        ▼
           bull ⇄ bear（N 轮辩论 + 计数器）
                        ▼
                     manager
                        ▼
                       END
        │
        ▼
   run_events（先落库再推送）→ SSE 断点续传
```

### 3.2 目录

```
mini-agents/
├── backend/
│   ├── main.py            FastAPI 入口（组装，不含业务）
│   ├── settings.py        配置（.env）
│   ├── models.py          请求/响应模型
│   ├── api/               analysis / tasks / health
│   ├── services/          graph / runner / task_runner / store / pg_store
│   ├── common/            deps / sse / resilience / observability / pg / state
│   ├── agents/            specs / base / analysts / researchers / manager
│   ├── data/              sources / indicators / cache / service
│   ├── prompts/           analysts / debaters / manager / templates
│   ├── tools/             technical / fundamental / news
│   ├── migrations/        0001 / 0002 / 0003 + runner
│   ├── eval/              轮数消融与稳定性评测
│   └── tests/             单元 + API
├── ui/streamlit_app.py    可视化界面
└── docs/
    ├── architecture.md    架构详解
    └── PROGRESS.md        ← 本文件
```

---

## 四、变更记录

> 最新在上。每条记录包含：**为什么 / 做了什么 / 验证 / 留下的坑**。

### 2026-10-17 · 建立进度文件 + 更正 README

**为什么**
项目演进得快，README 和实际能力已经脱节；同时需要一份可持续积累的进度记录。

**做了什么**
- 新增 `docs/PROGRESS.md`（本文件）：项目定位 / 能力矩阵 / 架构 / 变更记录 / 技术债 / 下一步
- 更正 `README.md`：
  - 规模 4,000 → 5,200 行
  - 删掉"刻意不做 FastAPI / 数据库 / 前端"的旧说法（已经不成立），
    改为「按真实需求逐步补齐」，并说明**为什么不上 Redis**（单进程不需要跨进程投递）
- README 顶部加上本文件的链接

**约定**
以后每次改动都在本文件追加一条，不再单独询问。

### 2026-10-17 · P1：存储层迁移到 PostgreSQL（**未提交**）

**为什么**
SQLite 是本地单文件 + 单机写锁，无法支撑后续的多 worker 并行分析。
既然目标里有"并行运行股票"，存储必须先换。

**做了什么**
- 新增 `services/store.py`：`StoreProtocol` 接口（12 个方法），业务层只依赖接口
- 新增 `services/pg_store.py`：`PgTaskStore`（连接池 + 全量实现）
- 新增 `common/pg.py` 的 `make_pool()`（连接池工厂，`dict_row`）
- 新增迁移 `0003_runs_result_and_seq.sql`：`runs.result JSONB` + `runs.event_seq`
- **删除** `services/task_store.py`（SQLite 实现，215 行）
- `main.py`：改用 `PgTaskStore`，`create_app(settings, store=...)` 可注入
- `settings.py`：移除 `run_db_path` / `run_store`，新增 `pg_pool_min` / `pg_pool_max`
- 测试：`tests/api/conftest.py` 重写（独立测试库 `ai_trade_test` + 用例隔离 + 连不上就 skip）

**验证**
- `ai_trade` / `ai_trade_test` 均应用 3 个迁移
- **70 个测试全过（0 skip）**，其中 19 个 API 用例跑真实 PG
- 端到端：`POST /sync` → 200，报告落库；`run_events` 54 条，**seq 1..54 连续**

**三个坑（都解决并验证）**
1. **事件序号并发**：`SELECT MAX(seq)+1` → `UPDATE ... RETURNING`（原子）
2. **psycopg3 的 JSONB**：必须 `Jsonb()` 包装
3. **时间语义**：库里统一 UTC（`TIMESTAMPTZ`），出口转 ISO

**留下的坑**
- API 测试 90 秒 / 19 个用例（约 4.7s/个）：每个用例建池 + 跨网络 TRUNCATE
- 改动未提交

### 2026-10-16 · P0：建库建表 + 迁移机制（**未提交**）

**为什么**
为迁移到 PostgreSQL 做准备；先确认能连上、表能建出来。

**做了什么**
- 新增 `migrations/`：`0001_init.sql`（5 张表 + 索引）、`0002_trgm_index.sql`（中文子串索引）
- 新增 `migrations/runner.py`：`status` / `apply`，每个文件一个事务
- 新增 `common/pg.py`：DSN / 连接 / 健康检查
- `settings.py` + `.env`：新增 `PG_*` 配置

**验证**
- 连接成功（PG 18.3，走 SSH 隧道 `127.0.0.1:15432`）
- 5 张表 + 18 个索引；`pg_trgm` 安装成功

**踩过的坑（重要）**
第一次 `apply` 报"应用成功、22 条语句"，但**表一张都没建出来**。
根因是事务边界：`autocommit=False` 下 `_applied()` 的 SELECT 开启了隐式事务，
导致 `conn.transaction()` 退化成 SAVEPOINT，最后没人 COMMIT，`conn.close()` 全部回滚。
**修法**：迁移器用 `autocommit=True`，并**加应用后校验**（`missing_core_tables`）。

> 教训：**"执行成功"和"生效"是两件事**。工具跑完必须校验结果。

### 2026-10-16 · 前后端框架：FastAPI + Streamlit（`cb63f7b`）

**做了什么**
- 目录重构：`src/` → `backend/`，按 `api / services / common / agents / data / tools` 分层
- FastAPI：`POST /api/analysis`（202 + run_id）、`/sync`、`GET /{id}`、`/events`（SSE）、`/cancel`、`/api/tasks`
- 任务中心：SQLite 两张表（状态机 + 事件），启动自愈 `sweep_interrupted`
- 后台线程 + 事件总线：**客户端断开 ≠ 任务停止**
- 并发控制：`max_concurrent_runs` + 线程池
- 进度百分比**算出来**（不是硬编码）
- pytest 测试套件

**关键判断**
- 同步端点写成 `def` 而非 `async def` —— langgraph / akshare 都是阻塞的，
  写成 async 会卡住事件循环，症状是"服务随机假死"
- `DataService` 每个请求新建 —— 内部有可变 `last_source`，共享会让报告标错数据源

### 2026-10-15 · 可观测性 + Streamlit（`6a615fa` / `75492c3`）

**做了什么**
- `common/observability.py`：`TimedNode`（节点耗时）、`llm_call_record`（token）、`emit_event`（实时进度）
- `state` 新增 `node_timings` / `llm_calls`（`operator.add` 累加）
- `ui/streamlit_app.py`：输入 → 实时进度 → 6 个 Tab（结论/三份报告/辩论/运行指标）

**修过的问题**
- 进度消息重复 26 遍：`container.markdown()` 是**追加**不是替换 → 改用 `st.empty()` 占位符
- akshare "卡住"：无 socket 超时（无限挂起）+ 同一上游重复下载（缺 memo）+ 无进度反馈

### 2026-10-13 · 提示词与工具契约 + 工具层扩充（`71ecc54` / `75492c3`）

**做了什么**
- 分析师提示词：3 句话 → **五段式职责契约**（角色职责 / 任务 / 要求 / 正反例 / 输出结构）
- 工具描述：1 句话 → **四段契约**（何时使用 / 何时不要用 / 返回·不包含 / 局限）
- 工具 14 → 18 个：新增 `detect_kline_patterns`、`analyze_dupont`、`check_cashflow_quality`、`analyze_solvency`

**口径规则来自实测**
- 包钢案例：净利率 0.05% 与「归母净利 ÷ 营收 ≈ 1.0%」不一致 → 写进契约"必须交叉验算"
- 茅台案例：应收账款周转率 57047 倍（该科目几乎不存在）→ 写进契约"异常值必须标注"
- 多头连续两轮重复同一论点 → 写进辩论契约"不要重复上一轮"

### 2026-10-12 · 四个连续阶段（`2f012a3` / `45d091a` / `a071aa3` / `31547a1`）

| 阶段 | 做了什么 | 关键点 |
|---|---|---|
| 最小实现 | 3 分析师并行 → merge → 多空辩论 → 经理 | `state` + `graph` + `runner` |
| 图级工具循环 | 把工具循环从节点内 `while` 搬到图上（回边） | 每一步可观测 / 可中断 / 可降级 |
| 可读性重构 | 散落配置 → 一张 `AnalystSpec` 表；闭包 → 类 | 读一个分析师 = 读一条记录 |
| 节点级容错 | `GuardedNode` 重试 + 各节点 `degrade()` + `_node_errors` | 异常永不穿透到图外 |

**同期修掉的真实 bug**
- **扇入屏障失效**：`merge` 默认"任一入边到达就执行"，三个分析师循环长度不同时被触发多次，
  导致辩论在缺料时进行。修法：`add_node("merge", ..., defer=True)`。
  根因是**等长测试掩盖了不等长的现实**。

---

## 五、已知问题与技术债

| # | 问题 | 影响 | 备注 |
|---|---|---|---|
| 1 | 改动未提交 | 无法回滚 | P0+P1 全在工作区 |
| 2 | `run_nodes` / `run_llm_calls` / `run_tool_calls` **还没有写入** | 表是空的 | 需要 P2/P3 埋点 |
| 3 | 无记忆层 | 每次分析都是白纸 | 数据已落库，缺"召回+注入" |
| 4 | 评测只有过程指标 | 无法衡量"报告好不好" | 需结构合规 + 数字溯源 + judge |
| 5 | 辩论无收敛判断 | 即使重复也会跑满轮数 | 计划用"结构化新论点"规则判断 |
| 6 | 单进程 | 无法水平扩展 | 需多 worker + 队列 |
| 7 | API 测试慢（4.7s/用例） | 反馈慢 | 连接池 session 级复用 / 事务回滚隔离 |
| 8 | `vector` 扩展已装未用 | — | 记忆层第三档再用 |

---

## 六、下一步

| 阶段 | 目标 | 状态 |
|---|---|---|
| **P2** | `run_nodes` 落库 —— **每个节点的报告** | 待做 |
| P3 | `run_llm_calls` / `run_tool_calls` 埋点 —— 可观测性完整 | 待做 |
| P4 | 查询接口（按 ticker 查历史 / 按内容搜）—— 记忆层第 1 档 | 待做 |
| P5 | 多 worker + `SKIP LOCKED` 队列 —— 并行运行股票 | 待做 |
| — | 辩论收敛判断 / 评测质量 | 独立可并行推进 |

---

## 七、维护约定

1. **每次改动后更新本文件**：在「变更记录」顶部追加一条（含为什么 / 做了什么 / 验证 / 坑）。
2. **同步「当前状态」**：能力矩阵里从 ⚠️/❌ 变成 ✅ 时更新。
3. **记录失败与教训**，不只记成功 —— "踩过的坑"往往比"做了什么"更有价值。
4. 日期用本地日期（Asia/Shanghai）。
