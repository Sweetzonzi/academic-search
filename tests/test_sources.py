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
    records = openalex.search(client, SearchSpec(any_of=["world model"]), limit=3)
    assert len(records) == 3
    assert client.requests == 1


def test_openalex_build_filter_and_or():
    spec = SearchSpec(all_of=["a", "b"], any_of=["c", "d"], venues=["ICRA"], authors=["Doe"])
    value = openalex.build_filter(spec, from_date="2024-01-01", to_date=None)
    assert value == (
        "title_and_abstract.search:a,title_and_abstract.search:b,"
        "title_and_abstract.search:c|d,"
        "primary_location.source.display_name.search:ICRA,"
        "raw_author_name.search:Doe,"
        "from_publication_date:2024-01-01"
    )


def test_openalex_bridges_from_arxiv_location():
    locations = [
        {"landing_page_url": "https://arxiv.org/abs/1501.00001v2", "doi": None},
        {"pdf_url": None, "doi": "10.48550/arXiv.2210.03629"},
    ]
    assert openalex.bridges_from_locations(locations) == [
        ("arxiv", "1501.00001"),
        ("arxiv", "2210.03629"),
    ]


def test_arxiv_search_parses_real_fixture():
    client = FakeClient({"export.arxiv.org/api/query": _text("arxiv_feed.xml")})
    records = arxiv.search(client, SearchSpec(any_of=["control"]), limit=2)
    assert len(records) == 2
    first = records[0]
    assert first["source"] == "arxiv"
    assert first["source_id"] == "2610.18808"
    assert first["type"] == "preprint"
    assert first["language"] == "en"
    assert first["pdf_url"] == "https://arxiv.org/pdf/2610.18808"
    assert first["abstract"]
    assert ("arxiv", "2610.18808") in first["idents"]


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


def test_type_maps_cover_contract():
    assert openalex.normalize_type("journal-article") == "other"
    assert openalex.normalize_type("review") == "article"
    assert openalex.normalize_type("erratum") == "paratext"
    assert crossref.normalize_type("journal-article") == "article"
    assert crossref.normalize_type("posted-content") == "preprint"
    assert crossref.normalize_type("strange-type") == "other"
