"""`data/runs/<run_id>/` 的留档：`run.json`、`report.md` 与 `data/latest-report.md`。

契约：docs/接口/输出载荷.md §3、docs/接口/存储与迁移.md §2、ADR-0055、ADR-0066。
只拼路径、裁字段、渲染文本；SQL 在 store.py、编排在 service.py（DEV-CONV §4）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

#: `items` 两栏用的可溯源最小集（IFACE-PAYLOAD §2；不带摘要与正文，ADR-0057）。
MINIMAL_FIELDS = ("work_key", "title", "published_at", "doi", "arxiv_id", "sources")

#: `sync` 的 stdout 子集要从 `run.json` 摘掉的字段（ADR-0066 决策 4）：
#: `command` / `responses` / `timezone` / 时间字段 / `items`。
STDOUT_DROPPED = ("command", "responses", "timezone", "started_at", "finished_at", "items")

RUN_JSON = "run.json"
REPORT_MD = "report.md"
LATEST_REPORT = "latest-report.md"


# --------------------------------------------------------------------------
# 路径（现场拼，绝不落库——IFACE-STORE §2）
# --------------------------------------------------------------------------

def runs_dir(root) -> Path:
    return Path(root) / "data" / "runs"


def run_dir(root, run_id: int) -> Path:
    return runs_dir(root) / str(run_id)


def responses_dir(root, run_id: int) -> Path:
    return run_dir(root, run_id) / "responses"


def latest_report_path(root) -> Path:
    return Path(root) / "data" / LATEST_REPORT


def local_timezone() -> str:
    """渲染报告用的本地时区；拿不到 IANA 名就退回 `UTC±HH:MM`。"""
    local = datetime.now().astimezone()
    key = getattr(local.tzinfo, "key", None)
    if key:
        return str(key)
    offset = local.utcoffset()
    if offset is None:
        return "UTC"
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return "UTC" + sign + "%02d:%02d" % (total // 3600, (total % 3600) // 60)


# --------------------------------------------------------------------------
# 结果清单：可溯源最小集与「新发表 / 新发现」两栏（ADR-0066 决策 3）
# --------------------------------------------------------------------------

def minimal_item(item: Mapping) -> dict:
    """结果条目 → `items` 两栏的可溯源最小集。"""
    return {field: item.get(field) for field in MINIMAL_FIELDS}


def _by_date_desc(items: Sequence[Mapping]) -> list:
    """按 `published_at` 降序；无日期的排后（IFACE-PAYLOAD §3）。"""
    dated = [item for item in items if item.get("published_at")]
    undated = [item for item in items if not item.get("published_at")]
    dated.sort(key=lambda item: item["published_at"], reverse=True)
    return dated + undated


def classify_items(
    items: Sequence[Mapping], *, window_from: str | None, window_to: str | None
) -> dict:
    """分成「新发表」（日期落在本次窗口内）与「新发现」（更早）。

    ADR-0066 决策 3：本次 run 才入池的候选里，日期在窗口内的算新发表，
    其余算新发现。无日期的记新发现——它证明不了「刚发表」。
    """
    published: list = []
    discovered: list = []
    for item in items:
        when = item.get("published_at")
        in_window = (
            bool(when)
            and (window_from is None or when >= window_from)
            and (window_to is None or when <= window_to)
        )
        (published if in_window else discovered).append(item)
    return {
        "new_published": _by_date_desc(published),
        "new_discovered": _by_date_desc(discovered),
    }


# --------------------------------------------------------------------------
# run.json 与 stdout 子集
# --------------------------------------------------------------------------

def _loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def cost_of(row: Mapping) -> dict:
    """账本行的花费三件套；`basis` 缺席时按是否联网补一句口径。"""
    basis = row["cost_basis"] or ("本次未联网" if not row["refreshed"] else "")
    return {
        "usd": round(row["cost_usd"] or 0.0, 6),
        "basis": basis,
        "requests": row["requests"] or 0,
    }


def build_run_json(
    row: Mapping, *, command: str, timezone_name: str, items: Mapping
) -> dict:
    """账本行 + 结果清单 → 落盘的 `run.json`（IFACE-PAYLOAD §3）。"""
    return {
        "run_id": row["run_id"],
        "command": command,
        "query": row["query_name"],
        "spec_hash": row["spec_hash"],
        "refreshed": bool(row["refreshed"]),
        "window": {"from": row["window_from"], "to": row["window_to"]},
        "timezone": timezone_name,
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "counts": _loads(row["counts"], {}),
        "cost": cost_of(row),
        "responses": _loads(row["responses"], []),
        "items": {
            "new_published": list(items.get("new_published") or []),
            "new_discovered": list(items.get("new_discovered") or []),
        },
    }


def stdout_entry(run_json: Mapping) -> dict:
    """`run.json` → `sync` 的 `data.runs[]` 元素（真子集，ADR-0066 决策 4）。"""
    return {key: value for key, value in run_json.items() if key not in STDOUT_DROPPED}


# --------------------------------------------------------------------------
# 渲染与落盘
# --------------------------------------------------------------------------

def _source_names(item: Mapping) -> list[str]:
    names: list[str] = []
    for entry in item.get("sources") or []:
        name = entry.get("source") if isinstance(entry, Mapping) else None
        if name and name not in names:
            names.append(name)
    return names


def _item_lines(item: Mapping) -> list[str]:
    title = item.get("title") or "（无标题）"
    when = item.get("published_at") or "日期未知"
    ids = []
    if item.get("doi"):
        ids.append("`" + str(item["doi"]) + "`")
    if item.get("arxiv_id"):
        ids.append("`arXiv:" + str(item["arxiv_id"]) + "`")
    detail = ids + ["`" + str(item.get("work_key") or "?") + "`"]
    names = _source_names(item)
    if names:
        detail.append("来源 " + "、".join(names))
    return ["- " + str(when) + "　" + str(title), "  - " + "；".join(detail), ""]


def _failure_line(counts: Mapping) -> str:
    failures = counts.get("failures") or []
    if not failures:
        return "无"
    return "；".join(str(f.get("source")) + "＝" + str(f.get("reason")) for f in failures)


def _by_source_line(counts: Mapping) -> str:
    by_source = counts.get("by_source") or {}
    if not by_source:
        return "—"
    return "、".join(str(name) + " " + str(count) for name, count in by_source.items())


def render_report(run_json: Mapping) -> str:
    """一条 run 的 `report.md` 正文（不含末尾换行）。头部与同目录 `run.json` 同源。"""
    counts = run_json.get("counts") or {}
    cost = run_json.get("cost") or {}
    window = run_json.get("window") or {}
    items = run_json.get("items") or {}
    published = items.get("new_published") or []
    discovered = items.get("new_discovered") or []
    basis = cost.get("basis") or "—"
    lines = [
        "# run " + str(run_json.get("run_id")) + "　" + str(run_json.get("query") or "（无查询名）"),
        "",
        "- 窗口：" + str(window.get("from") or "—") + " → " + str(window.get("to") or "—")
        + "（" + str(run_json.get("timezone") or "—") + "）",
        "- 刷新：" + ("是" if run_json.get("refreshed") else "否")
        + "；状态：" + str(run_json.get("status")),
        "- 花费：$" + ("%.4f" % float(cost.get("usd") or 0.0)) + " / "
        + str(cost.get("requests") or 0) + " 次请求（" + str(basis) + "）",
        "- 计数：新候选 " + str(counts.get("new_candidates", 0))
        + "、去重拦下 " + str(counts.get("dedup_blocked", 0))
        + "；各源 " + _by_source_line(counts),
        "- 失败源：" + _failure_line(counts),
        "",
        "## 新发表（" + str(len(published)) + "）",
        "",
    ]
    for item in published:
        lines.extend(_item_lines(item))
    if not published:
        lines.extend(["（无）", ""])
    lines.extend(["## 新发现（" + str(len(discovered)) + "）", ""])
    for item in discovered:
        lines.extend(_item_lines(item))
    if not discovered:
        lines.append("（无）")
    return "\n".join(lines).rstrip("\n")


def write_run(root, run_json: Mapping, *, report_text: str | None = None) -> Path:
    """落 `data/runs/<run_id>/run.json` 与 `report.md`，返回该目录。"""
    directory = run_dir(root, run_json["run_id"])
    directory.mkdir(parents=True, exist_ok=True)
    text = render_report(run_json) if report_text is None else report_text
    (directory / RUN_JSON).write_text(
        json.dumps(run_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (directory / REPORT_MD).write_text(text + "\n", encoding="utf-8")
    return directory


def write_latest_report(root, bodies: Sequence[str]) -> Path:
    """`data/latest-report.md` = 最近一次调用的汇总，按查询分节（ADR-0066 决策 5）。

    每节就是对应 run 的 `report.md` 原文，因此单查询调用时两者逐字相同。
    """
    path = latest_report_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n---\n\n".join(body.rstrip("\n") for body in bodies) + "\n", encoding="utf-8")
    return path


def write_response(root, run_id: int, n: int, source: str, payload: bytes) -> Path:
    """把一页响应原文写进 `responses/<n>-<source>.json`（ADR-0066 决策 10）。"""
    directory = responses_dir(root, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (str(n) + "-" + source + ".json")
    path.write_bytes(payload)
    return path
