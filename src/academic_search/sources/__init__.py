"""检索源的请求、分页、限流与逐源字段映射。

契约：docs/接口/数据源适配.md。本层只返回记录对象，不写库（DEV-CONV §4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class SourceError(Exception):
    """某个源的失败；reason 进 counts.failures[]，不阻断其它源。"""

    def __init__(self, source: str, reason: str, retryable: bool = True):
        super().__init__(source + ": " + reason)
        self.source = source
        self.reason = reason
        self.retryable = retryable


#: DOI 的 URL / scheme 前缀。**前缀由 adaptor 负责剥**（IFACE-STORE §1）。
_DOI_PREFIXES = (
    "https://doi.org/", "http://doi.org/",
    "https://dx.doi.org/", "http://dx.doi.org/",
    "doi:",
)


def normalize_doi(value: str | None) -> str | None:
    """DOI → 裸小写形式（10.xxxx/yyy）。

    OpenAlex 的 `ids.doi` 与 `locations[].doi` 都写成 https://doi.org/… 形式（实测），
    不剥前缀会有两处后果：① 与 Crossref / arXiv 的裸 DOI 永远撞不上（L0 失效）；
    ② arXiv 的 DataCite DOI（10.48550/arxiv.…）桥也挂不上，同一条论文会裂成两个 work。
    """
    text = (value or "").strip().lower()
    for prefix in _DOI_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip()
    return text or None


@dataclass
class SearchSpec:
    """一次检索式；spec_hash 的规范化见 config.canonical_spec。"""

    all_of: list[str] = field(default_factory=list)
    any_of: list[str] = field(default_factory=list)
    venues: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    since_years: int | None = None
