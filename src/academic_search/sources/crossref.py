"""Crossref 客户端：初版只用于 lookup 兜 DOI（IFACE-SOURCE §3.3）。

本机尚未实测，字段以官方 REST API 文档为准；M1 落地时用一次真实请求复核。
"""

from __future__ import annotations

import urllib.parse

from . import SourceError
from .. import contenthash

BASE = "https://api.crossref.org/works"

TYPE_MAP = {
    "journal-article": "article",
    "proceedings-article": "article",
    "posted-content": "preprint",
    "book-chapter": "chapter",
    "reference-entry": "chapter",
    "dissertation": "thesis",
    "editorial": "paratext",
    "erratum": "paratext",
    "peer-review": "paratext",
    "component": "paratext",
}


def normalize_type(raw: str | None) -> str:
    return TYPE_MAP.get((raw or "").lower(), "other")


def _date_from_parts(parts) -> str | None:
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
        return None
    values = list(parts[0]) + [1, 1]
    try:
        year, month, day = int(values[0]), int(values[1]), int(values[2])
    except (TypeError, ValueError):
        return None
    return "%04d-%02d-%02d" % (year, month, day)


def _first(value) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return None


def parse_work(message: dict, doi: str) -> dict:
    authors: list[str] = []
    for author in message.get("author") or []:
        name = " ".join(p for p in [author.get("given"), author.get("family")] if p).strip()
        if not name:
            name = (author.get("name") or "").strip()
        if name:
            authors.append(name)
    abstract = message.get("abstract")
    if abstract:
        abstract = contenthash.strip_markup(abstract).strip() or None
    updated = (message.get("deposited") or {}).get("date-time") or (
        message.get("indexed") or {}
    ).get("date-time")
    return {
        "source": "crossref",
        "source_id": doi.lower(),
        "title": _first(message.get("title")),
        "abstract": abstract,
        "authors": authors,
        "published_at": _date_from_parts((message.get("issued") or {}).get("date-parts")),
        "updated_at": (updated or "")[:10] or None,
        "type": normalize_type(message.get("type")),
        "venue": _first(message.get("container-title")),
        "language": message.get("language"),
        "idents": [("doi", doi.lower())],
    }


def lookup(client, doi: str, *, mailto: str | None = None) -> dict | None:
    url = BASE + "/" + urllib.parse.quote(doi, safe="")
    headers = {}
    if mailto:
        headers["Accept"] = "application/json"
    try:
        response = client.get(url, headers=headers, source="crossref")
    except SourceError as exc:
        if "404" in exc.reason:
            return None
        raise
    payload = response.json()
    message = payload.get("message") or {}
    return parse_work(message, doi)
