"""sources：逐源映射与字段解析（用 tests/fixtures/ 的真实响应，不联网）。"""

from __future__ import annotations

import json
import pathlib

import pytest

from academic_search import identity
from academic_search.sources import SearchSpec, SourceError
from academic_search.sources import arxiv, crossref, openalex
from academic_search.sources.http import HttpResponse

FIX = pathlib.Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.requests = 0
        self.cost_usd = 0.0
        self.rate_remaining_usd = None

    def get(self, url, headers=None, source=""):
        self.requests += 1
        for needle, response in self.mapping.items():
            if needle in url:
                if isinstance(response, Exception):
                    raise response
                return HttpResponse(url, 200, response, {})
        raise AssertionError("no fixture for " + url)


def _text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def openalex_payload():
    return json.loads(_text("openalex_work.json"))


def test_openalex_restore_abstract():
    inverted = {"Hello": [0], "world": [1], "again": [3]}
    assert openalex.restore_abstract(inverted) == "Hello world again"
    assert openalex.restore_abstract(None) is None


def test_openalex_parse_work_real_fixture(openalex_payload):
    record = openalex.parse_work(openalex_payload["results"][0])
    assert record["source"] == "openalex"
    assert record["source_id"] == record["source_id"].lower()
    assert record["title"]
    assert isinstance(record["authors"], list) and record["authors"]
    assert record["published_at"] and len(record["published_at"]) == 10
    assert record["type"] in {
        "article", "preprint", "chapter", "thesis", "paratext", "other"
    }
    namespaces = {ns for ns, _ in record["idents"]}
    assert "openalex" in namespaces
    uid = identity.record_uid(record["source"], record["source_id"])
    assert identity.normalize_source_id(record["source"], record["source_id"]) == record["source_id"]


def test_openalex_search_uses_fixture(openalex_payload):
    client = FakeClient({"api.openalex.org/works": json.dumps(openalex_payload)})
    records = openalex.search(client, SearchSpec(any_of=["world model"]))
    assert len(records) == 3
    assert client.requests == 1


def test_openalex_search_query_is_one_boolean_string():
    """主题条件收成一个带引号的布尔串（ADR-0063 决策 1–4）。"""
    spec = SearchSpec(all_of=["a", "b c"], any_of=["d", 'e"f'])
    assert openalex.build_search_query(spec) == '("a" AND "b c") AND ("d" OR "ef")'
    assert openalex.build_search_query(SearchSpec()) == ""


def test_openalex_build_filter_is_structured_only():
    spec = SearchSpec(all_of=["a"], venues=["ICRA"], authors=["Doe"],
                      min_citations=100, min_fwci=2.5)
    value = openalex.build_filter(
        spec, from_date="2024-01-01", to_date=None, venue_ids=["S4210217939"]
    )
    assert value == (
        "locations.source.id:S4210217939,"
        'raw_author_name.search:"Doe",'
        "from_publication_date:2024-01-01,"
        "cited_by_count:100-,"
        "fwci:2.5-"
    )


def test_openalex_authors_are_one_or_group():
    value = openalex.build_filter(
        SearchSpec(all_of=["decision"], authors=["Jane Doe", "Alice Smith"]),
        from_date="2024-01-01", to_date=None,
    )
    assert value == ('raw_author_name.search:"Jane Doe" OR "Alice Smith",'
                     'from_publication_date:2024-01-01')


def test_openalex_fwci_lower_bound_uses_decimal_not_epsilon():
    value = openalex.build_filter(SearchSpec(min_fwci=0.00001), from_date=None, to_date=None)
    assert value == "fwci:0.00001-"


def test_openalex_build_filter_refuses_unresolved_venue():
    """给了 venues 却没给解析结果时直接报错：静默丢过滤条件比报错危险（ADR-0060）。"""
    with pytest.raises(SourceError):
        openalex.build_filter(SearchSpec(venues=["ICRA"]), from_date=None, to_date=None)


