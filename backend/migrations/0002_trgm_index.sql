-- 0002_trgm_index.sql —— 报告正文的中文子串检索（可选）
--
-- 为什么用 pg_trgm 而不是 PG 自带的全文检索：
--   to_tsvector 的默认分词器对中文几乎不分词（整段当成一个 token），
--   中文检索基本不可用；pg_trgm 做三元组子串索引反而实用。
--
-- ⚠️ 如果当前账号没有 CREATE EXTENSION 权限，这个迁移会失败 ——
--    但它不影响 0001 已经建好的表。让 DBA 执行 `CREATE EXTENSION pg_trgm;`
--    后重跑 `python -m migrations.runner apply` 即可。

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_nodes_content_trgm
    ON run_nodes USING gin (content gin_trgm_ops);
