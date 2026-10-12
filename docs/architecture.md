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

**别从 `base.py` 开始**——那是零件。按「先形状、后细节」读：

1. `src/graph.py` —— 整张图的形状（谁连谁、循环在哪）
2. `src/agents/specs.py` —— 角色设定（一个分析师 = 一条记录）
3. `src/agents/analysts.py` —— 节点实现（LLM / 工具 / 报告 / 路由）
4. `src/state.py` —— 数据怎么流动（reducer）
5. `src/agents/base.py` —— 最后的原子操作
6. `src/offline_llm.py` —— 离线验证是怎么做到的

配合运行时轨迹读会快很多：

- `python -m src.runner --ticker 600519 --offline` —— 节点级轨迹
- `python scripts/inspect_messages.py --ticker 600519` —— 消息级细节

## 六、扩展练习（难度递增）

| 练习 | 做什么 | 能学到 |
|---|---|---|
| L1 | 新增一个「资金流向分析师」（只需在 `agents/specs.py` 加一条记录 + 补 3 个 state 字段） | 角色注册表、加节点、加工具 |
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

## 八、节点级容错（失败不毁全局）

### 分工：重试是横切的，降级是业务的

| 关注点 | 放在哪 | 理由 |
|---|---|---|
| 重试 | `resilience.GuardedNode`（包装器） | 每个 I/O 节点都要，属于横切关注点 |
| 降级 | 各节点自己的 `degrade(state, exc)` | 每个节点的降级方式不同，属于业务语义 |

```python
class GuardedNode:
    def __call__(self, state):
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.node(state)
            except Exception as exc:
                if not is_retryable(exc) or attempt >= self.max_attempts:
                    break
                self.sleeper(backoff_delay(attempt, self.base_delay))
        return self.node.degrade(state, last_exc)   # ← 异常不穿透
```

### 只有会 I/O 的节点才需要护栏

`merge` 和报告提取是纯函数，没有失败的理由，所以不套 `GuardedNode`。
**加护栏也要挑地方**——无差别包裹只会让图更难读。

### 三个反直觉的坑

1. **降级必须推进计数器**。辩论节点降级时不推进 `debate_count`，路由器就会把控制权
   永远交回给失败节点——死循环。所以 `DebaterNode.degrade()` 里那句
   `"debate_count": 1` 是必须的，不是冗余。
2. **`_node_errors` 需要合并 reducer**。三个分析师并行失败时各写各的键，
   不能用「后写覆盖先写」，所以 state 里它是 `Annotated[Dict, merge_dict]`。
3. **降级点选在出口节点最省事**。分析师 LLM 节点降级时，我们只注入一条「没有工具调用」的
   兜底 AI 消息，剩下的交给既有的 `msg_clear` 节点——降级逻辑只写一处。

### 故障注入验证

容错能力必须能被证明，而不是嘴上说。`scripts/selfcheck.py` 里定义了
`FlakyChatModel`（前 N 次调用抛异常），用来验证四种情况：

| 场景 | 期望 |
|---|---|
| 可重试错误，第 1 次失败 | 重试后成功，无降级记录 |
| 可重试错误，一直失败 | 尝试 3 次后降级，其余节点与最终结论不受影响 |
| 不可重试错误 | 只尝试 1 次就降级 |
| 辩论节点一直失败 | 计数器照常推进，辩论正常结束（不死循环） |
