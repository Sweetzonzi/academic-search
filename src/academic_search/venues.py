"""会名 → OpenAlex source id 的登记表，以及载体判据（ADR-0060 决策 1–4）。

契约：docs/接口/数据源适配.md §9 的 `venues` 行、docs/使用/配置.md 的 venue 字段。

两侧名单（`venues` 白名单与 `quality.venue_blacklist` 黑名单）共用一条判据：
**作品的载体 source id 集合 ∩ 名单 ≠ ∅**。载体集合 = 各成员记录
`locations[].source.id` 的并集，外加「该来源自己的载体」（见 `source_carrier_ids`）。

判据的家在这里：`group` 与 `service` 都调本模块，本模块不反向依赖它们，
也不碰数据库与网络。
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Iterable

from . import contenthash

REGISTRY_NAME = "venues.toml"
_REGISTRY_PATH = Path(__file__).with_name(REGISTRY_NAME)

#: 登记表读取失败时的兜底（空表：什么都解析不出来，于是名字全部报错）。
_EMPTY: tuple[dict, dict, list] = ({}, {}, [])


def venue_key(name: str) -> str:
    """会名 / 载体名的匹配键：剥标记 + NFKC + 连字符/引号统一 + 折叠空白 + 小写。"""
    return contenthash.normalize_source_text(name or "", "venue").lower()


def _id_key(value) -> str:
    return str(value).strip().lower()


def _value(record, key: str):
    """同时兼容 dict 与 sqlite3.Row 的取值。"""
    try:
        return record[key]
    except (KeyError, IndexError, TypeError):
        return None


def load_registry(
    path: str | Path | None = None,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]], list[str]]:
    """返回 (会名键 → ids, 来源名 → ids, 登记表里的会名)。文件缺失即空表。"""
    target = Path(path) if path is not None else _REGISTRY_PATH
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return _EMPTY

    venues: dict[str, tuple[str, ...]] = {}
    names: list[str] = []
    for entry in raw.get("venue", []) or []:
        if not isinstance(entry, dict):
            continue
        ids = tuple(_id_key(v) for v in (entry.get("ids") or []) if _id_key(v))
        if not ids:
            continue
        for name in [entry.get("name")] + list(entry.get("aliases") or []):
            if isinstance(name, str) and name.strip():
                venues[venue_key(name)] = ids
        if isinstance(entry.get("name"), str) and entry["name"].strip():
            names.append(entry["name"].strip())

    carriers: dict[str, tuple[str, ...]] = {}
    for entry in raw.get("source_carrier", []) or []:
        if not isinstance(entry, dict):
            continue
        source = venue_key(str(entry.get("source") or ""))
        ids = tuple(_id_key(v) for v in (entry.get("ids") or []) if _id_key(v))
        if source and ids:
            carriers[source] = ids
    return venues, carriers, names


_VENUES, _CARRIERS, _NAMES = load_registry()

#: `--venue` / 黑名单里可以直写 `S…`：未登记载体的逃生门（ADR-0060 决策 2）。
_SOURCE_ID_RE = re.compile(r"^[Ss]\d+$")


def is_source_id(name: str) -> bool:
    return bool(_SOURCE_ID_RE.match(str(name).strip()))


def resolve(names: Iterable[str]) -> tuple[list[str], list[str]]:
    """会名列表 → (source id 列表, 解析不到的名字列表)。

    返回的 id 已小写、去重、保序；未知名字交给调用方转成错误——**不静默失效**。
    """
    found: list[str] = []
    unknown: list[str] = []
    for name in names:
        text = str(name).strip()
        if not text:
            continue
        ids = (_id_key(text),) if is_source_id(text) else _VENUES.get(venue_key(text))
        if not ids:
            unknown.append(text)
            continue
        for value in ids:
            if value not in found:
                found.append(value)
    return found, unknown


def known_names() -> list[str]:
    """登记表里的会名（按注册顺）——供报错信息与文档对照用。"""
    return list(_NAMES)


def source_carrier_ids(source: str | None) -> tuple[str, ...]:
    """某个来源命名空间自己的载体 id（arXiv → S4306400194）。"""
    return _CARRIERS.get(venue_key(str(source or "")), ())


def record_carriers(record) -> set[str]:
    """一条记录的载体 source id 集合（ADR-0060 决策 1/4）。

    来源自报的 `locations[].source.id` ∪ 「该来源自己的载体」。第二条不能省：
    arXiv 记录没有 `locations[]`，而「这篇挂在 arXiv 上」是明确事实，
    `--venue arXiv` 必须能命中 arXiv 记录本身。
    """
    raw = _value(record, "venue_source_ids")
    if isinstance(raw, str):
        try:
            values = json.loads(raw)
        except ValueError:
            values = []
    else:
        values = raw or []
    found = {_id_key(v) for v in values if _id_key(v)}
    found |= set(source_carrier_ids(_value(record, "source")))
    return found


def hits(carriers: Iterable[str], ids: Iterable[str]) -> bool:
    """载体集合与名单是否有交集——白名单与黑名单共用这一条判据。"""
    wanted = {_id_key(v) for v in ids if str(v).strip()}
    if not wanted:
        return False
    return bool({_id_key(v) for v in carriers} & wanted)
