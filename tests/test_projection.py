"""投影目录名与写盘对账（ADR-0068）。

四件事各一条：名字的形状与尾段反解、元数据变了要改名、合并后的空壳要清掉、
`--in body` 从尾段反解出 `work_key`。取件与缓存本身在 `test_download.py`。
"""

from __future__ import annotations

import os
import stat

from academic_search import config, identity, paths, service, store
from academic_search.sources import SearchSpec
from academic_search.sources.http import HttpBytesResponse, HttpResponse

ARXIV_A = "2610.00001"
ARXIV_B = "2610.00002"
EPRINT = "https://arxiv.org/e-print/"

#: 一份像样的「LaTeX 源码包」——download 不解包，只当字节存。
BLOB = b"\x1f\x8b" + b"fake-eprint-bytes" * 8


class StubClient:
    """按 URL 回响应；未登记的 URL 当 404。"""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests = 0
        self.cost_usd = 0.0
        self.cost_basis = ""

    def get_bytes(self, url, *, headers=None, source="", raise_on_error=True, max_bytes=None):
        self.requests += 1
        status, payload = self.routes.get(url, (404, b""))
        return HttpBytesResponse(url, status, payload, {})

    def get(self, url, *, headers=None, source=""):
        raw = self.get_bytes(url, headers=headers, source=source)
        return HttpResponse(url, raw.status, raw.content.decode("utf-8", "replace"), raw.headers)


def add_record(root, source_id, *, title, authors=None, published_at="2026-10-01",
               version="v1", source="arxiv", work_key=None):
    """一条记录 + 一个 work；返回 (work_key, record_uid)。work_key 恒等于一作的 record_uid。"""
    uid = identity.record_uid(source, source_id)
    work_key = work_key or uid
    conn = store.connect(config.db_path(root))
    try:
        with store.write_tx(conn):
            store.insert_record(conn, {
                "record_uid": uid, "source": source, "source_id": source_id,
                "title": title, "authors": authors, "published_at": published_at,
                "type": "preprint", "source_version": version,
            })
            if source == "arxiv":
                store.upsert_ident(conn, "arxiv", source_id, uid)
            store.create_work(conn, work_key)
            store.add_member(conn, uid, work_key, "primary")
    finally:
        conn.close()
    return work_key, uid


def set_title(root, uid, title):
    conn = store.connect(config.db_path(root))
    try:
        with store.write_tx(conn):
            store.update_record(conn, uid, {"title": title})
    finally:
        conn.close()


def test_projection_dir_is_readable_and_ends_with_the_work_key(tmp_path):
    (tmp_path / "config").mkdir()
    key, _ = add_record(tmp_path, ARXIV_A, title="Sky World Models: A Study",
                        authors=["Jane Doe"])
    client = StubClient({EPRINT + ARXIV_A + "v1": (200, BLOB)})

    outcome = service.run_download(tmp_path, key, client=client)

    name = "2026-Doe-Sky-World-Models-A-Study-" + key
    target = tmp_path / "works" / name
    assert target.is_dir()
    assert identity.work_key_of(name) == key
    assert outcome.artifacts[0]["work_path"] == str(target / "source.tar.gz")


def test_projection_renames_when_the_title_changes(tmp_path):
    """元数据一刷新名字就变：改名是 rename，不搬字节、也不重新取件。"""
    (tmp_path / "config").mkdir()
    key, uid = add_record(tmp_path, ARXIV_A, title="Old Title", authors=["Jane Doe"])
    client = StubClient({EPRINT + ARXIV_A + "v1": (200, BLOB)})
    service.run_download(tmp_path, key, client=client)
    old = tmp_path / "works" / ("2026-Doe-Old-Title-" + key)
    assert old.is_dir()
    entity = paths.byte_path(tmp_path, paths.sha256_bytes(BLOB))

    set_title(tmp_path, uid, "New Title")
    outcome = service.run_download(tmp_path, key, client=client)

    assert outcome.network is False  # 版本没变：改名不该连带一次取件
    new = tmp_path / "works" / ("2026-Doe-New-Title-" + key)
    assert new.is_dir() and not old.exists()
    projected = new / "source.tar.gz"
    assert os.stat(projected).st_ino == os.stat(entity).st_ino
    assert outcome.artifacts[0]["work_path"] == str(projected)


def test_projection_keeps_the_old_dir_when_the_rename_fails(tmp_path):
    """改名失败不阻断（决策 5）：沿用现有目录，投影照写。"""
    (tmp_path / "config").mkdir()
    key, uid = add_record(tmp_path, ARXIV_A, title="Old Title", authors=["Jane Doe"])
    client = StubClient({EPRINT + ARXIV_A + "v1": (200, BLOB)})
    service.run_download(tmp_path, key, client=client)
    old = tmp_path / "works" / ("2026-Doe-Old-Title-" + key)
    blocker = tmp_path / "works" / ("2026-Doe-New-Title-" + key)
    blocker.write_text("占住这个名字", encoding="utf-8")  # rename 会失败

    set_title(tmp_path, uid, "New Title")
    outcome = service.run_download(tmp_path, key, client=client)

    assert old.is_dir()
    assert outcome.artifacts[0]["work_path"] == str(old / "source.tar.gz")


def test_projection_removes_a_merged_shell_dir(tmp_path):
    (tmp_path / "config").mkdir()
    key, _ = add_record(tmp_path, ARXIV_A, title="Keep Me", authors=["Jane Doe"])
    # 被合并的那条换个来源，免得 _pick_record 选中它（它没有 arXiv id）
    drop, _ = add_record(tmp_path, "W2963403868", title="Dropped One", authors=["John Roe"],
                         source="openalex")
    shell = tmp_path / "works" / ("2026-Roe-Dropped-One-" + drop)
    shell.mkdir(parents=True)
    stale = shell / "source.tar.gz"
    stale.write_bytes(b"old projection")
    os.chmod(stale, stat.S_IREAD)  # 只读文件删不掉：顺带证明 remove_tree 会去只读

    conn = store.connect(config.db_path(tmp_path))
    try:
        with store.write_tx(conn):
            store.merge_works(conn, key, drop)
    finally:
        conn.close()

    client = StubClient({EPRINT + ARXIV_A + "v1": (200, BLOB)})
    service.run_download(tmp_path, key, client=client)

    assert not shell.exists()
    assert (tmp_path / "works" / ("2026-Doe-Keep-Me-" + key)).is_dir()


def test_body_scan_takes_the_work_key_from_the_tail(tmp_path):
    (tmp_path / "config").mkdir()
    key, _ = add_record(tmp_path, ARXIV_A, title="T", authors=["Jane Doe"])
    doc = tmp_path / "works" / ("2026-Doe-T-" + key) / "paper.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("a transformer baseline", encoding="utf-8")
    for junk in ("notes", "X-" + key.upper()):  # 手工目录、尾段不是小写 base32
        other = tmp_path / "works" / junk
        other.mkdir()
        (other / "paper.md").write_text("a transformer baseline", encoding="utf-8")

    conn = store.connect(config.db_path(tmp_path))
    try:
        found = service._body_work_keys(conn, tmp_path, SearchSpec(any_of=["transformer"]))
    finally:
        conn.close()
    assert found == [key]
