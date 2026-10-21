"""真实 launcher 与本地库的 warnings 验收；--live 另发一次 OpenAlex 请求。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent / "out" / "warnings-acceptance"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def invoke(label: str, args: list[str], *, root: Path = ROOT, live: bool = False) -> dict:
    key = os.environ.get("OPENALEX_API_KEY", "")
    env = dict(os.environ, ACADEMIC_SEARCH_ROOT=str(ROOT),
               ACADEMIC_SEARCH_PYTHON=sys.executable, PYTHONIOENCODING="utf-8",
               OPENALEX_API_KEY=key if live else "offline-acceptance-key")
    command = [str(ROOT / "skills/academic-search/scripts/run.cmd"), "--root", str(root), *args]
    result = subprocess.run(
        [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"), "/d", "/c", *command],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    raw = result.stdout.replace(key, "REDACTED") if key else result.stdout
    (OUT / (label + ".json")).write_text(raw, encoding="utf-8")
    require(result.returncode == 0, label + ": launcher failed; inspect redacted artifact")
    envelope = json.loads(raw)
    require(envelope.get("ok") is True, label + ": unsuccessful envelope")
    data = envelope["data"]
    if not live:
        require(data["network"] is False and data["cost"]["requests"] == 0,
                label + ": unexpected network request")
    require(not data.get("counts", {}).get("failures"), label + ": source failures")
    warnings = data.get("warnings", [])
    require(isinstance(warnings, list) and all(isinstance(w, str) for w in warnings),
            label + ": invalid warnings type")
    require(len(warnings) == len(set(warnings)), label + ": duplicate warnings")
    row = {"probe": label, "network": data["network"], "cost": data["cost"],
           "returned": len(data["items"]), "warnings": warnings}
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.live:
        require(bool(os.environ.get("OPENALEX_API_KEY")), "OPENALEX_API_KEY is required")
    OUT.mkdir(parents=True, exist_ok=True)
    probes = []
    plain = invoke("offline-catalog", ["search", "--refresh", "false", "--limit", "20"])
    require("warnings" not in plain and bool(plain["items"]), "normal catalog warning/missing items")
    probes.append(plain)
    sorted_data = invoke("offline-citations", ["search", "--refresh", "false", "--sort", "citations"])
    require("warnings" not in sorted_data, "sorting alone must not warn of a source skip")
    probes.append(sorted_data)

    for label, options, terms in [
        ("arxiv-citations", ["--min-citations", "100"], ["被引次数", "--min-citations"]),
        ("arxiv-fwci", ["--min-fwci", "2"], ["FWCI", "--min-fwci"]),
        ("arxiv-combined", ["--min-citations", "100", "--min-fwci", "2", "--venue", "ICRA"],
         ["被引次数", "FWCI", "载体信息", "--venue"]),
    ]:
        data = invoke(label, ["search", "--any-of", "multi-agent", "--source", "arxiv",
                              "--refresh", "true", *options])
        require(len(data.get("warnings", [])) == 1, label + ": missing/duplicate skip warning")
        require(all(term in data["warnings"][0] for term in ["arXiv", "不提供", *terms]),
                label + ": incomplete explanation")
        require(data["counts"]["complete"] is False, label + ": all-skipped collection complete")
        probes.append(data)

    # 避免 cmd 的元字符；候选标题来自实际 CLI 已返回的有效本地 work。
    title = next(item["title"] for item in plain["items"]
                 if re.fullmatch(r"[A-Za-z0-9 ,.:?'\-]+", item["title"]))
    title_data = invoke("title-refresh", ["lookup", "--title", title, "--refresh", "true"])
    require(title_data["warnings"] == [title_data["degraded"]["reason"]], "title warning mismatch")
    probes.append(title_data)

    snapshot = OUT / ("snapshot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    (snapshot / "data").mkdir(parents=True)
    (snapshot / "config").mkdir()
    # launcher 的显式根同时绑定代码与数据，验收副本需要完整的 src/。
    shutil.copytree(ROOT / "src", snapshot / "src", ignore=shutil.ignore_patterns("__pycache__"))
    # 一致性备份实际语料，只在验收副本中将预算改为 1；不改用户配置。
    with sqlite3.connect((ROOT / "data/sidecar.db").as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(snapshot / "data/sidecar.db") as target:
            source.backup(target)
    (snapshot / "config/search.toml").write_text("[local]\nmax_candidates=1\n", encoding="utf-8")
    budget = invoke("candidate-budget", ["search", "--any-of", "multi-agent", "--refresh", "false",
                                          "--offset", "1"], root=snapshot)
    require(budget["counts"]["local"]["truncated"] is True and not budget["items"],
            "budget probe did not reach truncated empty page")
    require(len(budget.get("warnings", [])) == 1 and "候选预算" in budget["warnings"][0],
            "missing budget warning")
    probes.append(budget)

    if args.live:
        query = ["search", "--any-of", "multi-agent decision", "--min-citations", "100",
                 "--sort", "citations", "--limit", "3"]
        data = invoke("live-openalex", [*query, "--refresh", "true"], live=True)
        require(data["network"] is True and data["cost"]["requests"] == 1, "live request count mismatch")
        require(data["counts"]["complete"] is True and len(data.get("warnings", [])) == 1,
                "live skip/complete mismatch")
        require("arXiv" in data["warnings"][0], "live warning missing source explanation")
        require(all(item["cited_by_count"] >= 100 for item in data["items"]), "live threshold failed")
        probes.append(data)
        fresh = invoke("freshness-reuse", [*query, "--refresh", "auto"], live=True)
        require(fresh["network"] is False and fresh["cost"]["requests"] == 0
                and "warnings" not in fresh, "fresh cache falsely warned of a source skip")
        probes.append(fresh)

    summary = {"measured_at": datetime.now(timezone.utc).isoformat(), "passed": True,
               "live": args.live, "probes": len(probes),
               "requests": sum(p["cost"]["requests"] for p in probes),
               "cost_usd": round(sum(p["cost"]["usd"] for p in probes), 6),
               "artifacts": str(OUT), "budget_snapshot": str(snapshot)}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
