"""用例编排：检索、查询、入库、成本汇总与费用可见。

契约：docs/03-接口契约.md、docs/接口/数据源适配.md §5/§9。
cli.py 只做参数与信封；SQL 与网络都在这层（DEV-CONV §4）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from . import config, envelope, group, htmlclean, identity, paths, report, store, textify, venues
from .sources import SearchSpec, SourceError, normalize_doi as source_normalize_doi
from .sources import arxiv as arxiv_source
from .sources import crossref as crossref_source
from .sources import openalex as openalex_source
from .sources.http import HttpClient

SEARCH_CLIENTS = {"openalex", "arxiv"}
ARXIV_INTERVAL = 3.0
CROSSREF_INTERVAL = 0.15


@dataclass
class Outcome:
    items: list = field(default_factory=list)
    network: bool = False
    requests: int = 0
    cost_usd: float = 0.0
    cost_basis: str = ""
    degraded: dict | None = None
    counts: dict = field(default_factory=dict)
    run_id: int | None = None

    def payload(self) -> dict:
        data = {
            "items": self.items,
            "network": self.network,
            "cost": {
                "usd": round(self.cost_usd, 6),
                "basis": self.cost_basis or ("本次未联网" if not self.network else ""),
                "requests": self.requests,
            },
        }
        if self.counts:
            data["counts"] = self.counts
        if self.degraded is not None:
            data["degraded"] = self.degraded
        return data


def make_client() -> HttpClient:
    return HttpClient(min_interval={"arxiv": ARXIV_INTERVAL, "crossref": CROSSREF_INTERVAL})


def _api_key() -> str | None:
    return os.environ.get("OPENALEX_API_KEY") or None


def _should_refresh(conn, spec_hash: str, mode: str, max_age_hours: float) -> bool:
    if mode == "false":
        return False
    if mode == "true":
        return True
    last = store.last_refresh_at(conn, spec_hash)
    if not last:
        return True
    try:
        when = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - when > timedelta(hours=max_age_hours)


def _refresh_lookup(mode: str, has_ident: bool, work_key: str | None) -> bool:
    """`lookup` 的三档（ADR-0065 决策 1）：`true` 无条件先刷、`auto` 未命中才刷、`false` 不刷。

    与 `search` 的区别只在 `auto` 的判据：`search` 按 `spec_hash` 看新鲜度，
    `lookup` 按标识取、只看本地有没有命中（ADR-0065 决策 4 不做 per-work 账）。
    """
    if mode == "false" or not has_ident:
        return False
    if mode == "true":
        return True
    return work_key is None


def _work_by_ident(conn, ident: tuple[str, str]) -> str | None:
    """按 `(namespace, value)` 解析出 work——刷新前后各用一次，不夹带任何缓存。"""
    hits = store.record_uids_by_ident(conn, ident[0], ident[1])
    return store.member_work(conn, hits[0]) if hits else None


def _resolve_names(names: Iterable[str], *, what: str) -> list[str]:
    """会名列表 → 载体 source id 列表；解析不到即报错（ADR-0060 决策 2）。"""
    ids, unknown = venues.resolve(names)
    if unknown:
        raise envelope.EnvelopeError(
            envelope.INVALID_ARGS,
            what + "里有登记表解析不到的名字：" + "、".join(unknown)
            + "；登记表见 src/academic_search/venues.toml，也可直写 S… 逃生",
        )
    return ids


def _fetch(client, source: str, spec: SearchSpec, *, sort: str,
           from_date: str | None, to_date: str | None, venue_ids: Sequence[str]) -> list[dict]:
    if source == "openalex":
        return openalex_source.search(
            client, spec, api_key=_api_key(), sort=sort,
            from_date=from_date, to_date=to_date, venue_ids=venue_ids,
        )
    if source == "arxiv":
        return arxiv_source.search(client, spec, from_date=from_date, to_date=to_date)
    raise SourceError(source, "该源不参与采集（初版仅 openalex / arxiv）", False)


def _skip_legs(spec: SearchSpec, *, venue_ids: Sequence[str]) -> set[str]:
    """哪些腿不参与本次采集。

    - 给了 venue 白名单：arXiv 没有载体概念，取回来也判不了（ADR-0060 决策 5）；
    - 给了任一引用阈值：arXiv 侧没有被引数据，放行等于不筛（ADR-0061 决策 4）。
    两种情况都不报错，该腿在 `counts.by_source` 里如实记 0。
    """
    if venue_ids or spec.classical_only():
        return {"arxiv"}
    return set()


def _fts_query(spec: SearchSpec) -> str:
    """检索式 → FTS5 查询：词组按短语，组间 all_of AND / any_of OR。

    每个词的**最后一段带前缀匹配**（FTS5 的 * 只作用于短语末词）。这是刻意的宽松：
    远端（arXiv / OpenAlex）自己做词形归并，本地若按精确词过滤，同一次检索会变成
    「远端取了 5 条、本地只回 3 条」，丢掉的恰是 drones 这类复数形式
    （实测见 EX-2026-10-15-firstrun）。
    """

    def term(value: str) -> str:
        return '"' + value.replace('"', '""') + '" *'

    groups: list[str] = []
    for values, joiner in ((spec.all_of, " AND "), (spec.any_of, " OR ")):
        cleaned = [term(value) for value in values if value and value.strip()]
        if cleaned:
            groups.append(joiner.join(cleaned))
    if not groups:
        return ""
    return "(" + ") AND (".join(groups) + ")"


def _body_work_keys(conn, root, spec: SearchSpec) -> list[str]:
    """--in body|both：对 works/ 做线性子串扫描（ADR-0036）。

    all_of 全满足、非空 any_of 至少满足一项；条件在同一份 Markdown 内判断。
    目录名 → `work_key` 只看尾段 20 位（ADR-0068 决策 7）；不匹配的目录跳过——
    `works/` 里可能有手工放进去的东西，为它报错只会挡住检索。
    """
    base = paths.works_dir(root)
    if not base.is_dir():
        return []
    all_of = [t.lower() for t in spec.all_of if t.strip()]
    any_of = [t.lower() for t in spec.any_of if t.strip()]
    if not (all_of or any_of):
        return []
    found: list[str] = []
    for work in sorted(base.iterdir()):
        if not work.is_dir():
            continue
        key = identity.work_key_of(work.name)
        if key is None:
            continue
        for doc in sorted(work.rglob("*.md")):
            try:
                text = doc.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if all(term in text for term in all_of) and (
                not any_of or any(term in text for term in any_of)
            ):
                found.append(store.resolve_work(conn, key))
                break
    return found


def _threshold_ok(item: dict, spec: SearchSpec) -> bool:
    """本地按同一条件再判一次；空值不通过（ADR-0061 决策 3）。"""
    if spec.min_citations is not None:
        value = item.get("cited_by_count")
        if value is None or value < spec.min_citations:
            return False
    if spec.min_fwci is not None:
        value = item.get("fwci")
        if value is None or value < spec.min_fwci:
            return False
    return True


def _author_ok(item: dict, authors: Iterable[str]) -> bool:
    wanted = [a.strip().lower() for a in authors if a.strip()]
    if not wanted:
        return True
    names = [name.lower() for name in item.get("authors") or []]
    return any(author in name for author in wanted for name in names)


def local_work_keys(
    conn, spec: SearchSpec, *, sources: Iterable[str], from_date: str | None,
    to_date: str | None, in_scope: str, root,
    max_candidates: int = config.DEFAULT_LOCAL_MAX_CANDIDATES,
    stats: dict | None = None,
) -> list[str]:
    """`--in` 三档的候选集：`meta` 只走 FTS5、`body` 只扫 `works/`、`both` 取并集。

    三档是**两个数据源的三档开关**，不是同一档的宽窄（ADR-0036 决策 2）；两路都过
    同一套非文本过滤（`status` / `--source` / 日期窗），逐 work 去重后再交给
    `_sorted_items` 判 venue / 作者 / 阈值 / 撤稿。没有检索式时三档同义：列出本地库。

    `max_candidates` 是**候选预算**（ADR-0070）：FTS 命中的记录先过结构化过滤，
    再按 bm25 取前 N 条；`0` 或负数 = 不限。预算用尽时 `stats["truncated"] = True`
    ——这是「可能还有匹配没看到」，不是「结果就这么多」。
    """
    where = ["r.status = 'live'"]
    params: list = []
    source_list = [s for s in sources]
    if source_list:
        where.append("r.source IN (" + ",".join("?" for _ in source_list) + ")")
        params.extend(source_list)
    if from_date:
        where.append("(r.published_at IS NULL OR r.published_at >= ?)")
        params.append(from_date)
    if to_date:
        where.append("(r.published_at IS NULL OR r.published_at <= ?)")
        params.append(to_date)

    def scoped(seed: str | None, seed_params: Sequence = ()) -> list[str]:
        """把一条「种子」子句过一遍非文本过滤，返回 work_key。"""
        clauses = ([seed] if seed else []) + where
        sql = (
            "SELECT DISTINCT wm.work_key FROM record r "
            "JOIN work_member wm ON wm.record_uid = r.record_uid "
            "WHERE " + " AND ".join(clauses) + " ORDER BY wm.work_key"
        )
        return [row["work_key"] for row in conn.execute(sql, list(seed_params) + params)]

    def works_of(records: Sequence[str]) -> list[str]:
        """记录 → work_key（记录已过结构化过滤，这里只做映射）。"""
        found: list[str] = []
        for start in range(0, len(records), 400):
            chunk = records[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            found.extend(
                row["work_key"]
                for row in conn.execute(
                    "SELECT DISTINCT wm.work_key FROM work_member wm "
                    "WHERE wm.record_uid IN (" + placeholders + ") ORDER BY wm.work_key",
                    chunk,
                )
            )
        return found

    def finish(keys: list[str], *, truncated: bool = False) -> list[str]:
        unique = list(dict.fromkeys(keys))  # 并集保序去重
        if stats is not None:
            stats["considered"] = len(unique)
            stats["truncated"] = truncated
        return unique

    if not (spec.all_of or spec.any_of):
        return finish(scoped(None))  # 裸 search：列本地库，与 --in 无关
    keys: list[str] = []
    truncated = False
    if in_scope in ("meta", "both"):
        budget = int(max_candidates or 0)
        ask = budget + 1 if budget > 0 else -1
        rids = store.search_fts(
            conn, _fts_query(spec), ask, where=tuple(where), params=tuple(params)
        )
        if budget > 0 and len(rids) > budget:
            rids = rids[:budget]
            truncated = True
        if rids:
            keys.extend(works_of(rids))
    if in_scope in ("body", "both"):
        seeds = _body_work_keys(conn, root, spec)
        if seeds:
            keys.extend(scoped("wm.work_key IN (" + ",".join("?" for _ in seeds) + ")", seeds))
    return finish(keys, truncated=truncated)


def _sorted_items(
    conn, keys, *, spec: SearchSpec, blocked_venues, venue_ids, sort: str, limit: int,
    stats: dict | None = None,
) -> list[dict]:
    items = []
    venue_filtered_out = 0
    for key in keys:
        if group.work_is_excluded(conn, key, blocked_venues):
            continue
        # 白名单与黑名单同一条判据：载体 source id 集合 ∩ 名单（ADR-0060 决策 1）。
        # 载体集合为空即不通过——fail-open 会让阈值与名单一起失去意义。
        if venue_ids and not venues.hits(group.work_carriers(conn, key), venue_ids):
            venue_filtered_out += 1
            continue
        item = store.aggregate_work(conn, key)
        if item is None or item["is_retracted"]:
            continue
        if not _author_ok(item, spec.authors):
            continue
        if not _threshold_ok(item, spec):
            continue
        items.append(item)
    if sort == "citations":
        counted = sorted(
            [i for i in items if i["cited_by_count"] is not None],
            key=lambda i: i["cited_by_count"],
            reverse=True,
        )
        uncounted = [i for i in items if i["cited_by_count"] is None]
        items = counted + uncounted
    else:
        dated = sorted(
            [i for i in items if i["published_at"]],
            key=lambda i: i["published_at"],
            reverse=True,
        )
        undated = [i for i in items if not i["published_at"]]
        items = dated + undated
    if stats is not None:
        stats["matched"] = len(items)
        stats["venue_filtered_out"] = venue_filtered_out
    return items[:limit]


def _shape_abstracts(items: list[dict], *, include: bool, chars: int) -> list[dict]:
    """按 ADR-0058 决策 2 处理条目里的 abstract：开关关时删键，开时截断。

    chars 为 0 或负数 = 不截断（与 config/search.toml 的注释同一口径）。
    摘要逐字符取前 chars 个，不加省略号——载荷里没有标记位承载它。
    """
    for item in items:
        if not include:
            item.pop("abstract", None)
            continue
        value = item.get("abstract")
        if value is not None and chars > 0:
            item["abstract"] = value[:chars]
    return items


# --------------------------------------------------------------------------
# 采集：search 与 sync 共用的那一条流水线（ADR-0049 决策 3）
# --------------------------------------------------------------------------

def _quota_exhausted(client, threshold: float | None) -> bool:
    """余额是否低于阈值。**从没读到过余额不算耗尽**（ADR-0066 决策 11）。

    余额来自 OpenAlex 的 `x-ratelimit-remaining-usd` 响应头，第一次请求之前无从得知；
    因此单次 `search` 通常不会在开头就触发，多查询的 `sync` 才会——那正是要保护的情形。
    """
    if threshold is None:
        return False
    remaining = getattr(client, "rate_remaining_usd", None)
    return remaining is not None and float(remaining) < float(threshold)


def _archive_responses(root, run_id: int, client, log_start: int, with_records) -> list[dict]:
    """留档本 run 的响应原文，返回 `responses[]` 清单（ADR-0066 决策 10）。

    留档判据：该响应产生了至少一条写入 `record` 的记录，或该请求失败。
    `n` 按本 run 内的请求顺序从 1 起；`page` 初版恒为 1（一次取数固定一页 100 条，ADR-0062）。
    """
    log = list(getattr(client, "log", None) or [])[log_start:]
    kept: list[dict] = []
    for entry in log:
        failed = entry.status == 0 or entry.status >= 400
        if not failed and entry.source not in with_records:
            continue
        index = len(kept) + 1
        report.write_response(root, run_id, index, entry.source, entry.payload())
        kept.append({
            "n": index,
            "sha256": entry.sha256,
            "source": entry.source,
            "url": entry.url,
            "page": 1,
            "status": entry.status,
        })
    return kept


def collect_run(
    conn,
    root,
    spec: SearchSpec,
    *,
    spec_key: str,
    query_name: str | None,
    sources: Sequence[str],
    refresh: str,
    max_age_hours: float,
    from_date: str | None,
    to_date: str | None,
    venue_ids: Sequence[str],
    blocked_venues: Sequence[str],
    skipped: set[str],
    sort: str = "date",
    client=None,
    quota_min_usd: float | None = None,
    archive: bool = False,
) -> dict:
    """跑一条检索式：判刷新 → 取数入池 → 收尾写一行账本。

    `search` 与 `sync` 共用这一层，差别只在窗口怎么算、`query_name` 填什么、
    要不要落 `data/runs/`（`archive=True`）。**这里不抛「全失败」**——那由调用方决定：
    `search` 一次只有这一条，失败即整体失败；`sync` 的单条失败不该拖垮整次调用
    （ADR-0066 决策 4）。

    返回该 run 的原始值：`run_id` / `status` / `refreshed` / `counts` / `cost` /
    `responses` / `degraded` / `skip_reason` / `all_failed`。
    counts.complete 只证明未被条件排除的来源都取完本次规定的一页，不证明全库召回完整。
    """
    if not _should_refresh(conn, spec_key, refresh, max_age_hours):
        do_refresh, skip_reason = False, ("freshness" if refresh == "auto" else None)
        reason = "同一检索式在新鲜度窗口内，跳过联网（--refresh auto）" if skip_reason else None
    elif _quota_exhausted(client, quota_min_usd):
        do_refresh, skip_reason, reason = False, "quota", "余额不足，只查本地"
    else:
        do_refresh, skip_reason, reason = True, None, None

    if not do_refresh:
        with store.write_tx(conn):
            run_id = store.start_run(
                conn, spec_hash=spec_key, query_name=query_name, refreshed=False,
                window_from=from_date, window_to=to_date,
            )
            store.finish_run(conn, run_id, status="ok", requests=0, cost_usd=0.0,
                             cost_basis="跳过联网", counts={})
        return {
            "run_id": run_id, "status": "ok", "refreshed": False, "counts": {},
            "cost": {"usd": 0.0, "basis": "跳过联网", "requests": 0}, "responses": [],
            "degraded": reason, "skip_reason": skip_reason, "quota_stopped": False,
            "all_failed": False,
        }

    client = client or make_client()
    log_start = len(getattr(client, "log", None) or [])
    requests_before, cost_before = client.requests, client.cost_usd
    with store.write_tx(conn):
        run_id = store.start_run(
            conn, spec_hash=spec_key, query_name=query_name, refreshed=False,
            window_from=from_date, window_to=to_date,
        )
    failures: list[dict] = []
    by_source: dict[str, int] = {}
    attempted: list[str] = []
    with_records: set[str] = set()
    new_candidates = 0
    dedup_blocked = 0
    wrote_anything = False
    quota_hit = False
    for source in sources:
        if source in skipped:
            by_source[source] = 0
            continue
        # 每个源返回后的检查点：余额不足就不再发后续请求（ADR-0066 决策 11）
        if quota_hit or _quota_exhausted(client, quota_min_usd):
            quota_hit = True
            by_source[source] = 0
            continue
        attempted.append(source)
        try:
            records = _fetch(client, source, spec, sort=sort,
                             from_date=from_date, to_date=to_date,
                             venue_ids=venue_ids)
        except SourceError as exc:
            failures.append({"source": source, "reason": exc.reason})
            by_source[source] = 0
            continue
        by_source[source] = len(records)
        if records:
            wrote_anything = True
            with_records.add(source)
        with store.write_tx(conn):
            for record in records:
                result = group.ingest(conn, record, run_id=run_id,
                                      blocked_venues=blocked_venues)
                if result.new_candidate:
                    new_candidates += 1
                if result.dedup_level:
                    dedup_blocked += 1
    counts = {
        "new_candidates": new_candidates,
        "dedup_blocked": dedup_blocked,
        "by_source": by_source,
        "failures": failures,
    }
    # 「全失败」只对**真的发过请求**的腿成立：被跳过的腿不算失败，
    # 否则带上白名单的查询会连「一条都没取到」都被记成 ok。
    all_failed = bool(failures) and len(failures) == len(attempted) and not wrote_anything
    # 花费按**本次 run 的增量**记：多查询 sync 共用一个客户端，累计值不能当单 run 的值。
    requests = client.requests - requests_before
    cost_usd = client.cost_usd - cost_before
    refreshed = requests > 0
    counts["complete"] = bool(attempted) and refreshed and not failures and not quota_hit
    responses = _archive_responses(root, run_id, client, log_start, with_records) if archive else []
    with store.write_tx(conn):
        store.finish_run(
            conn, run_id, status="failed" if all_failed else "ok", requests=requests,
            cost_usd=cost_usd, cost_basis=client.cost_basis, counts=counts,
            responses=responses or None, refreshed=refreshed,
        )
    return {
        "run_id": run_id,
        "status": "failed" if all_failed else "ok",
        "refreshed": refreshed,
        "counts": counts,
        "cost": {"usd": round(cost_usd, 6), "basis": client.cost_basis, "requests": requests},
        "responses": responses,
        "degraded": "余额不足，已停止本次 run 的剩余源" if quota_hit else None,
        # 与「整条查询被跳过」不同：这一条已经刷过、只是没跑完全部腿（决策 11）。
        "quota_stopped": quota_hit,
        "skip_reason": None,
        "all_failed": all_failed,
    }


def run_search(
    root,
    spec: SearchSpec,
    *,
    limit: int = 20,
    sources: Iterable[str] = ("openalex", "arxiv"),
    refresh: str = "auto",
    max_age_hours: float = config.DEFAULT_MAX_AGE_HOURS,
    lookback_days: int | None = None,
    sort: str = "date",
    in_scope: str = "meta",
    venue_blacklist: Iterable[str] = (),
    abstract: bool = False,
    abstract_chars: int = config.DEFAULT_ABSTRACT_CHARS,
    max_candidates: int = config.DEFAULT_LOCAL_MAX_CANDIDATES,
    quota_min_usd: float | None = config.DEFAULT_MIN_REMAINING_USD,
    client=None,
    today=None,
) -> Outcome:
    root = str(root)
    sources = list(sources)
    # 名单先解析：写库之前就要知道这次能不能跑（ADR-0060 决策 2）
    venue_ids = _resolve_names(spec.venues, what="venue 白名单")
    blocked_venues = _resolve_names(venue_blacklist, what="venue 黑名单")
    skipped = _skip_legs(spec, venue_ids=venue_ids)
    # 正文只在本地语料里，body 档不该为了它去联网（ADR-0036 决策 2）；显式 true 仍照字面执行
    if in_scope == "body" and refresh == "auto":
        refresh = "false"
    conn = store.connect(config.db_path(root))
    try:
        spec_key = config.spec_hash(
            all_of=spec.all_of, any_of=spec.any_of, venues=spec.venues,
            authors=spec.authors, since_years=spec.since_years,
            sources=sources, sort=sort,
            min_citations=spec.min_citations, min_fwci=spec.min_fwci,
        )
        from_date, to_date = config.date_window(
            since_years=spec.since_years, lookback_days=lookback_days, today=today
        )
        collected = collect_run(
            conn, root, spec, spec_key=spec_key, query_name=None, sources=sources,
            refresh=refresh, max_age_hours=max_age_hours, from_date=from_date,
            to_date=to_date, venue_ids=venue_ids, blocked_venues=blocked_venues,
            skipped=skipped, sort=sort, client=client, quota_min_usd=quota_min_usd,
        )
        if collected["all_failed"]:
            failures = collected["counts"]["failures"]
            raise envelope.EnvelopeError(
                envelope.SOURCE_ERROR,
                "全部源失败且无写入：" + "; ".join(f["source"] + "=" + f["reason"] for f in failures),
            )
        outcome = Outcome(
            network=collected["refreshed"],
            requests=collected["cost"]["requests"],
            cost_usd=collected["cost"]["usd"],
            cost_basis=collected["cost"]["basis"],
            degraded={"reason": collected["degraded"]} if collected["degraded"] else None,
            counts=collected["counts"],
            run_id=collected["run_id"],
        )
        local_stats: dict = {}
        keys = local_work_keys(
            conn, spec, sources=sources, from_date=from_date, to_date=to_date,
            in_scope=in_scope, root=root, max_candidates=max_candidates,
            stats=local_stats,
        )
        outcome.items = _shape_abstracts(
            _sorted_items(conn, keys, spec=spec, blocked_venues=blocked_venues,
                          venue_ids=venue_ids, sort=sort, limit=limit, stats=local_stats),
            include=abstract,
            chars=abstract_chars,
        )
        # 本地阶段的账放进同一个 counts（ADR-0070）：离线和联网都能看到
        # 「候选看了多少、过滤后多少、为什么变少」，不再只报远端取了多少条。
        counts = dict(outcome.counts or {})
        counts["local"] = {
            "considered": local_stats.get("considered", 0),
            "matched": local_stats.get("matched", 0),
            "returned": len(outcome.items),
            "truncated": bool(local_stats.get("truncated", False)),
            "venue_filtered_out": local_stats.get("venue_filtered_out", 0),
        }
        outcome.counts = counts
        return outcome
    finally:
        conn.close()


# --------------------------------------------------------------------------
# sync：画像里的具名查询（ADR-0066）
# --------------------------------------------------------------------------

@dataclass
class SyncOutcome:
    """一次 `sync` 的结果：逐 run 自包含，调用级只留 `degraded`（ADR-0066 决策 4）。"""

    runs: list = field(default_factory=list)
    degraded: dict | None = None

    def payload(self) -> dict:
        data: dict = {"runs": self.runs}
        if self.degraded is not None:
            data["degraded"] = self.degraded
        return data


def _select_queries(profile, names: Iterable[str], *, today=None) -> list:
    """`--query` 的解析语义（ADR-0066 决策 8）。

    缺省 = 画像里全部启用且未过期的查询；显式点名必须解析得到、启用、未过期——
    静默跳过一件画像明说不做的事，只会让整次调用白跑。
    """
    wanted = [str(name).strip() for name in names if str(name).strip()]
    if not wanted:
        runnable = [q for q in profile.enabled_queries() if not q.expired(today)]
        if not runnable:
            raise envelope.EnvelopeError(
                envelope.INVALID_ARGS,
                "画像里没有启用且未过期的查询；enabled 与 expires 都在 config/profile.toml 里",
            )
        return runnable
    chosen = []
    for name in wanted:
        query = profile.by_name(name)
        if query is None:
            available = "、".join(q.name for q in profile.queries) or "（画像里没有 [[query]]）"
            raise envelope.EnvelopeError(
                envelope.INVALID_ARGS, "画像里没有这条查询：" + name + "；可用：" + available
            )
        if not query.enabled:
            raise envelope.EnvelopeError(
                envelope.INVALID_ARGS,
                "查询 " + name + " 在画像里是 enabled = false；先改画像，再点名它",
            )
        if query.expired(today):
            raise envelope.EnvelopeError(
                envelope.INVALID_ARGS,
                "查询 " + name + " 已过期（expires = " + str(query.expires) + "）",
            )
        chosen.append(query)
    return chosen


def _sync_degraded(fresh: int, quota_skips: int, quota_stops: int, total: int) -> dict | None:
    """调用级降级汇总——`degraded` 是调用级唯一的家（ADR-0066 决策 4/7/11）。

    `quota_skips` 是整条查询没联网，`quota_stops` 是刷到一半被余额拦下——
    `sync` 把两者分开报，是因为后者已经写进了东西，读报告的人不该以为它什么都没做。
    """
    parts = []
    if fresh:
        parts.append(str(fresh) + "/" + str(total) + " 条查询在新鲜度窗口内，跳过联网")
    if quota_skips:
        parts.append("余额不足，只查本地（" + str(quota_skips) + "/" + str(total) + " 条查询）")
    if quota_stops:
        parts.append("余额不足，另有 " + str(quota_stops) + " 条查询只跑完部分源")
    return {"reason": "；".join(parts)} if parts else None


def _query_window(conn, query, *, spec_key: str, lookback_days: int, today):
    """窗口按查询现场算：**首次执行**用它的 `since_years`，否则用 `lookback_days`。

    判据是该 `query_name` 与当前 `spec_hash` 有没有完整联网采集行，不新增列。
    画像里后来新加的方向因此能按自己的 `since_years` 回溯，而不是永远只有 30 天。
    """
    if store.has_successful_query(conn, query.name, spec_key):
        return config.date_window(lookback_days=lookback_days, today=today)
    return config.date_window(since_years=query.since_years, today=today)


def _query_spec(query) -> SearchSpec:
    return SearchSpec(
        all_of=list(query.all_of),
        any_of=list(query.any_of),
        venues=list(query.venues),
        authors=list(query.authors),
        since_years=query.since_years,
    )


def run_sync(
    root,
    *,
    queries: Iterable[str] = (),
    refresh: str = "auto",
    sources: Iterable[str] | None = None,
    client=None,
    today=None,
) -> SyncOutcome:
    """执行画像里的具名查询：一条查询一行 `run`、一个目录、一份报告（ADR-0066）。

    只有 `sync` 落 `data/runs/<run_id>/`——`search` / `lookup` 只写账本行，
    因为留档的消费者是调度器与事后审计，而按交互次数增长的 `data/runs/` 是
    ADR-0055 代价 5 担心的那件事（ADR-0066 决策 6）。
    """
    root = str(root)
    profile, errors = config.load_profile(root)
    if profile is None:
        message = errors[0].message if errors else (
            "没有 profile：" + str(config.config_dir(root) / config.PROFILE)
        )
        raise envelope.EnvelopeError(
            envelope.PROFILE_INVALID if errors else envelope.NO_PROFILE, message
        )
    cfg = config.load_search(root)
    selected = _select_queries(profile, queries, today=today)
    enabled = config.load_sources(root)
    source_list = list(sources) if sources is not None else list(config.active_sources(enabled))
    blocked_venues = _resolve_names(cfg.venue_blacklist, what="venue 黑名单")
    timezone_name = report.local_timezone()

    conn = store.connect(config.db_path(root))
    try:
        client = client or make_client()
        entries: list[dict] = []
        bodies: list[str] = []
        fresh_skips = 0
        quota_skips = 0
        quota_stops = 0
        failed = 0
        for query in selected:
            spec = _query_spec(query)
            venue_ids = _resolve_names(spec.venues, what="query " + query.name + " 的 venue 白名单")
            skipped = _skip_legs(spec, venue_ids=venue_ids)
            spec_key = config.spec_hash(
                all_of=spec.all_of, any_of=spec.any_of, venues=spec.venues,
                authors=spec.authors, since_years=spec.since_years,
                sources=source_list, sort="date",
                min_citations=spec.min_citations, min_fwci=spec.min_fwci,
            )
            from_date, to_date = _query_window(
                conn, query, spec_key=spec_key, lookback_days=cfg.lookback_days, today=today
            )
            collected = collect_run(
                conn, root, spec, spec_key=spec_key, query_name=query.name,
                sources=source_list, refresh=refresh, max_age_hours=cfg.max_age_hours,
                from_date=from_date, to_date=to_date, venue_ids=venue_ids,
                blocked_venues=blocked_venues, skipped=skipped, sort="date",
                client=client, quota_min_usd=cfg.min_remaining_usd, archive=True,
            )
            if collected["all_failed"]:
                failed += 1
            if collected["skip_reason"] == "freshness":
                fresh_skips += 1
            elif collected["skip_reason"] == "quota":
                quota_skips += 1
            if collected.get("quota_stopped"):
                quota_stops += 1
            # items 两栏只装本次 run 才入池的候选（ADR-0066 决策 3）
            items = []
            for key in store.candidates_first_seen_in_run(conn, collected["run_id"]):
                work = store.aggregate_work(conn, key)
                if work is not None:
                    items.append(report.minimal_item(work))
            run_json = report.build_run_json(
                store.get_run(conn, collected["run_id"]),
                command="sync",
                timezone_name=timezone_name,
                items=report.classify_items(items, window_from=from_date, window_to=to_date),
            )
            report.write_run(root, run_json)
            entries.append(report.stdout_entry(run_json))
            bodies.append(report.render_report(run_json))
        report.write_latest_report(root, bodies)
        if selected and failed == len(selected):
            raise envelope.EnvelopeError(
                envelope.SOURCE_ERROR,
                "全部 " + str(failed) + " 条查询都失败且零写入；细节见 data/runs/ 下的 run.json",
            )
        outcome = SyncOutcome(runs=entries)
        outcome.degraded = _sync_degraded(fresh_skips, quota_skips, quota_stops, len(selected))
        return outcome
    finally:
        conn.close()


def run_lookup(
    root,
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    title: str | None = None,
    refresh: str = "auto",
    limit: int = 20,
    venue_blacklist: Iterable[str] = (),
    mailto: str | None = None,
    client=None,
) -> Outcome:
    root = str(root)
    blocked_venues = _resolve_names(venue_blacklist, what="venue 黑名单")
    conn = store.connect(config.db_path(root))
    try:
        outcome = Outcome()
        work_key = None
        ident: tuple[str, str] | None = None
        if doi:
            doi = source_normalize_doi(doi) or doi
            ident = ("doi", doi.lower())
        elif arxiv_id:
            ident = ("arxiv", arxiv_id.lower())

        if ident is not None:
            work_key = _work_by_ident(conn, ident)
        elif title:
            rids = store.search_fts(conn, '"' + title.replace('"', '""') + '"', limit * 5)
            work_key = store.member_work(conn, rids[0]) if rids else None
        else:
            raise envelope.EnvelopeError(envelope.INVALID_ARGS, "必须给 --doi / --arxiv / --title 之一")

        if refresh == "true" and ident is None:
            outcome.degraded = {"reason": "标题不是标识，`--title` 用不了免费的单实体 GET；本次只查本地"}

        if _refresh_lookup(refresh, ident is not None, work_key):
            client = client or make_client()
            outcome.network = True
            records: list[dict] = []
            failures: list[dict] = []
            if doi:
                try:
                    found = openalex_source.work_by_doi(client, doi, api_key=_api_key())
                    if found:
                        records.append(found)
                except SourceError as exc:
                    failures.append({"source": "openalex", "reason": exc.reason})
                try:
                    found = crossref_source.lookup(client, doi, mailto=mailto)
                    if found:
                        records.append(found)
                except SourceError as exc:
                    failures.append({"source": "crossref", "reason": exc.reason})
            else:
                try:
                    found = openalex_source.work_by_arxiv(client, arxiv_id, api_key=_api_key())
                    if found:
                        records.append(found)
                except SourceError as exc:
                    failures.append({"source": "openalex", "reason": exc.reason})
                try:
                    for record in arxiv_source.lookup(client, arxiv_id):
                        records.append(record)
                except SourceError as exc:
                    failures.append({"source": "arxiv", "reason": exc.reason})
            with store.write_tx(conn):
                run_id = store.start_run(conn, spec_hash=None, query_name=None, refreshed=True,
                                         window_from=None, window_to=None)
                for record in records:
                    result = group.ingest(conn, record, run_id=run_id,
                                          blocked_venues=blocked_venues)
                    work_key = result.work_key
                store.finish_run(conn, run_id, status="ok", requests=client.requests,
                                 cost_usd=client.cost_usd, cost_basis=client.cost_basis,
                                 counts={"failures": failures})
            outcome.run_id = run_id
            outcome.requests = client.requests
            outcome.cost_usd = client.cost_usd
            outcome.cost_basis = client.cost_basis
            outcome.counts = {"new_candidates": 0, "dedup_blocked": 0,
                              "by_source": {}, "failures": failures}
            # 先刷再查本地：刷新后重新按标识解析——归组与桥接管都可能换掉持有者
            # （ADR-0065 决策 3），不拿 ingest 的返回值当结论。
            work_key = _work_by_ident(conn, ident) or work_key

        if work_key is not None and not group.work_is_excluded(conn, work_key, blocked_venues):
            item = store.aggregate_work(conn, work_key)
            if item and not item["is_retracted"]:
                outcome.items = [item]
        if not outcome.items and work_key is None:
            if ident is None:
                if refresh == "true":
                    raise envelope.EnvelopeError(
                        envelope.NOT_FOUND,
                        "标题未命中本地；`--title` 不联网（标题不是标识），要刷新请改用 --doi / --arxiv",
                    )
                raise envelope.EnvelopeError(
                    envelope.NOT_FOUND,
                    "标题未命中本地；`--title` 只做标题+摘要的短语匹配、从不联网，"
                    "换更准的写法或改用 --doi / --arxiv",
                )
            message = "标识未命中，且联网未取到" if outcome.network else (
                "标识未命中本地；--refresh false 不联网"
            )
            raise envelope.EnvelopeError(envelope.NOT_FOUND, message)
        return outcome
    finally:
        conn.close()

# --------------------------------------------------------------------------
# download：取原件 → bytes/ → artifact → works/ 投影（ADR-0067）
# --------------------------------------------------------------------------

#: 取件路线 → `artifact.kind`；`source_*` 是**抓来的原件**，`paper_*` 才是转换产物。
DOWNLOAD_KINDS = {
    "latex": "source_tar_gz",
    "html": "source_html",
    "pdf": "source_pdf",
}

#: 投影到 `works/<work_key>/` 时的文件名（IFACE-STORE §5 的 `source.*`）。
PROJECTION_NAMES = {
    "source_tar_gz": "source.tar.gz",
    "source_html": "source.html",
    "source_pdf": "source.pdf",
}

DOWNLOAD_SOURCES = ("auto", "latex", "html", "pdf", "all")

#: 每条 `--source` 的取件顺序；`auto` 是唯一会回退的（ADR-0067 决策 6）。
DOWNLOAD_PLANS = {
    "auto": ("latex", "html"),
    "latex": ("latex",),
    "html": ("html",),
    "pdf": ("pdf",),
    "all": ("latex", "html", "pdf"),
}


@dataclass
class DownloadOutcome:
    """一次取件的结果；`artifacts[]` 逐路线一行，含失败的那几条（IFACE-PAYLOAD §5.1）。"""

    work_key: str = ""
    record_uid: str = ""
    arxiv_id: str = ""
    artifacts: list = field(default_factory=list)
    network: bool = False
    requests: int = 0
    cost_usd: float = 0.0
    cost_basis: str = ""
    run_id: int | None = None

    def payload(self) -> dict:
        return {
            "work_key": self.work_key,
            "record_uid": self.record_uid,
            "arxiv_id": self.arxiv_id,
            "artifacts": self.artifacts,
            "network": self.network,
            "cost": {
                "usd": round(self.cost_usd, 6),
                "basis": self.cost_basis or ("本次未联网" if not self.network else ""),
                "requests": self.requests,
            },
        }


def same_version(local: str | None, remote: str | None) -> bool:
    """版本相等才算同一份；**任一侧 NULL 视为不等**（ADR-0067 决策 4）。"""
    return local is not None and remote is not None and local == remote


def _learn_version(client, arxiv_id: str) -> str | None:
    """从 feed 读一次当前版本，读不到就返回 None。

    只在 `record.source_version` 为空时用：不补这一次，按「NULL 视为不等」
    过渡期每次取件都会把源码包整份重下一遍（ADR-0067 决策 3 的代价 3）。
    """
    try:
        records = arxiv_source.lookup(client, arxiv_id)
    except SourceError:
        return None
    for record in records:
        if record.get("source_version"):
            return record["source_version"]
    return None


#: 这些键即使没有值也照样出现（显式 null 比缺席更好判：IFACE-PAYLOAD §5.1）。
_ARTIFACT_ALWAYS = frozenset({"source_version"})


def _artifact_entry(kind: str, status: str, **extra) -> dict:
    entry = {"kind": kind, "status": status}
    for key, value in extra.items():
        if value is not None or key in _ARTIFACT_ALWAYS:
            entry[key] = value
    return entry


def _reuse(root, cached) -> dict:
    """命中缓存：不再发请求，路径与大小现场算（IFACE-STORE §2：路径不落库）。"""
    sha = cached["sha256"]
    return _artifact_entry(
        cached["kind"], "reused", sha256=sha,
        source_version=cached["source_version"],
        size=paths.byte_path(root, sha).stat().st_size,
        path=str(paths.byte_path(root, sha)),
    )


def _work_projection(conn, root, work_key: str) -> Path:
    """投影目录：按当前元数据对账（改名、清掉合并空壳），返回可用目录（不建）。

    ADR-0068 决策 5/6：目录名是**对账出来**的，不是算出来的。改名与删除都尽力而为——
    失败就沿用现有目录，展示层的动作不该挡住取件。
    """
    base = paths.works_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    work_key = store.resolve_work(conn, work_key)
    name = store.projection_name(conn, work_key)
    merged = store.merged_targets(conn)
    stale: list[Path] = []
    current = False
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        key = identity.work_key_of(entry.name)
        if key is None:
            continue
        if key in merged:
            paths.remove_tree(entry)  # 合并后的空壳：成员已经搬走了（决策 6）
            continue
        if key != work_key:
            continue
        if entry.name == name:
            current = True
        else:
            stale.append(entry)
    target = paths.work_dir(root, name)
    if current:
        return target
    for old in stale:
        try:
            old.rename(target)
        except OSError:
            return old  # 改名失败：沿用旧目录（决策 5）
        return target
    return target


def run_download(
    root,
    work_key: str,
    *,
    source: str = "auto",
    refresh: str = "auto",
    client=None,
) -> DownloadOutcome:
    """取**原始字节**存 `bytes/`、投影 `works/<proj>/source.*`、登记 `artifact`。

    契约：IFACE §1 的 download 行、IFACE-PAYLOAD §5.1、ADR-0067 决策 4/5/6。
    **这里不转换**——转换属于 `read`。缓存判据是版本相等：`--refresh auto` 命中即零请求，
    `true` 无视版本、带验证器做条件 GET（304 复用已有字节），`false` 只读本地。
    """
    if source not in DOWNLOAD_SOURCES:
        raise envelope.EnvelopeError(envelope.INVALID_ARGS, "未知 --source：" + str(source))
    if refresh not in ("auto", "true", "false"):
        raise envelope.EnvelopeError(envelope.INVALID_ARGS, "未知 --refresh：" + str(refresh))
    root = str(root)
    conn = store.connect(config.db_path(root))
    try:
        work_key = store.resolve_work(conn, work_key)
        member, arxiv_id = _pick_record(conn, work_key)
        record_uid = member["record_uid"]
        remote_version = member["source_version"]
        outcome = DownloadOutcome(work_key=work_key, record_uid=record_uid, arxiv_id=arxiv_id)
        client = client or make_client()
        requests_before, cost_before = client.requests, client.cost_usd
        if refresh != "false" and remote_version is None:
            remote_version = _learn_version(client, arxiv_id)
            if remote_version:
                with store.write_tx(conn):
                    store.update_record(conn, record_uid, {"source_version": remote_version})
        failures: list[dict] = []
        pending: list[tuple] = []

        for route in DOWNLOAD_PLANS[source]:
            kind = DOWNLOAD_KINDS[route]
            cached = store.artifact_of(conn, record_uid, kind)
            usable = cached is not None and paths.byte_path(root, cached["sha256"]).is_file()
            matched = usable and same_version(cached["source_version"], remote_version)
            if refresh == "false" or (refresh == "auto" and matched):
                if usable:
                    outcome.artifacts.append(_reuse(root, cached))
                    if source == "auto":
                        break
                    continue
                reason = "本地没有这份产物，且 --refresh false 不联网"
                failures.append({"kind": kind, "reason": reason})
                outcome.artifacts.append(_artifact_entry(kind, "failed", source=route, reason=reason))
                continue
            # auto 钉住 record 声明的版本，true 取不带版本号的 URL（逃生口：不怕 HTML 重渲染，ADR-0067 决策 5）
            version = remote_version if refresh == "auto" else None
            headers = {}
            if refresh == "true" and usable:
                if cached["etag"]:
                    headers["If-None-Match"] = cached["etag"]
                if cached["last_modified"]:
                    headers["If-Modified-Since"] = cached["last_modified"]
            try:
                blob = arxiv_source.fetch_source(client, route, arxiv_id, version=version, headers=headers)
            except SourceError as exc:
                failures.append({"kind": kind, "reason": exc.reason})
                outcome.artifacts.append(
                    _artifact_entry(kind, "failed", source=route, reason=exc.reason)
                )
                continue
            if blob.status == 304 and usable:
                outcome.artifacts.append(_reuse(root, cached))
                if source == "auto":
                    break
                continue
            if blob.status != 200:
                reason = "HTTP " + str(blob.status)
                failures.append({"kind": kind, "reason": reason})
                outcome.artifacts.append(_artifact_entry(kind, "failed", source=route, reason=reason))
                continue
            if not blob.content:
                reason = "响应正文为空"
                failures.append({"kind": kind, "reason": reason})
                outcome.artifacts.append(_artifact_entry(kind, "failed", source=route, reason=reason))
                continue
            headers_lower = {key.lower(): value for key, value in blob.headers.items()}
            sha = paths.store_bytes(root, blob.content)
            # 取件那一刻 record 声明的版本；true 档拿到的可能更新，宁可低估也不误报「已是最新」
            pending.append((kind, sha, remote_version, headers_lower.get("etag"),
                            headers_lower.get("last-modified")))
            outcome.artifacts.append(
                _artifact_entry(
                    kind, "fetched", source=route, sha256=sha, size=len(blob.content),
                    source_version=remote_version, path=str(paths.byte_path(root, sha)),
                )
            )
            if source == "auto":
                break

        requests = client.requests - requests_before
        outcome.network = requests > 0
        outcome.requests = requests
        outcome.cost_usd = client.cost_usd - cost_before
        outcome.cost_basis = client.cost_basis
        served = [item for item in outcome.artifacts if item["status"] in ("fetched", "reused")]
        if requests:
            with store.write_tx(conn):
                outcome.run_id = store.start_run(
                    conn, spec_hash=None, query_name=None, refreshed=True,
                    window_from=None, window_to=None,
                )
                for kind, sha, source_version, etag, last_modified in pending:
                    store.add_artifact(
                        conn, record_uid, kind, sha, None, None,
                        source_version=source_version, etag=etag, last_modified=last_modified,
                    )
                store.finish_run(
                    conn, outcome.run_id, status="ok" if served else "failed",
                    requests=requests, cost_usd=outcome.cost_usd, cost_basis=client.cost_basis,
                    counts={"artifacts": [item[0] for item in pending], "failures": failures},
                )
        if not served:
            if refresh == "false":
                raise envelope.EnvelopeError(
                    envelope.PROJECTION_MISSING,
                    "本地没有这份原件，且 --refresh false 不联网；去掉 --refresh false 重跑",
                )
            detail = "; ".join(item["kind"] + "=" + item["reason"] for item in failures)
            raise envelope.EnvelopeError(envelope.SOURCE_ERROR, "取件失败：" + detail)

        target = _work_projection(conn, root, work_key)
        for item in served:
            name = PROJECTION_NAMES.get(item["kind"])
            if not name:
                continue
            paths.project_bytes(root, item["sha256"], target / name)
            item["work_path"] = str(target / name)
        return outcome
    finally:
        conn.close()


# --------------------------------------------------------------------------
# textify：取原生文本源 → 转换（M1.5；落盘与 artifact 登记归 M2）
# --------------------------------------------------------------------------

TEXTIFY_SOURCES = ("auto", "latex", "html")
ROLE_RANK = {"primary": 0, "preprint": 1, "published": 2, "duplicate": 3}

#: 单张图件的下载上限（正文整体由 fetch_html 自己的上限兜）。
HTML_MEDIA_MAX_BYTES = 8 * 1024 * 1024


@dataclass
class TextifyOutcome:
    """一次文本化的产物。**不写库、不落 bytes/ 与 works/**——那是 M2 的全文通道。

    本阶段全部输出写在调用方给的 work_dir 里，markdown 直接带回调用方，
    由调用方（M2 的 CLI / 本阶段的测试）决定写到哪儿。
    """

    work_key: str = ""
    record_uid: str = ""
    source: str = ""
    source_url: str = ""
    converter: str = "pandoc"
    converter_version: str = ""
    markdown: str = ""
    figures: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    attempts: list = field(default_factory=list)
    work_dir: str = ""
    network: bool = True
    requests: int = 0
    cost_usd: float = 0.0
    cost_basis: str = ""


def _arxiv_ids(conn, record_uid: str) -> list:
    return [row["value"] for row in store.idents_of(conn, record_uid) if row["ns"] == "arxiv"]


def _pick_record(conn, work_key: str, arxiv_id: str | None = None):
    """选代表记录：能出 arXiv 源的成员里按 role 排；返回 (member_row, arxiv_id)。"""
    if not store.work_exists(conn, work_key):
        raise envelope.EnvelopeError(envelope.NOT_FOUND, "没有这个 work_key：" + work_key)
    members = store.member_records(conn, work_key)
    if not members:
        raise envelope.EnvelopeError(envelope.NOT_FOUND, "work 没有成员记录：" + work_key)
    if arxiv_id:
        wanted = arxiv_id.lower()
        for member in members:
            if wanted in [value.lower() for value in _arxiv_ids(conn, member["record_uid"])]:
                return member, arxiv_id
        raise envelope.EnvelopeError(envelope.NOT_FOUND, "该 work 里没有这条 arXiv id：" + arxiv_id)
    ordered = sorted(members, key=lambda m: (ROLE_RANK.get(m["role"], 9), m["record_uid"]))
    for member in ordered:
        values = _arxiv_ids(conn, member["record_uid"])
        if values:
            return member, values[0]
    raise envelope.EnvelopeError(
        envelope.SOURCE_ERROR,
        "该 work 没有 arXiv 记录；初版只从 arXiv 取原件（LaTeX 源码 / HTML / PDF，REQ R3）",
    )


def _convert_latex(client, arxiv_id: str, base: Path, tool: str, pdftocairo: str | None):
    """路线 A：e-print → 解包 → 主 .tex → pandoc → 归一化。"""
    blob = arxiv_source.fetch_eprint(client, arxiv_id)
    source_dir = str(base / "src")
    info = textify.unpack_eprint(blob, source_dir, name_hint=arxiv_id)
    if info["kind"] == "pdf":
        raise textify.TextifyError("eprint_pdf_only", "e-print 只有 PDF，没有 LaTeX 源码")
    root_tex = textify.find_root_tex(source_dir, arxiv_id=arxiv_id)
    with open(root_tex, "r", encoding="utf-8", errors="replace") as handle:
        tex_text = handle.read()
    media = str(base / "media")
    raw = textify.run_pandoc(
        root_tex, media, pandoc=tool, reader="latex",
        bibliography=textify.find_bibliography(source_dir),
    )
    return textify.normalise_pandoc_output(
        raw, media, str(base / "figures"), tex_text=tex_text, pdftocairo=pdftocairo
    )


def _convert_html(client, arxiv_id: str, base: Path, tool: str, pdftocairo: str | None):
    """路线 B：/html/<id> → LaTeXML 清洗 → 图件本地化 → pandoc → 归一化。"""
    cleaned, clean_stats = htmlclean.clean_latexml(arxiv_source.fetch_html(client, arxiv_id))
    media = base / "html"
    media.mkdir(parents=True, exist_ok=True)
    entries = htmlclean.collect_media(cleaned, base_url=arxiv_source.html_url(arxiv_id))
    mapping: dict = {}
    missing = 0
    for index, entry in enumerate(entries, start=1):
        name = htmlclean.media_name(entry, index)
        try:
            if entry["kind"] == "inline":
                (media / name).write_bytes(entry["payload"])
            else:
                blob = client.get_bytes(entry["url"], source="arxiv", max_bytes=HTML_MEDIA_MAX_BYTES)
                if blob.status != 200:
                    missing += 1
                    continue
                (media / name).write_bytes(blob.content)
        except SourceError:
            missing += 1
            continue
        mapping[entry["src"]] = name
    source_file = str(media / "paper.html")
    with open(source_file, "w", encoding="utf-8") as handle:
        handle.write(htmlclean.rewrite_media(cleaned, mapping))
    markdown, stats = textify.normalise_pandoc_output(
        textify.run_pandoc(source_file, str(media), pandoc=tool, reader="html"),
        str(media), str(base / "figures"), pdftocairo=pdftocairo,
    )
    clean_stats["media_total"] = len(entries)
    clean_stats["media_missing"] = missing
    stats["html"] = clean_stats
    return markdown, stats


def run_textify(
    root,
    work_key: str,
    *,
    source: str = "auto",
    work_dir: str,
    client=None,
    pandoc: str | None = None,
    pdftocairo: str | None = None,
    arxiv_id: str | None = None,
) -> TextifyOutcome:
    """取原生文本源并转 Markdown；全程只写 work_dir。

    契约：docs/03-接口契约.md 的 textify 行、ADR-0056 决策 3、EX-2026-10-14-文本化 §7。
    auto = 先 LaTeX 源码、失败再 HTML；latex / html 指定即不回退。
    **本阶段不落 bytes/、不投影 works/、不登记 artifact、不做缓存命中**——那是 M2 的
    「全文按需取用通道」。
    """
    if source not in TEXTIFY_SOURCES:
        raise envelope.EnvelopeError(envelope.INVALID_ARGS, "未知 --source：" + str(source))
    root = str(root)
    base = Path(work_dir)
    base.mkdir(parents=True, exist_ok=True)
    conn = store.connect(config.db_path(root))
    try:
        work_key = store.resolve_work(conn, work_key)
        member, arxiv_id = _pick_record(conn, work_key, arxiv_id=arxiv_id)
        outcome = TextifyOutcome(
            work_key=work_key, record_uid=member["record_uid"], work_dir=str(base)
        )
        tool = pandoc or textify.find_pandoc(root)
        if not tool:
            raise envelope.EnvelopeError(
                envelope.SOURCE_ERROR,
                "没有 pandoc：设 ACADEMIC_SEARCH_PANDOC、放 vendor/pandoc/ 或装到 PATH（DEV-CONV §4）",
            )
        renderer = pdftocairo if pdftocairo is not None else textify.find_pdftocairo(root)
        client = client or make_client()
        attempts: list = []
        plan = ["latex", "html"] if source == "auto" else [source]
        for index, route in enumerate(plan):
            try:
                if route == "latex":
                    markdown, stats = _convert_latex(client, arxiv_id, base, tool, renderer)
                    url = arxiv_source.eprint_url(arxiv_id)
                else:
                    markdown, stats = _convert_html(client, arxiv_id, base, tool, renderer)
                    url = arxiv_source.html_url(arxiv_id)
            except (textify.TextifyError, SourceError) as exc:
                attempts.append({
                    "source": route,
                    "reason": getattr(exc, "reason", str(exc)),
                    "message": str(exc),
                })
                if index + 1 == len(plan):
                    detail = "; ".join(a["source"] + "=" + a["reason"] for a in attempts)
                    raise envelope.EnvelopeError(
                        envelope.SOURCE_ERROR, "文本化失败：" + detail
                    ) from exc
                continue
            outcome.source = "arxiv_latex" if route == "latex" else "arxiv_html"
            outcome.source_url = url
            outcome.markdown = markdown
            outcome.stats = stats
            outcome.converter_version = textify.converter_version(
                tool, textify.renderer_id(renderer)
            )
            break
        outcome.attempts = attempts
        outcome.requests = client.requests
        outcome.cost_usd = client.cost_usd
        outcome.cost_basis = client.cost_basis
        figures_dir = base / "figures"
        if figures_dir.is_dir():
            outcome.figures = [
                {"name": path.name, "path": str(path), "bytes": path.stat().st_size}
                for path in sorted(figures_dir.iterdir()) if path.is_file()
            ]
        return outcome
    finally:
        conn.close()