def test_openalex_url_fixes_page_size_and_always_sorts():
    """一次取数固定一页 100 条、排序始终显式下发（ADR-0062 决策 5/6、ADR-0063 决策 6）。"""
    url = openalex.search_url(SearchSpec(all_of=["a"]), api_key=None, sort="citations")
    assert "per_page=100" in url
    assert "sort=cited_by_count%3Adesc" in url
    assert "search.title_and_abstract=%28%22a%22%29" in url
    assert "publication_date%3Adesc" in openalex.search_url(
        SearchSpec(all_of=["a"]), api_key=None
    )


def test_openalex_carrier_source_ids():
    """载体集合取 `locations[].source.id` 的并集（ADR-0060 决策 1）。"""
    locations = [
        {"source": {"id": "https://openalex.org/S4210217939"}},
        {"source": {"id": "https://openalex.org/S4306400194"}},
        {"source": {"id": "https://openalex.org/S4210217939"}},
        {"source": None},
        {},
    ]
    assert openalex.carrier_source_ids(locations) == ["s4210217939", "s4306400194"]
    assert openalex.carrier_source_ids(None) == []


def test_openalex_bridges_from_arxiv_location():
    locations = [
        {"landing_page_url": "https://arxiv.org/abs/1501.00001v2", "doi": None},
        {"pdf_url": None, "doi": "10.48550/arXiv.2210.03629"},
    ]
    assert openalex.bridges_from_locations(locations) == [
        ("arxiv", "1501.00001"),
        ("arxiv", "2210.03629"),
    ]


def test_openalex_doi_is_normalised():
    # OpenAlex 的 ids.doi / locations[].doi 都是 https://doi.org/… 形式（fixture 实测）：
    # 不剥前缀，L0 的 DOI 判据和 arXiv 的 DataCite DOI 桥都会失效
    record = openalex.parse_work({
        "id": "https://openalex.org/W1",
        "ids": {"doi": "https://doi.org/10.1109/MCSE.2011.37"},
        "locations": [{"doi": "https://doi.org/10.48550/arXiv.2210.03629"}],
    })
    assert ("doi", "10.1109/mcse.2011.37") in record["idents"]
    assert ("arxiv", "2210.03629") in record["bridges"]


def test_openalex_real_fixture_doi_is_bare(openalex_payload):
    record = openalex.parse_work(openalex_payload["results"][0])
    assert record["idents"][0] == ("openalex", "w2146292423")
    assert ("doi", "10.1109/mcse.2011.37") in record["idents"]


def test_arxiv_search_parses_real_fixture():
    client = FakeClient({"export.arxiv.org/api/query": _text("arxiv_feed.xml")})
    records = arxiv.search(client, SearchSpec(any_of=["control"]))
    assert len(records) == 2
    first = records[0]
    assert first["source"] == "arxiv"
    assert first["source_id"] == "2610.18808"
    assert first["type"] == "preprint"
    assert first["language"] == "en"
    assert first["pdf_url"] == "https://arxiv.org/pdf/2610.18808"
    assert first["abstract"]
    assert ("arxiv", "2610.18808") in first["idents"]
    # 版本从 <id> 取出来记成元数据事实，source_id 本身不带版本（ADR-0067 决策 3）
    assert first["source_version"] == "v1"
    assert "v" not in first["source_id"]


def test_arxiv_search_query_shape():
    query = arxiv.build_search_query(
        SearchSpec(all_of=["world model"], any_of=["UAV", "drone"], authors=["Doe"]),
        from_date="2024-01-01",
        to_date="2024-12-31",
    )
    assert query == (
        'abs:"world model" AND (abs:"UAV" OR abs:"drone") AND au:"Doe" '
        "AND submittedDate:[202401010000 TO 202412312359]"
    )


def test_arxiv_search_query_omits_degenerate_date_bounds():
    # 实测：arXiv 对退化的边界值（000000000000 / * / 999999999999）一律回 HTTP 500
    only_upper = arxiv.build_search_query(
        SearchSpec(all_of=["world model"]), from_date=None, to_date="2026-10-15"
    )
    assert "submittedDate" not in only_upper
    only_lower = arxiv.build_search_query(
        SearchSpec(all_of=["world model"]), from_date="2024-01-01", to_date=None
    )
    assert only_lower.endswith("AND submittedDate:[202401010000 TO 999912312359]")


