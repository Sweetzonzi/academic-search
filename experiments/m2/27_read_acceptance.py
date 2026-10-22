"""read 的真实工具与可选网络验收；输出独立库与 JSON 证据，不触碰日常库。

示例：--out experiments/m2/out/read-local --sample experiments/m1.5/2610.18630v1
联网：--out experiments/m2/out/read-live --arxiv 2610.18630
目录必须是新目录；pandoc 使用生产查找顺序，必要时设置 ACADEMIC_SEARCH_PANDOC。
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from academic_search import config, identity, paths, service, store, textify  # noqa: E402


def prepare_snapshot(root, sample, route):
    source_id = sample.name
    version = re.search(r"v\d+$", source_id)
    uid = identity.record_uid("arxiv", source_id)
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        store.insert_record(conn, {"record_uid": uid, "source": "arxiv", "source_id": source_id,
                                   "title": "arXiv " + source_id, "type": "preprint",
                                   "source_version": version[0] if version else None})
        store.upsert_ident(conn, "arxiv", identity.normalize_source_id("arxiv", source_id), uid)
        store.create_work(conn, uid)
        store.add_member(conn, uid, uid, "preprint")
        if route == "html":
            blob, kind = (sample / "arxiv.html").read_bytes(), "source_html"
        else:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as archive:
                for file in sorted(sample.rglob("*")):
                    if file.is_file() and file.name not in ("arxiv.html", ".done"):
                        archive.add(file, arcname=file.relative_to(sample).as_posix())
            blob, kind = buf.getvalue(), "source_tar_gz"
        sha = paths.store_bytes(root, blob)
        store.add_artifact(conn, uid, kind, sha, None, None,
                           source_version=version[0] if version else None)
    conn.close()
    return uid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample", type=Path)
    parser.add_argument("--arxiv")
    parser.add_argument("--route", choices=("latex", "html", "both"), default="both")
    args = parser.parse_args()
    if bool(args.sample) == bool(args.arxiv):
        parser.error("--sample 与 --arxiv 恰好给一个")
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    pandoc = textify.find_pandoc(REPO)
    if not pandoc:
        parser.error("缺少 pandoc")
    results = []
    for route in (("latex", "html") if args.route == "both" else (args.route,)):
        root = output / route
        try:
            if args.sample:
                uid = prepare_snapshot(root, args.sample.resolve(), route)
            else:
                lookup = service.run_lookup(root, arxiv_id=args.arxiv, refresh="true")
                uid = lookup.items[0]["work_key"]
                if route == "html":
                    service.run_download(root, uid, source="html")
            first = service.run_read(root, uid, refresh="false" if args.sample else "auto", pandoc=pandoc)
            body = Path(first["doc"]["path"]).read_text(encoding="utf-8")
            second = service.run_read(root, uid, refresh="false", pandoc=pandoc)
            assert second["network"] is False
            assert second["doc"]["sha256"] == first["doc"]["sha256"]
            projection = paths.works_dir(root).resolve()
            assert projection.is_relative_to(root.resolve()) and projection.name == "works"
            paths.remove_tree(projection)
            rebuilt = service.run_read(root, uid, refresh="false", pandoc=pandoc)
            assert not rebuilt["network"] and Path(rebuilt["doc"]["work_path"]).is_file()
            for figure in rebuilt["figures"]:
                assert (Path(rebuilt["doc"]["work_path"]).parent / figure["ref"]).read_bytes() == Path(figure["path"]).read_bytes()
            anchors_ok = all(body.splitlines()[entry["line"] - 1].startswith("<!-- " + entry["kind"] + ":")
                             for entry in first["anchors"])
            assert anchors_ok and first["anchors"] and body.strip()
            assert paths.verify_bytes(root, sample=0) == []
            results.append({"route": route, "ok": True, "work_key": uid,
                            "first": first, "chars": len(body), "anchors": len(first["anchors"]),
                            "figures": len(first["figures"]), "cache_network": second["network"],
                            "rebuild_network": rebuilt["network"], "verify_mismatches": 0})
        except Exception as exc:
            results.append({"route": route, "ok": False, "error": str(exc)})
        (output / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in results[-1].items() if key != "first"}, ensure_ascii=False), flush=True)
    return 0 if all(entry["ok"] for entry in results) else 1


if __name__ == "__main__":
    sys.exit(main())
