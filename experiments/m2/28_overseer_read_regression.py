"""OverSeer 阅读缺损的离线回归；源包只读，输出必须是新的独立目录。"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from academic_search import config, identity, paths, service, store, textify  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    blob = source.read_bytes()
    original_sha = hashlib.sha256(blob).hexdigest()
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    uid = identity.record_uid("arxiv", "2610.28554")
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        store.insert_record(conn, {"record_uid": uid, "source": "arxiv", "source_id": "2610.28554",
                                   "title": "OverSeer: Multi-Agent World Models Beyond What Agents See",
                                   "type": "preprint", "source_version": "v1"})
        store.upsert_ident(conn, "arxiv", "2610.28554", uid)
        store.create_work(conn, uid)
        store.add_member(conn, uid, uid, "preprint")
        sha = paths.store_bytes(root, blob)
        store.add_artifact(conn, uid, "source_tar_gz", sha, None, None, source_version="v1")
    conn.close()
    tool = textify.find_pandoc(REPO)
    assert tool, "缺 pandoc"
    data = service.run_read(root, uid, refresh="false", pandoc=tool)
    projected = Path(data["doc"]["work_path"])
    body = projected.read_text(encoding="utf-8")
    second = service.run_read(root, uid, refresh="false", pandoc=tool)
    abstract = re.search(r"(?im)^#+ Abstract\s*$", body)
    metrics = {
        "chars": len(body), "figures": len(data["figures"]),
        "html_table_lines": len(re.findall(r"(?m)^<(?:table|thead|tbody|tr|td|th)", body)),
        "cmidrule_residue": body.count("(lr)"), "nocase_spans": body.count("{.nocase}"),
        "unresolved_cites": re.findall(r"\*\*[^*\n]{1,60}\?\*\*", body),
        "raw_xref_links": body.count(r"[\["), "has_abstract": abstract is not None,
        "appendix_b": "[Appendix B](#app:pose_sampling)" in body,
        "appendix_g": "[Appendix G](#appendix:more_vis)" in body,
        "table_one": "[Table 1](#tab:minecraft_main)" in body,
        "appendix_data_anchor": any(item["label"] == "A · Data. id:data." for item in data["anchors"]),
        "appendix_judge_paragraph": any(item["label"] == "F.1 · Revisiting benchmark. id:revisiting-benchmark."
                                        for item in data["anchors"]),
        "appendix_e_anchor": "<!-- sec: E -->\n# Limitations and Future Work" in body,
        "numeric_appendix_anchors": [item["label"] for item in data["anchors"]
                                    if item["line"] > next(entry["line"] for entry in data["anchors"]
                                                            if entry["label"] == "A id:app:data")
                                    and re.match(r"\d", item["label"])],
        "source_unchanged": original_sha == hashlib.sha256(source.read_bytes()).hexdigest(),
        "cache_same": second["doc"]["sha256"] == data["doc"]["sha256"],
        "network": data["network"] or second["network"],
        "projection_equal": projected.read_bytes() == Path(data["doc"]["path"]).read_bytes(),
        "figure_paths_ok": all((projected.parent / item["ref"]).read_bytes()
                               == Path(item["path"]).read_bytes() for item in data["figures"]),
        "verify_mismatches": len(paths.verify_bytes(root, sample=0)),
    }
    result = {"metrics": metrics, "locator": data, "source_sha256": original_sha}
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    assert metrics["has_abstract"] and "Despite the success of video world models" in body[abstract.end():abstract.end() + 300]
    assert metrics["cmidrule_residue"] == metrics["nocase_spans"] == metrics["raw_xref_links"] == 0
    assert metrics["unresolved_cites"] == [] and metrics["figures"] == 11
    assert metrics["appendix_b"] and metrics["appendix_g"] and metrics["table_one"]
    assert metrics["appendix_data_anchor"] and metrics["appendix_judge_paragraph"] and metrics["appendix_e_anchor"]
    assert metrics["numeric_appendix_anchors"] == []
    assert "sec. 8" not in body and "sec. 13" not in body
    assert metrics["source_unchanged"] and metrics["cache_same"] and not metrics["network"]
    assert metrics["projection_equal"] and metrics["figure_paths_ok"] and metrics["verify_mismatches"] == 0


if __name__ == "__main__":
    main()
