"""group：L0/L1/L2、合并重定向、硬排除与 work 聚合。"""

from __future__ import annotations

import pytest

from academic_search import group, store


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "data" / "sidecar.db")
    with store.write_tx(c):
        c.execute("INSERT INTO run (started_at, status) VALUES ('2026-01-01T00:00:00Z', 'ok')")
    yield c
    c.close()


def run_id(conn) -> int:
    return conn.execute("SELECT run_id FROM run ORDER BY run_id DESC LIMIT 1").fetchone()["run_id"]


def rec(source="openalex", source_id="W1", **over):
    row = {
        "source": source,
        "source_id": source_id,
        "title": "A Paper",
        "authors": ["Jane Doe"],
        "published_at": "2024-01-01",
        "type": "preprint",
    }
    row.update(over)
    return row


def ingest(conn, record, **kw):
    with store.write_tx(conn):
        return group.ingest(conn, record, run_id=run_id(conn), **kw)


def test_l0_join_by_shared_ident(conn):
    a = ingest(conn, rec(idents=[("doi", "10.1/x")]))
    b = ingest(conn, rec(source="arxiv", source_id="2401.00001", idents=[("arxiv", "2401.00001"), ("doi", "10.1/x")]))
    assert b.work_key == a.work_key
    assert b.dedup_level == "L0"
    assert b.new_candidate is False
    assert store.candidate_exists(conn, a.work_key)


def test_distinct_records_two_works(conn):
    a = ingest(conn, rec(source_id="W1"))
    b = ingest(conn, rec(source_id="W2", title="Different"))
    assert a.work_key != b.work_key


def test_l1_bridge_joins(conn):
    a = ingest(conn, rec(idents=[("doi", "10.1/x")]))
    b = ingest(
        conn,
        rec(source="arxiv", source_id="2401.00002", bridges=[("doi", "10.1/x")]),
    )
    assert b.work_key == a.work_key
    assert b.dedup_level == "L1"


def test_refresh_does_not_create_new_record(conn):
    a = ingest(conn, rec(idents=[("doi", "10.1/x")]))
    b = ingest(conn, rec(idents=[("doi", "10.1/x")]))
    assert b.new_record is False
    assert b.work_key == a.work_key
    assert b.record_uid == a.record_uid
    assert conn.execute("SELECT COUNT(*) AS n FROM record").fetchone()["n"] == 1
    assert store.get_record(conn, a.record_uid)["seen_count"] == 2


def test_multi_hit_merges_works(conn):
    a = ingest(conn, rec(source_id="W1", idents=[("doi", "10.1/x")]))
    c = ingest(conn, rec(source_id="W2", title="Other", idents=[("doi", "10.1/y")]))
    assert a.work_key != c.work_key
    d = ingest(
        conn,
        rec(source="arxiv", source_id="2401.00003", idents=[("doi", "10.1/x"), ("doi", "10.1/y")]),
    )
    assert store.resolve_work(conn, a.work_key) == d.work_key
    assert store.resolve_work(conn, c.work_key) == d.work_key
    assert store.member_work(conn, c.record_uid) == d.work_key


def test_retracted_excluded_from_pool(conn):
    r = ingest(conn, rec(is_retracted=1))
    assert r.excluded is True
    assert r.new_candidate is False
    assert not store.candidate_exists(conn, r.work_key)


def test_paratext_excluded(conn):
    r = ingest(conn, rec(type="paratext"))
    assert r.excluded is True


def test_venue_blacklist_is_normalized_containment(conn):
    r = ingest(conn, rec(type="article", venue="Journal of Predatory Press Inc."),
               blacklist=["predatory press"])
    assert r.excluded is True
    assert group.venue_blacklisted("Journal of Predatory Press", ["Predatory  Press"]) is True
    assert group.venue_blacklisted("ICRA", ["predatory"]) is False


