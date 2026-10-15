"""源文本规范化与 content_hash。

契约：docs/接口/数据源适配.md §6。顺序固定、五步都做：
剥标记 → NFKC → 连字符/引号统一 → 空白折叠 → （标题/摘要类）小写。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

#: 剥除 HTML / JATS / MathML 标签，标签内文本保留。
_TAG_RE = re.compile(r"<[^>]+>")

#: 空白折叠（涵盖换行、制表）。
_WS_RE = re.compile(r"\s+", re.UNICODE)

#: 各种连字符 → ASCII '-'（U+2010…U+2015、U+2212）。
_HYPHENS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"

#: 弯引号 → 直引号。
_QUOTES = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u2032": "'",
    "\u2033": '"',
}

#: 空白字符里 NFKC 不一定折叠的窄空格/不换行空格。
_NBSP = {
    "\u00a0": " ",
    "\u2007": " ",
    "\u202f": " ",
    "\u2009": " ",
    "\u200a": " ",
    "\u3000": " ",
}

#: 大小写不敏感的 kind：标题与摘要类；正文类保留大小写（IFACE-SOURCE §6 第 5 条）。
CASE_INSENSITIVE_KINDS = frozenset(
    {"title", "abstract", "summary.oneline", "summary.points", "summary.mrl"}
)


def _make_translate_table() -> dict:
    table: dict = {}
    for ch in _HYPHENS:
        table[ord(ch)] = "-"
    for k, v in _QUOTES.items():
        table[ord(k)] = v
    return table


_TRANSLATE = str.maketrans(_make_translate_table())


def strip_markup(text: str) -> str:
    """剥除标签，标签内文本保留。"""
    if not text:
        return ""
    return _TAG_RE.sub(" ", text)


def normalize_source_text(text: str, kind: str) -> str:
    """把源文本规范化成 content_hash 的原像。

    kind 决定第 5 步是否小写；kind 未列入 CASE_INSENSITIVE_KINDS 时
    保留大小写（正文类）。
    """
    out = strip_markup(text or "")
    out = unicodedata.normalize("NFKC", out)
    out = out.translate(_TRANSLATE)
    for src, dst in _NBSP.items():
        out = out.replace(src, dst)
    out = _WS_RE.sub(" ", out).strip()
    if kind in CASE_INSENSITIVE_KINDS:
        out = out.lower()
    return out


def content_hash(text: str, kind: str) -> str:
    """sha256(规范化后的源文本) 的十六进制小写摘要。"""
    normalized = normalize_source_text(text, kind)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
