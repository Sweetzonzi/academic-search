"""OpenAlex 客户端与字段映射（IFACE-SOURCE §3.1）。"""

from __future__ import annotations

import re
from typing import Iterable

from . import SourceError, SearchSpec, normalize_doi
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
        doi_match = _ARXIV_DOI_RE.match(normalize_doi(location.get("doi")) or "")
        if doi_match:
            found.append(("arxiv", doi_match.group(1).lower()))
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def carrier_source_ids(locations: list[dict] | None) -> list[str]:
    """作品的载体 source id 集合（ADR-0060 决策 1/4）。

    取 `locations[].source.id` 的并集——`locations.` 比 `primary_location.` 宽，
    能覆盖「主载体是预印本仓库、同一作品也挂在会议 proceedings 上」的情形。
    本地与远端的 venue 判据都读这份集合，不再对 `venue` 字符串做子串匹配。
    """
    found: list[str] = []
    for location in locations or []:
        value = _strip_id((location.get("source") or {}).get("id"), "S")
        if value:
            found.append(value.lower())
    return sorted(set(found))


def parse_work(work: dict) -> dict:
    """OpenAlex work -> 本库 record 形状（含 idents / bridges）。"""
    source_id = _strip_id(work.get("id"), "W") or ""
    if not source_id:
        raise SourceError("openalex", "记录缺少 work id", False)
    source_id = source_id.lower()
    ids = work.get("ids") or {}
    idents: list[tuple[str, str]] = [("openalex", source_id)]
    doi = normalize_doi(ids.get("doi"))
    if doi:
        idents.append(("doi", doi))
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
        "venue_source_ids": carrier_source_ids(work.get("locations")),
        "raw_type": work.get("type"),
    }


#: 一次远端取数固定取一页 100 条（ADR-0062 决策 5）。计费按次不按条，实测
#: `per-page=1` 与 `per-page=100` 同价，所以让小 `--limit` 限制取数是纯损失；
#: `--limit` 自此只截断展示。单页上限仍是 100，要更多得走 cursor（ADR-0062 代价 4）。
FETCH_PAGE = 100

#: `--sort` 档 → OpenAlex 的排序值。**排序始终显式下发**：不带 `sort` 时
#: `search.` 的默认排序是 relevance，不是日期（ADR-0063 决策 6）。
_SORTS = {
    "date": "publication_date:desc",
    "citations": "cited_by_count:desc",
}


def sort_value(sort: str | None) -> str:
    return _SORTS.get(sort or "date", _SORTS["date"])


def _quote(value: str) -> str:
    """主题值一律加双引号，值里的 `"` 直接剥掉、不转义（ADR-0063 决策 3–4）。"""
    return '"' + str(value).replace('"', "") + '"'


def build_search_query(spec: SearchSpec) -> str:
    """主题条件 → `search.title_and_abstract=` 的单个布尔串（ADR-0063 决策 1–2）。

    每个值加引号、`all_of` 组内用 ` AND `、`any_of` 组内用 ` OR `、两组各自加括号
    再用 ` AND ` 连接。两组都为空时返回空串（调用方因此不发这个参数）。
    """
    groups: list[str] = []
    for values, joiner in ((spec.all_of, " AND "), (spec.any_of, " OR ")):
        cleaned = [_quote(v) for v in values if str(v).strip()]
        if cleaned:
            groups.append("(" + joiner.join(cleaned) + ")")
    return " AND ".join(groups)


def build_filter(
    spec: SearchSpec,
    *,
    from_date: str | None,
    to_date: str | None,
    venue_ids: Iterable[str] = (),
) -> str:
    """结构化条件 → `filter=`（ADR-0063 决策 5）；**主题条件不在这里**。

    `venue_ids` 是已解析的 OpenAlex source id（会名 → S-id 的登记表在 `venues.py`）；
    `spec.venues` 里写的是会名，本函数只认 id，因此给了 `venues` 却没给解析结果时
    直接报错——静默丢掉一个过滤条件比报错危险（ADR-0060 决策 2/3）。
    """
    if spec.venues and not venue_ids:
        raise SourceError("openalex", "venues 未经 venues.resolve() 解析出 source id", False)
    parts: list[str] = []
    ids = [str(v).strip() for v in venue_ids if str(v).strip()]
    if ids:
        parts.append("locations.source.id:" + "|".join(ids))
    for author in spec.authors:
        parts.append("raw_author_name.search:" + author)
    if from_date:
        parts.append("from_publication_date:" + from_date)
    if to_date:
        parts.append("to_publication_date:" + to_date)
    if spec.min_citations is not None:
        parts.append("cited_by_count:>" + str(int(spec.min_citations)))
    if spec.min_fwci is not None:
        parts.append("fwci:>" + str(spec.min_fwci))
    return ",".join(parts)


def search_url(
    spec: SearchSpec,
    *,
    api_key: str | None,
    sort: str = "date",
    from_date: str | None = None,
    to_date: str | None = None,
    venue_ids: Iterable[str] = (),
    cursor: str | None = None,
) -> str:
    params = {
        "filter": build_filter(
            spec, from_date=from_date, to_date=to_date, venue_ids=venue_ids
        ),
        "search.title_and_abstract": build_search_query(spec),
        "per_page": FETCH_PAGE,
        "select": SELECT,
        "sort": sort_value(sort),
        "cursor": cursor if cursor else None,
        "api_key": api_key,
    }
    return build_url(BASE, params)


def search(client, spec: SearchSpec, *, api_key: str | None = None,
           sort: str = "date", from_date: str | None = None,
           to_date: str | None = None, venue_ids: Iterable[str] = ()) -> list[dict]:
    if not build_filter(spec, from_date=from_date, to_date=to_date, venue_ids=venue_ids) \
            and not build_search_query(spec):
        return []
    response = client.get(search_url(spec, api_key=api_key, sort=sort,
                                    from_date=from_date, to_date=to_date,
                                    venue_ids=venue_ids),
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
