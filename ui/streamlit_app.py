"""mini-agents 的可视化界面（Streamlit）。

用法（两个终端）：
    1) 起后端：cd backend && uvicorn main:app --port 8100
    2) 起界面：streamlit run ui/streamlit_app.py

设计要点：本文件**不 import 后端任何代码**，只通过 HTTP 调接口。
它和 vue/ 前端的角色完全一致 —— 前端一个进程、后端一个进程，中间只有 HTTP。
这样将来把 Streamlit 换成 Vue，前端这一侧是干净的，不用动后端。

接口地址可用环境变量覆盖：MINI_AGENTS_API=http://127.0.0.1:8100
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Dict, Iterator, List, Optional

import httpx
import pandas as pd
import streamlit as st

API_BASE = os.getenv("MINI_AGENTS_API", "http://127.0.0.1:8100").rstrip("/")
ROLE_LABELS = {"technical": "技术面", "fundamental": "基本面", "news": "消息面"}
READ_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=30.0)

# ⚠️ trust_env=False 不是可选优化，是必须的：
# httpx 默认会读取系统代理（Windows 上还会读注册表里的 WinINet 设置，
# 所以即使环境变量是空的也可能中招）。机器上一旦配了代理，
# 对 http://127.0.0.1:8100 的请求也会被丢给代理，拿回来一个 502 ——
# 表面看像"后端没起来"，实际是本机请求被代理拦了。
# 本机接口调用永远不该走代理。
CLIENT = httpx.Client(trust_env=False, timeout=30.0)

st.set_page_config(page_title="mini-agents · 多智能体投研", page_icon="📈", layout="wide")


def api_get(path: str, **params: Any) -> Dict[str, Any]:
    resp = CLIENT.get(f"{API_BASE}{path}", params=params or None)
    resp.raise_for_status()
    return resp.json()


def backend_health() -> Optional[Dict[str, Any]]:
    try:
        return api_get("/api/health")
    except Exception:  # noqa: BLE001 —— 连不上后端是预期内的情况
        return None


def diagnose() -> str:
    """连不上时给一句可操作的排查线索，而不是只说"连不上"。"""
    try:
        resp = CLIENT.get(f"{API_BASE}/api/health")
        return (
            f"该地址有响应，但状态码是 {resp.status_code} —— "
            "通常说明这个端口上跑的不是本项目（被别的程序占用），或请求被中间层改写了。"
        )
    except httpx.ConnectError:
        return "连接被拒绝：后端没在跑，或者地址/端口不对。"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def create_run(payload: Dict[str, Any]) -> str:
    resp = CLIENT.post(f"{API_BASE}/api/analysis", json=payload)
    resp.raise_for_status()
    return str(resp.json()["run_id"])


def iter_events(run_id: str, last_event_id: Optional[int] = None) -> Iterator[Dict[str, Any]]:
    """读 SSE 事件流；遇到 done / error 就结束。"""
    url = f"{API_BASE}/api/analysis/{run_id}/events"
    headers = {"Last-Event-ID": str(last_event_id)} if last_event_id else {}
    with CLIENT.stream("GET", url, headers=headers, timeout=READ_TIMEOUT) as resp:
        resp.raise_for_status()
        name: Optional[str] = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: ") and name:
                yield {"event": name, "data": json.loads(line[len("data: ") :])}
                if name in ("done", "error"):
                    return


def node_label(node: str) -> str:
    """把接口给的节点名翻译成人话（接口用的是和 CLI trace 一致的名字）。"""
    if node.startswith("llm:"):
        role = node.split(":", 1)[1]
        return f"{ROLE_LABELS.get(role, role)}分析师 · 模型思考"
    if node.startswith("tools:"):
        role = node.split(":", 1)[1].split("(", 1)[0]
        return f"{ROLE_LABELS.get(role, role)}分析师 · 执行工具"
    if node.startswith("report:"):
        role = node.split(":", 1)[1]
        return f"{ROLE_LABELS.get(role, role)}分析师 · 产出报告"
    if node == "merge":
        return "汇合（等齐三份报告）"
    if node.startswith("debate:"):
        return "多头研究员发言" if node.endswith("bull") else "空头研究员发言"
    if node == "manager":
        return "研究经理 · 综合结论"
    return node


st.title("mini-agents · 多智能体投研")

health = backend_health()
if health is None:
    st.error(
        f"连不上后端 `{API_BASE}`。\n\n"
        f"**诊断**：{diagnose()}\n\n"
        "**通常的处理**：在另一个终端启动后端\n\n"
        "```\ncd backend\npython main.py\n```\n\n"
        "如果它提示端口被占用，看看是谁占着：\n\n"
        "```\nnetstat -ano | findstr :8100\n```"
    )
    st.stop()
st.caption(f"后端 {API_BASE} · 版本 {health.get('version')}")


with st.sidebar:
    st.subheader("分析参数")
    ticker = st.text_input("股票代码", "600519")
    trade_date = st.date_input("分析日期", date.today())
    rounds = st.slider("辩论轮数", min_value=0, max_value=3, value=2)
    provider = st.selectbox("数据源", ["auto", "akshare", "mock"], index=0)
    offline = st.checkbox("强制离线（桩模型，不需要 Key）", value=False)
    run_clicked = st.button("开始分析", type="primary", width="stretch")
    st.divider()
    show_trace = st.checkbox("显示节点执行轨迹", value=True)

if run_clicked:
    payload = {
        "ticker": ticker.strip(),
        "trade_date": trade_date.isoformat(),
        "rounds": rounds,
        "provider": provider,
        "offline": offline,
    }
    try:
        run_id = create_run(payload)
    except httpx.HTTPStatusError as exc:
        st.error(f"创建任务失败（HTTP {exc.response.status_code}）：{exc.response.text[:300]}")
        st.stop()
    except httpx.HTTPError as exc:
        st.error(f"请求后端失败：{exc}")
        st.stop()

    st.session_state["run_id"] = run_id
    st.session_state["detail"] = None

    progress = st.progress(0.0, text="任务已提交…")
    lines: List[str] = []
    log_box = st.empty()
    failed = False

    for ev in iter_events(run_id):
        kind, data = ev["event"], ev["data"]
        if kind == "node":
            lines.append("✓ " + node_label(str(data.get("node", ""))))
            if "progress" in data:
                progress.progress(min(0.99, float(data["progress"])), text="分析中…")
        elif kind == "progress":
            lines.append("… " + str(data.get("text", "")))
        elif kind == "error":
            failed = True
            st.error(f"任务失败：{data.get('type')} — {data.get('message')}")
        elif kind == "done":
            progress.progress(1.0, text=f"已结束（{data.get('status')}）")
        if lines:
            log_box.code("\n".join(lines[-14:]), language=None)

    if not failed:
        st.session_state["detail"] = api_get(f"/api/analysis/{run_id}")


detail = st.session_state.get("detail")
run_id = st.session_state.get("run_id")

if detail and detail.get("result"):
    result = detail["result"]
    metrics = result.get("metrics") or {}

    st.caption(f"run_id `{run_id}` · {result.get('ticker')} · {result.get('trade_date')}")
    if result.get("degraded"):
        st.warning("以下节点降级（其余结论不受影响）：" + "、".join(result["degraded"]))

    tabs = st.tabs(["最终结论", "技术面", "基本面", "消息面", "辩论记录", "运行指标", "任务详情"])
    with tabs[0]:
        st.markdown(result.get("final_decision") or "_（空）_")
    with tabs[1]:
        st.markdown(result.get("technical_report") or "_（空）_")
    with tabs[2]:
        st.markdown(result.get("fundamental_report") or "_（空）_")
    with tabs[3]:
        st.markdown(result.get("news_report") or "_（空）_")
    with tabs[4]:
        for item in result.get("debate_history") or []:
            speaker = "多头" if item.get("speaker") == "bull" else "空头"
            st.markdown(f"**{speaker}**：{item.get('content')}")
    with tabs[5]:
        c1, c2, c3 = st.columns(3)
        c1.metric("LLM 调用", metrics.get("llm_call_count", 0))
        c2.metric("token 合计", metrics.get("total_tokens", 0))
        c3.metric("累计节点耗时", f"{(metrics.get('node_duration_ms') or 0) / 1000:.1f}s")
        if not metrics.get("total_tokens"):
            st.caption("离线桩模型不返回 token 用量，换成真实模型即可看到分布")
        by_role = metrics.get("by_role") or {}
        if by_role:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "角色": role,
                            "调用次数": b.get("calls"),
                            "input": b.get("input_tokens"),
                            "output": b.get("output_tokens"),
                            "耗时(s)": round((b.get("duration_ms") or 0) / 1000, 2),
                        }
                        for role, b in by_role.items()
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
        timings = metrics.get("node_timings") or []
        if timings:
            st.caption("节点耗时（并行节点会重叠，合计不是墙上时间）")
            st.dataframe(pd.DataFrame(timings), width="stretch", hide_index=True)
    with tabs[6]:
        st.json(
            {
                "run_id": detail.get("run_id"),
                "status": detail.get("status"),
                "created_at": detail.get("created_at"),
                "started_at": detail.get("started_at"),
                "finished_at": detail.get("finished_at"),
                "params": detail.get("params"),
                "error": detail.get("error"),
            }
        )

    if show_trace and result.get("trace"):
        with st.expander("节点执行轨迹（trace）"):
            st.code("\n".join(result["trace"]), language=None)
elif run_id:
    st.info("任务已提交但还没拿到结果。事件流支持 Last-Event-ID 续传，重新连即可续上。")
    if st.button("拉取结果"):
        st.session_state["detail"] = api_get(f"/api/analysis/{run_id}")
        st.rerun()
else:
    st.info("左侧填好参数，点「开始分析」。界面只调用后端接口，不 import 后端代码。")
