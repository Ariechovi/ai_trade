# mini-agents · 最小多智能体投研系统

> 一个约 4,000 行的多智能体系统最小实现（核心编排约 600 行），核心设计是 **「工作流控制拓扑，Agent 控制内容」**。
> 没有 API Key 也能离线跑通全流程（内置确定性桩模型）；有 Key 时一键切换 DeepSeek。
> 结构上分成 `backend/`（Python 应用）与 `ui/`（前端），两者之间只有 HTTP。

## 这是什么

```
3 个分析师并行  →  合并  →  多空辩论（N 轮）  →  研究经理综合  →  最终结论
```

它刻意只保留了多智能体系统的「内核」，把产品化的外壳（数据库、队列、Web 服务）全部砍掉：

| 维度 | 保留 | 刻意不做 |
|---|---|---|
| 编排 | LangGraph 状态图 + 条件边 | —— |
| 智能体 | ReAct 工具调用循环 | 20+ 个冗余 Agent |
| 数据 | 数据源抽象（mock / akshare） | MongoDB / Redis |
| 接口 | 命令行 + JSON 输出 | FastAPI / 前端 |

## 架构

```
                     ┌──────────────┐
               START │              │
                     └──────┬───────┘
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
      技术面分析师     基本面分析师      消息面分析师     ← 各带 1 个工具，内部是 ReAct 循环
            │               │               │
            └───────────────┼───────────────┘
                            ▼
                        [ merge ]                     ← 扇入屏障：三个都完成才继续
                            ▼
                    ┌───► [ bull ] ───┐
                    │                 ▼
                    │             [ bear ]            ← 条件边 + 计数器控制轮数
                    └──── 未达上限 ───┘
                            │ 达到上限
                            ▼
                       [ manager ]                    ← 综合三份报告 + 辩论，出最终结论
                            ▼
                           END
```

## 快速开始

### 零、安装（一次即可）

```bash
pip install -r backend/requirements.txt
```

下面所有命令都在 `backend/` 目录里执行。

### 一、离线模式（无需 Key，开箱即跑）

```bash
cd backend
python cli.py --ticker 600519 --offline
```

### 二、接入 DeepSeek

```bash
cp .env.example .env          # Windows: copy .env.example .env
# 在 .env 里填入 DEEPSEEK_API_KEY
python cli.py --ticker 600519
```

`.env` 关键项：

```
DEEPSEEK_API_KEY=sk-xxxxxxxx
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1
DATA_PROVIDER=auto          # auto = akshare 真实数据；mock = 离线模拟数据
MAX_DEBATE_ROUNDS=2
```

### 三、常用参数

```bash
python cli.py --ticker 000001 --rounds 0     # 不做辩论，直接给结论
python cli.py --ticker 000001 --rounds 3     # 辩论 3 轮
python cli.py --ticker 600519 --json         # 输出完整状态（便于二次处理）
python cli.py --ticker 600519 --provider akshare   # 用真实数据源
```

## 目录结构

```
mini-agents/
├── backend/                  # ← Python 应用：所有命令都在这里执行
│   ├── api/                  # 路由层：__init__ 聚合 api_router + 每域一个文件
│   ├── common/               # 横切：deps / exception_handler / sse / state / observability / resilience
│   ├── services/             # 任务编排（task_store / task_runner）+ 执行通路（runner / graph / llm）
│   ├── agents/               # 角色：specs（注册表）/ base / analysts / researchers / manager
│   ├── prompts/              # 提示词契约：analysts / debaters / manager / templates
│   ├── tools/                # 工具层：technical / fundamental / news
│   ├── data/                 # 数据层：sources / indicators / cache / service / fixtures
│   ├── tests/                # pytest（conftest 统一断网，tests/api 覆盖 HTTP 层）
│   ├── scripts/  eval/       # 开发脚本与评测（用 python -m scripts.x 运行）
│   ├── main.py               # HTTP 入口：uvicorn main:app
│   ├── cli.py                # 命令行入口
│   ├── models.py             # 对外数据模型
│   ├── settings.py           # 配置（读仓库根的 .env）
│   ├── pytest.ini
│   └── requirements.txt
├── ui/streamlit_app.py       # 前端：只通过 HTTP 调后端，不 import 后端代码
├── docs/architecture.md      # 架构详解与扩展练习
└── .github/workflows/ci.yml  # 四档 Python 的 CI
```

## 读代码从哪开始