def test_arxiv_authors_are_ored_and_grouped_with_other_conditions():
    query = arxiv.build_search_query(
        SearchSpec(all_of=["decision"], authors=["Jane Doe", "Alice Smith"]),
    )
    assert query == 'abs:"decision" AND (au:"Jane Doe" OR au:"Alice Smith")'


def test_crossref_lookup_parses_real_fixture():
    client = FakeClient({"api.crossref.org/works/": _text("crossref_work.json")})
    record = crossref.lookup(client, "10.1109/mcse.2011.37")
    assert record is not None
    assert record["source"] == "crossref"
    assert record["source_id"] == "10.1109/mcse.2011.37"
    assert record["idents"] == [("doi", "10.1109/mcse.2011.37")]
    assert record["type"] in {"article", "preprint", "chapter", "thesis", "paratext", "other"}


def test_crossref_404_returns_none():
    class NotFound(FakeClient):
        def get(self, url, headers=None, source=""):
            raise SourceError("crossref", "404 未找到", False)

    assert crossref.lookup(NotFound({}), "10.1/nope") is None


def test_crossref_title_and_venue_lose_markup_and_padding():
    """`title` / `container-title` 都剥 JATS 标记并折叠空白（IFACE-SOURCE §3.3）。"""
    record = crossref.parse_work(
        {
            "title": ["<scp>GenAI</scp>\n        Multi‐agent Systems"],
            "container-title": ["Generative           AI           for Vehicles"],
            "type": "other",
        },
        "10.1002/9781394426713.ch11",
    )
    assert record["title"] == "GenAI Multi-agent Systems"
    assert record["venue"] == "Generative AI for Vehicles"


def test_crossref_abstract_drops_jats_heading():
    """`jats:title` 是标题元素、不是摘要内容，不能留下字面的 `Abstract`（同 §3.3）。"""
    record = crossref.parse_work(
        {
            "title": ["T"],
            "abstract": "<jats:title>Abstract</jats:title>\n<jats:p>\n  Array programming…",
        },
        "10.1038/s41586-020-2649-2",
    )
    assert record["abstract"] == "Array programming..."


def test_crossref_lookup_puts_mailto_in_request():
    """`mailto` 要真的进请求（查询串或 User-Agent），否则进不了 polite pool。"""
    seen: dict = {}

    class Recorder(FakeClient):
        def get(self, url, headers=None, source=""):
            seen["url"] = url
            seen["headers"] = headers or {}
            return super().get(url, headers=headers, source=source)

    client = Recorder({"api.crossref.org/works/": _text("crossref_work.json")})
    crossref.lookup(client, "10.1109/mcse.2011.37", mailto="me@example.com")
    assert "mailto=me%40example.com" in seen["url"]
    assert "mailto:me@example.com" in seen["headers"].get("User-Agent", "")


def test_crossref_lookup_without_mailto_keeps_plain_request():
    seen: dict = {}

    class Recorder(FakeClient):
        def get(self, url, headers=None, source=""):
            seen["url"] = url
            seen["headers"] = headers or {}
            return super().get(url, headers=headers, source=source)

    client = Recorder({"api.crossref.org/works/": _text("crossref_work.json")})
    crossref.lookup(client, "10.1109/mcse.2011.37")
    assert "mailto" not in seen["url"]
    assert "User-Agent" not in seen["headers"]


def test_type_maps_cover_contract():
    assert openalex.normalize_type("journal-article") == "other"
    assert openalex.normalize_type("review") == "article"
    assert openalex.normalize_type("erratum") == "paratext"
    assert crossref.normalize_type("journal-article") == "article"
    assert crossref.normalize_type("posted-content") == "preprint"
    assert crossref.normalize_type("strange-type") == "other"
