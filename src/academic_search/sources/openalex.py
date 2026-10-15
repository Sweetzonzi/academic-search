"""OpenAlex 客户端与字段映射（IFACE-SOURCE §3.1）。"""

from __future__ import annotations

import re

from . import SourceError, SearchSpec
from .http import HttpResponse, build_url

BASE = "https://api.openalex.org/works"
SELECT = (
    "id,doi,title,display_name,publication_date,updated_date,type,language,"
    "primary_location,best_oa_location,locations,open_access,ids,authorships,"
    "abstract_inverted_index,cited_by_count,counts_by_year,fwci,"
    "citation_normalized_percentile,is_retracted,publication_year"
)

#: 规范 type（IFACE-SOURCE §4）。
TYPE_MAP = {
    "article": "article",
    "review": "article",
    "letter": "article",
    "preprint": "preprint",
    "book-chapter": "chapter",
    "dissertation": "thesis",
    "paratext": "paratext",
    "editorial": "paratext",
    "erratum": "paratext",
    "peer-review": "paratext",
    "libguides": "paratext",
    "supplementary-materials": "paratext",
}

_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", re.IGNORECASE)
_ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)


def normalize_type(raw: str | None) -> str:
    return TYPE_MAP.get((raw or "").lower(), "other")


def restore_abstract(inverted: dict | None) -> str | None:
    """abstract_inverted_index（{token: [pos…]}）按位置还原成近似原文。"""
    if not inverted:
        return None
    positions: dict[int, str] = {}
    for token, places in inverted.items():
        for place in places:
            positions[int(place)] = token
    if not positions:
        return None
    return " ".join(positions[i] for i in range(max(positions) + 1) if i in positions)


def _strip_id(value: str | None, prefix: str) -> str | None:
    if not value:
        return None
    return value.rstrip("/").rsplit("/", 1)[-1]


def bridges_from_locations(locations: list[dict] | None) -> list[tuple[str, str]]:
    """从 locations[] 找 arXiv 副本，作为 L1 显式桥（IFACE-SOURCE §8）。"""
    found: list[tuple[str, str]] = []
    for location in locations or []:
        for key in ("landing_page_url", "pdf_url"):
            match = _ARXIV_URL_RE.search(location.get(key) or "")
            if match:
                value = match.group(1)
                value = re.sub(r"v\d+$", "", value, flags=re.IGNORECASE)
                found.append(("arxiv", value.lower()))
        doi = location.get("doi") or ""
        doi_match = _ARXIV_DOI_RE.match(doi)
        if doi_match:
            found.append(("arxiv", doi_match.group(1).lower()))
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def parse_work(work: dict) -> dict:
    """OpenAlex work -> 本库 record 形状（含 idents / bridges）。"""
    source_id = _strip_id(work.get("id"), "W") or ""
    if not source_id:
        raise SourceError("openalex", "记录缺少 work id", False)
    source_id = source_id.lower()
    ids = work.get("ids") or {}
    idents: list[tuple[str, str]] = [("openalex", source_id)]
    if ids.get("doi"):
        idents.append(("doi", ids["doi"].strip().lower()))
    if ids.get("pmid"):
        idents.append(("pmid", (_strip_id(ids["pmid"], "") or "").lower()))
    if ids.get("pmcid"):
        idents.append(("pmcid", (_strip_id(ids["pmcid"], "") or "").lower()))
    idents = [(ns, value) for ns, value in idents if value]

    best = work.get("best_oa_location") or {}
    primary = work.get("primary_location") or {}
    percentile = work.get("citation_normalized_percentile")
    if isinstance(percentile, dict):
        percentile = percentile.get("value")
    return {
        "source": "openalex",
        "source_id": source_id,
        "title": work.get("title") or work.get("display_name"),
        "abstract": restore_abstract(work.get("abstract_inverted_index")),
        "authors": [
            (a.get("author") or {}).get("display_name")
            for a in (work.get("authorships") or [])
            if (a.get("author") or {}).get("display_name")
        ],
        "published_at": work.get("publication_date"),
        "updated_at": (work.get("updated_date") or "")[:10] or None,
        "type": normalize_type(work.get("type")),
        "venue": ((primary.get("source") or {}) or {}).get("display_name"),
        "language": work.get("language"),
        "cited_by_count": work.get("cited_by_count"),
        "counts_by_year": work.get("counts_by_year"),
        "fwci": work.get("fwci"),
        "citation_normalized_percentile": percentile,
        "is_retracted": 1 if work.get("is_retracted") else 0,
        "oa_status": (work.get("open_access") or {}).get("oa_status"),
        "pdf_url": best.get("pdf_url"),
        "idents": idents,
        "bridges": bridges_from_locations(work.get("locations")),
        "raw_type": work.get("type"),
    }


def build_filter(spec: SearchSpec, *, from_date: str | None, to_date: str | None) -> str:
    parts: list[str] = []
    for keyword in spec.all_of:
        parts.append("title_and_abstract.search:" + keyword)
    if spec.any_of:
        parts.append("title_and_abstract.search:" + "|".join(spec.any_of))
    for venue in spec.venues:
        parts.append("primary_location.source.display_name.search:" + venue)
    for author in spec.authors:
        parts.append("raw_author_name.search:" + author)
    if from_date:
        parts.append("from_publication_date:" + from_date)
    if to_date:
        parts.append("to_publication_date:" + to_date)
    return ",".join(parts)


def search_url(
    spec: SearchSpec,
    *,
    limit: int,
    api_key: str | None,
    sort: str = "date",
    from_date: str | None = None,
    to_date: str | None = None,
    cursor: str | None = None,
) -> str:
    sort_value = "cited_by_count:desc" if sort == "citations" else "publication_date:desc"
    params = {
        "filter": build_filter(spec, from_date=from_date, to_date=to_date),
        "per_page": min(max(limit, 1), 100),
        "select": SELECT,
        "sort": sort_value,
        "cursor": cursor if cursor else None,
        "api_key": api_key,
    }
    return build_url(BASE, params)


def search(client, spec: SearchSpec, *, limit: int, api_key: str | None = None,
           sort: str = "date", from_date: str | None = None,
           to_date: str | None = None) -> list[dict]:
    if not build_filter(spec, from_date=from_date, to_date=to_date):
        return []
    response = client.get(search_url(spec, limit=limit, api_key=api_key, sort=sort,
                                    from_date=from_date, to_date=to_date),
                          source="openalex")
    payload = response.json()
    return [parse_work(w) for w in (payload.get("results") or [])]


def _single(client, path: str, api_key: str | None = None) -> dict | None:
    url = BASE + "/" + path
    if api_key:
        url = build_url(url, {"api_key": api_key})
    try:
        response = client.get(url, source="openalex")
    except SourceError as exc:
        if not exc.retryable and "404" in exc.reason:
            return None
        raise
    return response.json()


def work_by_doi(client, doi: str, api_key: str | None = None) -> dict | None:
    work = _single(client, "doi:" + doi, api_key)
    return parse_work(work) if work else None


def work_by_arxiv(client, arxiv_id: str, api_key: str | None = None) -> dict | None:
    work = _single(client, "doi:10.48550/arXiv." + arxiv_id, api_key)
    return parse_work(work) if work else None