**别从 `base.py` 开始读**——那是零件，读它等于在读螺丝而不是读机器。按「先形状、后细节」：

```
graph.py             ① 整张图的形状：谁连谁、循环在哪
   ↓
agents/specs.py      ② 角色设定：一个分析师的全部设定，就一条记录
   ↓
agents/analysts.py   ③ 节点实现：LLM / 工具 / 报告 / 路由，四个类
   ↓
state.py             ④ 数据怎么在节点间流动（reducer）
   ↓
agents/base.py       ⑤ 最后看原子操作：到底怎么调 LLM、怎么执行工具
```

配合**运行时轨迹**读，比静态阅读快得多：

```bash
python cli.py --ticker 600519 --offline               # 节点级轨迹
python -m scripts.inspect_messages --ticker 600519    # 消息级细节
```

### 加一个分析师要改哪里？

只有一处：在 [backend/agents/specs.py](backend/agents/specs.py) 的 `ANALYSTS` 里加一条记录。
（外加 `common/state.py` 里补 3 个字段——这是 LangGraph 的静态约束，躲不掉。）

## 三个核心设计

**1. 状态就是数据总线（`src/state.py`）**

字段上标注的 reducer 决定并发/循环时如何合并。不标注 = 后写覆盖；`operator.add` = 追加/累加。
辩论之所以能一轮轮转下去、历史能一段段累积，靠的就是这两个累加器。

**2. 拓扑由代码写死，内容由 LLM 决定（`src/graph.py`）**

「谁先谁后、辩论几轮」是确定性的，与 LLM 无关；LLM 只能决定「节点内部调哪些工具、说什么内容」。
这是"可控"与"智能"的分工线。

**3. Agent 的本质就是一个 30 行的循环（`src/agents/base.py`）**

```
调 LLM → 要调工具？→ 执行工具、结果喂回 → 再调 LLM → …… → 不调工具就收口
```

`max_iterations` 是防死循环的保险丝。读懂了这 30 行，就读懂了所有 Agent 框架的内核。

## 工具循环：每一步都在图上（本项目最值得看的一处）

「LLM ↔ 工具 反复来回」这个循环**不在节点内部，而是由图上的节点和回边实现**：

```
<role> ──┬─(有 tool_calls 且未超上限)─> tools_<role> ─┐
         │                                           │
         └─(无 tool_calls / 超上限)──> msg_clear_<role> ─> merge
                          ▲                          │
                          └──── 回边（循环在这里）─────┘
```

跑一次就能看到每一步：

```bash
python cli.py --ticker 600519 --offline
```

```
llm:fundamental, llm:news, llm:technical,
tools:fundamental(1), tools:news(1), tools:technical(1),
llm:fundamental, llm:news, llm:technical,
report:fundamental, report:news, report:technical,
merge, debate:bull, debate:bear, debate:bull, debate:bear, manager
```

注意 `llm:technical` 出现了两次——第一次是模型要求调工具，第二次是拿到工具结果后出结论。
**循环就是那条 `tools_<role> → <role>` 的回边**，全程没有 `while`。

**为什么一定要这么做？** 只有图上的节点才能被观测、中断、审批、单独重试和回放；
藏在节点内部的 `while` 里，这些统统做不到。

### 与 V3 的代码对照

| 本项目 | 对应 TradingAgents-CN 的哪段实现 |
|---|---|
| `make_analyst_llm_node` | `core/workflow/builder.py` → `_create_analyst_wrapper` |
| `make_analyst_tools_node` | `core/workflow/builder.py` → `_create_tool_node_wrapper` |
| `make_analyst_report_node` | `core/workflow/builder.py` → `_create_msg_clear_node` |
| `make_analyst_router` | `core/workflow/builder.py` → `_create_analyst_condition_func` |
| `technical_messages` / `technical_tool_calls` | `_market_messages` / `market_tool_call_count` |

读 V3 时你会遇到那 4 个「怪东西」——为什么要给每个分析师独立的 `_xxx_messages`、
为什么要 `msg_clear` 节点、为什么工具节点还要包一层 wrapper——答案都在本项目的
`src/agents/analysts.py` 里。

## 节点级容错：失败不毁全局

LLM 会限流、会超时、会挂；数据源会抖。一个不做容错的系统，任何一处抖动都会让整轮分析白跑。

**分工**（这是关键的设计判断）：

| 关注点 | 放在哪 | 为什么 |
|---|---|---|
| **重试** | `resilience.GuardedNode` 包装器 | 横切关注点，每个 I/O 节点都要 |
| **降级** | 各节点自己的 `degrade()` | 业务语义，每个节点降级方式不同 |

