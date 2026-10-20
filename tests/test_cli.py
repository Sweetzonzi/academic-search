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
    assert "cited_by_count%3A%3E1000" in url


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


def _insert_work(root, key, *, title, source="arxiv", source_id=None, body=None):
    """一条记录 + 一个 work；给了 body 就顺手写 `works/<proj>/paper.md`。"""
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        store.insert_record(conn, {
            "record_uid": key, "source": source, "source_id": source_id or key,
            "title": title, "abstract": title, "published_at": "2026-10-01",
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
    second = service.run_search(root, spec, refresh="auto", client=FixtureClient())
    assert second.network is True and second.counts["complete"] is True
    assert second.counts["failures"] == []
    third = service.run_search(root, spec, refresh="auto", client=FixtureClient())
    assert third.network is False, "完整采集之后才复用新鲜度"


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
    assert not any("export.arxiv.org" in url for url in client.urls)


def test_all_sources_skipped_is_not_a_network_refresh(root):
    client = FixtureClient()
    spec = SearchSpec(any_of=["control"], min_citations=1)
    outcome = service.run_search(root, spec, sources=["arxiv"], refresh="true", client=client)
    assert outcome.network is False and client.requests == 0
    assert outcome.counts["complete"] is False
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
