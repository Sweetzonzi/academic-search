"""用例编排：检索、查询、入库、成本汇总与费用可见。

契约：docs/03-接口契约.md、docs/接口/数据源适配.md §5/§9。
cli.py 只做参数与信封；SQL 与网络都在这层（DEV-CONV §4）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from . import config, envelope, group, paths, store
from .sources import SearchSpec, SourceError
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


def _should_refresh(conn, spec_hash: str, mode: str, max_age_hours: int) -> bool:
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


def _fetch(client, source: str, spec: SearchSpec, *, limit: int,
           from_date: str | None, to_date: str | None) -> list[dict]:
    if source == "openalex":
        return openalex_source.search(
            client, spec, limit=limit, api_key=_api_key(),
            from_date=from_date, to_date=to_date,
        )
    if source == "arxiv":
        return arxiv_source.search(client, spec, limit=limit,
                                   from_date=from_date, to_date=to_date)
    raise SourceError(source, "该源不参与采集（初版仅 openalex / arxiv）", False)


def _fts_query(spec: SearchSpec) -> str:
    def term(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    groups: list[str] = []
    if spec.all_of:
        groups.append(" AND ".join(term(t) for t in spec.all_of))
    if spec.any_of:
        groups.append(" OR ".join(term(t) for t in spec.any_of))
    if not groups:
        return ""
    return "(" + ") AND (".join(groups) + ")"


def _body_work_keys(root, spec: SearchSpec) -> list[str]:
    """--in body|both：对 works/ 做线性子串扫描（ADR-0036）。"""
    base = paths.works_dir(root)
    if not base.is_dir():
        return []
    needles = [t.lower() for t in list(spec.all_of) + list(spec.any_of) if t.strip()]
    if not needles:
        return []
    found: list[str] = []
    for work in sorted(base.iterdir()):
        if not work.is_dir():
            continue
        for doc in sorted(work.rglob("*.md")):
            try:
                text = doc.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if any(needle in text for needle in needles):
                found.append(work.name)
                break
    return found


def _venue_ok(item: dict, venues: Iterable[str]) -> bool:
    whitelist = [group.normalize_venue(v) for v in venues if group.normalize_venue(v)]
    if not whitelist:
        return True
    venue = group.normalize_venue(item.get("venue"))
    return any(entry in venue for entry in whitelist)


def _author_ok(item: dict, authors: Iterable[str]) -> bool:
    wanted = [a.strip().lower() for a in authors if a.strip()]
    if not wanted:
        return True
    names = " ".join(item.get("authors") or []).lower()
    return any(a in names for a in wanted)


def local_work_keys(
    conn, spec: SearchSpec, *, sources: Iterable[str], from_date: str | None,
    to_date: str | None, in_scope: str, root,
) -> list[str]:
    where = ["r.status = 'live'"]
    params: list = []
    if spec.all_of or spec.any_of:
        rids = store.search_fts(conn, _fts_query(spec), 500)
        if not rids:
            return []
        where.append("r.record_uid IN (" + ",".join("?" for _ in rids) + ")")
        params.extend(rids)
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
    sql = (
        "SELECT DISTINCT wm.work_key FROM record r "
        "JOIN work_member wm ON wm.record_uid = r.record_uid "
        "WHERE " + " AND ".join(where) + " ORDER BY wm.work_key"
    )
    keys = [row["work_key"] for row in conn.execute(sql, params)]
    if in_scope in ("body", "both"):
        for key in _body_work_keys(root, spec):
            if key not in keys:
                keys.append(key)
    return keys


def _sorted_items(conn, keys, *, spec: SearchSpec, blacklist, sort: str, limit: int) -> list[dict]:
    items = []
    for key in keys:
        if group.work_is_excluded(conn, key, blacklist):
            continue
        item = store.aggregate_work(conn, key)
        if item is None or item["is_retracted"]:
            continue
        if not _venue_ok(item, spec.venues):
            continue
        if not _author_ok(item, spec.authors):
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
    return items[:limit]


def run_search(
    root,
    spec: SearchSpec,
    *,
    limit: int = 20,
    sources: Iterable[str] = ("openalex", "arxiv"),
    refresh: str = "auto",
    max_age_hours: int = config.DEFAULT_MAX_AGE_HOURS,
    lookback_days: int | None = None,
    sort: str = "date",
    in_scope: str = "meta",
    blacklist: Iterable[str] = (),
    client=None,
    today=None,
) -> Outcome:
    root = str(root)
    conn = store.connect(config.db_path(root))
    try:
        spec_key = config.spec_hash(
            all_of=spec.all_of, any_of=spec.any_of, venues=spec.venues,
            authors=spec.authors, since_years=spec.since_years,
        )
        from_date, to_date = config.date_window(
            since_years=spec.since_years, lookback_days=lookback_days, today=today
        )
        outcome = Outcome()
        do_refresh = _should_refresh(conn, spec_key, refresh, max_age_hours)
        if do_refresh:
            outcome.network = True
            client = client or make_client()
            with store.write_tx(conn):
                run_id = store.start_run(
                    conn, spec_hash=spec_key, query_name=None, refreshed=True,
                    window_from=from_date, window_to=to_date,
                )
            failures: list[dict] = []
            by_source: dict[str, int] = {}
            new_candidates = 0
            dedup_blocked = 0
            wrote_anything = False
            for source in sources:
                try:
                    records = _fetch(client, source, spec, limit=limit,
                                     from_date=from_date, to_date=to_date)
                except SourceError as exc:
                    failures.append({"source": source, "reason": exc.reason})
                    by_source[source] = 0
                    continue
                by_source[source] = len(records)
                if records:
                    wrote_anything = True
                with store.write_tx(conn):
                    for record in records:
                        result = group.ingest(conn, record, run_id=run_id, blacklist=blacklist)
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
            with store.write_tx(conn):
                store.finish_run(
                    conn, run_id, status="ok", requests=client.requests,
                    cost_usd=client.cost_usd, cost_basis=client.cost_basis, counts=counts,
                )
            if failures and len(failures) == len(list(sources)) and not wrote_anything:
                raise envelope.EnvelopeError(
                    envelope.SOURCE_ERROR,
                    "全部源失败且无写入：" + "; ".join(f["source"] + "=" + f["reason"] for f in failures),
                )
            outcome.run_id = run_id
            outcome.counts = counts
            outcome.requests = client.requests
            outcome.cost_usd = client.cost_usd
            outcome.cost_basis = client.cost_basis
        else:
            with store.write_tx(conn):
                run_id = store.start_run(
                    conn, spec_hash=spec_key, query_name=None, refreshed=False,
                    window_from=from_date, window_to=to_date,
                )
                store.finish_run(conn, run_id, status="ok", requests=0, cost_usd=0.0,
                                 cost_basis="跳过联网", counts={})
            outcome.run_id = run_id
            if refresh == "auto":
                outcome.degraded = {"reason": "同一检索式在新鲜度窗口内，跳过联网（--refresh auto）"}

        keys = local_work_keys(
            conn, spec, sources=sources, from_date=from_date, to_date=to_date,
            in_scope=in_scope, root=root,
        )
        outcome.items = _sorted_items(conn, keys, spec=spec, blacklist=blacklist,
                                      sort=sort, limit=limit)
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
    blacklist: Iterable[str] = (),
    client=None,
) -> Outcome:
    root = str(root)
    conn = store.connect(config.db_path(root))
    try:
        outcome = Outcome()
        work_key = None
        if doi:
            hits = store.record_uids_by_ident(conn, "doi", doi.lower())
            work_key = store.member_work(conn, hits[0]) if hits else None
        elif arxiv_id:
            hits = store.record_uids_by_ident(conn, "arxiv", arxiv_id.lower())
            work_key = store.member_work(conn, hits[0]) if hits else None
        elif title:
            rids = store.search_fts(conn, '"' + title.replace('"', '""') + '"', limit * 5)
            work_key = store.member_work(conn, rids[0]) if rids else None
        else:
            raise envelope.EnvelopeError(envelope.INVALID_ARGS, "必须给 --doi / --arxiv / --title 之一")

        if work_key is None and refresh != "false" and (doi or arxiv_id):
            client = client or make_client()
            outcome.network = True
            records: list[dict] = []
            failures: list[dict] = []
            if doi:
                try:
                    found = openalex_source.work_by_doi(client, doi)
                    if found:
                        records.append(found)
                except SourceError as exc:
                    failures.append({"source": "openalex", "reason": exc.reason})
                try:
                    found = crossref_source.lookup(client, doi)
                    if found:
                        records.append(found)
                except SourceError as exc:
                    failures.append({"source": "crossref", "reason": exc.reason})
            else:
                try:
                    found = openalex_source.work_by_arxiv(client, arxiv_id)
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
                    result = group.ingest(conn, record, run_id=run_id, blacklist=blacklist)
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

        if work_key is not None and not group.work_is_excluded(conn, work_key, blacklist):
            item = store.aggregate_work(conn, work_key)
            if item and not item["is_retracted"]:
                outcome.items = [item]
        if not outcome.items and work_key is None:
            raise envelope.EnvelopeError(envelope.NOT_FOUND, "标识未命中，且联网未取到")
        return outcome
    finally:
        conn.close()