def test_l2_registers_without_grouping(conn):
    a = ingest(conn, rec(source_id="W1"))
    b = ingest(conn, rec(source_id="W2"))
    assert a.work_key != b.work_key
    assert b.l2_hits == 1
    rows = conn.execute("SELECT * FROM dup_suspect").fetchall()
    assert len(rows) == 1
    agg = store.aggregate_work(conn, b.work_key)
    assert agg["dup_suspect"] is True


def test_l2_skipped_on_ident_conflict(conn):
    a = ingest(conn, rec(source_id="W1", idents=[("doi", "10.1/x")]))
    b = ingest(conn, rec(source_id="W2", idents=[("doi", "10.1/y")]))
    assert a.work_key != b.work_key
    assert conn.execute("SELECT COUNT(*) AS n FROM dup_suspect").fetchone()["n"] == 0


def test_aggregate_prefers_published_and_earliest_date(conn):
    pub = ingest(conn, rec(source_id="W1", type="article", venue="ICRA",
                           title="Formal", published_at="2024-06-01", idents=[("doi", "10.1/x")]))
    with store.write_tx(conn):  # 预印本来自后一次 run，首领仍是先见到的正式版
        conn.execute("INSERT INTO run (started_at, status) VALUES ('2026-01-02T00:00:00Z', 'ok')")
    pre = ingest(conn, rec(source="arxiv", source_id="2401.00004", type="preprint",
                           title="Preprint", published_at="2024-01-01", idents=[("doi", "10.1/x")]))
    assert pre.work_key == pub.work_key
    agg = store.aggregate_work(conn, pub.work_key)
    assert agg["title"] == "Formal"          # published 记录的元数据
    assert agg["published_at"] == "2024-01-01"  # 组内最早主日期
    assert agg["venue"] == "ICRA"
    assert {s["source"] for s in agg["sources"]} == {"openalex", "arxiv"}
    assert agg["doi"] == "10.1/x"
    assert agg["first_seen"] == "2026-01-01"


def test_aggregate_retraction_is_or(conn):
    a = ingest(conn, rec(source_id="W1", idents=[("doi", "10.1/x")], is_retracted=1))
    ingest(conn, rec(source="arxiv", source_id="2401.00005", idents=[("doi", "10.1/x")]))
    agg = store.aggregate_work(conn, a.work_key)
    assert agg["is_retracted"] is True


def test_aggregate_abstract_prefers_published_over_fidelity(conn):
    """版本层级压过文本保真度：正式版 Crossref 盖过预印本 arXiv（ADR-0058 决策 4）。"""
    pre = ingest(conn, rec(source="arxiv", source_id="2401.00006", type="preprint",
                           abstract="ARXIV NATIVE", idents=[("doi", "10.1/z")]))
    pub = ingest(conn, rec(source="crossref", source_id="10.1/z", type="article",
                           abstract="PUBLISHED TEXT", idents=[("doi", "10.1/z")]))
    assert pre.work_key == pub.work_key
    agg = store.aggregate_work(conn, pre.work_key)
    assert agg["abstract"] == "PUBLISHED TEXT"


def test_aggregate_abstract_fidelity_within_tier(conn):
    """同层级内按文本保真度：Crossref 原生盖过 OpenAlex 还原（ADR-0058 决策 4）。"""
    a = ingest(conn, rec(source="openalex", source_id="W9", type="preprint",
                         abstract="RESTORED", idents=[("doi", "10.1/w")]))
    b = ingest(conn, rec(source="crossref", source_id="10.1/w", type="preprint",
                         abstract="NATIVE", idents=[("doi", "10.1/w")]))
    assert a.work_key == b.work_key
    agg = store.aggregate_work(conn, a.work_key)
    assert agg["abstract"] == "NATIVE"


def test_aggregate_abstract_none_when_all_empty(conn):
    a = ingest(conn, rec(source_id="W10", abstract=None))
    agg = store.aggregate_work(conn, a.work_key)
    assert agg["abstract"] is None


def test_aggregate_missing_work_returns_none(conn):
    assert store.aggregate_work(conn, "nope") is None
