"""节点级容错：重试、降级、不死循环（自检 [8][9][10][11]）

容错能力必须能被证明，而不是嘴上说。这里用故障注入模型验证四种情况。
"""


def test_retry_then_success(make_flaky_factory, run_graph_with, fast_settings):
    factory, created = make_flaky_factory(
        ticker="600519",
        targets={"technical"},
        fail_first_n=1,
        message="connection timeout (injected)",
    )
    state = run_graph_with(factory, fast_settings)

    assert "调用失败" not in str(state.get("technical_report"))
    assert not (state.get("_node_errors") or {})
    assert len(created[0].attempts) >= 2
    assert state.get("final_decision")


def test_retry_exhausted_degrades_locally(make_flaky_factory, run_graph_with, fast_settings):
    factory, created = make_flaky_factory(
        ticker="600519",
        targets={"technical"},
        fail_first_n=999,
        message="connection timeout (injected)",
    )
    state = run_graph_with(factory, fast_settings)
    record = (state.get("_node_errors") or {}).get("technical") or {}

    assert "technical" in (state.get("_node_errors") or {})
    assert record.get("stage") and record.get("message")
    assert len(created[0].attempts) == 3  # 用尽重试预算
    assert "调用失败" in str(state.get("technical_report"))
    assert "调用失败" not in str(state.get("fundamental_report"))  # 降级是局部的
    assert "report:technical" in [str(t) for t in state.get("trace") or []]
    assert state.get("final_decision")


def test_unretryable_error_degrades_immediately(
    make_flaky_factory, run_graph_with, fast_settings
):
    """参数/鉴权类错误重试多少次都没用，应该立刻降级。"""
    factory, created = make_flaky_factory(
        ticker="600519",
        targets={"news"},
        fail_first_n=999,
        message="bad request: invalid parameter",
    )
    state = run_graph_with(factory, fast_settings)

    assert "news" in (state.get("_node_errors") or {})
    assert len(created[0].attempts) == 1


def test_debate_failure_does_not_loop_forever(make_flaky_factory, run_graph_with, fast_settings):
    """降级必须推进计数器，否则路由器会把控制权永远交回失败节点。"""
    factory, _ = make_flaky_factory(
        ticker="600519",
        targets={"bull"},
        fail_first_n=999,
        message="connection timeout (injected)",
    )
    state = run_graph_with(factory, fast_settings)

    assert "debate.bull" in (state.get("_node_errors") or {})
    assert int(state.get("debate_count", 0)) == 4
    assert state.get("final_decision")
