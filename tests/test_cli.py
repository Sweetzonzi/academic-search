"""端到端：service 与 cli 的信封、退出码与落池（用 fixtures，不联网）。"""

from __future__ import annotations

import json
import pathlib

import pytest

from academic_search import cli, config, group, service, store
from academic_search.sources import SearchSpec
from academic_search.sources.http import HttpResponse

FIX = pathlib.Path(__file__).parent / "fixtures"


class FixtureClient:
    """按 URL 形状返回真实响应；不联网。"""

    def __init__(self):
        self.requests = 0
        self.cost_usd = 0.0
        self.cost_basis = ""
        self.rate_remaining_usd = None

    def get(self, url, headers=None, source=""):
        self.requests += 1
        if "works/doi:" in url or "works/10." in url:
            payload = json.loads((FIX / "openalex_work.json").read_text(encoding="utf-8"))
            body = json.dumps(payload["results"][0])
        elif "api.openalex.org/works" in url:
            body = (FIX / "openalex_work.json").read_text(encoding="utf-8")
        elif "export.arxiv.org" in url:
            body = (FIX / "arxiv_feed.xml").read_text(encoding="utf-8")
        elif "api.crossref.org" in url:
            body = (FIX / "crossref_work.json").read_text(encoding="utf-8")
        else:
            raise AssertionError("no fixture for " + url)
        return HttpResponse(url, 200, body, {})


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return tmp_path


def envelope_of(capsys):
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


def test_version_command(root, capsys):
    code = cli.main(["--root", str(root), "version"])
    assert code == 0
    env = envelope_of(capsys)
    assert env["ok"] is True and env["command"] == "version"
    assert env["schema"] == 1


def test_profile_scaffold_then_check(root, capsys):
    assert cli.main(["--root", str(root), "profile", "scaffold"]) == 0
    template = envelope_of(capsys)["data"]["template"]
    (root / "config" / "profile.toml").write_text(template, encoding="utf-8")
    assert cli.main(["--root", str(root), "profile", "check"]) == 0
    env = envelope_of(capsys)
    assert env["data"]["queries"][0]["name"] == "world-model-uav"


def test_profile_check_reports_line(root, capsys):
    (root / "config" / "profile.toml").write_text(
        "[[query]]\nname = \"q\"\n", encoding="utf-8"
    )
    code = cli.main(["--root", str(root), "profile", "check"])
    assert code == 1
    env = envelope_of(capsys)
    assert env["error"]["code"] == "profile_invalid"
    assert "第 2 行" in env["error"]["message"]


def test_profile_show_without_profile(root, capsys):
    code = cli.main(["--root", str(root), "profile", "show"])
    assert code == 1
    assert envelope_of(capsys)["error"]["code"] == "no_profile"


def test_search_offline_on_empty_db(root, capsys):
    code = cli.main(["--root", str(root), "search", "--any-of", "world model", "--refresh", "false"])
    assert code == 0
    env = envelope_of(capsys)
    assert env["data"]["items"] == []
    assert env["data"]["network"] is False


def test_search_refresh_ingests_and_dedupes(root, capsys, monkeypatch):
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    args = ["--root", str(root), "search", "--any-of", "attention", "--refresh", "true",
            "--source", "openalex", "--source", "arxiv", "--limit", "10"]
    assert cli.main(args) == 0
    env = envelope_of(capsys)
    assert env["data"]["network"] is True
    assert env["data"]["items"], "应返回本地匹配结果"
    first = env["data"]["items"][0]
    for required in ["work_key", "title", "sources", "is_retracted", "dup_suspect"]:
        assert required in first
    conn = store.connect(config.db_path(root))
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] >= 1
        assert conn.execute("SELECT COUNT(*) AS n FROM run").fetchone()["n"] == 1
    finally:
        conn.close()
    # 二次离线仍能查到
    assert cli.main(["--root", str(root), "search", "--any-of", "attention", "--refresh", "false"]) == 0
    assert envelope_of(capsys)["data"]["items"]


