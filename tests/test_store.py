"""store：迁移、写锁、幂等键与刷新不覆盖 NULL。"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from academic_search import envelope, store

_SCHEMA_SQL = pathlib.Path(store.__file__).with_name("schema.sql")


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "data" / "sidecar.db")
    yield c
    c.close()


def test_schema_version_set(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION


def test_all_v1_tables_exist(conn):
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    }
    for t in [
        "record", "record_ident", "work", "work_member", "candidate",
        "derived_content", "artifact", "run", "dup_suspect", "record_fts",
    ]:
        assert t in names, t


def test_reopen_is_idempotent(tmp_path):
    p = tmp_path / "sidecar.db"
    c1 = store.connect(p)
    c1.close()
    c2 = store.connect(p)  # ensure_schema 再跑一次不应报错
    c2.close()


def test_write_tx_commits(conn):
    with store.write_tx(conn):
        store.start_run(
            conn, spec_hash="h", query_name=None, refreshed=True,
            window_from="2026-01-01", window_to="2026-02-01",
        )
    assert conn.execute("SELECT COUNT(*) AS n FROM run").fetchone()["n"] == 1


def test_write_tx_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError):
        with store.write_tx(conn):
            store.start_run(
                conn, spec_hash="h", query_name=None, refreshed=False,
                window_from=None, window_to=None,
            )
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) AS n FROM run").fetchone()["n"] == 0


def test_busy_when_lock_held(tmp_path):
    p = tmp_path / "sidecar.db"
    a = store.connect(p)
    b = store.connect(p)
    try:
        a.execute("BEGIN IMMEDIATE")
        with pytest.raises(envelope.EnvelopeError) as ei:
            with store.write_tx(b):
                pass
        assert ei.value.code == envelope.BUSY
    finally:
        a.execute("ROLLBACK")
        a.close()
        b.close()


def _record(uid="u1", **over):
    row = {"record_uid": uid, "source": "openalex", "source_id": "w1", "title": "T",
           "abstract": "A"}
    row.update(over)
    return row


def test_insert_and_ident_unique(conn):
    with store.write_tx(conn):
        store.start_run(conn, spec_hash="h", query_name=None, refreshed=False,
                        window_from=None, window_to=None)
        store.insert_record(conn, _record())
        assert store.upsert_ident(conn, "doi", "10.1/x", "u1") is True
        # 同一别名再登记给别人 -> 拒绝
        store.insert_record(conn, _record(uid="u2", source="doi", source_id="10.1/x"))
        assert store.upsert_ident(conn, "doi", "10.1/x", "u2") is False
    assert store.record_uids_by_ident(conn, "doi", "10.1/x") == ["u1"]


def test_refresh_does_not_overwrite_with_null(conn):
    with store.write_tx(conn):
        store.start_run(conn, spec_hash="h", query_name=None, refreshed=False,
                        window_from=None, window_to=None)
        store.insert_record(conn, _record(cited_by_count=5))
        store.update_record(conn, "u1", {"cited_by_count": None, "venue": "ICRA"})
    row = store.get_record(conn, "u1")
    assert row["cited_by_count"] == 5          # 取不到不覆盖
    assert row["venue"] == "ICRA"


def test_fts_search(conn):
    with store.write_tx(conn):
        store.start_run(conn, spec_hash="h", query_name=None, refreshed=False,
                        window_from=None, window_to=None)
        store.insert_record(conn, _record(uid="u1", title="World Models", abstract="UAV planning"))
        store.insert_record(conn, _record(uid="u2", source="arxiv", source_id="1",
                                          title="Cooking", abstract="recipes"))
    assert store.search_fts(conn, "world", 10) == ["u1"]


def test_fts_updated_on_refresh(conn):
    with store.write_tx(conn):
        store.start_run(conn, spec_hash="h", query_name=None, refreshed=False,
                        window_from=None, window_to=None)
        store.insert_record(conn, _record(uid="u1", title="Old", abstract="x"))
        store.update_record(conn, "u1", {"title": "New"})
    assert store.search_fts(conn, "old", 10) == []
    assert store.search_fts(conn, "new", 10) == ["u1"]


def test_candidate_idempotent(conn):
    with store.write_tx(conn):
        run_id = store.start_run(conn, spec_hash="h", query_name=None, refreshed=False,
                                 window_from=None, window_to=None)
        store.create_work(conn, "wk")
        assert store.add_candidate(conn, "wk", run_id) is True
        assert store.add_candidate(conn, "wk", run_id) is False


def test_merge_redirects(conn):
    with store.write_tx(conn):
        run_id = store.start_run(conn, spec_hash="h", query_name=None, refreshed=False,
                                 window_from=None, window_to=None)
        store.insert_record(conn, _record(uid="u1"))
        store.insert_record(conn, _record(uid="u2", source="arxiv", source_id="1"))
        store.create_work(conn, "wk1")
        store.create_work(conn, "wk2")
        store.add_member(conn, "u1", "wk1", "primary")
        store.add_member(conn, "u2", "wk2", "primary")
        store.merge_works(conn, "wk1", "wk2")
    assert store.member_work(conn, "u2") == "wk1"
    assert store.resolve_work(conn, "wk2") == "wk1"
    # 行不删
    assert store.work_exists(conn, "wk2")


def test_foreign_keys_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO work_member (record_uid, work_key, role) VALUES ('x','y','primary')")


def test_artifact_keeps_the_new_columns_and_is_idempotent(conn):
    with store.write_tx(conn):
        store.start_run(conn, spec_hash="h", query_name=None, refreshed=False,
                        window_from=None, window_to=None)
        store.insert_record(conn, _record(uid="u1"))
        store.add_artifact(conn, "u1", "source_html", "a" * 64, None, None,
                           source_version="v3", etag='"e"', last_modified="LM")
        # 同一份字节再登记一次：不新增行，空值不抹掉已有值
        store.add_artifact(conn, "u1", "source_html", "a" * 64, None, None)
    rows = conn.execute("SELECT * FROM artifact").fetchall()
    assert len(rows) == 1
    assert rows[0]["source_version"] == "v3"
    assert rows[0]["etag"] == '"e"' and rows[0]["last_modified"] == "LM"


def test_migration_adds_fulltext_version_columns_to_a_v3_database(tmp_path):
    """旧库（user_version=3）打开后升到 4：三列加好，存量行不受影响。"""
    db = tmp_path / "data" / "sidecar.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(db))
    # 先建全量 DDL，再把 v4 加的那几列拆掉，得到一个真实的 v3 库
    raw.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
    for table, column in (
        ("record", "source_version"),
        ("artifact", "source_version"),
        ("artifact", "etag"),
        ("artifact", "last_modified"),
    ):
        raw.execute("ALTER TABLE " + table + " DROP COLUMN " + column)
    raw.execute("INSERT INTO record (record_uid, source, source_id, title)"
                " VALUES ('u1', 'arxiv', '2610.00001', 'T')")
    raw.execute("INSERT INTO artifact VALUES ('u1', 'source_tar_gz', '%s', NULL, NULL)" % ("b" * 64))
    raw.execute("PRAGMA user_version = 3")
    raw.commit()
    raw.close()

    conn = store.connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
        record_cols = {row["name"] for row in conn.execute("PRAGMA table_info(record)")}
        artifact_cols = {row["name"] for row in conn.execute("PRAGMA table_info(artifact)")}
        assert "source_version" in record_cols
        assert {"source_version", "etag", "last_modified"} <= artifact_cols
        assert conn.execute("SELECT COUNT(*) AS n FROM artifact").fetchone()["n"] == 1
        with store.write_tx(conn):
            store.add_artifact(conn, "u1", "source_pdf", "c" * 64, None, None)
    finally:
        conn.close()


def test_connect_reports_data_not_writable(tmp_path):
    """数据目录建不出来时给 data_not_writable，而不是 SQLite 的 internal_error。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(envelope.EnvelopeError) as excinfo:
        store.connect(blocker / "sidecar.db")
    assert excinfo.value.code == envelope.DATA_NOT_WRITABLE
    assert str(blocker) in excinfo.value.message


