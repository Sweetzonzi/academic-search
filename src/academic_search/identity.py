"""确定性身份派生：record_uid、normalize(source_id)、title_key。

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


def first_author_surname(authors: Sequence[str] | str | None) -> str:
    """从作者数组取一作姓氏，小写。

    支持 "Given Family" 与 "Family, Given" 两种常见形态；取不到空串。
    这是 L2 的辅助字段，允许启发式（DESIGN §4.2.3）。
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
    surname = unicodedata.normalize("NFKC", surname).lower()
    return _SURNAME_PUNCT_RE.sub("", surname)
