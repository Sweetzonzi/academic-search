-- sidecar SQLite 物理 DDL。
-- 语义与键的家是 docs/接口/数据模型.md；本文件不得引入其中没有的表或唯一键。
-- schema_version 用 PRAGMA user_version 承载（见 store.py SCHEMA_VERSION）。
-- 时刻一律 UTC ISO-8601 带 Z；日期型字段存 YYYY-MM-DD 原值不换算（DEV-CONV §3）。

-- §3.1 record：一条来源记录
CREATE TABLE IF NOT EXISTS record (
    record_uid      TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    first_run_id    INTEGER REFERENCES run(run_id),
    title           TEXT,
    abstract        TEXT,
    authors         TEXT,                       -- 保序 JSON 数组
    published_at    TEXT,                       -- YYYY-MM-DD
    updated_at      TEXT,
    type            TEXT,                       -- 规范值（IFACE-SOURCE §4）
    venue           TEXT,                       -- 主载体名，**只用于展示**
    venue_source_ids TEXT,                      -- 载体 source id 集合（JSON 数组，ADR-0060）
    language        TEXT,
    title_key       TEXT,                       -- 派生缓存，永不进唯一约束
    first_author_surname TEXT,
    cited_by_count  INTEGER,
    counts_by_year  TEXT,
    fwci            REAL,
    citation_normalized_percentile REAL,
    is_retracted    INTEGER,
    oa_status       TEXT,
    pdf_url         TEXT,
    metadata_conflict INTEGER,
    refreshed_at    TEXT,
    seen_count      INTEGER NOT NULL DEFAULT 1,
    last_seen_at    TEXT,
    status          TEXT NOT NULL DEFAULT 'live',   -- live / tombstone
    UNIQUE (source, source_id)
);

-- §3.2 record_ident：标识别名
-- kind 区分「事实」与「声明」（ADR-0064 决策 2）：来源自带、可当事实用的写 ident；
-- sources 从 locations[] 取来的桥写 bridge。ident 先写者占位、不可改；
-- bridge 可被真正的持有者接管（接管在 group.ingest() 里校验后执行）。
CREATE TABLE IF NOT EXISTS record_ident (
    ns          TEXT NOT NULL,
    value       TEXT NOT NULL,
    record_uid  TEXT NOT NULL REFERENCES record(record_uid),
    kind        TEXT NOT NULL DEFAULT 'ident' CHECK (kind IN ('ident', 'bridge')),
    PRIMARY KEY (ns, value)
);
CREATE INDEX IF NOT EXISTS idx_record_ident_record ON record_ident(record_uid);

-- §3.3 work：分组
CREATE TABLE IF NOT EXISTS work (
    work_key    TEXT PRIMARY KEY,
    merged_into TEXT REFERENCES work(work_key)
);

-- §3.4 work_member：成员关系
CREATE TABLE IF NOT EXISTS work_member (
    record_uid  TEXT PRIMARY KEY REFERENCES record(record_uid),
    work_key    TEXT NOT NULL REFERENCES work(work_key),
    role        TEXT NOT NULL                   -- primary / preprint / published / duplicate
);
CREATE INDEX IF NOT EXISTS idx_work_member_work ON work_member(work_key);

-- §3.5 candidate：候选池（行存在本身即事实，没有状态列）
CREATE TABLE IF NOT EXISTS candidate (
    work_key        TEXT PRIMARY KEY REFERENCES work(work_key),
    first_run_id    INTEGER REFERENCES run(run_id),
    score           REAL,
    grade           TEXT,
    reasons         TEXT,
    dismissed_at    TEXT,
    dismissed_until TEXT
);

-- §3.6 derived_content：派生文本缓存键（不装文本）
CREATE TABLE IF NOT EXISTS derived_content (
    content_hash    TEXT NOT NULL,
    kind            TEXT NOT NULL,
    lang            TEXT NOT NULL,
    model_id        TEXT NOT NULL DEFAULT '',
    prompt_version  TEXT NOT NULL DEFAULT '',
    terms_version   TEXT NOT NULL DEFAULT '',
    sha256          TEXT NOT NULL,
    PRIMARY KEY (content_hash, kind, lang, model_id, prompt_version, terms_version)
);

-- §3.7 artifact：产物溯源
CREATE TABLE IF NOT EXISTS artifact (
    record_uid      TEXT NOT NULL REFERENCES record(record_uid),
    kind            TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    converter       TEXT,
    converter_version TEXT,
    PRIMARY KEY (record_uid, kind, sha256)
);
CREATE INDEX IF NOT EXISTS idx_artifact_sha ON artifact(sha256);

-- §3.8 run：运行账本（run_id 是唯一的非派生标识）
CREATE TABLE IF NOT EXISTS run (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_hash       TEXT,
    query_name      TEXT,
    refreshed       INTEGER NOT NULL DEFAULT 0,
    window_from     TEXT,
    window_to       TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'running',   -- running / ok / failed
    requests        INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0.0,
    cost_basis      TEXT,
    counts          TEXT,
    responses       TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_spec ON run(spec_hash, refreshed, status);

-- §3.9 dup_suspect：L2 待确认登记
CREATE TABLE IF NOT EXISTS dup_suspect (
    record_uid  TEXT NOT NULL REFERENCES record(record_uid),
    work_key    TEXT NOT NULL REFERENCES work(work_key),
    evidence    TEXT,
    detected_at TEXT NOT NULL,
    PRIMARY KEY (record_uid, work_key)
);

-- §5 检索面：FTS5 只覆盖 record 的英文原文（标题、摘要），默认 unicode61。
-- 用独立虚表（非 external-content）以避开触发器同步的边界情形；由 store.py 维护。
CREATE VIRTUAL TABLE IF NOT EXISTS record_fts USING fts5(
    record_uid UNINDEXED,
    title,
    abstract,
    tokenize = 'unicode61'
);

-- 本地筛选要用的辅助索引
CREATE INDEX IF NOT EXISTS idx_record_published ON record(published_at);
CREATE INDEX IF NOT EXISTS idx_record_title_key ON record(title_key);
CREATE INDEX IF NOT EXISTS idx_record_source ON record(source);
