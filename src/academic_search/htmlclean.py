"""arXiv HTML（LaTeXML）正文抽取、清洗与图件收集。

契约：docs/02-设计说明书.md §4.10 装配第 1、3 步、EX-2026-10-14-文本化 §3.4/§7。
lxml 是**可选依赖**（DEV-CONV §4）：缺失时 HTML 路线不可用，LaTeX 路线不受影响。
**未做**（EX §7 第 4、5 条）：单公式布局表的拆除、自定义宏展开。
"""

from __future__ import annotations

import base64
import os
import re
from urllib.parse import urljoin

from .textify import TextifyError

#: 只在网页里存在、从不属于正文的元素；这一轮清洗实测省 35%（EX §3.4）。
DROP_XPATH = (
    ".//nav",
    ".//*[contains(@class,'ltx_TOC')]",
    ".//*[contains(@class,'ltx_page_logo')]",
    ".//*[contains(@class,'ltx_tag_note')]",
    ".//*[contains(@class,'ltx_note_mark')]",
    ".//a[contains(@class,'ltx_note_backref')]",
    ".//*[contains(@class,'ltx_authors')]//*[contains(@class,'ltx_personname')]",
    ".//*[contains(@class,'ltx_ERROR')]",
)

#: 值得保留的属性；其余都是 LaTeXML / CSS 管道。
KEEP_ATTR = frozenset(
    {"id", "href", "src", "data", "alt", "title", "type", "colspan", "rowspan", "datetime"}
)

#: MathML 的语义都在属性里（display / accent / mathvariant），<math> 子树一律不碰。
MATH_TAGS = frozenset({"math", "semantics", "annotation", "annotation-xml"})

MEDIA_ATTRS = ("src", "data")

_DATA_URI = re.compile(
    r"^data:(?P<mime>[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+);base64,(?P<body>.*)$", re.I | re.S
)
EXT_BY_MIME = {
    "image/png": ".png", "image/jpeg": ".jpg",
    "image/svg+xml": ".svg", "image/gif": ".gif",
}

#: 单篇 HTML 的图件上限（EX §6.1：路线 B 每篇 2–16 张）。
MAX_MEDIA = 200

_SRC_STEM = re.compile(r"[^A-Za-z0-9._-]+")


def _lxml():
    try:
        from lxml import html as LH
    except Exception as exc:
        raise TextifyError(
            "html_route_unavailable", "没装 lxml，--source html 不可用：" + str(exc)
        ) from exc
    return LH


def _in_math(element) -> bool:
    """元素是否落在 <math> 子树内（含自身）。"""
    node = element
    while node is not None:
        tag = getattr(node, "tag", None)
        if isinstance(tag, str) and tag.lower() in MATH_TAGS:
            return True
        node = node.getparent()
    return False


def clean_latexml(html_text: str) -> tuple[str, dict]:
    """抽 <article> 并清掉网页管道；返回 (正文 HTML, 统计)。"""
    LH = _lxml()
    try:
        doc = LH.fromstring(html_text)
    except Exception as exc:
        raise TextifyError("html_parse_failed", "HTML 解析失败：" + str(exc)) from exc
    stats = {"dropped": 0, "attrs_removed": 0, "spans_unwrapped": 0, "math_kept": 0}
    articles = doc.xpath("//article")
    if articles:
        article = articles[0]
    else:
        bodies = doc.xpath("//body")
        article = bodies[0] if bodies else doc.getroot()
    for path in DROP_XPATH:
        for element in article.xpath(path):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
                stats["dropped"] += 1
    for element in article.iter():
        if not isinstance(element.tag, str):
            continue
        if _in_math(element):
            stats["math_kept"] += 1
            continue
        for name in list(element.attrib):
            if name not in KEEP_ATTR:
                del element.attrib[name]
                stats["attrs_removed"] += 1
    for span in article.xpath(".//span"):
        if _in_math(span):
            continue
        span.drop_tag()
        stats["spans_unwrapped"] += 1
    return LH.tostring(article, encoding="unicode"), stats


def media_name(entry: dict, index: int) -> str:
    """给图件一个稳定、安全的文件名；序号前缀用于避免同名相撞。"""
    raw = entry.get("path") or entry.get("url") or entry.get("src") or ""
    base = os.path.basename(raw.split("?")[0].split("#")[0])
    if entry["kind"] == "inline" and not base:
        base = "inline" + EXT_BY_MIME.get(entry.get("mime", ""), ".bin")
    base = _SRC_STEM.sub("_", base) or "figure"
    return str(index).zfill(2) + "-" + base


def collect_media(html_text: str, base_url: str = "") -> list[dict]:
    """收集 <img>/<object>/<embed> 的图件来源；内联 base64 就地解码（EX §7 第 9 条）。

    条目：{kind: inline|remote, src, mime?, payload?|url?, path?}
    """
    LH = _lxml()
    try:
        doc = LH.fromstring(html_text)
    except Exception as exc:
        raise TextifyError("html_parse_failed", "HTML 解析失败：" + str(exc)) from exc
    entries: list[dict] = []
    seen: set = set()
    for element in doc.iter():
        if not isinstance(element.tag, str):
            continue
        for attr in MEDIA_ATTRS:
            value = (element.get(attr) or "").strip()
            if not value or value in seen:
                continue
            match = _DATA_URI.match(value)
            if match:
                try:
                    payload = base64.b64decode(match.group("body"))
                except Exception:
                    continue
                seen.add(value)
                entries.append({
                    "kind": "inline", "src": value,
                    "mime": match.group("mime").lower(), "payload": payload,
                })
                continue
            if value.startswith("data:"):
                continue
            if value.startswith(("http://", "https://", "//")):
                seen.add(value)
                entries.append({
                    "kind": "remote", "src": value,
                    "url": "https:" + value if value.startswith("//") else value,
                    "path": value,
                })
                continue
            if value.startswith("/"):
                continue  # 站点内资源；正文容器里的这类已经被清洗摘掉
            seen.add(value)
            entries.append({
                "kind": "remote", "src": value,
                "url": urljoin(base_url, value), "path": value,
            })
    return entries[:MAX_MEDIA]


def rewrite_media(html_text: str, mapping: dict) -> str:
    """把命中的 src / data 换成映射里的本地文件名。"""
    if not mapping:
        return html_text
    LH = _lxml()
    try:
        doc = LH.fromstring(html_text)
    except Exception as exc:
        raise TextifyError("html_parse_failed", "HTML 解析失败：" + str(exc)) from exc
    for element in doc.iter():
        if not isinstance(element.tag, str):
            continue
        for attr in MEDIA_ATTRS:
            if element.get(attr) in mapping:
                element.set(attr, mapping[element.get(attr)])
    return LH.tostring(doc, encoding="unicode")
