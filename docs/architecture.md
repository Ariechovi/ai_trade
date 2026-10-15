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

## 九、数据层：照搬 V3 的四层分离

### 分层

| 层 | 文件 | 只负责 |
|---|---|---|
| 数据源 | `data/sources/base.py` + 各实现 | 怎么从某个来源拿到数据 |
| 指标 | `data/indicators.py` | 怎么算 |
| 缓存 | `data/cache.py` | 怎么存 |
| 服务 | `data/service.py` | 用哪个源、失败了怎么办、要不要缓存 |
| 工具 | `src/tools.py` | 暴露什么给 LLM |

**为什么值得分这么细？** 因为这几件事的变更原因完全不同：数据源会换
（akshare 挂了换 baostock）、指标会加（今天加 KDJ）、工具描述会调。
堆在一个文件里，改任何一处都要动全局。

### 关键约定：列名标准化

`BaseDataSource.get_price_history()` 必须返回列名为
`date/open/high/low/close/volume` 的 DataFrame。
各数据源原始列名千奇百怪（akshare 是中文列名），**在这一层抹平**，
于是 `indicators.py` 永远只认一套列名。

### 指标用 pandas 手写，不用第三方库

V3 用 `stockstats` 库。这里手写，理由有两个：少一个依赖；**公式是可见的**——
RSI 用 Wilder 平滑（`alpha = 1/period`）而不是简单均值，这件事写一遍才会记住。

自检里用「已知序列反推」验证公式：单调上涨 → RSI = 100，单调下跌 → RSI = 0，
常数序列 → MA 等于该常数。**指标算错是最隐蔽的 bug**，必须用能反推的用例验。

### 降级链

```
DataService.get_xxx
   → 源1（akshare）→ 失败？
   → 源2（备用源）→ 失败？
   → 缓存
   → 全部失败 → 抛 DataSourceError
                 → tools 层捕获 → 作为失败的 ToolMessage 交给 LLM
                 → 若消息里含可重试特征 → GuardedNode 重试整个节点
```

注意最后两行：**数据层的失败会被容错层接住**，两层是串联的。这就是为什么
先做工程、再做业务是有道理的——业务层的失败模式天然复杂，没有容错层你会淹在里面。

### 一个反直觉的规则

真数据失败时**不要**静默切到模拟数据。看起来「更健壮」，实际上是把
「分析失败」伪装成「分析成功」，产出一份基于假数据的结论——在金融场景是灾难。
所以 mock 只在显式指定（`DATA_PROVIDER=mock`）或 akshare 根本没装时启用，
且输出永远带 `· 离线模拟数据` 标记。

### 接口互备与「部分降级」

真实环境实测发现：akshare 的**东财行情域名**（`push2his.eastmoney.com`）在部分网络下
不可达，但新浪（`stock_zh_a_daily`）和东财财务/新闻接口正常。
所以 `AkshareDataSource` 内部做**按能力互备**：

```
行情   新浪 → 东财         （多一条路径就多一次机会）
财务   东财财务摘要
新闻   东财新闻
```

同时在 `DataService.get_fundamentals` 里做**部分降级**：
公司名拿不到时只退化成「用代码当名字」，财务指标照常输出。
**整体失败 vs 局部失败**——能让一部分数据活下来，就不要让全部死掉。

## 十、工具层：按分析师域组织

### 文件布局

```
src/tools/
├── __init__.py      build_tools() 汇总，返回 {工具名: 工具对象}
├── technical.py     5 个：概览 / 指标 / 交叉信号 / 支撑压力 / 量能
├── fundamental.py   5 个：报表 / 成长 / 健康度 / 估值 / 同业
└── news.py          4 个：新闻 / 检索 / 情绪 / 市场环境
```

对应 V3 的 `core/tools/implementations/{technical,fundamentals,news,social,market}/`。

### 指标原语 vs 业务判断

| 层 | 放什么 | 例子 |
|---|---|---|
| `data/indicators.py` | 指标**数列**（数学） | `rsi()`、`macd()`、`bollinger()` |
| `src/tools/technical.py` | 业务**判断**（分析） | 「MA5/MA20 在 3 个交易日前金叉」 |

V3 也是这么分的（`dataflows/technical/` 出指标，`core/tools/` 出结论）。
**分界线是「换个数据集还成立吗」**：RSI 公式对任何价格序列都成立（数学）；
「金叉意味着看多」是业务判断（会变）。

### 一个必须解决的边界问题：工具预算用完后要强制收口

工具从 1 个变成 5 个之后，出现了一个新 bug 类：

```
第 4 轮：LLM 还想调工具 → 路由器说"预算用完了" → 直接去报告节点
        但历史最后一条是「带未执行 tool_calls 的 AI 消息」
        报告节点扫不到纯文本结论 → 只能写兜底文案
```

