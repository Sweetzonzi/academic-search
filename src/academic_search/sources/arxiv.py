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

#: 一次远端取数固定取一页 100 条（ADR-0062 决策 5）；`--limit` 只截断展示。
#: arXiv 单次响应上限 2000，这里取与 OpenAlex 同一个页大小，两源口径一致。
FETCH_PAGE = 100


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
    # 日期子句只在**有下界**时下发。实测：arXiv 对退化的边界值（000000000000、*、
    # 999999999999）一律回 HTTP 500，而只有上界等于没有过滤（论文都在过去）。
    if from_date:
        low = from_date.replace("-", "") + "0000"
        high = (to_date.replace("-", "") + "2359") if to_date else "999912312359"
        parts.append("submittedDate:[" + low + " TO " + high + "]")
    return " AND ".join(parts)


def parse_entry(entry) -> dict:
    raw_id = _text(entry, ATOM + "id") or ""
    match = _ABS_RE.search(raw_id)
    value = match.group(1) if match else raw_id.rstrip("/").rsplit("/", 1)[-1]
    # 版本先从 id 尾部取出来再归一掉：同一篇的 v1/v2 是同一条 record，
    # 但「远端最新到第几版」是元数据事实，记在 record.source_version（ADR-0067 决策 3）。
    found = _VERSION_RE.search(value)
    source_version = found.group(0).lower() if found else None
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
        "source_version": source_version,
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
            "max_results": FETCH_PAGE,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
    )
    return parse_feed(client.get(url, source="arxiv").text)


def lookup(client, arxiv_id: str) -> list[dict]:
    url = build_url(BASE, {"id_list": arxiv_id, "max_results": 1})
    return parse_feed(client.get(url, source="arxiv").text)


# --------------------------------------------------------------------------
# 取件：e-print（LaTeX 源码）与 HTML（EX §3.5、ADR-0056 决策 3）
# --------------------------------------------------------------------------

EPRINT_BASE = "https://arxiv.org/e-print/"
HTML_BASE = "https://arxiv.org/html/"

#: e-print 防呆上限；arXiv 单篇源码实测 158–693 KB（EX §6.2）。
EPRINT_MAX_BYTES = 64 * 1024 * 1024

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-/]*$")
_URL_PREFIXES = (
    "https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv.org/abs/",
    "https://arxiv.org/pdf/", "http://arxiv.org/pdf/",
)


def normalise_id(value: str) -> str:
    """归一 id：去 arXiv: 与 URL 前缀；**保留版本后缀**（取件要的是具体版本）。"""
    text = (value or "").strip()
    if text.lower().startswith("arxiv:"):
        text = text[len("arxiv:"):].strip()
    for prefix in _URL_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    text = text.strip().strip("/")
    if not text or ".." in text or not _ID_RE.match(text):
        raise SourceError("arxiv", "不是合法的 arXiv id：" + repr(value), False)
    return text


def eprint_url(arxiv_id: str) -> str:
    return EPRINT_BASE + normalise_id(arxiv_id)


def html_url(arxiv_id: str) -> str:
    return HTML_BASE + normalise_id(arxiv_id)


def fetch_eprint(client, arxiv_id: str, *, max_bytes: int = EPRINT_MAX_BYTES) -> bytes:
    """下载 e-print 原始字节；格式判定与解包在 textify.unpack_eprint。"""
    response = client.get_bytes(eprint_url(arxiv_id), source="arxiv", max_bytes=max_bytes)
    if response.status != 200:
        raise SourceError("arxiv", "e-print HTTP " + str(response.status), response.status >= 500)
    return response.content


def fetch_html(client, arxiv_id: str) -> str:
    """取 /html/<id>；**可用性只看这里的状态码**，feed 里的 text/html 声明不可信（EX §3.5）。"""
    response = client.get_bytes(html_url(arxiv_id), source="arxiv")
    if response.status != 200:
        raise SourceError("arxiv", "HTML HTTP " + str(response.status), False)
    return response.content.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# 取原件：给 download 用，状态码与验证器交回调用方（ADR-0067）
# --------------------------------------------------------------------------

PDF_BASE = "https://arxiv.org/pdf/"

#: PDF 防呆上限：带图版的单篇论文实测可达数十 MB，超过即报错而不是截断。
PDF_MAX_BYTES = 128 * 1024 * 1024

#: 每条路线一次取件的字节上限；HTML 不给上限（它由 feed 侧的 HTML 版式决定，本就小）。
SOURCE_MAX_BYTES = {"latex": EPRINT_MAX_BYTES, "html": None, "pdf": PDF_MAX_BYTES}

SOURCE_BASES = {"latex": EPRINT_BASE, "html": HTML_BASE, "pdf": PDF_BASE}


def source_url(route: str, arxiv_id: str, *, version: str | None = None) -> str:
    """取件 URL；route 取 latex / html / pdf，给了 version 就钉住那一版。

    钉版本是为了让「取到的字节是哪一版」成为事实而不是猜测：arXiv 的
    /e-print/<id>v3 永久可取，返回的就是 v3 的字节（ADR-0067 决策 3）。
    """
    base = SOURCE_BASES.get(route)
    if base is None:
        raise SourceError("arxiv", "未知取件路线：" + str(route), False)
    plain = _VERSION_RE.sub("", normalise_id(arxiv_id))
    return base + plain + (version or "")


def fetch_source(
    client,
    route: str,
    arxiv_id: str,
    *,
    version: str | None = None,
    headers: dict | None = None,
):
    """取原件原始字节；**不抛 4xx/5xx**——304 与 404 都是正常回答，由调用方判（ADR-0067 决策 5）。"""
    return client.get_bytes(
        source_url(route, arxiv_id, version=version),
        source="arxiv",
        headers=headers,
        raise_on_error=False,
        max_bytes=SOURCE_MAX_BYTES[route],
    )

