"""分组：L0/L1/L2 判据、合并与重定向、补桥；以及读取端硬排除。

契约：docs/02-设计说明书.md §4.2、docs/接口/数据源适配.md §5/§7/§8。
本模块不联网；桥（外部标识）由 sources/ 取来后作为参数传入。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from . import contenthash, identity, store

#: 预印本 → 正式版年份窗口 [-1, +10]（DESIGN §4.2.3）。
YEAR_BACK = 10
YEAR_FORWARD = 1


@dataclass
class IngestResult:
    record_uid: str
    work_key: str
    new_record: bool
    new_candidate: bool
    dedup_level: str | None
    l2_hits: int
    excluded: bool


def normalize_venue(venue: str | None) -> str:
    """venue 归一化：剥标记 + NFKC + 连字符/引号统一 + 折叠空白 + 小写。"""
    return contenthash.normalize_source_text(venue or "", "venue").lower()


def venue_blacklisted(venue: str | None, blacklist: Iterable[str]) -> bool:
    """黑名单按归一化后的包含判断（IFACE-SOURCE §5、USAGE-配置 §3）。"""
    normalized = normalize_venue(venue)
    if not normalized:
        return False
    for entry in blacklist:
        needle = normalize_venue(entry)
        if needle and needle in normalized:
            return True
    return False


def _field(record, key):
    """同时兼容 dict 与 sqlite3.Row 的取值。"""
    try:
        return record[key]
    except (KeyError, IndexError, TypeError):
        return None


def record_is_excluded(record, blacklist: Iterable[str] = ()) -> bool:
    """初版的纯规则硬排除：撤稿 / paratext / venue 黑名单。"""
    if _field(record, "is_retracted"):
        return True
    if (_field(record, "type") or "") == "paratext":
        return True
    return venue_blacklisted(_field(record, "venue"), blacklist)


def work_is_excluded(conn, work_key: str, blacklist: Iterable[str] = ()) -> bool:
    for member in store.member_records(conn, work_key):
        if record_is_excluded(member, blacklist):
            return True
    return False


def _choose_keep(conn, work_keys: Sequence[str]) -> str:
    """多个组命中时保留首领「最早见到」的那个（同 run 内按 record_uid 字典序）。"""
    best_key = None
    best_order = None
    for work_key in sorted(set(work_keys)):
        leader = store.work_leader(conn, work_key)
        if leader is None:
            order = (10**18, work_key)
        else:
            order = (leader["first_run_id"] or 0, leader["record_uid"])
        if best_order is None or order < best_order:
            best_order = order
            best_key = work_key
    return best_key if best_key is not None else sorted(set(work_keys))[0]


def _existing_types(conn, work_key: str) -> set[str]:
    return {(m["type"] or "") for m in store.member_records(conn, work_key)}


def _role_for(conn, work_key: str, record_uid: str, record_type: str | None) -> str:
    if work_key == record_uid:
        return "primary"
    if (record_type or "") == "preprint":
        return "preprint"
    if any(t and t != "preprint" for t in _existing_types(conn, work_key)):
        return "duplicate"
    return "published"


def _idents_by_ns(conn, record_uid: str) -> dict[str, set[str]]:
    table: dict[str, set[str]] = {}
    for row in store.idents_of(conn, record_uid):
        table.setdefault(row["ns"], set()).add(row["value"])
    return table


def _ident_conflict(conn, record_uid: str, other_uid: str) -> bool:
    """强标识不冲突：同一 ns 下两边都有值且不相交即冲突（L2 否决）。"""
    mine = _idents_by_ns(conn, record_uid)
    theirs = _idents_by_ns(conn, other_uid)
    for ns, values in mine.items():
        other_values = theirs.get(ns)
        if other_values and not (values & other_values):
            return True
    return False


def register_suspects(conn, record_uid: str, row: Mapping, work_key: str) -> int:
    """L2：title_key + 一作姓氏 + 年份窗口 + 强标识不冲突 → 登记 dup_suspect，不归组。"""
    title_key = row.get("title_key")
    surname = row.get("first_author_surname")
    published_at = row.get("published_at")
    if not title_key or not surname or not published_at:
        return 0
    try:
        year = int(str(published_at)[:4])
    except ValueError:
        return 0
    count = 0
    for suspect in store.l2_candidates(
        conn, title_key, surname, year - YEAR_BACK, year + YEAR_FORWARD
    ):
        other_work = store.resolve_work(conn, suspect["work_key"])
        if other_work == work_key:
            continue
        if _ident_conflict(conn, record_uid, suspect["record_uid"]):
            continue
        store.add_dup_suspect(
            conn,
            record_uid,
            other_work,
            {
                "title_key": title_key,
                "first_author_surname": surname,
                "year": year,
                "other_record_uid": suspect["record_uid"],
                "other_source": suspect["source"],
                "other_source_id": suspect["source_id"],
            },
        )
        count += 1
    return count


def _ident(ns, value):
    """把标识别名归一到入库形态（与 source_id 同一套规则）。"""
    ns = str(ns).strip().lower()
    value = str(value).strip()
    if not ns or not value:
        return None
    return ns, identity.normalize_source_id(ns, value)


def ingest(
    conn,
    record: Mapping,
    *,
    run_id: int | None,
    refreshed: bool = True,
    blacklist: Iterable[str] = (),
) -> IngestResult:
    """把一条来源结果落库、分组、入池，并登记 L2 待确认。

    record 至少含 source / source_id；可选 idents（强标识，L0）与
    bridges（显式桥，L1），两者都是 (ns, value) 列表。
    """
    source = record["source"]
    source_id = record["source_id"]
    uid = identity.record_uid(source, source_id)
    now = store.utcnow()
    idents = [_ident(ns, value) for ns, value in (record.get("idents") or [])]
    bridges = [_ident(ns, value) for ns, value in (record.get("bridges") or [])]
    idents = [item for item in idents if item]
    bridges = [item for item in bridges if item]

    row = dict(record)
    if not row.get("title_key") and row.get("title"):
        row["title_key"] = identity.title_key(row["title"])
    if not row.get("first_author_surname") and row.get("authors"):
        authors = row["authors"]
        if isinstance(authors, str):
            try:
                authors = json.loads(authors)
            except ValueError:
                authors = [authors]
        row["first_author_surname"] = identity.first_author_surname(authors)

    existing = store.get_record_by_source(conn, source, source_id)
    if existing is not None:
        uid = existing["record_uid"]
        store.update_record(conn, uid, row)
        store.touch_seen(conn, uid, refreshed=refreshed)
        work_key = store.member_work(conn, uid) or uid
        level = None
    else:
        matched: list[str] = []
        level = None
        for ns, value in idents:
            for other in store.record_uids_by_ident(conn, ns, value):
                other_work = store.member_work(conn, other)
                if other_work:
                    matched.append(store.resolve_work(conn, other_work))
                    level = "L0"
        for ns, value in bridges:
            for other in store.record_uids_by_ident(conn, ns, value):
                other_work = store.member_work(conn, other)
                if other_work:
                    matched.append(store.resolve_work(conn, other_work))
                    if level is None:
                        level = "L1"
        row["record_uid"] = uid
        row["first_run_id"] = run_id
        row["last_seen_at"] = now
        store.insert_record(conn, row)
        if matched:
            work_key = _choose_keep(conn, matched)
            for other_work in sorted(set(matched)):
                if other_work != work_key:
                    store.merge_works(conn, work_key, other_work)
            store.add_member(conn, uid, work_key, _role_for(conn, work_key, uid, row.get("type")))
        else:
            work_key = uid
            store.create_work(conn, uid)
            store.add_member(conn, uid, uid, "primary")
        for ns, value in idents + bridges:
            store.upsert_ident(conn, ns, value, uid)

    excluded = work_is_excluded(conn, work_key, blacklist)
    new_candidate = False
    if not excluded and not store.candidate_exists(conn, work_key):
        new_candidate = store.add_candidate(conn, work_key, run_id)

    l2_hits = 0
    if existing is None:
        l2_hits = register_suspects(conn, uid, row, work_key)

    return IngestResult(
        record_uid=uid,
        work_key=work_key,
        new_record=existing is None,
        new_candidate=new_candidate,
        dedup_level=level,
        l2_hits=l2_hits,
        excluded=excluded,
    )