def test_connect_reports_readonly_sidecar(tmp_path):
    """目录可写、库文件只读：同样要报 data_not_writable，并指向那个文件。"""
    import os

    path = tmp_path / "data" / "sidecar.db"
    store.connect(path).close()
    os.chmod(path, 0o444)
    if os.access(path, os.W_OK):
        pytest.skip("当前用户对只读文件仍有写权限（如 root），这条用例不适用")
    try:
        with pytest.raises(envelope.EnvelopeError) as excinfo:
            store.connect(path)
        assert excinfo.value.code == envelope.DATA_NOT_WRITABLE
        assert str(path) in excinfo.value.message
    finally:
        os.chmod(path, 0o666)


def test_search_fts_applies_filters_before_the_limit(conn):
    """结构化过滤必须在 LIMIT 之前生效，否则预算会被无关记录吃掉（ADR-0070）。"""
    with store.write_tx(conn):
        store.insert_record(conn, {
            "record_uid": "r1", "source": "openalex", "source_id": "w1",
            "title": "alpha alpha alpha", "abstract": "",
        })
        store.insert_record(conn, {
            "record_uid": "r2", "source": "arxiv", "source_id": "a1",
            "title": "alpha", "abstract": "",
        })
    # 相关性上 r1 明显更高：不带过滤时第一条就是它
    assert store.search_fts(conn, '"alpha"', 1) == ["r1"]
    # 过滤进同一条 SQL 后，预算先给 arxiv，r2 不再被 r1 挤掉
    assert store.search_fts(
        conn, '"alpha"', 1, where=("r.source = ?",), params=("arxiv",)
    ) == ["r2"]
    assert len(store.search_fts(conn, '"alpha"', -1)) == 2


@pytest.mark.parametrize("counts", [None, {}, {"complete": False}])
def test_unproven_refresh_is_not_a_freshness_or_backfill_record(conn, counts):
    with store.write_tx(conn):
        run_id = store.start_run(conn, spec_hash="probe", query_name="alpha", refreshed=True,
                                 window_from=None, window_to=None)
        store.finish_run(conn, run_id, status="ok", requests=1, cost_usd=0,
                         cost_basis=None, counts=counts)
    assert store.last_refresh_at(conn, "probe") is None
    assert store.has_successful_query(conn, "alpha") is False

