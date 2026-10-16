"""mini-agents 的可视化界面（Streamlit）。

用法：
    streamlit run ui/streamlit_app.py

它做三件事：
  1. 左侧输入：股票代码 / 日期 / 辩论轮数 / 数据源 / 离线开关
  2. 点「开始分析」→ 实时显示每个节点的执行进度
  3. 完成后分 Tab 展示：最终结论 / 三份报告 / 辩论记录 / 运行指标
     运行指标含：token 消耗（总量与按角色）、每个节点耗时、降级记录

设计说明：页面本身不做任何分析逻辑，只调用 `src.runner.stream_case`。
这样 CLI 和页面共用同一条通路，不会出现两套实现。

位置
----
上游：无（用户直接运行）
下游：src/runner.py（stream_case / summarize）、data/（数据源）
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, List

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data import get_data_service  # noqa: E402
from src.config import load_settings  # noqa: E402
from src.runner import stream_case, summarize  # noqa: E402

ROLE_LABELS = {"technical": "技术面", "fundamental": "基本面", "news": "消息面"}

st.set_page_config(page_title="mini-agents · 多智能体投研", page_icon="📈", layout="wide")


def node_label(node: str) -> str:
    """把节点名翻译成人话——页面是给人看的，不是给日志看的。"""
    if node == "merge":
        return "汇合（等齐三份报告）"
    if node == "manager":
        return "研究经理：综合出结论"
    if node in ("bull", "bear"):
        return f"{'多头' if node == 'bull' else '空头'}研究员发言"
    if node.startswith("tools_"):
        return f"工具执行（{ROLE_LABELS.get(node[6:], node[6:])}）"
    if node.startswith("msg_clear_"):
        return f"报告提取（{ROLE_LABELS.get(node[10:], node[10:])}）"
    if node in ROLE_LABELS:
        return f"{ROLE_LABELS[node]}分析师：思考 / 调用工具"
    return node


# ============================ 左侧：输入 ============================
with st.sidebar:
    st.header("输入")
    ticker = st.text_input("股票代码", value="600519")
    trade_date = st.date_input("分析日期", value=date.today())
    rounds = st.slider("辩论轮数", min_value=0, max_value=3, value=2)
    provider_mode = st.selectbox("数据源", ["auto", "akshare", "mock"], index=0)
    offline = st.checkbox("强制离线（桩模型，不需要 API Key）", value=False)
    run = st.button("开始分析", type="primary", width="stretch")
    st.caption(
        "离线模式没有 token 统计；用真实模型请确认 `.env` 里配了 `DEEPSEEK_API_KEY`。"
    )
    if st.session_state.get("result") and st.button("清空结果"):
        st.session_state.result = None
        st.session_state.metrics = None
        st.session_state.meta = {}
        st.rerun()

st.title("mini-agents · 多智能体投研")
st.caption("三位分析师并行 → 多空辩论 → 研究经理综合。全流程节点级可观测。")


# ============================ 运行：实时进度 ============================
if run:
    settings = load_settings(offline=True if offline else None, rounds=rounds)
    data_service = get_data_service(provider_mode)
    sources = "+".join(source.name for source in data_service.sources)

    st.subheader("运行过程")
    st.info(
        f"数据源：{sources}　|　模型：{'离线桩' if settings.offline else settings.llm_model}"
        f"　|　辩论：{rounds} 轮　|　最大工具轮数：{settings.max_tool_iterations}"
    )
    if "akshare" in sources:
        st.caption(
            "akshare 需要联网取数（行情全量下载 + 财务摘要 + 新闻），"
            "单个工具节点可能等待 10~30 秒，属正常。已设 30 秒 socket 超时，"
            "超时会自动降级而不是无限挂起。"
        )

    progress = st.status("准备中…", expanded=True)
    with progress:
        # ⚠️ 必须用占位符：直接调 progress.markdown() 是**追加**而不是替换，
        # 每完成一个节点就会新加一块，于是同一个节点被重复显示 N 遍。
        log_slot = st.empty()
        # 当前正在做什么（模型思考中 / 正在执行工具）——解决"看起来卡住"
        activity = st.empty()
    lines: List[str] = []
    started = time.perf_counter()

    def on_node(node: str, _update: Any) -> None:
        lines.append(f"✔ {node_label(node)}")
        activity.empty()
        log_slot.markdown("  \n".join(lines[-14:]))
        progress.update(
            label=f"运行中… 已完成 {len(lines)} 个节点"
            f"（{time.perf_counter() - started:.0f}s）"
        )

    def on_event(payload: Any) -> None:
        if isinstance(payload, dict) and payload.get("text"):
            activity.info(
                f"⏳ {payload['text']}　（已用 {time.perf_counter() - started:.0f}s）"
            )

    try:
        state = stream_case(
            str(ticker).strip(),
            trade_date.isoformat(),
            settings,
            data_service,
            on_node=on_node,
            on_event=on_event,
        )
    except Exception as exc:  # 页面不能因为一次分析失败就白屏
        progress.update(label="运行失败", state="error", expanded=True)
        st.exception(exc)
        st.stop()
    elapsed = time.perf_counter() - started

    progress.update(
        label=f"完成：{len(lines)} 个节点，用时 {elapsed:.1f}s",
        state="complete",
        expanded=False,
    )
    st.session_state.result = state
    st.session_state.metrics = summarize(state)
    st.session_state.meta = {
        "ticker": str(ticker).strip(),
        "date": trade_date.isoformat(),
        "sources": sources,
        "offline": settings.offline,
        "rounds": rounds,
        "elapsed": elapsed,
    }


# ============================ 结果展示 ============================
state = st.session_state.get("result")
if not state:
    st.info("在左侧填好参数，点「开始分析」。")
    st.stop()

metrics = st.session_state.get("metrics") or summarize(state)
meta = st.session_state.get("meta") or {}

st.subheader("结果")
col1, col2, col3, col4 = st.columns(4)
col1.metric("总耗时", f"{meta.get('elapsed', 0):.1f}s")
col2.metric("LLM 调用", f"{metrics['llm_call_count']} 次")
col3.metric(
    "token 合计",
    f"{metrics['total_tokens']:,}" if metrics["total_tokens"] else "—（离线）",
)
col4.metric("降级节点", len(metrics["degraded"]))
st.caption(
    f"{meta.get('ticker', '')} · {meta.get('date', '')} · 数据源 {meta.get('sources', '')}"
    f" · 累计节点耗时 {metrics['node_duration_ms'] / 1000:.1f}s（并行节点会重叠）"
)

tabs = st.tabs(["最终结论", "技术面", "基本面", "消息面", "辩论记录", "运行指标"])


def _show(tab, content: str, empty: str = "_（无输出）_") -> None:
    with tab:
        st.markdown(content if str(content).strip() else empty)


_show(tabs[0], str(state.get("final_decision", "")))
_show(tabs[1], str(state.get("technical_report", "")))
_show(tabs[2], str(state.get("fundamental_report", "")))
_show(tabs[3], str(state.get("news_report", "")))

with tabs[4]:
    history = state.get("debate_history") or []
    if not history:
        st.write(f"本次没有辩论（轮数 = {meta.get('rounds', 0)}）。")
    for index, item in enumerate(history, 1):
        speaker = "多头" if item.get("speaker") == "bull" else "空头"
        st.markdown(f"**第 {index} 次发言 · {speaker}**")
        st.markdown(str(item.get("content", "")))
        st.divider()

with tabs[5]:
    st.markdown("**每个节点的耗时**")
    timings = pd.DataFrame(metrics["node_timings"] or [])
    if timings.empty:
        st.write("没有耗时记录。")
    else:
        timings = timings.rename(columns={"node": "节点", "duration_ms": "耗时(毫秒)"})
        st.dataframe(timings, width="stretch", hide_index=True)

    st.markdown("**按角色汇总的 token 与耗时**")
    rows = [
        {
            "角色": role,
            "调用次数": bucket["calls"],
            "输入 token": bucket["input_tokens"],
            "输出 token": bucket["output_tokens"],
            "合计 token": bucket["total_tokens"],
            "耗时(秒)": round(float(bucket["duration_ms"]) / 1000, 1),
        }
        for role, bucket in metrics["by_role"].items()
    ]
    if not rows:
        st.write("没有 LLM 调用记录。")
    else:
        frame = pd.DataFrame(rows)
        st.dataframe(frame, width="stretch", hide_index=True)
        if metrics["total_tokens"]:
            st.bar_chart(frame.set_index("角色")["合计 token"])
        else:
            st.caption("离线桩模型不返回 token 用量，换成真实模型即可看到分布。")

    if metrics["degraded"]:
        st.error("有节点被降级：" + "、".join(metrics["degraded"]))
        st.json(state.get("_node_errors") or {})

    with st.expander("节点执行轨迹（trace）"):
        st.write(state.get("trace") or [])
