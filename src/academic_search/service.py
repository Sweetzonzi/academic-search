"""用例编排：检索、查询、入库、成本汇总与费用可见。

契约：docs/03-接口契约.md、docs/接口/数据源适配.md §5/§9。
cli.py 只做参数与信封；SQL 与网络都在这层（DEV-CONV §4）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from . import config, envelope, group, htmlclean, paths, store, textify
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
    sources = list(sources)
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
            all_failed = bool(failures) and len(failures) == len(sources) and not wrote_anything
            with store.write_tx(conn):
                store.finish_run(
                    conn, run_id, status="failed" if all_failed else "ok", requests=client.requests,
                    cost_usd=client.cost_usd, cost_basis=client.cost_basis, counts=counts,
                )
            if all_failed:
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
            doi = source_normalize_doi(doi) or doi
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
        "该 work 没有 arXiv 记录；初版只有 arXiv LaTeX 源码与 arXiv HTML 两条路线（REQ R3）",
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

