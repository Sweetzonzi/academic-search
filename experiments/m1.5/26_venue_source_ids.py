r"""会名 → OpenAlex source id 的确认（ADR-0060 决策 2 的登记表来源）。

目的：`src/academic_search/venues.toml` 里每一条 `ids` 都要是人工确认过的 source
     实体。缩写搜不到、全称片段一次返回几十个候选，所以「会名 → S-id」只能列候选
     后人工拍板（ADR-0060 理由 6）；本脚本把这一步做成可复现的。

三个子命令：
     list  <全称片段>...   按片段列 `/sources` 候选（每次 10 credits / $0.001）
     show  <S-id>...       单实体 GET，打印完整 counts_by_year（**免费**）
     count <S-id> <年>     数该载体某一年的作品数（结构化 filter，1 credit / $0.0001）
     work  <DOI 或 W-id>…  反查一篇论文挂在哪些载体上（单实体 GET，**免费**）——
                           当 `/sources` 搜索按会名找不到时，这是最后一条线索

用法（PowerShell，key 只在 User 作用域）：

     $env:OPENALEX_API_KEY = [System.Environment]::GetEnvironmentVariable('OPENALEX_API_KEY','User')
     & 'D:\anaconda3\python.exe' experiments\m1.5\26_venue_source_ids.py list "intelligent robots and systems"
     & 'D:\anaconda3\python.exe' experiments\m1.5\26_venue_source_ids.py show S4363608614
     & 'D:\anaconda3\python.exe' experiments\m1.5\26_venue_source_ids.py count S4363608614 2024
     & 'D:\anaconda3\python.exe' experiments\m1.5\26_venue_source_ids.py work doi:10.48550/arXiv.1706.05296

输出支撑：`venues.toml` 的 `ids` 与 `note`；数字同时记进
     docs/实验/EX-2026-10-16-检索口径与来源校验.md §16。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.openalex.org"
SELECT = "id,display_name,type,works_count,counts_by_year"


def _get(path: str, params: dict, key: str | None) -> dict:
    if key:
        params = dict(params, api_key=key)
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def _short(value: str | None) -> str:
    return (value or "").rsplit("/", 1)[-1]


def _years(item: dict, newest: int = 5) -> str:
    counts = item.get("counts_by_year") or []
    counts = sorted(counts, key=lambda c: c["year"], reverse=True)[:newest]
    return " ".join(str(c["year"]) + ":" + str(c["works_count"]) for c in counts)


def cmd_list(fragments: list[str], key: str | None) -> int:
    for fragment in fragments:
        print("=" * 78)
        print("<- " + fragment)
        try:
            payload = _get(
                "/sources",
                {"filter": "display_name.search:" + fragment, "per-page": 6, "select": SELECT},
                key,
            )
        except urllib.error.HTTPError as exc:
            print("  HTTP", exc.code, exc.reason)
            continue
        for item in payload.get("results") or []:
            print("  %-12s %-10s %8d  %-58s %s" % (
                _short(item.get("id")), item.get("type"), item.get("works_count") or 0,
                (item.get("display_name") or "")[:58], _years(item),
            ))
    return 0


def cmd_show(ids: list[str], key: str | None) -> int:
    for source_id in ids:
        print("=" * 78)
        try:
            item = _get("/sources/" + source_id, {"select": SELECT}, key)
        except urllib.error.HTTPError as exc:
            print(source_id, "HTTP", exc.code, exc.reason)
            continue
        print("%s  %-10s %8d  %s" % (
            _short(item.get("id")), item.get("type"), item.get("works_count") or 0,
            item.get("display_name"),
        ))
        counts = sorted(item.get("counts_by_year") or [], key=lambda c: c["year"], reverse=True)
        print("  近年：" + " ".join(str(c["year"]) + ":" + str(c["works_count"]) for c in counts[:8]))
    return 0


def cmd_count(pairs: list[str], key: str | None) -> int:
    for pair in pairs:
        source_id, _, year = pair.partition(":")
        try:
            payload = _get(
                "/works",
                {
                    "filter": "locations.source.id:" + source_id + ",publication_year:" + year,
                    "per-page": 1,
                    "select": "id",
                },
                key,
            )
        except urllib.error.HTTPError as exc:
            print(pair, "HTTP", exc.code, exc.reason)
            continue
        print("%-14s %s  作品 %d" % (source_id, year, (payload.get("meta") or {}).get("count") or 0))
    return 0


def cmd_work(refs: list[str], key: str | None) -> int:
    for ref in refs:
        print("=" * 78)
        try:
            item = _get(
                "/works/" + urllib.parse.quote(ref, safe=""),
                {"select": "id,display_name,publication_year,type,primary_location,locations"},
                key,
            )
        except urllib.error.HTTPError as exc:
            print(ref, "HTTP", exc.code, exc.reason)
            continue
        print("%s  %s  %s" % (
            _short(item.get("id")), item.get("publication_year"), item.get("display_name"),
        ))
        print("  primary: " + _location(item.get("primary_location")))
        for location in item.get("locations") or []:
            print("  location: " + _location(location))

    return 0


def _location(location: dict | None) -> str:
    if not location:
        return "—"
    source = location.get("source") or {}
    return "%s  %s  version=%s" % (
        _short(source.get("id")), source.get("display_name"), location.get("version"),
    )


def main(argv: list[str]) -> int:
    key = os.environ.get("OPENALEX_API_KEY") or None
    if not key:
        print("没有 OPENALEX_API_KEY：匿名档会 429", file=sys.stderr)
        return 1
    if len(argv) < 3:
        print(__doc__)
        return 2
    mode, rest = argv[1], argv[2:]
    if mode == "list":
        return cmd_list(rest, key)
    if mode == "show":
        return cmd_show(rest, key)
    if mode == "count":
        return cmd_count(rest, key)
    if mode == "work":
        return cmd_work(rest, key)
    print("未知子命令：" + mode, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
