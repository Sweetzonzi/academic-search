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


@dataclass
class SearchSpec:
    """一次检索式；spec_hash 的规范化见 config.canonical_spec。"""

    all_of: list[str] = field(default_factory=list)
    any_of: list[str] = field(default_factory=list)
    venues: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    since_years: int | None = None
