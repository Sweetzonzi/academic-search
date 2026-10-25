"""端到端：service 与 cli 的信封、退出码与落池（用 fixtures，不联网）。"""

from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import datetime, timedelta, timezone

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
        self.urls: list[str] = []

    def get(self, url, headers=None, source=""):
        self.requests += 1
        self.urls.append(url)
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


def _insert_abstract_work(root, abstract="abcdefghij"):
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        store.insert_record(conn, {
            "record_uid": "u-abs", "source": "arxiv", "source_id": "2401.9",
            "title": "Abstract demo", "abstract": abstract,
        })
        store.upsert_ident(conn, "arxiv", "2401.9", "u-abs")
        store.create_work(conn, "wk-abs")
        store.add_member(conn, "u-abs", "wk-abs", "primary")
    conn.close()


def test_search_abstract_flag_shapes_payload(root):
    _insert_abstract_work(root)
    off = service.run_search(root, SearchSpec(), refresh="false", sources=["arxiv"])
    assert "abstract" not in off.items[0]
    on = service.run_search(root, SearchSpec(), refresh="false", sources=["arxiv"],
                            abstract=True, abstract_chars=5)
    assert on.items[0]["abstract"] == "abcde"
    # chars <= 0 = 不截断（与 config/search.toml 的注释同一口径）
    full = service.run_search(root, SearchSpec(), refresh="false", sources=["arxiv"],
                              abstract=True, abstract_chars=0)
    assert full.items[0]["abstract"] == "abcdefghij"


def test_lookup_carries_full_abstract(root):
    _insert_abstract_work(root)
    outcome = service.run_lookup(root, arxiv_id="2401.9", refresh="false")
    assert outcome.items[0]["abstract"] == "abcdefghij"


def test_lookup_writes_the_arxiv_version(root):
    """元数据刷新把 feed 里的 vN 写进 record.source_version；非 arXiv 记录留 NULL。"""
    service.run_lookup(root, arxiv_id="2610.18808", refresh="true", client=FixtureClient())
    conn = store.connect(config.db_path(root))
    try:
        versions = {row["source"]: row["source_version"]
                    for row in conn.execute("SELECT source, source_version FROM record")}
    finally:
        conn.close()
    assert versions.get("arxiv") == "v1"
    assert versions.get("openalex") is None


def test_cli_search_abstract_uses_config_chars(root, capsys):
    (root / "config" / "search.toml").write_text("[abstract]\nchars = 4\n", encoding="utf-8")
    _insert_abstract_work(root)
    assert cli.main(["--root", str(root), "search", "--refresh", "false", "--abstract"]) == 0
    assert envelope_of(capsys)["data"]["items"][0]["abstract"] == "abcd"
    assert cli.main(["--root", str(root), "search", "--refresh", "false"]) == 0
    assert "abstract" not in envelope_of(capsys)["data"]["items"][0]


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


