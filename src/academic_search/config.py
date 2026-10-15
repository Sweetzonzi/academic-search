"""四份固定配置文件的解析与校验。

契约：docs/使用/配置.md（字段集与缺省值的唯一家）、ADR-0040。
只读 profile.toml / sources.toml / search.toml / terms.toml；目录里的
其它文件一律忽略。CLI 不写配置。
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import envelope

PROFILE = "profile.toml"
SOURCES = "sources.toml"
SEARCH = "search.toml"
TERMS = "terms.toml"

#: 初版采集源（IFACE --source 的可收窄集合）；Crossref 只在 lookup 兜 DOI。
SEARCH_SOURCES = ("openalex", "arxiv")
ALL_SOURCES = ("openalex", "arxiv", "crossref", "semantic_scholar")
DEFAULT_SOURCE_ENABLED = {
    "openalex": True,
    "arxiv": True,
    "crossref": True,
    "semantic_scholar": False,
}

DEFAULT_MAX_AGE_HOURS = 24
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_ABSTRACT_CHARS = 800
DEFAULT_MIN_GRADE = "B"
DEFAULT_SINCE_YEARS = 3
LANG_DEFAULT = "zh"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

@dataclass
class ConfigError:
    message: str
    line: int | None = None

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"message": self.message}
        if self.line is not None:
            out["line"] = self.line
        return out


@dataclass
class Query:
    name: str
    all_of: list[str] = field(default_factory=list)
    any_of: list[str] = field(default_factory=list)
    venues: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    since_years: int = DEFAULT_SINCE_YEARS
    expires: str | None = None
    enabled: bool = True

    def as_dict(self) -> dict:
        out: dict[str, Any] = {
            "name": self.name,
            "all_of": list(self.all_of),
            "any_of": list(self.any_of),
            "since_years": self.since_years,
            "enabled": self.enabled,
        }
        if self.venues:
            out["venues"] = list(self.venues)
        if self.authors:
            out["authors"] = list(self.authors)
        if self.expires:
            out["expires"] = self.expires
        return out

    def expired(self, today: date | None = None) -> bool:
        if not self.expires:
            return False
        try:
            when = date.fromisoformat(self.expires)
        except ValueError:
            return False
        return when < (today or date.today())


@dataclass
class Profile:
    queries: list[Query]

    def by_name(self, name: str) -> Query | None:
        for q in self.queries:
            if q.name == name:
                return q
        return None

    def enabled_queries(self) -> list[Query]:
        return [q for q in self.queries if q.enabled]


@dataclass
class SearchConfig:
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    abstract_chars: int = DEFAULT_ABSTRACT_CHARS
    min_grade: str = DEFAULT_MIN_GRADE
    venue_blacklist: list[str] = field(default_factory=list)
    mailto: str | None = None


def config_dir(root: str | Path) -> Path:
    return Path(root) / "config"


def data_dir(root: str | Path) -> Path:
    return Path(root) / "data"


def db_path(root: str | Path) -> Path:
    return data_dir(root) / "sidecar.db"


def _read_toml(path: Path) -> tuple[dict | None, ConfigError | None]:
    if not path.exists():
        return None, None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")), None
    except tomllib.TOMLDecodeError as exc:
        match = re.search(r"at line (\d+)", str(exc))
        line = int(match.group(1)) if match else None
        return None, ConfigError("TOML 解析失败：" + str(exc), line)
    except OSError as exc:
        return None, ConfigError("读取失败：" + str(exc))


def load_profile(root: str | Path) -> tuple[Profile | None, list[ConfigError]]:
    """解析画像；文件不存在返回 (None, [])（由调用方转 no_profile）。"""
    path = config_dir(root) / PROFILE
    raw, err = _read_toml(path)
    if err:
        return None, [err]
    if raw is None:
        return None, []
    return _parse_profile(raw), []


def check_profile(root: str | Path) -> dict:
    """profile check：语法、必填、重名、日期；逐条给出出错行。"""
    path = config_dir(root) / PROFILE
    if not path.exists():
        return {
            "ok": False,
            "error": {"code": envelope.NO_PROFILE, "message": "没有 profile：" + str(path)},
            "errors": [],
            "queries": [],
        }
    errors: list[ConfigError] = []
    raw, err = _read_toml(path)
    if err:
        return {"ok": False, "errors": [err.as_dict()], "queries": []}
    assert raw is not None
    text = path.read_text(encoding="utf-8")
    entries = raw.get("query", [])
    if not isinstance(entries, list):
        errors.append(ConfigError("query 必须是数组（[[query]]）", _line_of(text, "query")))
        entries = []
    seen: dict[str, int] = {}
    occurrences: dict[str, int] = {}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            errors.append(ConfigError("第 " + str(index) + " 条 query 不是表", None))
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(ConfigError("第 " + str(index) + " 条 query 缺 name", _line_of(text, "name")))
            continue
        occurrences[name] = occurrences.get(name, 0) + 1
        if name in seen:
            errors.append(
                ConfigError(
                    "查询名重复：" + name + "（首次见第 " + str(seen[name]) + " 条）",
                    _nth_line(text, 'name = "' + name + '"', occurrences[name]),
                )
            )
        seen[name] = index
        if not entry.get("any_of") and not entry.get("all_of"):
            errors.append(
                ConfigError(
                    "query " + name + " 必须至少给 all_of 或 any_of 一组",
                    _line_of(text, 'name = "' + name + '"'),
                )
            )
        expires = entry.get("expires")
        if expires is not None and not (isinstance(expires, str) and _DATE_RE.match(expires)):
            errors.append(
                ConfigError(
                    "query " + name + " 的 expires 必须是 YYYY-MM-DD",
                    _line_of(text, 'name = "' + name + '"'),
                )
            )
        years = entry.get("since_years")
        if years is not None and not isinstance(years, int):
            errors.append(
                ConfigError(
                    "query " + name + " 的 since_years 必须是整数",
                    _line_of(text, 'name = "' + name + '"'),
                )
            )
    profile = _parse_profile(raw)
    return {
        "ok": not errors,
        "errors": [e.as_dict() for e in errors],
        "queries": [q.as_dict() for q in profile.queries],
    }


def _parse_profile(raw: dict) -> Profile:
    queries: list[Query] = []
    for entry in raw.get("query", []) or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        queries.append(
            Query(
                name=name,
                all_of=_str_list(entry.get("all_of")),
                any_of=_str_list(entry.get("any_of")),
                venues=_str_list(entry.get("venues")),
                authors=_str_list(entry.get("authors")),
                since_years=int(entry.get("since_years", DEFAULT_SINCE_YEARS)),
                expires=entry.get("expires"),
                enabled=bool(entry.get("enabled", True)),
            )
        )
    return Profile(queries=queries)


def load_sources(root: str | Path) -> dict[str, bool]:
    raw, _ = _read_toml(config_dir(root) / SOURCES)
    enabled = dict(DEFAULT_SOURCE_ENABLED)
    if raw:
        for name in ALL_SOURCES:
            section = raw.get(name)
            if isinstance(section, dict) and "enabled" in section:
                enabled[name] = bool(section["enabled"])
    return enabled


def load_search(root: str | Path) -> SearchConfig:
    raw, _ = _read_toml(config_dir(root) / SEARCH)
    cfg = SearchConfig()
    if raw:
        refresh = raw.get("refresh") or {}
        sync = raw.get("sync") or {}
        quality = raw.get("quality") or {}
        abstract = raw.get("abstract") or {}
        contact = raw.get("contact") or {}
        if isinstance(contact, dict) and contact.get("mailto"):
            cfg.mailto = str(contact["mailto"]).strip() or None
        if isinstance(refresh, dict) and "max_age_hours" in refresh:
            cfg.max_age_hours = int(refresh["max_age_hours"])
        if isinstance(sync, dict) and "lookback_days" in sync:
            cfg.lookback_days = int(sync["lookback_days"])
        if isinstance(abstract, dict) and "chars" in abstract:
            cfg.abstract_chars = int(abstract["chars"])
        if isinstance(quality, dict):
            if "min_grade" in quality:
                cfg.min_grade = str(quality["min_grade"])
            if "venue_blacklist" in quality:
                cfg.venue_blacklist = _str_list(quality["venue_blacklist"])
    return cfg


def load_terms(root: str | Path) -> tuple[dict[str, str], str]:
    """返回 (en->zh 表, terms_version)。文件缺失即空表。"""
    path = config_dir(root) / TERMS
    raw, _ = _read_toml(path)
    if not raw:
        return {}, ""
    table: dict[str, str] = {}
    for entry in raw.get("term", []) or []:
        if isinstance(entry, dict) and entry.get("en"):
            table[str(entry["en"])] = str(entry.get("zh", entry["en"]))
    version = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    return table, version


def active_sources(enabled: dict[str, bool], *, command: str = "search") -> list[str]:
    pool = SEARCH_SOURCES if command == "search" else ALL_SOURCES
    return [s for s in pool if enabled.get(s)]


def scaffold_profile() -> str:
    return (
        "# config/profile.toml —— profile（agent 写、CLI 只读）；一条 [[query]] 是一个关注方向\n"
        "# 一次性的检索式走 search --any-of；想让某个检索变成定期任务，再写成 [[query]]。\n"
        "\n"
        "[[query]]\n"
        'name        = "world-model-uav"\n'
        'all_of      = ["world model"]\n'
        'any_of      = ["UAV", "drone", "aerial", "unmanned"]\n'
        "since_years = 3\n"
    )


def canonical_spec(
    *,
    all_of: list[str] | tuple[str, ...] = (),
    any_of: list[str] | tuple[str, ...] = (),
    venues: list[str] | tuple[str, ...] = (),
    authors: list[str] | tuple[str, ...] = (),
    since_years: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    sources: list[str] | tuple[str, ...] = (),
    sort: str | None = None,
    min_citations: int | None = None,
    min_fwci: float | None = None,
) -> str:
    """检索式的规范形式：字段排序、大小写归一、关键词排序（ADR-0043）。

    收哪几个参数只有一条判据——**它改不改变远端请求**（ADR-0062 决策 1–3）。
    因此 `--limit` / `--abstract` / `--in` 不在这里；`from_date` / `to_date`
    只接**显式给出**的窗口，接一个算出来的日期会让 hash 每天变一次（决策 4）。
    """

    def norm(values) -> list[str]:
        return sorted({str(v).strip().lower() for v in values if str(v).strip()})

    return json.dumps(
        {
            "all_of": norm(all_of),
            "any_of": norm(any_of),
            "venues": norm(venues),
            "authors": norm(authors),
            "since_years": since_years,
            "from": from_date,
            "to": to_date,
            "sources": norm(sources),
            "sort": sort,
            "min_citations": min_citations,
            "min_fwci": min_fwci,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def spec_hash(**kwargs) -> str:
    """spec_hash = sha256(规范化后的检索式)。"""
    return hashlib.sha256(canonical_spec(**kwargs).encode("utf-8")).hexdigest()


def date_window(
    *, since_years: int | None = None, lookback_days: int | None = None,
    today: date | None = None,
) -> tuple[str | None, str]:
    """返回 (from_date, to_date)，均为 YYYY-MM-DD；to 恒为今天。"""
    base = today or date.today()
    start: date | None = None
    if lookback_days is not None:
        start = base - timedelta(days=int(lookback_days))
    elif since_years is not None:
        start = base - timedelta(days=int(since_years) * 365)
    return (start.isoformat() if start else None, base.isoformat())


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _line_of(text: str, needle: str) -> int | None:
    return _nth_line(text, needle, 1)


def _nth_line(text: str, needle: str, occurrence: int) -> int | None:
    """needle 第 occurrence 次出现的行号（1 起）。"""
    count = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            count += 1
            if count == occurrence:
                return number
    return None
