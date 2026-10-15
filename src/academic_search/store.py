"""sidecar SQLite：连接、迁移、写锁与全部 SQL。

契约：docs/接口/数据模型.md（语义）、docs/接口/存储与迁移.md（不变量）。
本模块不拼 bytes/ / works/ 路径（DEV-CONV §4）。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from . import envelope

#: sidecar 整库版本，与对外的 envelope.SCHEMA_VERSION 是两件事。
#: 2：`record.venue_source_ids`（ADR-0060）、`record_ident.kind`（ADR-0064）。
SCHEMA_VERSION = 2

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: 逐版迁移。**只加列**（IFACE-DATA §6：不改唯一键、不改 record_uid 的派生规则）。
#: 新库由 schema.sql 一次建全，因此每条都先查 `PRAGMA table_info` 再决定加不加。
_MIGRATIONS: dict[int, tuple[tuple[str, str, str], ...]] = {
    2: (
        ("record", "venue_source_ids", "venue_source_ids TEXT"),
        ("record_ident", "kind", "kind TEXT NOT NULL DEFAULT 'ident'"),
    ),
}


def utcnow() -> str:
    """当前 UTC 时刻，ISO-8601 带 Z（库里时刻唯一格式）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: str | Path, *, create: bool = True) -> sqlite3.Connection:
    """打开（必要时创建）sidecar。

    写事务由 write_tx 用 BEGIN IMMEDIATE 显式管理，故连接处于 autocommit；
    busy_timeout=0 让拿不到锁立即失败（ADR-0055 要求不排队）。
    """
    path = Path(db_path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 0")
    ensure_schema(conn)
    return conn


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(" + table + ")")}
    if column not in existing:
        conn.execute("ALTER TABLE " + table + " ADD COLUMN " + ddl)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建表 + 逐版迁移；版本号由 PRAGMA user_version 承载。"""
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise envelope.EnvelopeError(
            envelope.IO_ERROR,
            "sidecar schema_version=" + str(current) + "，本实现只支持到 " + str(SCHEMA_VERSION),
        )
    for version in sorted(_MIGRATIONS):
        if current >= version:
            continue
        for table, column, ddl in _MIGRATIONS[version]:
            _add_column(conn, table, column, ddl)
        current = version
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        conn.execute("PRAGMA user_version = " + str(SCHEMA_VERSION))


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """写事务：BEGIN IMMEDIATE 先取锁；拿不到即 busy 退出，不排队。"""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise envelope.EnvelopeError(envelope.BUSY, "写锁被占用，另一个 run 正在运行") from exc
        raise
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# --------------------------------------------------------------------------
# run 账本
# --------------------------------------------------------------------------

def start_run(
    conn: sqlite3.Connection,
    *,
    spec_hash: str | None,
    query_name: str | None,
    refreshed: bool,
    window_from: str | None,
    window_to: str | None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO run (spec_hash, query_name, refreshed, window_from, window_to,
                         started_at, status)
        VALUES (?, ?, ?, ?, ?, ?, 'running')
        """,
        (spec_hash, query_name, 1 if refreshed else 0, window_from, window_to, utcnow()),
    )
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    requests: int,
    cost_usd: float,
    cost_basis: str | None,
    counts: Mapping | None,
    responses: Sequence | None = None,
    last_success_watermark: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE run
           SET finished_at = ?, status = ?, requests = ?, cost_usd = ?, cost_basis = ?,
               counts = ?, responses = ?, last_success_watermark = ?
         WHERE run_id = ?
        """,
        (
            utcnow(),
            status,
            requests,
            cost_usd,
            cost_basis,
            json.dumps(counts, ensure_ascii=False) if counts is not None else None,
            json.dumps(responses, ensure_ascii=False) if responses is not None else None,
            last_success_watermark,
            run_id,
        ),
    )


def last_refresh_at(conn: sqlite3.Connection, spec_hash: str) -> str | None:
    """同一检索式最近一次真正联网成功的 run 的 started_at。"""
    row = conn.execute(
        """
        SELECT started_at FROM run
         WHERE spec_hash = ? AND refreshed = 1 AND status = 'ok'
         ORDER BY run_id DESC LIMIT 1
        """,
        (spec_hash,),
    ).fetchone()
    return row["started_at"] if row else None


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM run WHERE run_id = ?", (run_id,)).fetchone()


# --------------------------------------------------------------------------
# record / record_ident
# --------------------------------------------------------------------------

#: record 里由来源提供的书目与信号列；刷新时逐列覆盖（取到的才写）。
#: 存 JSON 文本的书目/信号列。
_JSON_COLUMNS = frozenset({"authors", "counts_by_year", "venue_source_ids"})

RECORD_FIELDS = (
    "title",
    "abstract",
    "authors",
    "published_at",
    "updated_at",
    "type",
    "venue",
    "venue_source_ids",
    "language",
    "title_key",
    "first_author_surname",
    "cited_by_count",
    "counts_by_year",
    "fwci",
    "citation_normalized_percentile",
    "is_retracted",
    "oa_status",
    "pdf_url",
    "metadata_conflict",
)


def insert_record(conn: sqlite3.Connection, row: Mapping) -> None:
    """新记录落库；bookskeeping 由调用方给 first_run_id / last_seen_at。"""
    cols = ["record_uid", "source", "source_id", "first_run_id"] + list(RECORD_FIELDS)
    cols += ["seen_count", "last_seen_at", "refreshed_at", "status"]
    values = []
    for c in cols:
        value = row.get(c)
        if c == "seen_count" and value is None:
            value = 1
        elif c == "status" and value is None:
            value = "live"
        elif c in _JSON_COLUMNS and value is not None and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        values.append(value)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        "INSERT INTO record (" + ", ".join(cols) + ") VALUES (" + placeholders + ")",
        values,
    )
    _sync_fts(conn, row["record_uid"], row.get("title"), row.get("abstract"))


def update_record(conn: sqlite3.Connection, record_uid: str, fields: Mapping) -> None:
    """刷新：只写非 None 的列，绝不用 NULL 覆盖已有值（IFACE-SOURCE §7）。

    空的 JSON 列（`authors: []` / `venue_source_ids: []`）同样视为「这次取不到」，
    不覆盖——载体集合被一次缺 `locations[]` 的响应清空，会让已有记录在带名单的
    查询里判不出来（ADR-0060 代价 1）。
    """
    updates = {k: v for k, v in fields.items() if v is not None and k in RECORD_FIELDS}
    for key in list(updates):
        if key in _JSON_COLUMNS:
            if not updates[key]:
                del updates[key]
                continue
            if not isinstance(updates[key], str):
                updates[key] = json.dumps(updates[key], ensure_ascii=False)
    if updates:
        assignments = ", ".join(k + " = ?" for k in updates)
        conn.execute(
            "UPDATE record SET " + assignments + " WHERE record_uid = ?",
            list(updates.values()) + [record_uid],
        )
    if "title" in updates or "abstract" in updates:
        row = conn.execute(
            "SELECT title, abstract FROM record WHERE record_uid = ?", (record_uid,)
        ).fetchone()
        _sync_fts(conn, record_uid, row["title"], row["abstract"])


def touch_seen(conn: sqlite3.Connection, record_uid: str, refreshed: bool = True) -> None:
    """一次 run 里再次见到同一记录。"""
    now = utcnow()
    if refreshed:
        conn.execute(
            """
            UPDATE record
               SET seen_count = seen_count + 1, last_seen_at = ?, refreshed_at = ?
             WHERE record_uid = ?
            """,
            (now, now, record_uid),
        )
    else:
        conn.execute(
            "UPDATE record SET seen_count = seen_count + 1, last_seen_at = ? WHERE record_uid = ?",
            (now, record_uid),
        )


def get_record(conn: sqlite3.Connection, record_uid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM record WHERE record_uid = ?", (record_uid,)).fetchone()


def get_record_by_source(
    conn: sqlite3.Connection, source: str, source_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM record WHERE source = ? AND source_id = ?", (source, source_id)
    ).fetchone()


def record_uids_by_ident(conn: sqlite3.Connection, ns: str, value: str) -> list[str]:
    rows = conn.execute(
        "SELECT record_uid FROM record_ident WHERE ns = ? AND value = ?", (ns, value)
    ).fetchall()
    return [r["record_uid"] for r in rows]


def ident_row(conn: sqlite3.Connection, ns: str, value: str) -> sqlite3.Row | None:
    """该别名的持有行（含 `record_uid` 与 `kind`）；没有则 None。"""
    return conn.execute(
        "SELECT ns, value, record_uid, kind FROM record_ident WHERE ns = ? AND value = ?",
        (ns, value),
    ).fetchone()


def upsert_ident(
    conn: sqlite3.Connection, ns: str, value: str, record_uid: str, *, kind: str = "ident"
) -> bool:
    """登记别名；(ns,value) 已被别人持有则不动并返回 False。

    「先写者占位、后来者不动」此后只对 `ident` 成立：`bridge` 行可被真正的持有者
    接管，接管由 `group.ingest()` 校验内容后调 `take_over_ident()` 完成（ADR-0064）。
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO record_ident (ns, value, record_uid, kind) VALUES (?, ?, ?, ?)",
        (ns, value, record_uid, kind),
    )
    return cur.rowcount > 0


