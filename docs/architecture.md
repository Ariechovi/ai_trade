# 架构详解

这份文档解释「为什么这么设计」，以及读懂代码的推荐顺序。

## 一、数据是怎么流动的

一次分析从 `runner.run_case()` 开始，只做三件事：

1. 组装工具（`build_tools`）
2. 组装 LLM 工厂（`llm_factory`：按角色返回 LLM，离线时返回桩模型）
3. 建图并执行（`build_graph(...).invoke(initial_state)`）

初始状态里必须显式给出带 reducer 的字段的初值：

```python
{
    "ticker": ..., "trade_date": ..., "max_rounds": ...,
    "debate_history": [],   # operator.add 的累加起点
    "debate_count": 0,      # 计数器起点
    "trace": [],
}
```

之后每个节点只需返回**增量**（partial update），LangGraph 负责按 reducer 合并。

## 二、为什么三个分析师要各写各的字段

三个分析师在同一个超级步里并行执行。如果它们都往 `messages` 里写，
就会互相覆盖、串味（这也是参考项目里要给每个分析师分配 `_xxx_messages` 的原因）。

本项目的做法更直白：**每个分析师拥有自己的报告字段**（`technical_report` /
`fundamental_report` / `news_report`），天然不冲突。

## 三、辩论循环是怎么转起来的

关键不是 `while`，而是**条件边 + 计数器**：

```python
graph.add_conditional_edges("bull", make_router(threshold, after_bull=True), ["bear", "manager"])
graph.add_conditional_edges("bear", make_router(threshold, after_bull=False), ["bull", "manager"])
```

每发言一次，节点返回 `{"debate_count": 1}`，被 `operator.add` 累加；
路由器读到累加值，决定「下一位发言」还是「交给经理」。

`threshold = rounds × 2`，因为一轮 = 多头 + 空头各说一次。

## 四、LangGraph 帮你做了什么

- **扇入屏障**：三条边同时指向 `merge`，LangGraph 会自动等三条都完成才执行 `merge`。
- **状态合并**：按字段的 reducer 自动合并增量。
- **循环终止**：条件边 + 计数器，不需要手写循环。

你手写的只有「节点函数」和「路由函数」，这就是"用框架"和"手搓"的区别。

## 五、推荐阅读顺序

1. `src/state.py` —— 先搞清楚数据长什么样、怎么合并
2. `src/graph.py` —— 再看拓扑长什么样
3. `src/agents/base.py` —— 最后看单个 Agent 怎么思考
4. `src/agents/*.py` —— 各角色只是"填内容"
5. `src/offline_llm.py` —— 理解离线验证是怎么做到的

## 六、扩展练习（难度递增）

| 练习 | 做什么 | 能学到 |
|---|---|---|
| L1 | 新增一个「资金流向分析师」 | 加节点、加工具、改状态 |
| L2 | 给分析师加第 2 个工具，验证 ReAct 多轮循环 | 工具调用循环 |
| L3 | 把 `MAX_DEBATE_ROUNDS` 加到 5，观察成本与稳定性 | 消融实验设计 |
| L4 | 新增「反思节点」：经理复盘辩论并写入长期记忆 | 记忆 / reflection |
| L5 | 把 `--provider` 换成 akshare，处理真实数据的缺失与异常 | 工程鲁棒性 |
| L6 | 加一个 LLM-as-judge 评估结论质量 | 系统评测 |

## 七、图级工具循环（本项目最核心的一处设计）

### 问题

如果工具循环藏在 Agent 节点内部的 `while` 里，整个过程对图就是**不可见**的：
你不知道调了几次工具、调了什么、失败在哪一步，也无法中途打断或加入人工审批。

### 解法：把它拆成图上的一串节点

```
analyst_<role>  ──┬─(有 tool_calls 且未超上限)─> tools_<role> ─┐
                  │                                          │
                  └─(无 tool_calls / 超上限)──> msg_clear_<role> ─> merge
                                     ▲                        │
                                     └────────────────────────┘
```

把「调 LLM」和「执行工具」拆成两个原子操作（`src/agents/base.py`）：
- `call_llm_once(llm, tool_map, messages)` —— 只调一次 LLM，不做循环
- `execute_tool_calls(tool_map, tool_calls)` —— 执行工具，返回 ToolMessage

### 必须解决的 5 个问题

| # | 问题 | 解法 | V3 里的对应物 |
|---|---|---|---|
| 1 | 三个分析师并行，共享 `messages` 会串味 | 每人一个私有消息字段 | `_market_messages` 等 |
| 2 | `tool_call_id` 必须与 `ToolMessage` 严格配对 | `execute_tool_calls` 里透传 id | `ToolNode` 内部完成 |
| 3 | 循环可能永不终止 | `Annotated[int, operator.add]` 计数器 + 条件边阈值 | `max_tool_calls = 3` |
| 4 | 报告要落到独立字段，不能污染下游 | 独立的 `msg_clear` 节点 | `_create_msg_clear_node` |
| 5 | 私有历史如何初始化 | 首次进入时用 System + Human 播种 | `_create_analyst_wrapper` 的初始化分支 |

### 这么做之后白拿到的能力

- 每一步工具调用都在 trace / 状态里，可观测
- 可对单个工具节点做超时、重试、降级
- 可对危险工具（下单、写库）插入人工审批
- 可配合 LangGraph 的持久化做断点续跑与回放
