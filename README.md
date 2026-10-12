# mini-agents · 最小多智能体投研系统

> 一个约 600 行的多智能体系统最小实现，核心设计是 **「工作流控制拓扑，Agent 控制内容」**。
> 没有 API Key 也能离线跑通全流程（内置确定性桩模型）；有 Key 时一键切换 DeepSeek。

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

### 一、离线模式（无需 Key，开箱即跑）

```bash
python -m src.runner --ticker 600519 --offline
```

### 二、接入 DeepSeek

```bash
cp .env.example .env          # Windows: copy .env.example .env
# 在 .env 里填入 DEEPSEEK_API_KEY
python -m src.runner --ticker 600519
```

`.env` 关键项：

```
DEEPSEEK_API_KEY=sk-xxxxxxxx
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1
DATA_PROVIDER=mock          # 换成 akshare 可拉真实 A 股数据
MAX_DEBATE_ROUNDS=2
```

### 三、常用参数

```bash
python -m src.runner --ticker 000001 --rounds 0     # 不做辩论，直接给结论
python -m src.runner --ticker 000001 --rounds 3     # 辩论 3 轮
python -m src.runner --ticker 600519 --json         # 输出完整状态（便于二次处理）
python -m src.runner --ticker 600519 --provider akshare   # 用真实数据源
```

## 目录结构

```
mini-agents/
├── src/
│   ├── graph.py          # ★ 从这里读起：整张图的形状
│   ├── state.py          # 全局状态 + reducer（数据总线）
│   ├── llm.py            # LLM 工厂（DeepSeek / 离线桩模型）
│   ├── tools.py          # 工具定义
│   ├── prompts.py        # 提示词模板（辩论 / 经理 + 模板函数）
│   ├── offline_llm.py    # 离线桩模型（保证无 Key 也能跑）
│   ├── resilience.py     # 韧性层：重试 + 降级护栏
│   ├── runner.py         # 命令行入口
│   └── agents/
│       ├── specs.py      # ★ 分析师注册表：一个角色一条记录，全部设定在此
│       ├── base.py       # LLM 调用的原子操作（不做循环）
│       ├── analysts.py   # 分析师节点实现（LLM / 工具 / 报告 / 路由）
│       ├── researchers.py# 多头 / 空头
│       └── manager.py    # 研究经理
├── data/
│   ├── provider.py       # 数据源抽象（mock / akshare）
│   └── fixtures/         # 离线样本数据
├── eval/
│   ├── cases.json        # 评测股票池
│   └── run_eval.py       # 评测 / 消融脚本
├── scripts/
│   ├── selfcheck.py      # 零依赖自检（不需要 pytest）
│   └── inspect_messages.py  # 消息级观测：逐条打印工具调用
└── docs/
    └── architecture.md   # 架构详解与扩展练习
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
python -m src.runner --ticker 600519 --offline      # 节点级轨迹
python scripts/inspect_messages.py --ticker 600519  # 消息级细节
```

### 加一个分析师要改哪里？

只有一处：在 [src/agents/specs.py](src/agents/specs.py) 的 `ANALYSTS` 里加一条记录。
（外加 `state.py` 里补 3 个字段——这是 LangGraph 的静态约束，躲不掉。）

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
python -m src.runner --ticker 600519 --offline
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

## 验证

### 自检：49/49 通过

```bash
python scripts/selfcheck.py
```

覆盖：配置降级、数据可复现、工具返回、端到端跑通、辩论计数严格等于「轮数 × 2」、
并行扇入、不同标的数据不串味、工具循环每一步可见且顺序正确、工具轮数正确落库，
以及**故障注入**下的重试 / 降级 / 不阻断 / 不死循环。

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