def take_over_ident(conn: sqlite3.Connection, ns: str, value: str, record_uid: str) -> None:
    """把别名移交给 `record_uid` 并提升为 `ident`（ADR-0064 决策 3）。"""
    conn.execute(
        "UPDATE record_ident SET record_uid = ?, kind = 'ident' WHERE ns = ? AND value = ?",
        (record_uid, ns, value),
    )


def mark_metadata_conflict(conn: sqlite3.Connection, record_uid: str) -> None:
    """标 `metadata_conflict`（DESIGN §4.1：冲突不静默采用、不归组）。"""
    conn.execute("UPDATE record SET metadata_conflict = 1 WHERE record_uid = ?", (record_uid,))


def idents_of(conn: sqlite3.Connection, record_uid: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT ns, value FROM record_ident WHERE record_uid = ? ORDER BY ns, value",
        (record_uid,),
    ).fetchall()


def search_fts(conn: sqlite3.Connection, query: str, limit: int) -> list[str]:
    """FTS5 查询，返回命中的 record_uid（按 bm25 排序）。"""
    rows = conn.execute(
        """
        SELECT record_uid FROM record_fts
         WHERE record_fts MATCH ?
         ORDER BY bm25(record_fts)
         LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    return [r["record_uid"] for r in rows]


def _sync_fts(conn: sqlite3.Connection, record_uid: str, title, abstract) -> None:
    conn.execute("DELETE FROM record_fts WHERE record_uid = ?", (record_uid,))
    conn.execute(
        "INSERT INTO record_fts (record_uid, title, abstract) VALUES (?, ?, ?)",
        (record_uid, title or "", abstract or ""),
    )


# --------------------------------------------------------------------------
# work / work_member / candidate / dup_suspect
# --------------------------------------------------------------------------

def create_work(conn: sqlite3.Connection, work_key: str) -> None:
    conn.execute("INSERT OR IGNORE INTO work (work_key) VALUES (?)", (work_key,))


def work_exists(conn: sqlite3.Connection, work_key: str) -> bool:
    return conn.execute("SELECT 1 FROM work WHERE work_key = ?", (work_key,)).fetchone() is not None


def add_member(conn: sqlite3.Connection, record_uid: str, work_key: str, role: str) -> None:
    conn.execute(
        """
        INSERT INTO work_member (record_uid, work_key, role) VALUES (?, ?, ?)
        ON CONFLICT(record_uid) DO UPDATE SET work_key = excluded.work_key, role = excluded.role
        """,
        (record_uid, work_key, role),
    )


def member_work(conn: sqlite3.Connection, record_uid: str) -> str | None:
    row = conn.execute(
        "SELECT work_key FROM work_member WHERE record_uid = ?", (record_uid,)
    ).fetchone()
    return row["work_key"] if row else None


def members_of(conn: sqlite3.Connection, work_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM work_member WHERE work_key = ? ORDER BY record_uid", (work_key,)
    ).fetchall()


def candidate_exists(conn: sqlite3.Connection, work_key: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM candidate WHERE work_key = ?", (work_key,)).fetchone()
        is not None
    )


def add_candidate(conn: sqlite3.Connection, work_key: str, run_id: int | None) -> bool:
    """首次入池才写；已入池返回 False（「行存在本身就是事实」）。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO candidate (work_key, first_run_id) VALUES (?, ?)",
        (work_key, run_id),
    )
    return cur.rowcount > 0