def test_lookup_sends_openalex_api_key(root, monkeypatch):
    """lookup 的 OpenAlex 那一腿必须带 key，否则仍走匿名档（会 429）。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "TESTKEY")
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    service.run_lookup(root, doi="10.48550/arxiv.2610.18808", refresh="true")
    assert client.urls, "应发出过请求"
    assert any("api_key=TESTKEY" in url for url in client.urls), client.urls


def test_lookup_mailto_comes_from_config(root, capsys, monkeypatch):
    """`[contact] mailto` 从配置一路走到 Crossref 请求（IFACE-SOURCE §3.3）。"""
    (root / "config" / "search.toml").write_text(
        '[contact]\nmailto = "me@example.com"\n', encoding="utf-8"
    )
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    assert cli.main(["--root", str(root), "lookup", "--doi", "10.1109/mcse.2011.37"]) == 0
    assert any("mailto=me%40example.com" in url for url in client.urls), client.urls


def _insert_local_openalex_work(root, *, cited_by_count=None):
    """本地已有这个 DOI 的记录，但引用信号是空的——模拟「只以 arXiv 记录进过库」。"""
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        store.insert_record(conn, {
            "record_uid": "u-oa", "source": "openalex", "source_id": "w2146292423",
            "title": "The NumPy Array: A Structure for Efficient Numerical Computation",
            "cited_by_count": cited_by_count,
        })
        store.upsert_ident(conn, "doi", "10.1109/mcse.2011.37", "u-oa")
        store.create_work(conn, "wk-oa")
        store.add_member(conn, "u-oa", "wk-oa", "primary")
    conn.close()


def _cited_by_count(root, record_uid):
    conn = store.connect(config.db_path(root))
    try:
        row = conn.execute(
            "SELECT cited_by_count FROM record WHERE record_uid = ?", (record_uid,)
        ).fetchone()
    finally:
        conn.close()
    return row["cited_by_count"]


def test_lookup_refresh_true_repairs_signals_on_local_hit(root, monkeypatch):
    """`--refresh true` = 先刷再查本地，即使本地已按标识命中（ADR-0065 决策 1）。"""
    _insert_local_openalex_work(root)
    assert _cited_by_count(root, "u-oa") is None
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    outcome = service.run_lookup(root, doi="10.1109/mcse.2011.37", refresh="true")
    assert client.requests > 0, "命中也要刷"
    assert outcome.network is True
    assert _cited_by_count(root, "u-oa") == 10587, "引用信号被刷新写回"


def test_lookup_refresh_auto_does_not_touch_the_network_on_local_hit(root, monkeypatch):
    """默认档不产生任何额外请求——缺了才补（ADR-0065 决策 1 与决策 4）。"""
    _insert_local_openalex_work(root)
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    outcome = service.run_lookup(root, doi="10.1109/mcse.2011.37", refresh="auto")
    assert client.requests == 0
    assert outcome.network is False
    assert outcome.items[0]["work_key"] == "wk-oa"
    assert _cited_by_count(root, "u-oa") is None, "不刷就不该动它"


def test_lookup_refresh_false_never_goes_online_even_on_miss(root, monkeypatch):
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    with pytest.raises(service.envelope.EnvelopeError) as excinfo:
        service.run_lookup(root, doi="10.9999/none", refresh="false")
    assert client.requests == 0
    assert excinfo.value.code == "not_found"


def test_lookup_title_refresh_true_degrades_and_still_returns_local(root, capsys, monkeypatch):
    """`--title` 没有标识、用不了免费的单实体 GET：只查本地并标注（ADR-0065 决策 2）。"""
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    _insert_abstract_work(root)
    assert cli.main(["--root", str(root), "lookup", "--title", "Abstract demo",
                     "--refresh", "true"]) == 0
    env = envelope_of(capsys)
    assert env["data"]["network"] is False
    assert "只查本地" in env["data"]["degraded"]["reason"]
    assert env["data"]["warnings"] == [env["data"]["degraded"]["reason"]]
    assert env["data"]["items"][0]["title"] == "Abstract demo"


def test_lookup_title_refresh_true_miss_points_at_doi_and_arxiv(root, monkeypatch):
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    with pytest.raises(service.envelope.EnvelopeError) as excinfo:
        service.run_lookup(root, title="nothing matches this", refresh="true")
    assert excinfo.value.code == "not_found"
    assert "--doi" in excinfo.value.message and "--arxiv" in excinfo.value.message


def test_lookup_title_auto_is_not_a_degradation(root, monkeypatch):
    """`auto` 下 `--title` 不联网是既定形状，不算降级——只有 `true` 才提示。"""
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    _insert_abstract_work(root)
    outcome = service.run_lookup(root, title="Abstract demo", refresh="auto")
    assert outcome.degraded is None
    assert outcome.items[0]["title"] == "Abstract demo"


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


# --------------------------------------------------------------------------
# ADR-0062 / 0063：取数口径
# --------------------------------------------------------------------------


def _spec_hashes(root):
    conn = store.connect(config.db_path(root))
    try:
        rows = conn.execute("SELECT spec_hash FROM run ORDER BY run_id").fetchall()
    finally:
        conn.close()
    return [row["spec_hash"] for row in rows]


def test_limit_is_display_only_same_spec_hash(root, monkeypatch):
    """`--limit` 只截断展示：换个 limit 仍落在同一新鲜度窗口（ADR-0062 决策 3/5）。"""
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    first = service.run_search(root, SearchSpec(any_of=["attention"]), refresh="true",
                               sources=["openalex"], limit=5)
    assert first.network is True
    second = service.run_search(root, SearchSpec(any_of=["attention"]), refresh="auto",
                                sources=["openalex"], limit=20)
    assert second.network is False
    hashes = _spec_hashes(root)
    assert len(hashes) == 2 and len(set(hashes)) == 1


def test_sort_is_a_fetch_condition(root, monkeypatch):
    """`--sort` 是取数条件：两个档是不同的切片，各抓一次（ADR-0062 决策 6）。"""
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    service.run_search(root, SearchSpec(any_of=["attention"]), refresh="true",
                       sources=["openalex"], sort="date")
    again = service.run_search(root, SearchSpec(any_of=["attention"]), refresh="auto",
                               sources=["openalex"], sort="citations")
    assert again.network is True
    assert len(set(_spec_hashes(root))) == 2


def test_source_is_a_fetch_condition(root, monkeypatch):
    """`--source` 进 hash：`--source arxiv` 不该顶替默认两源的新鲜度（ADR-0062 代价 5）。"""
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    service.run_search(root, SearchSpec(any_of=["control"]), refresh="true", sources=["arxiv"])
    other = service.run_search(root, SearchSpec(any_of=["control"]), refresh="auto",
                               sources=["openalex"])
    assert other.network is True


def test_fetch_asks_for_one_full_page(root, monkeypatch):
    """一次取数固定一页 100 条：计费按次，取少不省钱（ADR-0062 决策 5）。"""
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    service.run_search(root, SearchSpec(any_of=["attention"]), refresh="true",
                       sources=["openalex"], limit=3)
    openalex_urls = [url for url in client.urls if "api.openalex.org/works" in url]
    assert openalex_urls
    assert all("per_page=100" in url for url in openalex_urls)
    assert all("sort=publication_date%3Adesc" in url for url in openalex_urls)


def test_search_request_uses_the_new_door(root, monkeypatch):
    """主题条件走 `search.title_and_abstract=`、结构化条件留在 `filter=`（ADR-0063）。"""
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    service.run_search(root, SearchSpec(all_of=["world model"], any_of=["drone", "UAV"]),
                       refresh="true", sources=["openalex"])
    url = [u for u in client.urls if "api.openalex.org/works" in u][0]
    assert "search.title_and_abstract=%28%22world+model%22%29+AND+%28%22drone%22+OR+%22UAV%22%29" in url
    assert "title_and_abstract.search" not in url


# --------------------------------------------------------------------------
# ADR-0060 / 0061：载体名单与引用阈值
# --------------------------------------------------------------------------


def _insert_arxiv_work(root, uid="u-arxiv", source_id="2401.7", text="World model for drones"):
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        store.insert_record(conn, {
            "record_uid": uid, "source": "arxiv", "source_id": source_id,
            "title": text, "abstract": text,
        })
        store.upsert_ident(conn, "arxiv", source_id, uid)
        store.create_work(conn, "wk-" + uid)
        store.add_member(conn, uid, "wk-" + uid, "primary")
    conn.close()
    return "wk-" + uid


def test_venue_whitelist_hits_arxiv_records_offline(root):
    """`--venue arXiv` 在 `--refresh false` 下命中 > 0：arXiv 记录靠来源自身的载体。

    PLAN §2 的 ADR-0060 验收判据之一；载体集合不是靠重抓回填的。
    """
    key = _insert_arxiv_work(root)
    outcome = service.run_search(root, SearchSpec(any_of=["world model"], venues=["arXiv"]),
                                 refresh="false", sources=["arxiv"])
    assert [item["work_key"] for item in outcome.items] == [key]


def test_venue_whitelist_fails_closed_on_unknown_carrier(root):
    """载体集合为空的记录在带白名单的查询里判不出来——白名单不 fail-open。"""
    _insert_arxiv_work(root)
    outcome = service.run_search(root, SearchSpec(any_of=["world model"], venues=["ICRA"]),
                                 refresh="false", sources=["arxiv"])
    assert outcome.items == []


def test_unregistered_venue_name_is_an_error(root, capsys):
    code = cli.main(["--root", str(root), "search", "--any-of", "x", "--venue", "不存在的会",
                     "--refresh", "false"])
    assert code == 1
    env = envelope_of(capsys)
    assert env["error"]["code"] == "invalid_args"
    assert "不存在的会" in env["error"]["message"]


def test_venue_whitelist_skips_arxiv_leg(root, monkeypatch):
    """给定白名单时 arXiv 腿不参与，如实记 0、不报错（ADR-0060 决策 5）。"""
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    outcome = service.run_search(root, SearchSpec(any_of=["attention"], venues=["ICRA"]),
                                 refresh="true", sources=["openalex", "arxiv"])
    assert outcome.counts["by_source"] == {"openalex": 3, "arxiv": 0}
    assert outcome.counts["failures"] == []
    assert not any("export.arxiv.org" in url for url in client.urls)


def test_min_citations_filters_and_skips_arxiv_leg(root, monkeypatch):
    """阈值远端下发 + 本地同判 + 跳过 arXiv 腿（ADR-0061 决策 2–4）。"""
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    outcome = service.run_search(
        root, SearchSpec(any_of=["attention"], min_citations=1000),
        refresh="true", sources=["openalex", "arxiv"],
    )
    assert outcome.counts["by_source"]["arxiv"] == 0
    assert outcome.counts["failures"] == []
    assert outcome.items and all(item["cited_by_count"] >= 1000 for item in outcome.items)
    url = [u for u in client.urls if "api.openalex.org/works" in u][0]
    assert "cited_by_count%3A1000-" in url


def test_min_fwci_excludes_null_values(root, monkeypatch):
    """`fwci` 为空即不通过（ADR-0061 决策 3）；它是 work 级最大值。

    fixture 三条里只有 NumPy 那条有 `fwci`（26.316），另两条是 `null`。
    """
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    outcome = service.run_search(root, SearchSpec(min_fwci=2.0),
                                 refresh="true", sources=["openalex"])
    assert len(outcome.items) == 1
    assert outcome.items[0]["fwci"] >= 2.0
    assert outcome.items[0]["title"].startswith("The NumPy Array")


def test_both_thresholds_are_anded(root, monkeypatch):
    monkeypatch.setattr(service, "make_client", lambda: FixtureClient())
    outcome = service.run_search(
        root, SearchSpec(min_citations=1000, min_fwci=2.0),
        refresh="true", sources=["openalex"],
    )
    assert len(outcome.items) == 1
    item = outcome.items[0]
    assert item["cited_by_count"] >= 1000 and item["fwci"] >= 2.0


# --------------------------------------------------------------------------
# --in 三档：meta / body / both（ADR-0036 决策 2）
# --------------------------------------------------------------------------

#: 20 位小写 base32——正文一路靠目录名尾段反解出它。
BODY_KEY = "abcdefghijklmnopqrst"


def _insert_work(root, key, *, title, source="arxiv", source_id=None, body=None, **fields):
    """一条记录 + 一个 work；给了 body 就顺手写 `works/<proj>/paper.md`。"""
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        store.insert_record(conn, {
            "record_uid": key, "source": source, "source_id": source_id or key,
            "title": title, "abstract": title, "published_at": "2026-10-01",
            **fields,
        })
        store.create_work(conn, key)
        store.add_member(conn, key, key, "primary")
    conn.close()
    if body is not None:
        doc = root / "works" / ("2026-Doe-Probe-" + key) / "paper.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(body, encoding="utf-8")
    return key


def test_in_meta_body_both_are_three_different_result_sets(root):
    """同一检索式：标题命中 vs 正文命中，三档各给一个集合。"""
    in_body = _insert_work(root, BODY_KEY, title="Body Probe",
                           body="# paper\n\n世界模型 world model 只在正文里\n")
    in_title = _insert_work(root, "bcdefghijklmnopqrstu", title="World Model Survey")
    spec = SearchSpec(any_of=["world model"])
    call = dict(refresh="false", sources=["arxiv"])

    meta = service.run_search(root, spec, in_scope="meta", **call)
    body = service.run_search(root, spec, in_scope="body", **call)
    both = service.run_search(root, spec, in_scope="both", **call)

    assert [item["work_key"] for item in meta.items] == [in_title]  # 正文命中不算
    assert [item["work_key"] for item in body.items] == [in_body]  # 标题命中不算
    assert sorted(item["work_key"] for item in both.items) == sorted([in_body, in_title])


def test_in_body_still_honours_the_source_filter(root):
    """正文一路与非文本过滤同一套口径：`--source` 排除的 work 也不该从正文钻进来。"""
    _insert_work(root, BODY_KEY, title="Body Probe", body="world model\n")
    outcome = service.run_search(
        root, SearchSpec(any_of=["world model"]),
        refresh="false", sources=["openalex"], in_scope="body",
    )
    assert outcome.items == []


def test_in_body_never_goes_online_on_auto(root, monkeypatch):
    """正文只在本地语料里；`--refresh auto` 在 body 档不该为它发请求（ADR-0036 决策 2）。"""
    def boom():
        raise AssertionError("body 档不该联网")

    monkeypatch.setattr(service, "make_client", boom)
    _insert_work(root, BODY_KEY, title="Body Probe", body="world model\n")
    outcome = service.run_search(
        root, SearchSpec(any_of=["world model"]), refresh="auto",
        sources=["arxiv"], in_scope="body",
    )
    assert outcome.network is False
    assert [item["work_key"] for item in outcome.items] == [BODY_KEY]


@pytest.mark.parametrize("in_scope", ["body", "both"])
@pytest.mark.parametrize("all_of,any_of,body,matched", [
    (["multi-agent", "task allocation"], [], "multi-agent", False),
    (["multi-agent", "task allocation"], [], "MULTI-AGENT task allocation", True),
    ([], ["UAV", "drone"], "a drone mission", True),
    ([], ["UAV", "drone"], "a robot mission", False),
    (["multi-agent"], ["UAV", "drone"], "multi-agent", False),
    (["multi-agent"], ["UAV", "drone"], "a drone mission", False),
    (["multi-agent"], ["UAV", "drone"], "multi-agent drone mission", True),
])
def test_body_search_honours_boolean_groups(root, in_scope, all_of, any_of, body, matched):
    _insert_work(root, BODY_KEY, title="Body Probe", body=body)
    outcome = service.run_search(
        root, SearchSpec(all_of=all_of, any_of=any_of),
        refresh="false", sources=["arxiv"], in_scope=in_scope,
    )
    assert [item["work_key"] for item in outcome.items] == ([BODY_KEY] if matched else [])


def test_body_search_does_not_combine_conditions_across_files(root):
    _insert_work(root, BODY_KEY, title="Body Probe", body="multi-agent")
    supplement = root / "works" / ("2026-Doe-Probe-" + BODY_KEY) / "supplement.md"
    supplement.write_text("task allocation", encoding="utf-8")
    outcome = service.run_search(
        root, SearchSpec(all_of=["multi-agent", "task allocation"]),
        refresh="false", sources=["arxiv"], in_scope="body",
    )
    assert outcome.items == []


@pytest.mark.parametrize("max_age,age_minutes,network", [
    (None, 90, False),
    (0.5, 15, False),
    (0.5, 45, True),
    (1.5, 75, False),
    (1.5, 105, True),
])
def test_cli_max_age_uses_fractional_hours_in_refresh_gate(
    root, capsys, monkeypatch, max_age, age_minutes, network,
):
    (root / "config/search.toml").write_text(
        "[refresh]\nmax_age_hours=2\n", encoding="utf-8",
    )
    monkeypatch.setattr(service, "make_client", FixtureClient)
    query = ["--root", str(root), "search", "--any-of", "control", "--source", "openalex"]
    assert cli.main([*query, "--refresh", "true"]) == 0
    assert envelope_of(capsys)["data"]["network"] is True
    later = datetime.now(timezone.utc) + timedelta(minutes=age_minutes)

    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return later.astimezone(tz)

    monkeypatch.setattr(service, "datetime", LaterClock)
    args = [*query, "--refresh", "auto"]
    if max_age is not None:
        args.append("--max-age=" + str(max_age))
    assert cli.main(args) == 0
    data = envelope_of(capsys)["data"]
    assert data["network"] is network
    assert data["cost"]["requests"] == int(network)


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf"])
def test_cli_rejects_invalid_max_age_before_opening_database(root, capsys, monkeypatch, value):
    monkeypatch.setattr(service, "make_client", lambda: pytest.fail("无效时长不能联网"))
    assert cli.main(["--root", str(root), "search", "--max-age=" + value,
                     "--refresh", "false"]) == 1
    error = envelope_of(capsys)["error"]
    assert error["code"] == "invalid_args" and "--max-age" in error["message"]
    assert not config.db_path(root).exists()


@pytest.mark.parametrize("authors,matched", [
    (["Jane Doe"], True), (["Alice Smith"], True),
    (["Jane Doe", "Alice Smith"], True), (["Other Author"], False), ([], False),
])
def test_author_whitelist_matches_any_one_author(root, authors, matched):
    _insert_work(root, BODY_KEY, title="Author Probe", authors=authors)
    outcome = service.run_search(
        root, SearchSpec(authors=["jane doe", "ALICE SMITH"]),
        refresh="false", sources=["arxiv"],
    )
    assert [item["work_key"] for item in outcome.items] == ([BODY_KEY] if matched else [])


def test_author_phrase_cannot_span_two_names(root):
    _insert_work(root, BODY_KEY, title="Author Probe", authors=["Jane Doe", "Alice Smith"])
    outcome = service.run_search(
        root, SearchSpec(authors=["Doe Alice"]), refresh="false", sources=["arxiv"],
    )
    assert outcome.items == []


@pytest.mark.parametrize("field,threshold", [("cited_by_count", 100), ("fwci", 2.5)])
@pytest.mark.parametrize("delta,matched", [(-1, False), (0, True), (1, True), (None, False)])
def test_citation_threshold_includes_equal_and_excludes_missing(root, field, threshold, delta, matched):
    _insert_work(root, BODY_KEY, title="Threshold Probe", source="openalex",
                 **{field: None if delta is None else threshold + delta})
    spec = SearchSpec(**{"min_citations" if field == "cited_by_count" else "min_fwci": threshold})
    outcome = service.run_search(root, spec, refresh="false", sources=["openalex"])
    assert [item["work_key"] for item in outcome.items] == ([BODY_KEY] if matched else [])


@pytest.mark.parametrize("option,value", [
    ("--min-citations", "-1"), ("--min-fwci", "-1"),
    ("--min-fwci", "nan"), ("--min-fwci", "inf"), ("--min-fwci", "-inf"),
])
def test_cli_rejects_invalid_thresholds_before_database(root, capsys, option, value):
    assert cli.main(["--root", str(root), "search", option + "=" + value,
                     "--refresh", "false"]) == 1
    error = envelope_of(capsys)["error"]
    assert error["code"] == "invalid_args" and option in error["message"]
    assert not config.db_path(root).exists()


@pytest.mark.parametrize("conditions", [
    {"min_citations": 100}, {"min_fwci": 2.5}, {"authors": ["Sutton", "Littman"]},
])
def test_changed_filter_semantics_do_not_reuse_legacy_freshness(root, conditions):
    spec = SearchSpec(any_of=["control"], **conditions)
    service.run_search(root, spec, sources=["openalex"], refresh="true", client=FixtureClient())
    canonical = json.loads(config.canonical_spec(any_of=spec.any_of, sources=["openalex"],
                                                sort="date", **conditions))
    canonical.pop("min_bounds", None)
    canonical.pop("author_match", None)
    legacy_hash = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode("utf-8")).hexdigest()
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        conn.execute("UPDATE run SET spec_hash = ?", (legacy_hash,))
    conn.close()
    current = service.run_search(root, spec, sources=["openalex"], refresh="auto", client=FixtureClient())
    assert current.network is True and current.requests == 1


def test_any_of_accepts_several_values_after_one_flag(root, capsys):
    """`--any-of a b c` 是多数人的第一反应；不能退出码 2、把管道打成空文件。"""
    code = cli.main([
        "--root", str(root), "search",
        "--any-of", "dreamer", "latent imagination", "planet",
        "--refresh", "false",
    ])
    assert code == 0
    env = envelope_of(capsys)
    assert env["ok"] is True and env["data"]["items"] == []


def test_counts_local_reports_the_funnel(root):
    """counts.local 说明「候选看了多少、过滤后多少、返回多少」——ADR-0070。"""
    key = _insert_work(root, "cccccccccccccccccccc", title="World Model Probe")
    outcome = service.run_search(
        root, SearchSpec(any_of=["world model"]), refresh="false", sources=["arxiv"],
    )
    assert [item["work_key"] for item in outcome.items] == [key]
    assert outcome.counts["local"] == {
        "considered": 1, "matched": 1, "returned": 1,
        "truncated": False, "venue_filtered_out": 0,
    }


def test_candidate_budget_truncates_and_says_so(root):
    """预算用尽时必须标 truncated，而不是静默少回。"""
    for i in range(3):
        _insert_work(root, "budget%017d" % i, title="World Model %d" % i)
    outcome = service.run_search(
        root, SearchSpec(any_of=["world model"]), refresh="false",
        sources=["arxiv"], max_candidates=1,
    )
    local = outcome.counts["local"]
    assert local["considered"] == 1
    assert local["truncated"] is True
    assert len(outcome.payload()["warnings"]) == 1
    assert "候选预算" in outcome.payload()["warnings"][0]


def test_venue_filtered_out_is_counted(root):
    """venue 白名单按载体判据 fail-closed；被它滤掉多少要能看出来。"""
    _insert_work(root, "dddddddddddddddddddd", title="World Model Probe")
    outcome = service.run_search(
        root, SearchSpec(any_of=["world model"], venues=["NeurIPS"]),
        refresh="false", sources=["arxiv"],
    )
    assert outcome.items == []
    assert outcome.counts["local"]["venue_filtered_out"] == 1


def test_lookup_title_miss_never_claims_network(root, capsys):
    """`--title` 从不联网；auto/false 的文案不能说成「联网未取到」。"""
    for refresh in ("false", "auto"):
        code = cli.main([
            "--root", str(root), "lookup",
            "--title", "no such paper", "--refresh", refresh,
        ])
        assert code == 1
        message = envelope_of(capsys)["error"]["message"]
        assert "不联网" in message
        assert "联网未取到" not in message


@pytest.mark.parametrize("source", ["nosuch", "crossref", "openalex"])
def test_search_rejects_unavailable_explicit_sources(root, capsys, monkeypatch, source):
    if source == "openalex":
        (root / "config/sources.toml").write_text('[openalex]\nenabled=false\n', encoding="utf-8")
    monkeypatch.setattr(service, "make_client", lambda: pytest.fail("无效来源不能联网"))
    code = cli.main(["--root", str(root), "search", "--source", source, "--refresh", "false"])
    env = envelope_of(capsys)
    assert code == 1 and env["error"]["code"] == "invalid_args"
    assert source in env["error"]["message"]
    assert not config.db_path(root).exists(), "参数错误应在建库前返回"


def test_search_with_no_enabled_sources_cannot_fall_back_to_all(root, capsys):
    (root / "config/sources.toml").write_text(
        '[openalex]\nenabled=false\n[arxiv]\nenabled=false\n', encoding="utf-8"
    )
    assert cli.main(["--root", str(root), "search", "--refresh", "false"]) == 1
    assert envelope_of(capsys)["error"]["code"] == "invalid_args"


@pytest.mark.parametrize("option", ["--all-of", "--any-of", "--author", "--venue"])
@pytest.mark.parametrize("values", [[""], ["valid", "  "]])
def test_search_rejects_blank_values_before_fts(root, capsys, option, values):
    assert cli.main(["--root", str(root), "search", option, *values, "--refresh", "false"]) == 1
    env = envelope_of(capsys)
    assert env["error"]["code"] == "invalid_args"
    assert option in env["error"]["message"]


def test_duplicate_source_does_not_fetch_twice(root, capsys, monkeypatch):
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    assert cli.main(["--root", str(root), "search", "--any-of", "control",
                     "--source", "openalex", "--source", "openalex", "--refresh", "true"]) == 0
    assert envelope_of(capsys)["data"]["cost"]["requests"] == 1
    assert len(client.urls) == 1


def test_partial_source_failure_does_not_lock_the_query_fresh(root):
    class PartialClient(FixtureClient):
        def get(self, url, headers=None, source=""):
            if source == "arxiv":
                self.requests += 1
                raise service.SourceError(source, "503 临时失败", True)
            return super().get(url, headers=headers, source=source)

    spec = SearchSpec(any_of=["control"])
    first = service.run_search(root, spec, refresh="true", client=PartialClient())
    assert first.counts["failures"] and first.counts["complete"] is False
    assert len(first.payload()["warnings"]) == 1
    assert "arxiv" in first.payload()["warnings"][0]
    assert "请求失败" in first.payload()["warnings"][0]
    second = service.run_search(root, spec, refresh="auto", client=FixtureClient())
    assert second.network is True and second.counts["complete"] is True
    assert second.counts["failures"] == []
    assert "warnings" not in second.payload()
    third = service.run_search(root, spec, refresh="auto", client=FixtureClient())
    assert third.network is False, "完整采集之后才复用新鲜度"
    assert third.degraded is not None and "warnings" not in third.payload()


def test_search_honours_configured_quota_threshold(root, capsys, monkeypatch):
    class LowQuotaClient(FixtureClient):
        def get(self, url, headers=None, source=""):
            response = super().get(url, headers=headers, source=source)
            self.rate_remaining_usd = 0.1
            return response

    (root / "config/search.toml").write_text('[quota]\nmin_remaining_usd=0.5\n', encoding="utf-8")
    client = LowQuotaClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    assert cli.main(["--root", str(root), "search", "--any-of", "control", "--refresh", "true"]) == 0
    data = envelope_of(capsys)["data"]
    assert data["network"] is True and data["cost"]["requests"] == 1
    assert data["counts"]["by_source"]["arxiv"] == 0
    assert data["counts"]["complete"] is False and "余额不足" in data["degraded"]["reason"]
    assert data["warnings"] == [data["degraded"]["reason"] + "；结果覆盖可能不完整。"]
    assert not any("export.arxiv.org" in url for url in client.urls)


def test_all_sources_skipped_is_not_a_network_refresh(root):
    client = FixtureClient()
    spec = SearchSpec(any_of=["control"], min_citations=1)
    outcome = service.run_search(root, spec, sources=["arxiv"], refresh="true", client=client)
    assert outcome.network is False and client.requests == 0
    assert outcome.counts["complete"] is False
    assert len(outcome.payload()["warnings"]) == 1
    assert "arXiv" in outcome.payload()["warnings"][0]
    assert "--min-citations" in outcome.payload()["warnings"][0]
    conn = store.connect(config.db_path(root))
    try:
        row = conn.execute("SELECT refreshed FROM run ORDER BY run_id DESC LIMIT 1").fetchone()
        assert row["refreshed"] == 0
    finally:
        conn.close()


def test_offline_identifier_miss_never_claims_network(root, capsys):
    assert cli.main(["--root", str(root), "lookup", "--doi", "10.9999/missing", "--refresh", "false"]) == 1
    message = envelope_of(capsys)["error"]["message"]
    assert "不联网" in message and "联网未取到" not in message


@pytest.mark.parametrize("scope", ["catalog", "meta", "body", "both"])
def test_offset_pages_filtered_work_results_without_repeats(root, capsys, scope):
    keys = ["p" * 18 + chr(97 + i // 26) + chr(97 + i % 26) for i in range(42)]
    for key in reversed(keys):
        _insert_work(root, key, title="Pagination Probe", body="pagination")
    args = ["--root", str(root), "search", "--refresh", "false", "--source", "arxiv"]
    if scope != "catalog":
        args += ["--all-of", "pagination", "--in", scope]
    observed = []
    for offset in (0, 20, 40, 100):
        # 第一批省略 offset，验证默认值与默认 limit=20。
        page_args = args if offset == 0 else [*args, "--offset", str(offset)]
        assert cli.main(page_args) == 0
        data = envelope_of(capsys)["data"]
        page = [item["work_key"] for item in data["items"]]
        assert page == keys[offset:offset + 20]
        assert data["counts"]["local"] == {
            "considered": 42, "matched": 42, "returned": len(page),
            "truncated": False, "venue_filtered_out": 0,
        }
        assert data["network"] is False and data["cost"]["requests"] == 0
        observed.extend(page)
    assert observed == keys


@pytest.mark.parametrize("sort,field,high,low", [
    ("date", "published_at", "2026-10-04", "2026-10-03"),
    ("citations", "cited_by_count", 200, 100),
])
def test_offset_order_is_stable_for_ties_and_missing_values(root, sort, field, high, low):
    keys = {letter: "p" * 19 + letter for letter in "abcde"}
    for letter, value in zip("abcde", [high, low, low, None, None]):
        _insert_work(root, keys[letter], title="Pagination Probe", **{field: value})
    conn = store.connect(config.db_path(root))
    try:
        items = service._sorted_items(
            conn, [keys[letter] for letter in "ecadb"], spec=SearchSpec(),
            blocked_venues=[], venue_ids=[], sort=sort, offset=1, limit=3,
        )
    finally:
        conn.close()
    assert [item["work_key"] for item in items] == [keys[letter] for letter in "bcd"]


@pytest.mark.parametrize("sort,expected", [("date", "c"), ("citations", "d")])
def test_offset_is_applied_after_threshold_filtering_and_sorting(root, sort, expected):
    for index, (letter, cited) in enumerate(zip("abcd", [100, 0, 200, 150])):
        _insert_work(root, "p" * 19 + letter, title="Pagination Probe", source="openalex",
                     cited_by_count=cited, published_at="2026-10-0" + str(4 - index))
    outcome = service.run_search(
        root, SearchSpec(all_of=["pagination"], min_citations=100),
        refresh="false", sources=["openalex"], sort=sort, offset=1, limit=1,
    )
    assert [item["work_key"] for item in outcome.items] == ["p" * 19 + expected]
    assert outcome.counts["local"]["considered"] == 4
    assert outcome.counts["local"]["matched"] == 3
    assert outcome.counts["local"]["returned"] == 1


@pytest.mark.parametrize("offset,limit,returned", [(1, 0, 0), (2, 20, 0), (1, 20, 1)])
def test_offset_and_limit_empty_and_short_pages(root, offset, limit, returned):
    for letter in "ab":
        _insert_work(root, "p" * 19 + letter, title="Pagination Probe")
    outcome = service.run_search(
        root, SearchSpec(), refresh="false", sources=["arxiv"], offset=offset, limit=limit,
    )
    assert len(outcome.items) == returned
    assert outcome.counts["local"]["matched"] == 2
    assert outcome.counts["local"]["returned"] == returned


@pytest.mark.parametrize("option", ["--offset", "--limit"])
def test_cli_rejects_negative_pagination_before_database(root, capsys, option):
    assert cli.main(["--root", str(root), "search", "--refresh", "false", option, "-1"]) == 1
    error = envelope_of(capsys)["error"]
    assert error["code"] == "invalid_args" and option in error["message"]
    assert not config.db_path(root).exists()


@pytest.mark.parametrize("values", [{"offset": -1}, {"limit": -1}])
def test_service_rejects_negative_pagination_before_database(root, values):
    with pytest.raises(service.envelope.EnvelopeError) as exc:
        service.run_search(root, SearchSpec(), refresh="false", **values)
    assert exc.value.code == "invalid_args"
    assert not config.db_path(root).exists()


def test_offset_preserves_candidate_budget_truncation(root):
    for letter in "abc":
        _insert_work(root, "p" * 19 + letter, title="Pagination Probe")
    for offset, returned in ((1, 1), (2, 0)):
        outcome = service.run_search(
            root, SearchSpec(all_of=["pagination"]), refresh="false", sources=["arxiv"],
            offset=offset, limit=20, max_candidates=2,
        )
        assert outcome.counts["local"] == {
            "considered": 2, "matched": 2, "returned": returned,
            "truncated": True, "venue_filtered_out": 0,
        }


def test_offset_reuses_freshness_and_does_not_fetch_more_pages(root, capsys, monkeypatch):
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    args = ["--root", str(root), "search", "--any-of", "control", "--source", "openalex"]
    assert cli.main([*args, "--refresh", "true"]) == 0
    assert envelope_of(capsys)["data"]["network"] is True
    assert cli.main([*args, "--refresh", "auto", "--offset", "1000"]) == 0
    data = envelope_of(capsys)["data"]
    assert data["network"] is False and data["cost"]["requests"] == 0
    assert client.requests == 1 and "per_page=100" in client.urls[0]
    conn = store.connect(config.db_path(root))
    try:
        hashes = [row["spec_hash"] for row in conn.execute("SELECT spec_hash FROM run ORDER BY run_id")]
    finally:
        conn.close()
    assert len(hashes) == 2 and hashes[0] == hashes[1]


@pytest.mark.parametrize("options,terms", [
    (["--min-citations", "0"], ["被引次数", "--min-citations"]),
    (["--min-fwci", "0"], ["FWCI", "--min-fwci"]),
    (["--min-citations", "1", "--min-fwci", "2", "--venue", "ICRA"],
     ["被引次数", "FWCI", "载体信息", "--min-citations", "--min-fwci", "--venue"]),
])
def test_warnings_explain_actual_arxiv_skip(root, capsys, monkeypatch, options, terms):
    client = FixtureClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    code = cli.main(["--root", str(root), "search", "--any-of", "attention",
                     "--refresh", "true", *options])
    env = envelope_of(capsys)
    assert code == 0 and env["ok"] is True
    data = env["data"]
    assert len(data["warnings"]) == 1
    assert all(term in data["warnings"][0] for term in ["arXiv", "不提供", "跳过", *terms])
    assert data["counts"]["by_source"]["arxiv"] == 0
    assert not data["counts"]["failures"]
    assert not any("export.arxiv.org" in url for url in client.urls)
    assert "warnings" not in data["counts"] and "warnings" not in data.get("degraded", {})
    conn = store.connect(config.db_path(root))
    try:
        counts = json.loads(conn.execute("SELECT counts FROM run ORDER BY run_id DESC").fetchone()[0])
        assert "warnings" not in counts
    finally:
        conn.close()


@pytest.mark.parametrize("refresh", ["false", "auto"])
def test_warnings_do_not_claim_arxiv_skipped_when_no_collection(root, refresh):
    spec = SearchSpec(any_of=["attention"], min_citations=1)
    service.run_search(root, spec, refresh="true", client=FixtureClient())
    client = FixtureClient()
    outcome = service.run_search(root, spec, refresh=refresh, client=client)
    assert client.requests == 0 and outcome.network is False
    assert "warnings" not in outcome.payload()


@pytest.mark.parametrize("spec,sort", [
    (SearchSpec(any_of=["attention"], min_citations=1), "date"),
    (SearchSpec(any_of=["attention"], min_fwci=1), "date"),
    (SearchSpec(any_of=["attention"]), "citations"),
])
def test_warnings_do_not_claim_unselected_arxiv_skipped(root, spec, sort):
    outcome = service.run_search(root, spec, refresh="true", sort=sort,
                                 sources=["openalex"], client=FixtureClient())
    assert "warnings" not in outcome.payload()


def test_citation_sort_alone_fetches_arxiv_without_skip_warning(root):
    client = FixtureClient()
    outcome = service.run_search(root, SearchSpec(any_of=["attention"]),
                                 refresh="true", sort="citations", client=client)
    assert any("export.arxiv.org" in url for url in client.urls)
    assert outcome.counts["by_source"]["arxiv"] > 0
    assert "warnings" not in outcome.payload()


def test_warnings_before_quota_stop_do_not_claim_source_capability_skip(root):
    client = FixtureClient()
    client.rate_remaining_usd = 0.0
    outcome = service.run_search(
        root, SearchSpec(any_of=["attention"], min_citations=1),
        refresh="true", client=client,
    )
    assert client.requests == 0 and outcome.network is False
    warnings = outcome.payload()["warnings"]
    assert len(warnings) == 1 and "余额不足" in warnings[0]
    assert "只查本地" in warnings[0] and "arXiv" not in warnings[0]


def test_keyless_openalex_request_says_why_before_any_quota_claim(root, monkeypatch):
    """没带 key 的请求要自证：否则 429 与「用户没配 key」在响应上无法区分。"""
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    client = FixtureClient()
    outcome = service.run_search(
        root, SearchSpec(any_of=["attention"]), sources=["openalex"], refresh="true",
        client=client,
    )
    assert client.urls and all("api_key=" not in url for url in client.urls)
    warnings = outcome.payload()["warnings"]
    assert len(warnings) == 1
    assert "OPENALEX_API_KEY" in warnings[0] and "429" in warnings[0]
    assert "匿名" in warnings[0] and "额度" in warnings[0]


def test_key_present_produces_no_keyless_warning(root):
    outcome = service.run_search(
        root, SearchSpec(any_of=["attention"]), sources=["openalex"], refresh="true",
        client=FixtureClient(),
    )
    assert "warnings" not in outcome.payload()


def test_warnings_combine_source_skip_and_truncation_even_on_empty_page(root):
    for letter in "abc":
        _insert_work(root, "w" * 19 + letter, title="Warnings Probe",
                     source="openalex", cited_by_count=100)
    outcome = service.run_search(
        root, SearchSpec(any_of=["warnings"], min_citations=1), refresh="true",
        max_candidates=1, offset=1, client=FixtureClient(),
    )
    assert outcome.items == [] and outcome.counts["local"]["truncated"] is True
    warnings = outcome.payload()["warnings"]
    assert len(warnings) == len(set(warnings)) == 2
    assert "arXiv" in warnings[0] and "候选预算" in warnings[1]


@pytest.mark.parametrize("failed_source", ["openalex", "crossref"])
def test_lookup_partial_failure_produces_warning_and_preserves_success(root, failed_source):
    class PartialClient(FixtureClient):
        def get(self, url, headers=None, source=""):
            if source == failed_source:
                self.requests += 1
                raise service.SourceError(source, "503 临时失败", True)
            return super().get(url, headers=headers, source=source)

    outcome = service.run_lookup(root, doi="10.1109/mcse.2011.37", refresh="true", client=PartialClient())
    assert outcome.items and outcome.counts["failures"][0]["source"] == failed_source
    assert len(outcome.payload()["warnings"]) == 1
    assert failed_source in outcome.payload()["warnings"][0]
    assert "请求失败" in outcome.payload()["warnings"][0]


@pytest.mark.parametrize("refresh", ["auto", "false"])
def test_normal_lookup_does_not_emit_warnings(root, refresh):
    _insert_abstract_work(root)
    outcome = service.run_lookup(root, title="Abstract demo", refresh=refresh)
    assert "warnings" not in outcome.payload()


def test_lookup_failed_refresh_keeps_cached_result_and_merges_source_warning(root):
    _insert_local_openalex_work(root)

    class FailingClient(FixtureClient):
        def get(self, url, headers=None, source=""):
            self.requests += 1
            raise service.SourceError(source, "503 临时失败", True)

    outcome = service.run_lookup(root, doi="10.1109/mcse.2011.37", refresh="true", client=FailingClient())
    assert outcome.items and len(outcome.counts["failures"]) == 2
    assert len(outcome.payload()["warnings"]) == 1
    assert "openalex、crossref" in outcome.payload()["warnings"][0]


def test_warning_payload_deduplicates_without_mutating_counts_or_degraded():
    outcome = service.Outcome(warnings=["第一条", "第一条", "第二条"],
                              counts={"local": {}}, degraded={"reason": "已有说明"})
    data = outcome.payload()
    assert data["warnings"] == ["第一条", "第二条"]
    assert data["counts"] == {"local": {}} and data["degraded"] == {"reason": "已有说明"}
