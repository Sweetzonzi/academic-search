"""download：取原件 → bytes/ → artifact → works/ 投影（ADR-0067）。不联网。

验收对应 PLAN §2 的第一行与第四行：首次取件、二次零请求复用、条件 GET 的 304、
版本不等即重取、`--refresh false` 的本地优先，以及新登记的两个 `source_*` kind。
"""

from __future__ import annotations

import json
import os

import pytest

from academic_search import cli, config, envelope, identity, paths, service, store
from academic_search.sources import arxiv as arxiv_source
from academic_search.sources.http import HttpBytesResponse, HttpResponse

ARXIV_ID = "2610.00001"
EPRINT = "https://arxiv.org/e-print/" + ARXIV_ID
HTML = "https://arxiv.org/html/" + ARXIV_ID
PDF = "https://arxiv.org/pdf/" + ARXIV_ID
FEED = arxiv_source.build_url(arxiv_source.BASE, {"id_list": ARXIV_ID, "max_results": 1})

#: 只有一条 entry 的 Atom，用来喂「版本空缺时补一次元数据」（ADR-0067 决策 3）。
ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/{id}{version}</id>
    <title>T</title>
    <published>2026-10-01T00:00:00Z</published>
    <updated>2026-10-02T00:00:00Z</updated>
  </entry>