```python
# src/graph.py —— 只给会做 I/O 的节点套护栏，纯函数节点不套
graph.add_node(spec.role, guarded(AnalystLLMNode(...)))
graph.add_node(f"msg_clear_{spec.role}", AnalystReportNode(spec))   # 纯函数，不套
```

**执行流程**：

```
调用节点 → 失败且可重试且还有预算 → 指数退避后重试
         → 不可重试 / 重试用尽   → node.degrade() 产出降级结果
```

也就是说：**异常永远不会穿透到图外**。最坏情况是一个降级结果，工作流其余部分照常跑完。

**三个必须记住的细节**（也是自检里专门验证的）：

1. **降级也要推进计数器**。辩论节点失败时如果不推进 `debate_count`，路由器会一直把控制权交回它，形成死循环——图里的死循环比函数里的死循环更难察觉。
2. **不可重试的错误立即降级**。参数错误、鉴权失败重试多少次都没用。
3. **降级是局部的**。技术面挂了，基本面、消息面和最终结论照常产出，并把故障记进 `_node_errors`。

实测一次（让技术面分析师彻底失败）：

```
[重试] llm:technical 第 1 次失败(connection timeout (injected))，0.00s 后重试
[重试] llm:technical 第 2 次失败(connection timeout (injected))，0.00s 后重试
[降级] llm:technical 重试无果，转入降级路径

技术面报告 -> [技术面分析师调用失败] 本段分析不可用，其余节点的结论不受影响。
基本面报告 -> 正常
最终结论   -> 正常产出
_node_errors -> {"technical": {"stage": "llm", "type": "RuntimeError", "message": "..."}}
```

与 V3 的对照：`GuardedNode` ≈ `core/utils/retry.py` + `base.py:is_retryable_llm_error()`；
`degrade()` ≈ `builder.py:2083` 的 degraded 分支 + `_node_errors`；
`quality_gate.py` 则是更进一步——连「报告质量不合格」也算一种失败。

## 数据层：四层分离

这一层照搬 TradingAgents-CN 的 `tradingagents/dataflows/` 骨架：

| 本项目的文件 | 职责 | V3 的对应物 |
|---|---|---|
| `data/sources/` | 一个来源一个文件，统一实现 `BaseDataSource` | `dataflows/providers/` |
| `data/indicators.py` | 指标计算（纯函数，给价格序列就算） | `dataflows/technical/` |
| `data/cache.py` | 文件缓存 + TTL | `dataflows/cache/file_cache.py` |
| `data/service.py` | 用哪个源、失败了怎么办、要不要缓存 | `dataflows/data_source_manager.py` |
| `src/tools/` | 暴露什么给 LLM（按分析师域分文件） | `core/tools/implementations/` |

**指标是真算的，不是编的**（pandas 手写公式，每一行都看得出来源）：

```
[技术面· 离线模拟数据] 600519 最近 60 个交易日
- 最新收盘 1718.28，当日涨跌 -1.37%
- 均线：MA5=1721.92，MA10=1711.5，MA20=1719.75，MA60=1594.31
- 均线形态：多头排列（MA5>MA20）
- RSI(14)=53.4（中性区）
- MACD：DIF=22.921，DEA=30.077，DIF 在 DEA 下方（空头）
- 布林带：上轨 1778.1 / 下轨 1661.41，当前位于带内 48.7% 位置
- 波动率：ATR(14) 占价格 2.3%
- 量能：最新成交量为 20 日均量的 0.96 倍
```

**两条刻意的规则**：

1. **模拟数据必须显眼标注**——用 mock 时输出带 `· 离线模拟数据`，绝不让人把假数据当成真结论。
2. **真实数据失败时不偷偷用模拟数据顶替**——`with_mock_fallback` 默认关闭，宁可报错。

**接口互备**（真实环境实测得出）：akshare 的东财行情域名在部分网络下不可达，
所以行情走「新浪优先 → 东财兜底」；财务与新闻走东财。公司名拿不到时**只降级公司名**，
财务指标照常输出——这叫「部分降级」，比整体失败好。

真实数据实测（`--provider akshare`，2026-10-12 跑通）：

