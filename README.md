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
│   ├── state.py          # 全局状态 + reducer（数据总线）
│   ├── graph.py          # 图装配：节点与边（拓扑）
│   ├── llm.py            # LLM 工厂（DeepSeek / 离线桩模型）
│   ├── tools.py          # 工具定义 + 工具注册表
│   ├── prompts.py        # 提示词集中管理
│   ├── offline_llm.py    # 离线桩模型（保证无 Key 也能跑）
│   ├── runner.py         # 命令行入口
│   └── agents/
│       ├── base.py       # ★ ReAct 循环：Agent 的本质
│       ├── analysts.py   # 3 个分析师
│       ├── researchers.py# 多头 / 空头
│       └── manager.py    # 研究经理
├── data/
│   ├── provider.py       # 数据源抽象（mock / akshare）
│   └── fixtures/         # 离线样本数据
├── eval/
│   ├── cases.json        # 评测股票池
│   └── run_eval.py       # 评测 / 消融脚本
├── scripts/
│   └── selfcheck.py      # 零依赖自检（不需要 pytest）
└── docs/
    └── architecture.md   # 架构详解与扩展练习
```

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

## 验证

### 自检：21/21 通过

```bash
python scripts/selfcheck.py
```

覆盖：配置降级、数据可复现、工具返回、端到端跑通、辩论计数严格等于「轮数 × 2」、
并行扇入、不同标的数据不串味。

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