</feed>"""


def atom(version="v1", arxiv_id=ARXIV_ID):
    return ATOM.format(id=arxiv_id, version=version).encode("utf-8")

#: 一份像样的「LaTeX 源码包」——download 不解包，只当字节存。
BLOB = b"\x1f\x8b" + b"fake-eprint-bytes" * 8


class StubClient:
    """按 URL 回响应；未登记的 URL 当 404。记下每次请求的 URL 与请求头。"""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests = 0
        self.cost_usd = 0.0
        self.cost_basis = ""
        self.rate_remaining_usd = None
        self.calls: list[tuple[str, dict]] = []

    def get_bytes(self, url, *, headers=None, source="", raise_on_error=True, max_bytes=None):
        self.requests += 1
        self.calls.append((url, dict(headers or {})))
        if url not in self.routes:
            return HttpBytesResponse(url, 404, b"", {})
        status, payload = self.routes[url]
        return HttpBytesResponse(url, status, payload, {"ETag": '"a"', "Last-Modified": "LM"})

    def get(self, url, *, headers=None, source=""):
        raw = self.get_bytes(url, headers=headers, source=source)
        return HttpResponse(url, raw.status, raw.content.decode("utf-8", "replace"), raw.headers)


def route(payload=b"x", *, status=200):
    return (status, payload)


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return tmp_path


def make_work(root, *, version=None, arxiv_id=ARXIV_ID, source="arxiv"):
    uid = identity.record_uid(source, arxiv_id)
    conn = store.connect(config.db_path(root))
    try:
        with store.write_tx(conn):
            store.insert_record(conn, {
                "record_uid": uid, "source": source, "source_id": arxiv_id,
                "title": "T", "published_at": "2026-10-01", "type": "preprint",
                "source_version": version,
            })
            if source == "arxiv":
                store.upsert_ident(conn, "arxiv", arxiv_id, uid)
            store.create_work(conn, "wk1")
            store.add_member(conn, uid, "wk1", "primary")
    finally:
        conn.close()
    return uid


def set_version(root, uid, version):
    """元数据刷新把远端版本改掉（真实路径是 search / sync / lookup 重写 record）。"""
    conn = store.connect(config.db_path(root))
    try:
        with store.write_tx(conn):
            conn.execute("UPDATE record SET source_version = ? WHERE record_uid = ?", (version, uid))
    finally:
        conn.close()


def test_download_auto_fetches_then_reuses(root):
    uid = make_work(root, version="v3")
    client = StubClient({EPRINT + "v3": route(BLOB)})

    first = service.run_download(root, "wk1", client=client)
    assert first.network is True and first.requests == 1
    entry = first.artifacts[0]
    assert entry["kind"] == "source_tar_gz"
    assert entry["status"] == "fetched"
    assert entry["source_version"] == "v3"
    assert entry["size"] == len(BLOB)
    sha = entry["sha256"]
    entity = paths.byte_path(root, sha)
    assert entity.is_file() and entity.read_bytes() == BLOB

    projected = root / "works" / "2026-T-wk1" / "source.tar.gz"
    assert projected.is_file()
    assert os.stat(projected).st_ino == os.stat(entity).st_ino  # 投影是硬链接
    assert entry["work_path"] == str(projected)  # 载荷返回当前目录（ADR-0068 决策 8）

    second = service.run_download(root, "wk1", client=client)
    assert second.network is False
    assert client.requests == 1  # 版本相等即零请求
    assert [item["status"] for item in second.artifacts] == ["reused"]
    assert second.artifacts[0]["sha256"] == sha


def test_download_rebuilds_a_deleted_projection_without_the_network(root):
    """删掉 works/ 后重跑：投影按 bytes/ 重建，零请求（IFACE-STORE §4）。"""
    make_work(root, version="v3")
    client = StubClient({EPRINT + "v3": route(BLOB)})
    service.run_download(root, "wk1", client=client)

    projected = root / "works" / "2026-T-wk1" / "source.tar.gz"
    paths.make_writable(projected)
    projected.unlink()
    assert not projected.exists()

    outcome = service.run_download(root, "wk1", client=client)
    assert outcome.network is False and client.requests == 1
    assert outcome.artifacts[0]["status"] == "reused"
    assert projected.is_file() and projected.read_bytes() == BLOB


def test_download_records_the_run_ledger(root):
    make_work(root, version="v3")
    client = StubClient({EPRINT + "v3": route(BLOB)})
    service.run_download(root, "wk1", client=client)

    conn = store.connect(config.db_path(root))
    try:
        rows = conn.execute("SELECT * FROM run ORDER BY run_id").fetchall()
        artifacts = conn.execute("SELECT * FROM artifact").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["refreshed"] == 1 and rows[0]["requests"] == 1 and rows[0]["status"] == "ok"
    assert len(artifacts) == 1
    assert artifacts[0]["etag"] == '"a"' and artifacts[0]["last_modified"] == "LM"
    assert artifacts[0]["source_version"] == "v3"


def test_download_auto_falls_back_to_html(root):
    make_work(root, version="v3")
    client = StubClient({HTML + "v3": route(b"<html>hi</html>")})  # e-print 404

    outcome = service.run_download(root, "wk1", client=client)
    status = {(item["kind"], item["status"]) for item in outcome.artifacts}
    assert ("source_tar_gz", "failed") in status
    assert ("source_html", "fetched") in status
    assert (root / "works" / "2026-T-wk1" / "source.html").is_file()


def test_download_refresh_true_uses_validators_and_reuses_on_304(root):
    make_work(root, version="v3")
    client = StubClient({EPRINT + "v3": route(BLOB)})
    service.run_download(root, "wk1", client=client)

    # true 走**不带版本号**的 URL（逃生口：不怕 HTML 重渲染），并带上上次的验证器
    client.routes[EPRINT] = route(b"", status=304)
    outcome = service.run_download(root, "wk1", refresh="true", client=client)

    assert outcome.artifacts[0]["status"] == "reused"
    url, headers = client.calls[-1]
    assert url == EPRINT
    assert headers["If-None-Match"] == '"a"'
    assert headers["If-Modified-Since"] == "LM"


def test_download_refresh_true_stores_new_bytes_on_200(root):
    make_work(root, version="v3")
    client = StubClient({EPRINT + "v3": route(BLOB)})
    service.run_download(root, "wk1", client=client)

    client.routes[EPRINT] = route(b"re-rendered")
    outcome = service.run_download(root, "wk1", refresh="true", client=client)
    assert outcome.artifacts[0]["status"] == "fetched"
    assert outcome.artifacts[0]["sha256"] == paths.sha256_bytes(b"re-rendered")


def test_download_refetches_when_versions_differ(root):
    uid = make_work(root, version="v2")
    client = StubClient({EPRINT + "v2": route(b"old")})
    service.run_download(root, "wk1", client=client)

    set_version(root, uid, "v3")  # 远端升版
    client.routes[EPRINT + "v3"] = route(b"new")
    outcome = service.run_download(root, "wk1", client=client)

    entry = outcome.artifacts[0]
    assert entry["status"] == "fetched" and entry["source_version"] == "v3"
    assert entry["sha256"] == paths.sha256_bytes(b"new")
    assert entry["sha256"] != paths.sha256_bytes(b"old")


def test_download_learns_a_missing_version_first(root):
    """版本为空时补一次 feed：不补，过渡期每次 auto 都会把源码包整份重下。"""
    uid = make_work(root, version=None)
    client = StubClient({FEED: route(atom("v2")), EPRINT + "v2": route(BLOB)})
    outcome = service.run_download(root, "wk1", client=client)

    assert [url for url, _ in client.calls] == [FEED, EPRINT + "v2"]
    assert outcome.artifacts[0]["source_version"] == "v2"
    conn = store.connect(config.db_path(root))
    try:
        assert store.get_record(conn, uid)["source_version"] == "v2"
    finally:
        conn.close()


def test_download_falls_back_to_the_unversioned_url_when_the_feed_fails(root):
    make_work(root, version=None)
    client = StubClient({EPRINT: route(BLOB)})  # feed 未登记 → 404，学不到版本
    outcome = service.run_download(root, "wk1", client=client)
    assert client.calls[-1][0] == EPRINT
    assert outcome.artifacts[0]["source_version"] is None
    assert outcome.artifacts[0]["status"] == "fetched"


def test_download_refresh_false_reuses_the_local_copy(root):
    make_work(root, version="v2")
    client = StubClient({EPRINT + "v2": route(BLOB)})
    service.run_download(root, "wk1", client=client)

    set_version(root, identity.record_uid("arxiv", ARXIV_ID), "v9")
    outcome = service.run_download(root, "wk1", refresh="false", client=client)
    assert outcome.network is False
    assert [item["status"] for item in outcome.artifacts] == ["reused"]


def test_download_refresh_false_without_cache_is_projection_missing(root):
    make_work(root, version="v3")
    client = StubClient({})
    with pytest.raises(envelope.EnvelopeError) as excinfo:
        service.run_download(root, "wk1", refresh="false", client=client)
    assert excinfo.value.code == envelope.PROJECTION_MISSING
    assert client.requests == 0


def test_download_all_keeps_going_when_one_route_fails(root):
    make_work(root, version="v3")
    client = StubClient({EPRINT + "v3": route(BLOB), HTML + "v3": route(b"<html/>")})
    outcome = service.run_download(root, "wk1", source="all", client=client)

    status = {(item["kind"], item["status"]) for item in outcome.artifacts}
    assert ("source_tar_gz", "fetched") in status
    assert ("source_html", "fetched") in status
    assert ("source_pdf", "failed") in status  # 未登记 → 404
    assert (root / "works" / "2026-T-wk1" / "source.pdf").exists() is False


def test_download_pdf_is_its_own_kind(root):
    make_work(root, version="v3")
    client = StubClient({PDF + "v3": route(b"%PDF-1.7 bytes")})
    outcome = service.run_download(root, "wk1", source="pdf", client=client)

    entry = outcome.artifacts[0]
    assert entry["kind"] == "source_pdf" and entry["status"] == "fetched"
    assert (root / "works" / "2026-T-wk1" / "source.pdf").is_file()


def test_download_without_arxiv_record_is_source_error(root):
    make_work(root, source="openalex")
    client = StubClient({})
    with pytest.raises(envelope.EnvelopeError) as excinfo:
        service.run_download(root, "wk1", client=client)
    assert excinfo.value.code == envelope.SOURCE_ERROR
    assert client.requests == 0


def test_download_unknown_source_is_invalid_args(root):
    make_work(root, version="v3")
    with pytest.raises(envelope.EnvelopeError) as excinfo:
        service.run_download(root, "wk1", source="eprint", client=StubClient({}))
    assert excinfo.value.code == envelope.INVALID_ARGS


def test_cli_download_envelope(root, capsys, monkeypatch):
    make_work(root, version="v3")
    client = StubClient({EPRINT + "v3": route(BLOB)})
    monkeypatch.setattr(service, "make_client", lambda: client)

    code = cli.main(["--root", str(root), "download", "--work", "wk1"])
    assert code == 0
    payload = capsys.readouterr().out.strip().splitlines()
    env = json.loads(payload[-1])
    assert env["ok"] is True and env["command"] == "download"
    data = env["data"]
    assert data["work_key"] == "wk1" and data["network"] is True
    assert data["cost"]["requests"] == 1
    assert data["artifacts"][0]["kind"] == "source_tar_gz"
    assert data["artifacts"][0]["work_path"].endswith("source.tar.gz")


def test_source_url_pins_the_version():
    assert arxiv_source.source_url("latex", "2610.00001", version="v3") == EPRINT + "v3"
    assert arxiv_source.source_url("html", "2610.00001v3") == HTML
    assert arxiv_source.source_url("pdf", "2610.00001", version="v2") == PDF + "v2"