```
[技术面] 600519 最近 60 个交易日
- 最新收盘 1288.0，当日涨跌 1.98%
- 均线：MA5=1260.2，MA10=1253.95，MA20=1267.44，MA60=1292.46
- RSI(14)=56.2（中性区）
- MACD：DIF=-8.8，DEA=-11.713，DIF 在 DEA 上方（多头）

[基本面] 报告期 20260630，ROE=16.75%，营业总收入=922.78 亿，归母净利润=445.17 亿，
         资产负债率=15.19%，毛利率=89.56%，净利率=50.75%
```

切换数据源只需改一个环境变量：`DATA_PROVIDER=auto`（akshare 真实数据）或 `mock`（离线模拟）。

## 提示词层：把职责写成契约

每个角色的 system prompt 不再是三句话，而是**五段式职责契约**
（写法对照 V3 的 `app/services/system_prompt_seed_service.py`）：

| 段落 | 作用 |
|---|---|
| 角色与职责 | 为研究流程提供**可复核的**证据（而不是"你是一个分析师"） |
| 你的任务 | 编号列表，含工具使用要求 |
| 要求 | 硬约束：证据来源、口径、异常值 |
| 可以写 / 不得写 | 正反例——约束力最强的形式 |
| 输出结构 | 固定小节，便于下游综合与评测 |

工具描述同样从一句话扩成**四段契约**：`何时使用` / `何时不要用（改用哪个）` /
`返回·不包含` / `局限`。

> ⚠️ LangChain 只把 `docstring` 作为工具描述传给模型，所以这些必须写在 docstring 里。
> V3 把它们放在自己的 `ToolMetadata` 里，是因为它还有 UI 和 Skill 工坊要消费——
> 对我们只有一个消费者（模型），写进 docstring 就够了。

### 口径规则不是凭空写的，是实测踩出来的

前两轮真实运行暴露的两个坑，现在直接写进了基本面契约：

| 踩过的坑 | 写进契约的规则 |
|---|---|
| 包钢：净利率 0.05% 与「归母净利 2.79 亿 ÷ 营收 280.34 亿 ≈ 1.0%」不一致 | 「**交叉验算**：两者明显不一致时，说明口径不同，必须指出」 |
| 茅台：应收账款周转率 57047 倍（该公司几乎没有应收账款） | 「**异常值必须标注**——说明该科目不适用，不得当成优势或风险」 |

多空契约里也加了「**不要重复上一轮论点**」——因为真实运行里多头连续两轮
只重复同一句话，被空头当场点破「喊了三轮，骨牌至今没倒」。

## 可视化界面（Streamlit）

```powershell
# 先起后端（另一个终端）：cd backend; uvicorn main:app --port 8100
streamlit run ui/streamlit_app.py
```

页面分三块：

| 区域 | 内容 |
|---|---|
| 左侧输入 | 股票代码 / 日期 / 辩论轮数 / 数据源 / 强制离线 |
| 运行过程 | 实时显示每个节点完成情况（`stream_mode=["updates","values"]`） |
| 结果 | 6 个 Tab：最终结论 / 技术面 / 基本面 / 消息面 / 辩论记录 / **运行指标** |

「运行指标」里有：总耗时、LLM 调用次数、token 合计、**按角色汇总的 token 与耗时**、
每个节点的耗时表、降级记录、节点执行轨迹。

> 页面本身**不做任何分析逻辑**，只通过 HTTP 调后端的 `/api/analysis` 与事件流——
> 它和后端是两个进程，中间只有 HTTP（与将来的 Vue 前端角色完全一致）。

### 观测层是怎么来的

| 采什么 | 怎么采 | 存在哪 |
|---|---|---|
| 节点耗时 | `observe.TimedNode` 包一层（放在 `GuardedNode` 外面，所以耗时含重试等待） | `state["node_timings"]` |
| LLM token | 每次调用后从响应的 `usage_metadata` 提取 | `state["llm_calls"]` |

两者都是 `operator.add` 累加字段，`src/runner.py:summarize()` 负责聚合——
CLI 与页面共用这一个聚合函数，避免统计口径不一致。

⚠️ **离线桩模型不返回 token**，离线跑出来是 0 并会明确提示；换成真实模型即可看到分布。

### 遇到「卡住」怎么办

akshare 要联网，慢是正常的。项目里做了三层处理：

| 措施 | 解决什么 |
|---|---|
| **进程内 memo** | 同一 ticker 的行情 / 财务原始数据只拉一次（新浪接口返回全量 6000+ 行，不同 `days` 会重复下载） |
| **socket 超时** | `AKSHARE_TIMEOUT`（默认 30 秒）——不设上限时，网络抖动会让 akshare **无限挂起** |
| **实时进度事件** | 节点内部主动推「模型思考中 / 执行工具 X」，不用等节点完成才知道它在动 |