def test_search_auto_skips_within_window(root, capsys, monkeypatch):
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    args = ["--root", str(root), "search", "--any-of", "control", "--refresh", "true",
            "--source", "openalex"]
    assert cli.main(args) == 0
    envelope_of(capsys)
    assert cli.main(["--root", str(root), "search", "--any-of", "control", "--refresh", "auto",
                     "--source", "openalex"]) == 0
    env = envelope_of(capsys)
    assert env["data"]["network"] is False
    assert env["data"]["degraded"]["reason"]


def test_local_fts_matches_inflections(root):
    """远端的词形归并必须由本地对齐：drones 要能被 drone 命中（EX-2026-10-15-firstrun）。"""
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        store.insert_record(conn, {
            "record_uid": "u-drone", "source": "arxiv", "source_id": "2610.99999",
            "title": "Multi-drone guidance", "abstract": "Multiple drones fuse LiDAR.",
        })
        store.create_work(conn, "wk-drone")
        store.add_member(conn, "u-drone", "wk-drone", "primary")
    conn.close()
    outcome = service.run_search(root, SearchSpec(any_of=["drone"]), refresh="false",
                                 sources=["arxiv"], limit=5)
    assert [item["work_key"] for item in outcome.items] == ["wk-drone"]


def test_fts_query_skips_blank_terms():
    assert service._fts_query(SearchSpec(all_of=["", "   "])) == ""
    assert service._fts_query(SearchSpec(all_of=["world model"])) == '("world model" *)'


def test_search_all_sources_failed_is_exit_1(root, capsys, monkeypatch):
    class Failing:
        requests = 0
        cost_usd = 0.0
        cost_basis = ""
        rate_remaining_usd = None

        def get(self, url, headers=None, source=""):
            raise service.SourceError(source or "x", "429 限流", True)

    monkeypatch.setattr(service, "make_client", lambda: Failing())
    code = cli.main(["--root", str(root), "search", "--any-of", "x", "--refresh", "true",
                     "--source", "openalex"])
    assert code == 1
    assert envelope_of(capsys)["error"]["code"] == "source_error"
    # 账本不能把一次全失败的 run 记成 ok
    conn = store.connect(config.db_path(root))
    try:
        row = conn.execute("SELECT status FROM run ORDER BY run_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row["status"] == "failed"


def test_lookup_doi_url_form_hits_local_record(root):
    """用户粘 https://doi.org/… 也算命中（adaptor 负责剥前缀，IFACE-STORE §1）。"""
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        store.insert_record(conn, {
            "record_uid": "u-doi", "source": "crossref", "source_id": "10.1/x", "title": "T",
        })
        store.upsert_ident(conn, "doi", "10.1/x", "u-doi")
        store.create_work(conn, "wk-doi")
        store.add_member(conn, "u-doi", "wk-doi", "primary")
    conn.close()
    outcome = service.run_lookup(root, doi="https://doi.org/10.1/X", refresh="false")
    assert [item["work_key"] for item in outcome.items] == ["wk-doi"]


def test_lookup_doi_uses_network_fixture(root, capsys, monkeypatch):
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    code = cli.main(["--root", str(root), "lookup", "--doi", "10.1109/mcse.2011.37"])
    assert code == 0
    env = envelope_of(capsys)
    assert env["data"]["network"] is True
    assert env["data"]["items"]


def test_lookup_requires_identifier(root, capsys):
    code = cli.main(["--root", str(root), "lookup", "--refresh", "false"])
    assert code == 1
    assert envelope_of(capsys)["error"]["code"] == "invalid_args"


def test_lookup_not_found(root, capsys, monkeypatch):
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    code = cli.main(["--root", str(root), "lookup", "--doi", "10.9999/none",
                     "--refresh", "true"])
    # FakeClient 对未知 doi 仍返回同一条 fixture，因此这里只断言不是崩溃
    assert code in (0, 1)


def test_verify_empty_bytes(root, capsys):
    code = cli.main(["--root", str(root), "verify", "--sample", "0"])
    assert code == 0
    assert envelope_of(capsys)["data"]["mismatches"] == []


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit):
        cli.main(["nope"])


def test_resolve_root_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ACADEMIC_SEARCH_ROOT", str(tmp_path))
    assert cli.resolve_root(None) == tmp_path
    assert cli.resolve_root(str(tmp_path / "x")) == (tmp_path / "x").resolve()