修法（`AnalystLLMNode`）：**工具轮数用尽时，这次调用不再绑定工具**，
让模型必须给出一段文本收口。这正是最早 `run_agent` 里那句「强制收口」的作用——
重构到图上之后，它变成了「节点根据计数器决定绑不绑工具」。

**这是"工具变多"暴露出的第一个真实工程问题**，也是很好的例子：
能力一扩张，边界情况就冒出来了。

## 十一、第二个真实 bug：扇入屏障失效（只有真实 LLM 才暴露）

### 症状

第一次用真实 DeepSeek 跑完整流程，输出质量很好，但轨迹不对：

```
merge 出现 2 次
manager 出现 2 次
debate:bull 出现在 report:fundamental 之前   ← 辩论在缺料时进行
```

### 根因

`StateGraph.add_node(node, action, *, defer=False, ...)` —— **`defer` 默认是 `False`**。
它的语义是「**任一入边到达就执行**」，而不是「等齐所有入边」。

我们的三个分析师各自带工具循环，长度天然不同：

```
technical    2 步（一次返回 5 个 tool_calls，一轮就调完了）
fundamental  4 步
news         4 步
```

于是三条 `msg_clear_* → merge` 的入边分别落在三个超级步，
`merge` 被触发三次：**每次都在只有一部分报告到齐时就放行辩论**。

### 修法

```python
graph.add_node("merge", _merge_node, defer=True)
```

修完后：`merge` 一次、`manager` 一次、`debate_count=4`，
且 trace 里三份 `report:*` 全部出现在 `merge` 之前。

### 为什么离线自检没抓到

离线桩让三个分析师的循环长度**完全相同**，三条入边落在同一个超级步，
`merge` 恰好只触发一次——**测试数据太"整齐"，掩盖了真实世界的不整齐**。

这条教训比 bug 本身值钱：

> **等长的测试会掩盖不等长的现实。**
> 现在自检里有两个回归用例——等长分支和不等长分支，都断言
> 「三份报告全部出现在 merge 之前」。

### 防守型断言怎么写

不要只断言「成功了」，要断言**顺序关系**：

```python
merge_at = trace.index("merge")
assert all(trace.index(f"report:{role}") < merge_at for role in ROLES)
```

「结果非空」这种断言对这个 bug 完全无效——报告确实都产出了，
只是**在错误的时间**被合并。

## 十二、提示词层：从"一句话"到"契约"

### 为什么拆成包

分析师提示词从 3 句变成 40 行后，塞在 `specs.py` 的 dataclass 字段里会让那张
「分析师表」没法读。所以：

```
src/prompts/
├── analysts.py    三个分析师的职责契约（长文本）
├── debaters.py    多空研究员
├── manager.py     研究经理
└── templates.py   用户消息拼装函数
```

`specs.py` 只保留 `system_prompt=ANALYST_PROMPTS["technical"]` 这样的短引用。

**原则：抽象的边界应该跟着文本长度走。** 表里放标识，长文本放独立文件。
这不是推翻之前的可读性重构，而是它的延伸。

### 契约的五段结构

对照 V3 的措辞，每份契约包含：

1. **角色与职责** —— 「负责为研究流程提供**可复核的**财务与经营证据，并进行**平衡的**财务分析」
   （V3 原文）。注意它定义的是"责任 + 质量标准"，不是头衔。
2. **你的任务** —— 编号列表，含工具使用要求（至少调几个、先调什么）。
3. **要求** —— 硬约束。这是整份契约里最有价值的部分。
4. **可以写 / 不得写** —— 正反例。V3 的原话是「可以写："经营现金流弱于净利润，
   利润兑现质量仍待验证。"」「不得写："当前具备明显安全边际。"」——
   **给出具体句子比给出规则有效得多**。
5. **输出结构** —— 固定小节。下游的经理和评测都依赖它。

### 工具描述的四段结构

```
何时使用：...            ← 什么时候该调
何时不要用：...（改用 xxx） ← 什么时候不该调，以及该改用什么
返回 / 不包含：...        ← 能拿到什么、拿不到什么
局限：...                ← 已知的坑（零轴位置、低基数失真、词典法覆盖不足…）
```

V3 把这些放在 `ToolMetadata` 里（`when_to_use` / `when_not_to_use` / `returns` /
`limitations`），因为它要喂给多个消费者：模型、UI、Skill 工坊。
**我们的消费者只有模型一个，而 LangChain 只把 docstring 传给模型**——
所以直接写进 docstring。这是"照搬设计"和"照搬字段"的区别。

### 一条关于提示词工程的克制建议

提示词变长有两个代价：**token 成本** 和 **指令稀释**（指令太多，模型会挑着执行）。
所以：

- 宁可加「不得写」这种**具体反例**，也不要加「请务必仔细分析」这种**空泛要求**
- 每条规则都应该能对应一个真实踩过的坑，否则删掉