实测（600519，本机）：冷缓存全流程 **8.2 秒**，热缓存 **3.9 秒**。

如果还是慢，用下面的命令看是哪个工具在等：

```bash
python -m scripts.inspect_messages --ticker 600519
```

### 取不到数据怎么办

两条最常见的坑，都跟代码本身无关：

**一、系统代理拦了国内数据源。** Windows 上配了系统代理（Clash / V2Ray 之类）时，
Python 的 `requests` 会读到它 —— 而且**即使 `HTTP_PROXY` 环境变量是空的也会读到**，
因为 urllib 在 Windows 上会去读注册表。于是对东财/新浪的请求也发给代理，表现是
`ProxyError`，或者拿到空响应后的 `JSONDecodeError: No value to decode`。

本项目已在 `data/sources/akshare_source.py` 里默认把国内行情域名（东财 / 新浪 / 交易所）
加进 `NO_PROXY` 直连，一般不需要额外配置。V3 的 AGENTS.md 里也是同一条规矩。

**二、股票代码本身取不到数据。** 退市股（例如 600001 邯郸钢铁）、指数、B 股
用这套行情接口都取不到，表现是**两个源同时失败**。换个正常代码（600519 / 000001）
先验证链路是否通。

## HTTP API（可选）

```bash
# 依赖已包含在 requirements.txt 里，无需额外安装
cd backend
uvicorn main:app --port 8100
# 打开 http://127.0.0.1:8100/docs
```

```bash
curl -X POST http://127.0.0.1:8100/api/analysis/sync \
  -H "Content-Type: application/json" \
  -d '{"ticker":"600519","provider":"mock","offline":true}'
```

| 端点 | 说明 |
|---|---|
| `POST /api/analysis` | 创建任务，返回 **202** + `run_id`（任务在后台线程跑） |
| `POST /api/analysis/sync` | 同步跑完直接返回结果（给 curl / 脚本用） |
| `GET /api/analysis/{run_id}` | 任务详情（含结果与降级节点） |
| `GET /api/analysis/{run_id}/events` | **SSE 事件流**，支持 `Last-Event-ID` 续传 |
| `POST /api/analysis/{run_id}/cancel` | 请求取消（在节点边界生效） |
| `GET /api/tasks` | 任务列表 / 过滤 / 分页 |

三条设计约定：

1. **API 是薄壳** —— 所有分析都走 `runner.stream_case`，CLI / Streamlit / HTTP 是同一条通路；
2. **降级不是 HTTP 错误** —— 某个节点降级时仍是 200，信息在 `degraded` 字段里；
   只有「所有数据源都失败」才返回 503；
3. **进度是算出来的** —— `已完成节点 / 预估节点数`，不是手写的魔数。

端口用 8100 而不是 8000，是为了和 TradingAgents-CN 那套（8000 / 3000）同时跑时不打架。

## 验证

### 测试：69 个用例，全程离线

```bash
cd backend
pytest -q                        # 全部用例（不需要 Key、不联网）
pytest -m network                # 显式跑需要联网的用例
python -m scripts.selfcheck      # 等价于 pytest -m "not network"
```

`backend/tests/conftest.py` 里有一个 autouse fixture，**默认切断对外网络**（DNS 解析、
create_connection、非环回的 connect）。任何偷偷联网的用例都会立刻红，而不是等到
某台没有代理的机器上才失败 —— 这是「离线可复现」这个卖点的自动化证明。

覆盖：配置降级、数据可复现、工具返回、端到端跑通、辩论计数严格等于「轮数 × 2」、
并行扇入（等长 + **不等长**两个分支）、不同标的数据不串味、工具循环每一步可见且顺序正确、
工具轮数正确落库，**故障注入**下的重试 / 降级 / 不阻断 / 不死循环，数据层的
**指标公式正确性**（用已知序列反推 RSI、MA）、缓存命中与过期、多源降级、模拟数据标注，
以及 HTTP 层的路由注册、202 语义、SSE 顺序与续传、跨请求隔离、取消。

## 工具层：每个分析师 4~5 个真工具

对应 TradingAgents-CN 的 `core/tools/implementations/`（V3 给每个分析师配 3~8 个工具，
技术域还细分为 indicators / candlestick_patterns / crossover_signals /
support_resistance / volume_analysis 等）。本项目按同样的思路配：

