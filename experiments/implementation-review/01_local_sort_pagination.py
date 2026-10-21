"""通过真实 Windows launcher 验收本地库排序与 offset；全程 --refresh false。"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent / "out" / "local-sort-pagination"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_metrics() -> dict:
    with sqlite3.connect((ROOT / "data/sidecar.db").as_uri() + "?mode=ro", uri=True) as conn:
        return {
            key: {"published_at": published, "cited_by_count": citations}
            for key, published, citations in conn.execute(
                "SELECT wm.work_key, MIN(NULLIF(r.published_at, '')), MAX(r.cited_by_count) "
                "FROM work_member wm JOIN record r ON r.record_uid = wm.record_uid "
                "GROUP BY wm.work_key"
            )
        }


def invoke(sort: str, offset: int, limit: int, label: str) -> dict:
    launcher = ROOT / "skills/academic-search/scripts/run.cmd"
    args = [str(launcher), "search", "--refresh", "false", "--sort", sort,
            "--offset", str(offset), "--limit", str(limit)]
    env = dict(os.environ, ACADEMIC_SEARCH_ROOT=str(ROOT),
               ACADEMIC_SEARCH_PYTHON=sys.executable, PYTHONIOENCODING="utf-8",
               OPENALEX_API_KEY="offline-acceptance-key")
    started = perf_counter()
    result = subprocess.run(
        [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"), "/d", "/c", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    require(result.returncode == 0, f"{label}: launcher exit {result.returncode}")
    envelope = json.loads(result.stdout)
    require(envelope.get("ok") is True, f"{label}: unsuccessful envelope")
    data = envelope["data"]
    require(data["network"] is False, f"{label}: unexpected network flag")
    require(data["cost"]["requests"] == 0 and data["cost"]["usd"] == 0,
            f"{label}: unexpected remote request/cost")
    require(not data["counts"].get("failures"), f"{label}: source failures")
    require(data["counts"]["local"]["returned"] == len(data["items"]),
            f"{label}: returned count mismatch")
    (OUT / f"{label}.json").write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps({"probe": label, "seconds": round(perf_counter() - started, 3),
                      "local": data["counts"]["local"]}, ensure_ascii=False), flush=True)
    return data


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    metrics = read_metrics()
    summary = {"measured_at": datetime.now(timezone.utc).isoformat(),
               "database": str(ROOT / "data/sidecar.db"), "works_in_database": len(metrics),
               "launcher": "skills/academic-search/scripts/run.cmd",
               "refresh": "false", "results": []}
    for sort, field in (("date", "published_at"), ("citations", "cited_by_count")):
        baseline = invoke(sort, 0, len(metrics) + 1, sort + "-all")
        items = baseline["items"]
        keys = [item["work_key"] for item in items]
        require(len(keys) == len(set(keys)), f"{sort}: duplicate work")
        require(len(keys) == baseline["counts"]["local"]["matched"],
                f"{sort}: baseline limit did not cover evaluated matches")
        require(len(keys) >= 60, f"{sort}: insufficient records for three full pages")
        for item in items:
            for column in ("published_at", "cited_by_count"):
                require(item[column] == metrics[item["work_key"]][column],
                        f"{sort}: payload differs from SQL aggregate: {column}")

        # 独立 SQL 聚合 + 复合排序键；不导入服务或其排序辅助函数。
        def order_key(key: str) -> tuple:
            value = metrics[key][field]
            if value is None:
                return (True, 0, key)
            rank = date.fromisoformat(value[:10]).toordinal() if sort == "date" else value
            return (False, -rank, key)

        expected = sorted(keys, key=order_key)
        require(keys == expected, f"{sort}: incorrect primary/tie/null order")
        page_keys = []
        for offset, limit in ((0, 20), (20, 20), (40, 20), (len(keys), 20), (0, 0)):
            data = invoke(sort, offset, limit, f"{sort}-offset{offset}-limit{limit}")
            actual = [item["work_key"] for item in data["items"]]
            require(actual == expected[offset:offset + limit], f"{sort}: incorrect page slice")
            for count in ("considered", "matched", "truncated", "venue_filtered_out"):
                require(data["counts"]["local"][count] == baseline["counts"]["local"][count],
                        f"{sort}: pagination changed {count}")
            if limit and offset < 60:
                page_keys.extend(actual)
        require(page_keys == expected[:60] and len(set(page_keys)) == 60,
                f"{sort}: adjacent pages overlap or skip records")
        values = Counter(metrics[key][field] for key in keys)
        summary["results"].append({
            "sort": sort, "local": baseline["counts"]["local"],
            "non_null_tie_groups": sum(n > 1 for value, n in values.items() if value is not None),
            "null_items": values.get(None, 0),
            "first_value": metrics[keys[0]][field], "last_value": metrics[keys[-1]][field],
            "independent_sql_order": "passed", "adjacent_pages": "passed",
            "offset_at_end": "passed", "zero_limit": "passed",
        })
    summary.update(probes=12, requests=0, cost_usd=0, passed=True)
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
