-- 0001_init.sql —— mini-agents 存储层初始 schema
--
-- 五张表，五个粒度：
--   runs             一次分析        状态机 + 汇总
--   run_nodes        一个节点执行     ★ 每个节点的报告正文在这里
--   run_llm_calls    一次 LLM 调用     token / 耗时 / 重试
--   run_tool_calls   一次工具调用      工具名 / 参数 / 结果 / 耗时
--   run_events       一条 SSE 事件     断点续传 + 审计
--
-- 三条设计约定（后面加表也遵守）：
--   1. 时间统一用 TIMESTAMPTZ（带时区），不用 TIMESTAMP —— 否则跨时区一定出错
--   2. 报告正文用 TEXT，不用 JSONB —— 便于做子串检索
--   3. runs 不存报告正文，正文只在 run_nodes —— 单一真相源，避免双写不一致

-- ============================================================
-- 1. runs —— 一次分析
-- ============================================================
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT        PRIMARY KEY,
    ticker          TEXT        NOT NULL,
    trade_date      DATE        NOT NULL,
    status          TEXT        NOT NULL,
    params          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    model           TEXT,
    final_decision  TEXT,
    metrics         JSONB,
    degraded        TEXT[]      NOT NULL DEFAULT '{}',
    error           JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    duration_ms     INTEGER
);

-- 状态取值写成约束：写错状态在插入时就报错，而不是等到查询时才发现
ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check;
ALTER TABLE runs ADD  CONSTRAINT runs_status_check CHECK (
    status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')
);

-- ============================================================
-- 2. run_nodes —— ★ 每个节点的执行与产出
-- ============================================================
CREATE TABLE IF NOT EXISTS run_nodes (
    id             BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id         TEXT        NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq            INTEGER     NOT NULL,
    node_name      TEXT        NOT NULL,
    stage          TEXT,
    agent_id       TEXT,
    output_field   TEXT,
    content        TEXT,
    content_chars  INTEGER,
    status         TEXT        NOT NULL DEFAULT 'ok',
    duration_ms    INTEGER,
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);

ALTER TABLE run_nodes DROP CONSTRAINT IF EXISTS run_nodes_status_check;
ALTER TABLE run_nodes ADD  CONSTRAINT run_nodes_status_check CHECK (
    status IN ('ok', 'degraded', 'failed')
);

COMMENT ON COLUMN run_nodes.seq IS '同一 run 内的执行序号，从 1 开始（LLM 节点在工具循环里会跑多轮）';

-- ============================================================
-- 3. run_llm_calls —— 每次 LLM 调用
-- ============================================================
CREATE TABLE IF NOT EXISTS run_llm_calls (
    id             BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id         TEXT        NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node_name      TEXT        NOT NULL,
    role           TEXT        NOT NULL,
    stage          TEXT,
    model          TEXT,
    input_tokens   INTEGER     NOT NULL DEFAULT 0,
    output_tokens  INTEGER     NOT NULL DEFAULT 0,
    total_tokens   INTEGER     NOT NULL DEFAULT 0,
    duration_ms    INTEGER,
    retry_count    INTEGER     NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 4. run_tool_calls —— 每次工具调用
-- ============================================================
CREATE TABLE IF NOT EXISTS run_tool_calls (
    id            BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        TEXT        NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node_name     TEXT        NOT NULL,
    role          TEXT,
    tool_name     TEXT        NOT NULL,
    args          JSONB,
    result_chars  INTEGER,
    status        TEXT        NOT NULL DEFAULT 'ok',
    error         TEXT,
    duration_ms   INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE run_tool_calls DROP CONSTRAINT IF EXISTS run_tool_calls_status_check;
ALTER TABLE run_tool_calls ADD  CONSTRAINT run_tool_calls_status_check CHECK (
    status IN ('ok', 'failed')
);

-- ============================================================
-- 5. run_events —— SSE 断点续传 + 审计
-- ============================================================
CREATE TABLE IF NOT EXISTS run_events (
    run_id  TEXT        NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq     INTEGER     NOT NULL,
    ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    event   TEXT        NOT NULL,
    data    JSONB       NOT NULL,
    PRIMARY KEY (run_id, seq)
);

-- ============================================================
-- 索引
-- ============================================================
-- 列表 & 记忆层召回（"同标的历史结论"）
CREATE INDEX IF NOT EXISTS idx_runs_ticker_time ON runs (ticker, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_created     ON runs (created_at DESC);

-- 部分索引：只索引"需要盯着的"任务。
-- 任务表里绝大多数是 succeeded，全表索引白占空间；这个索引小且命中率高。
CREATE INDEX IF NOT EXISTS idx_runs_open ON runs (status, created_at DESC)
    WHERE status IN ('queued', 'running');

-- 节点级
CREATE INDEX IF NOT EXISTS idx_nodes_run        ON run_nodes (run_id, seq);
CREATE INDEX IF NOT EXISTS idx_nodes_field_time ON run_nodes (output_field, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_agent_time ON run_nodes (agent_id, created_at DESC);

-- 调用级
CREATE INDEX IF NOT EXISTS idx_llm_run        ON run_llm_calls (run_id);
CREATE INDEX IF NOT EXISTS idx_llm_role_time  ON run_llm_calls (role, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_run       ON run_tool_calls (run_id);
CREATE INDEX IF NOT EXISTS idx_tool_name_time ON run_tool_calls (tool_name, created_at DESC);