def add_dup_suspect(
    conn: sqlite3.Connection, record_uid: str, work_key: str, evidence: Mapping
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO dup_suspect (record_uid, work_key, evidence, detected_at)
        VALUES (?, ?, ?, ?)
        """,
        (record_uid, work_key, json.dumps(evidence, ensure_ascii=False), utcnow()),
    )


def l2_candidates(
    conn: sqlite3.Connection, title_key: str, surname: str, year_from: int, year_to: int
) -> list[sqlite3.Row]:
    """L2 召回：title_key + 一作姓氏 + 年份窗口。只召回，不归组。"""
    return conn.execute(
        """
        SELECT r.record_uid, r.source, r.source_id, r.published_at,
               wm.work_key, w.merged_into
          FROM record r
          JOIN work_member wm ON wm.record_uid = r.record_uid
          JOIN work w ON w.work_key = wm.work_key
         WHERE r.title_key = ?
           AND r.first_author_surname = ?
           AND substr(r.published_at, 1, 4) >= ?
           AND substr(r.published_at, 1, 4) <= ?
        """,
        (title_key, surname, str(year_from), str(year_to)),
    ).fetchall()


def resolve_work(conn: sqlite3.Connection, work_key: str) -> str:
    """一跳解析 merged_into 重定向链。"""
    seen = set()
    current = work_key
    while True:
        row = conn.execute(
            "SELECT merged_into FROM work WHERE work_key = ?", (current,)
        ).fetchone()
        if row is None or row["merged_into"] is None:
            return current
        if current in seen:
            return current
        seen.add(current)
        current = row["merged_into"]


def merge_works(conn: sqlite3.Connection, keep: str, drop: str) -> None:
    """合并：不改 work_key、不删记录，只重定向成员与写 merged_into。"""
    conn.execute("UPDATE work_member SET work_key = ? WHERE work_key = ?", (keep, drop))
    conn.execute("UPDATE work SET merged_into = ? WHERE work_key = ?", (keep, drop))


# --------------------------------------------------------------------------
# artifact / derived_content（M2 使用，DDL 先就位）
# --------------------------------------------------------------------------

def add_artifact(
    conn: sqlite3.Connection,
    record_uid: str,
    kind: str,
    sha256: str,
    converter: str | None,
    converter_version: str | None,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO artifact (record_uid, kind, sha256, converter, converter_version)
        VALUES (?, ?, ?, ?, ?)
        """,
        (record_uid, kind, sha256, converter, converter_version),
    )


