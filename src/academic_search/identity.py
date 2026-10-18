"""确定性身份派生：record_uid、normalize(source_id)、title_key、投影目录名。

契约：docs/接口/存储与迁移.md §1（公式的唯一家）。
本模块不改库、不联网。
"""

from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from typing import Sequence

from .contenthash import strip_markup

#: 字段分隔符 US（0x1f），避免拼接歧义（IFACE-STORE §1）。
SEPARATOR = b"\x1f"

#: arXiv 版本后缀：v1 / V2 …（去掉使 v1/v2 是同一条记录）。
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)

#: title_key 用：非字母数字（含 CJK）一律当分隔符。
_NON_WORD_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")

#: 首冠词，title_key 用（DESIGN §4.2.3）。
_LEADING_ARTICLE_RE = re.compile(r"^(a|an|the)\s+")

#: 一作姓氏里剥掉的标点。
_SURNAME_PUNCT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")

_WS_RE = re.compile(r"\s+")

#: 投影目录名（works/<proj>）里换掉的字符：Windows 禁用集 + 控制字符（ADR-0068 决策 3）。
_SLUG_BAD_RE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')

#: `work_key` 的形状：20 位小写 base32，与 record_uid 的取值域相同（IFACE-STORE §1）。
WORK_KEY_LEN = 20
_WORK_KEY_RE = re.compile(r"^[a-z2-7]{" + str(WORK_KEY_LEN) + r"}$")

#: 投影目录名各段的上限（ADR-0068 决策 3）：总长 / 标题 / 首作者。
PROJECTION_MAX = 84
PROJECTION_TITLE_MAX = 50
PROJECTION_TITLE_MIN = 20
PROJECTION_AUTHOR_MAX = 12


def normalize_source_id(source: str, source_id: str) -> str:
    """把来源坐标的 id 归一化到入库形态（IFACE-STORE §1）。

    - 去首尾空白；
    - arXiv：大小写不敏感，且去掉版本后缀（1501.00001v2 → 1501.00001）；
    - DOI / OpenAlex W id：大小写不敏感；
    - 其余来源同样只做小写，不猜、不剥前缀（前缀由各 adaptor 负责）。

    重复调用幂等。
    """
    value = (source_id or "").strip()
    if not value:
        raise ValueError("source_id 不能为空")
    if (source or "").strip().lower() == "arxiv":
        value = _ARXIV_VERSION_RE.sub("", value)
    return value.lower()


def record_uid(source: str, source_id: str) -> str:
    """lower(base32(sha256(source ‖ 0x1f ‖ normalize(source_id))))[:20]。

    source 是受控的机器值（openalex / arxiv / …），参与哈希前统一小写，
    使大小写不同的同一来源不会派生出两个 id（对合法输入幂等）。
    """
    src = (source or "").strip().lower()
    if not src:
        raise ValueError("source 不能为空")
    normalized = normalize_source_id(src, source_id)
    digest = hashlib.sha256(src.encode("utf-8") + SEPARATOR + normalized.encode("utf-8")).digest()
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=").lower()
    return encoded[:20]


def title_key(title: str) -> str:
    """L2 召回键：剥标记 + NFKC + 小写 + 去标点 + 去首冠词 + 折叠空白。

    它**只是召回键**，不是判据（单独使用假阳性 16%，DESIGN §4.2.3）。
    """
    text = strip_markup(title or "")
    text = unicodedata.normalize("NFKC", text).lower()
    text = _NON_WORD_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    while True:
        stripped = _LEADING_ARTICLE_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text.strip()


def _surname(authors: Sequence[str] | str | None) -> str:
    """一作姓氏的原文（保留大小写与标点），取不到空串。

    支持 "Given Family" 与 "Family, Given" 两种常见形态。
    """
    if not authors:
        return ""
    first = authors[0] if isinstance(authors, (list, tuple)) else authors
    name = str(first or "").strip()
    if not name:
        return ""
    if "," in name:
        surname = name.split(",", 1)[0]
    else:
        parts = name.split()
        surname = parts[-1] if parts else ""
    return unicodedata.normalize("NFKC", surname).strip()


def first_author_surname(authors: Sequence[str] | str | None) -> str:
    """从作者数组取一作姓氏，小写成匹配键。

    这是 L2 的辅助字段，允许启发式（DESIGN §4.2.3）；给人看的那个走
    `projection_slug`（保留大小写）。
    """
    return _SURNAME_PUNCT_RE.sub("", _surname(authors).lower())


def _slug_text(value: str | None) -> str:
    """一段自由文本 → 目录名的一段：禁用字符换空格 → 空白折叠成 `-` → 去首尾 `.` / `-`。"""
    text = _SLUG_BAD_RE.sub(" ", str(value or ""))
    return _WS_RE.sub("-", text).strip(".-")


def _cut(text: str, limit: int) -> str:
    """按词边界（`-`）截断；单个词本身就超长时硬截。"""
    if len(text) <= limit:
        return text
    head = text[:limit]
    boundary = head.rfind("-")
    if boundary > 0:
        head = head[:boundary]
    return head.strip(".-")


def projection_slug(
    work_key: str,
    *,
    title: str | None = None,
    authors: Sequence[str] | str | None = None,
    published_at: str | None = None,
) -> str:
    """`works/<proj>` 的目录名：`<年>-<首作者>-<标题>-<work_key>`（ADR-0068 决策 3）。

    纯函数、只吃展示记录（`aggregate_work` 的那三个字段）。前几段是渲染、会随元数据变；
    **尾段恒为 work_key**，是唯一可反解的身份段（决策 2）。名字给人看，因此保留
    大小写与非 ASCII——不做 `title_key` 那种小写归一。
    """
    key = (work_key or "").strip()
    if not key:
        raise ValueError("work_key 不能为空")
    parts: list[str] = []
    year = (published_at or "").strip()[:4]
    if len(year) == 4 and year.isdigit():
        parts.append(year)
    author = _cut(_slug_text(_surname(authors)), PROJECTION_AUTHOR_MAX)
    if author:
        parts.append(author)
    # 段间的分隔符也占位：总名不超过 PROJECTION_MAX，标题段至少留 PROJECTION_TITLE_MIN
    room = PROJECTION_MAX - len(key) - 1 - sum(len(part) + 1 for part in parts)
    heading = _cut(_slug_text(title), min(PROJECTION_TITLE_MAX, max(room, PROJECTION_TITLE_MIN)))
    return "-".join(parts + [heading or "untitled", key])


def work_key_of(dirname: str) -> str | None:
    """目录名 → `work_key`：只看最后 20 个字符（ADR-0068 决策 2/7）。

    尾段不匹配 `[a-z2-7]{20}` 就返回 None——调用方跳过，不报错：
    `works/` 里可能有手工放进去的东西。
    """
    tail = (dirname or "")[-WORK_KEY_LEN:]
    return tail if _WORK_KEY_RE.match(tail) else None
