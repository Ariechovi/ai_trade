-- 0003_runs_result_and_seq.sql —— runs 表补两列
--
-- result：
--   报告整块存这里。它在这里的角色是「读优化的物化结果」——
--   GET /api/analysis/{id} 一次查询就能返回全部，不用 JOIN 五张表。
--   P2 之后 run_nodes 会存同样的内容，但那是**可查询的明细**（按字段/时间查）；
--   两者由同一个写入点产生，不存在双写不一致的问题。
--
-- event_seq：
--   事件序号计数器。原先是 SELECT MAX(seq)+1，单进程没事，
--   **多 worker 一定撞主键**。改成 UPDATE ... RETURNING，天然原子。

ALTER TABLE runs ADD COLUMN IF NOT EXISTS result    JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS event_seq INTEGER NOT NULL DEFAULT 0;