| 分析师 | 工具（共 18 个） |
|---|---|
| 技术面（6） | 价格概览、按需算指标、交叉信号检测、支撑压力位、量能分析、K线形态 |
| 基本面（8） | 财务报表、成长性、健康度评分、估值估算、同业对比、**杜邦分析、利润含金量、偿债能力** |
| 消息面（4） | 新闻列表、关键词检索、情绪打分（词典规则）、市场环境 |

**每个工具都是一次真实计算，不是换个名字再包一层。** 例如：

- `detect_crossover_signals` 真的去扫 MA5/MA20、MA10/MA60、MACD 的交叉点，并报出距今几个交易日
- `estimate_valuation` 用「报告期 EPS × 年化系数」估 PE，并**把假设写在输出里**
- `score_news_sentiment` 用金融正负面词典做规则打分（便宜、可解释、可测试）

**工具变多之后，图上的工具循环才真正跑起来**：一次分析里
`llm → tools → llm → tools → llm → tools → llm → report`，
每个分析师往返 3 轮（受 `MAX_TOOL_ITERATIONS` 限制）。

这也是「为什么需要多个分析师」这个问题的答案：**他们的工具集不同**。

### 一个只有真实 LLM 才会暴露的 bug

真实跑一次就发现轨迹不对：`merge` 执行了 **2 次**、`manager` 执行了 **2 次**，
而且 `debate:bull` 出现在 `report:fundamental` 之前——**辩论是在缺料的情况下进行的**。

根因：LangGraph 的 `add_node` 默认 `defer=False`，含义是「**任一**入边到达就执行」。
三个分析师的循环长度往往不同（真实模型一次可能返回多个 `tool_calls`），
于是三条入边分别落在三个超级步 → `merge` 被触发三次。

```python
graph.add_node("merge", _merge_node, defer=True)   # 等齐所有入边再执行
```

**为什么离线自检没发现它**：离线桩让三个分析师的循环长度完全相同，
三条入边落在同一个超级步，`merge` 恰好只触发一次。
**"等长测试"掩盖了"不等长现实"**——现在自检里专门加了不等长分支的回归用例。

### 辩论轮数消融

```bash
python eval/run_eval.py --ablation --repeat 3      # 离线（确定性桩模型）
python eval/run_eval.py --ablation --repeat 3 --online   # 用真实 DeepSeek
```

离线模式实测（8 只股票 × 4 档轮数 × 3 次重复）：

| 轮数 | 平均耗时(s) | 平均报告字数 | 平均辩论发言 | 结论稳定率 |
|---|---|---|---|---|
| 0 | 0.023 | 496.2 | 0.0 | 100.0% |
| 1 | 0.022 | 496.2 | 2.0 | 100.0% |
| 2 | 0.029 | 496.2 | 4.0 | 100.0% |
| 3 | 0.023 | 496.2 | 6.0 | 100.0% |

> 说明：离线的桩模型是确定性的，所以三个指标天然是常数——它的作用是**证明流程正确**，
> 而不是产生有意义的结论差异。**真正用来写简历的数字，请用 `--online` 跑一遍真实模型**：
> 那时你会看到耗时、成本的上升，以及"辩论轮数越多结论越稳定"的曲线。

明细数据见 `eval/results.json`。

## 怎么用它写简历

> **多智能体投研系统（独立实现）**
> 用 LangGraph 实现「并行分析师 → 多空辩论 → 经理综合」的多智能体协作流程；
> 自研 ReAct 工具调用循环与基于 reducer 的状态管理；设计零依赖离线桩模型，
> 使项目在无 API Key 环境下可完整复现与自动化验证。
> 通过辩论轮数消融实验，量化了结论稳定性与推理成本之间的权衡。
> 技术栈：Python / LangGraph / LangChain / 多智能体协作（debate）/ ReAct / DeepSeek

面试时用这一句开场：**"它不是纯工作流也不是纯 Agent——工作流控制拓扑，Agent 控制内容。"**

## 局限与下一步

- 目前用字符数近似成本；接入真实模型后建议统计 `usage_metadata` 里的 token 数。
- 数据源只做了 3 个工具，可继续加（资金流向、龙虎榜、财报明细）。
- 桩模型是确定性的，无法验证"结论质量"；真实评测需要人工标注或 LLM-as-judge。
- 想进一步挑战自己：加一个「反思（reflection）」节点，让经理复盘辩论并沉淀经验。