def artifact_of(
    conn: sqlite3.Connection, record_uid: str, kind: str, converter_version: str | None = None
) -> sqlite3.Row | None:
    if converter_version is None:
        return conn.execute(
            """
            SELECT * FROM artifact WHERE record_uid = ? AND kind = ?
             ORDER BY rowid DESC LIMIT 1
            """,
            (record_uid, kind),
        ).fetchone()
    return conn.execute(
        """
        SELECT * FROM artifact WHERE record_uid = ? AND kind = ? AND converter_version = ?
         ORDER BY rowid DESC LIMIT 1
        """,
        (record_uid, kind, converter_version),
    ).fetchone()


def add_derived(
    conn: sqlite3.Connection,
    *,
    content_hash: str,
    kind: str,
    lang: str,
    sha256: str,
    model_id: str = "",
    prompt_version: str = "",
    terms_version: str = "",
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO derived_content
            (content_hash, kind, lang, model_id, prompt_version, terms_version, sha256)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (content_hash, kind, lang, model_id, prompt_version, terms_version, sha256),
    )


def derived_get(conn: sqlite3.Connection, key: Sequence[str]) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM derived_content
         WHERE content_hash = ? AND kind = ? AND lang = ?
           AND model_id = ? AND prompt_version = ? AND terms_version = ?
        """,
        tuple(key),
    ).fetchone()


# --------------------------------------------------------------------------
# work 级聚合（IFACE-SOURCE §5）
# --------------------------------------------------------------------------

#: 摘要取值的文本保真度排序（ADR-0058 决策 4）：数字小者优先。
ABSTRACT_FIDELITY = {"arxiv": 0, "crossref": 1, "openalex": 2}


def work_abstract(members: Sequence[sqlite3.Row]) -> str | None:
    """按 ADR-0058 决策 4 的两级规则取摘要：版本层级 → 文本保真度。

    第一级：非预印本记录（含 role=published）优先于预印本记录；
    第二级：同层级内 arxiv 原生 > crossref 原生 > openalex 还原。
    取第一个非空即停；都没有返回 None。
    """

    def rank(member: sqlite3.Row) -> tuple[int, int]:
        preprint = member["role"] == "preprint" or member["type"] == "preprint"
        return (1 if preprint else 0, ABSTRACT_FIDELITY.get(member["source"], 9))

    for member in sorted(members, key=rank):
        value = member["abstract"]
        if value:
            return value
    return None


def work_leader(conn: sqlite3.Connection, work_key: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT r.* FROM work_member wm
          JOIN record r ON r.record_uid = wm.record_uid
         WHERE wm.work_key = ? AND wm.role = 'primary'
         ORDER BY wm.record_uid LIMIT 1
        """,
        (work_key,),
    ).fetchone()


