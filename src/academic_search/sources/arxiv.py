"""arXiv 客户端与 Atom 字段映射（IFACE-SOURCE §3.2）。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from . import SourceError, SearchSpec
from .http import build_url

BASE = "http://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"

_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)
_ABS_RE = re.compile(r"abs/([^/?#]+)")


def _text(element, path: str) -> str | None:
    found = element.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def build_search_query(
    spec: SearchSpec, *, from_date: str | None = None, to_date: str | None = None
) -> str:
    parts: list[str] = []
    for keyword in spec.all_of:
        parts.append('abs:"' + keyword + '"')
    if spec.any_of:
        parts.append("(" + " OR ".join('abs:"' + k + '"' for k in spec.any_of) + ")")
    for author in spec.authors:
        parts.append('au:"' + author + '"')
    if from_date or to_date:
        low = (from_date.replace("-", "") + "0000") if from_date else "000000000000"
        high = (to_date.replace("-", "") + "2359") if to_date else "999999999999"
        parts.append("submittedDate:[" + low + " TO " + high + "]")
    return " AND ".join(parts)


def parse_entry(entry) -> dict:
    raw_id = _text(entry, ATOM + "id") or ""
    match = _ABS_RE.search(raw_id)
    value = match.group(1) if match else raw_id.rstrip("/").rsplit("/", 1)[-1]
    value = _VERSION_RE.sub("", value)
    if not value:
        raise SourceError("arxiv", "条目缺少 id", False)
    lowercase = value.lower()
    authors = [
        name for name in (_text(a, ATOM + "name") for a in entry.findall(ATOM + "author")) if name
    ]
    doi = _text(entry, ARXIV + "doi")
    idents: list[tuple[str, str]] = [("arxiv", lowercase)]
    if doi:
        idents.append(("doi", doi))
    return {
        "source": "arxiv",
        "source_id": lowercase,
        "title": _text(entry, ATOM + "title"),
        "abstract": _text(entry, ATOM + "summary"),
        "authors": authors,
        "published_at": (_text(entry, ATOM + "published") or "")[:10] or None,
        "updated_at": (_text(entry, ATOM + "updated") or "")[:10] or None,
        "type": "preprint",
        "venue": None,
        "language": "en",
        "oa_status": "green",
        "pdf_url": "https://arxiv.org/pdf/" + lowercase,
        "idents": idents,
    }


def parse_feed(text: str) -> list[dict]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SourceError("arxiv", "Atom 解析失败：" + str(exc), True) from exc
    return [parse_entry(entry) for entry in root.findall(ATOM + "entry")]


def search(
    client,
    spec: SearchSpec,
    *,
    limit: int,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    query = build_search_query(spec, from_date=from_date, to_date=to_date)
    if not query:
        return []
    url = build_url(
        BASE,
        {
            "search_query": query,
            "start": 0,
            "max_results": min(max(limit, 1), 2000),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
    )
    return parse_feed(client.get(url, source="arxiv").text)


def lookup(client, arxiv_id: str) -> list[dict]:
    url = build_url(BASE, {"id_list": arxiv_id, "max_results": 1})
    return parse_feed(client.get(url, source="arxiv").text)