def member_records(conn: sqlite3.Connection, work_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT wm.role, r.*, run.started_at AS run_started
          FROM work_member wm
          JOIN record r ON r.record_uid = wm.record_uid
          LEFT JOIN run ON run.run_id = r.first_run_id
         WHERE wm.work_key = ?
         ORDER BY COALESCE(r.first_run_id, 0), r.record_uid
        """,
        (work_key,),
    ).fetchall()


def aggregate_work(conn: sqlite3.Connection, work_key: str) -> dict | None:
    """按 IFACE-SOURCE §5 合成结果条目；work 不存在返回 None。"""
    members = member_records(conn, work_key)
    if not members:
        return None
    published = [m for m in members if m["role"] == "published"]
    display = published[0] if published else members[0]

    def _max_value(col: str):
        values = [m[col] for m in members if m[col] is not None]
        return max(values) if values else None

    def _non_null(col: str):
        for row in published + members:
            if row[col] is not None:
                return row[col]
        return None

    def _date(text: str | None) -> str | None:
        return text[:10] if text else None

    rows = conn.execute(
        """
        SELECT ri.ns, ri.value, ri.record_uid
          FROM record_ident ri
          JOIN work_member wm ON wm.record_uid = ri.record_uid
         WHERE wm.work_key = ?
         ORDER BY ri.ns, ri.value
        """,
        (work_key,),
    ).fetchall()
    idents: dict[str, list[str]] = {}
    for row in rows:
        idents.setdefault(row["ns"], []).append(row["value"])

    def _alias(ns: str) -> str | None:
        values = idents.get(ns)
        if not values:
            return None
        preferred = [
            r["value"] for r in rows if r["ns"] == ns and r["record_uid"] == display["record_uid"]
        ]
        return preferred[0] if preferred else values[0]

    sources = [
        {
            "source": m["source"],
            "source_id": m["source_id"],
            "first_seen": _date(m["run_started"]),
            "role": m["role"],
        }
        for m in members
    ]
    seen_dates = [d for d in (_date(m["run_started"]) for m in members) if d]
    last_dates = [d for d in (_date(m["last_seen_at"] or m["run_started"]) for m in members) if d]
    dup = conn.execute(
        """
        SELECT 1 FROM dup_suspect d
          JOIN work_member wm ON wm.record_uid = d.record_uid
         WHERE wm.work_key = ? LIMIT 1
        """,
        (work_key,),
    ).fetchone()
    published_dates = [m["published_at"] for m in members if m["published_at"]]
    return {
        "work_key": work_key,
        "title": display["title"],
        "abstract": work_abstract(members),
        "authors": json.loads(display["authors"]) if display["authors"] else [],
        "venue": _non_null("venue"),
        "language": _non_null("language"),
        "published_at": min(published_dates) if published_dates else None,
        "updated_at": max((m["updated_at"] for m in members if m["updated_at"]), default=None),
        "type": display["type"],
        "doi": _alias("doi"),
        "arxiv_id": _alias("arxiv"),
        "openalex_id": _alias("openalex"),
        "oa_status": _non_null("oa_status"),
        "pdf_url": _non_null("pdf_url"),
        "is_retracted": any(bool(m["is_retracted"]) for m in members),
        "cited_by_count": _max_value("cited_by_count"),
        "fwci": _max_value("fwci"),
        "sources": sources,
        "first_seen": min(seen_dates) if seen_dates else None,
        "last_seen": max(last_dates) if last_dates else None,
        "dup_suspect": dup is not None,
    }


def work_ids(conn: sqlite3.Connection) -> list[str]:
    return [r["work_key"] for r in conn.execute("SELECT work_key FROM work ORDER BY work_key")]
