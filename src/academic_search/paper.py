"""正文转换清单、内容寻址落盘与定位器（ADR-0074）。

清单绑定同一转换的正文、图件和输入；SQL 统一经过 store。
取件与转换的用例编排在 service，本文不解析命令、不调用转换器。
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from . import envelope, paths, store
from .sources import SourceError
from .sources.http import HttpBytesResponse

READER_VERSION = "read@3"
IMAGE = re.compile(r"!\[((?:\\.|[^\n])*?)\]\(([^)\n]+)\)")
ANCHOR = re.compile(r"^<!-- (sec|page):\s*(.*?)\s*-->$")


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def safe_ref(ref: str) -> bool:
    """包内引用不能逃出 figures/，也不能成为 Windows 绝对路径。"""
    parts = PurePosixPath(ref).parts
    return (len(parts) == 2 and parts[0] == "figures" and parts[1] not in (".", "..")
            and "\\" not in ref and ":" not in ref and "\x00" not in ref)


def bundles(conn, root, record_uid: str) -> list[dict]:
    """清单按登记顺序读取；坏清单不冒充可读缓存。"""
    result = []
    for row in store.artifacts_of(conn, record_uid, "paper_manifest"):
        try:
            bundle = json.loads(paths.byte_path(root, row["sha256"]).read_text(encoding="utf-8"))
            if bundle["schema"] != 1 or not paths.is_hex_sha256(bundle["doc"]["sha256"]):
                continue
            if not all(safe_ref(item["ref"]) and paths.is_hex_sha256(item["sha256"])
                       for item in bundle["figures"]):
                continue
            if (not paths.is_hex_sha256(bundle["source"]["sha256"])
                    or not paths.is_hex_sha256(bundle["captions_sha256"])
                    or bundle["source"]["kind"] not in ("source_tar_gz", "source_html")
                    or "version" not in bundle["source"]
                    or not isinstance(bundle["converter_version"], str)
                    or not isinstance(bundle["warnings"], list)
                    or not isinstance(bundle["front_matter"], dict)
                    or not all(isinstance(item["url"], str) and paths.is_hex_sha256(item["sha256"])
                               for item in bundle["media"])):
                continue
            bundle["_manifest_sha256"] = row["sha256"]
            result.append(bundle)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def complete(root, bundle: dict) -> bool:
    hashes = [bundle["doc"]["sha256"], bundle["captions_sha256"]]
    hashes += [item["sha256"] for item in bundle["figures"]]
    return all(paths.byte_path(root, sha).is_file() for sha in hashes)


class MediaClient:
    """HTML 图件复用同一原件的输入清单；离线绝不委托网络客户端。"""

    def __init__(self, conn, root, record_uid, version, client, *, offline, cached=()):
        self.conn, self.root, self.record_uid = conn, root, record_uid
        self.version, self.client, self.offline = version, client, offline
        self.cached = {item["url"]: item["sha256"] for item in cached}

    def get_bytes(self, url, **kwargs):
        sha = self.cached.get(url)
        if sha and paths.is_hex_sha256(sha) and paths.byte_path(self.root, sha).is_file():
            return HttpBytesResponse(url, 200, paths.byte_path(self.root, sha).read_bytes(), {})
        if self.offline:
            raise SourceError("arxiv", "离线缺少图件：" + url, False)
        response = self.client.get_bytes(url, **kwargs)
        if response.status == 200 and response.content:
            sha = paths.store_bytes(self.root, response.content)
            store.add_artifact(self.conn, self.record_uid, "source_media", sha, None, None,
                               source_version=self.version)
        return response


def save(conn, root, record_uid: str, *, markdown: str, stats: dict, base: Path,
         source: dict, converter_version: str, front_matter: dict, warnings: list[str]) -> dict:
    """成员先落盘，清单最后登记；失败的临时路线不能成为缓存。"""
    if not markdown.strip():
        raise envelope.EnvelopeError(envelope.SOURCE_ERROR, "转换失败：正文为空")
    warnings = list(warnings)
    warnings += ["pandoc 转换提示：" + message for message in stats.get("pandoc_warnings", [])]
    figures = {}

    def image(match):
        caption, ref = match.groups()
        ref = ref.strip().strip("<>")
        file = base / ref
        if not safe_ref(ref) or not file.is_file():
            warnings.append("图件缺失：" + caption)
            return "[图件缺失：" + caption + "]"
        figures[ref] = {"ref": ref, "sha256": paths.store_bytes(root, file.read_bytes()),
                        "caption": caption}
        return match.group(0)

    markdown = IMAGE.sub(image, markdown)
    for key, label in (("missing_src", "图片来源缺失"), ("render_failed", "PDF 图渲染失败"),
                       ("render_skipped", "PDF 图未渲染，保留原件")):
        count = stats.get("figure", {}).get(key, 0)
        if count:
            warnings.append(label + "：" + str(count))
    if stats.get("refs", {}).get("unresolved"):
        warnings.append("未解析交叉引用：" + str(stats["refs"]["unresolved"]))
    if stats.get("anchors", {}).get("unmatched_heads"):
        warnings.append("章节标题未与源码对齐，使用标题定位而非推定编号："
                        + str(stats["anchors"]["unmatched_heads"]))
    if source["kind"] == "source_html":
        warnings.append("HTML 公式的自定义宏与布局未全面验证；涉及公式的结论须核对原件")
        missing = stats.get("html", {}).get("media_missing", 0)
        if missing:
            warnings.append("HTML 媒体缺失：" + str(missing))
    header = "---\n" + "\n".join(key + ": " + json.dumps(value, ensure_ascii=False)
                                   for key, value in front_matter.items()) + "\n---\n\n"
    doc_sha = paths.store_bytes(root, (header + markdown).encode("utf-8"))
    figures = list(figures.values())
    captions_sha = paths.store_bytes(root, _json_bytes(figures))
    media = []
    for item in stats.get("_media", []):
        sha = paths.store_bytes(root, Path(item["file"]).read_bytes())
        media.append({"url": item["url"], "sha256": sha})
    bundle = {"schema": 1, "source": source, "converter_version": converter_version,
              "doc": {"sha256": doc_sha}, "figures": figures, "captions_sha256": captions_sha,
              "media": media, "front_matter": front_matter,
              "warnings": list(dict.fromkeys(warnings))}
    for kind, sha in [("paper_md", doc_sha), ("captions", captions_sha)] + [
            ("figure", item["sha256"]) for item in figures]:
        store.add_artifact(conn, record_uid, kind, sha, "pandoc", converter_version,
                           source_version=source["version"])
    for item in media:
        store.add_artifact(conn, record_uid, "source_media", item["sha256"], None, None,
                           source_version=source["version"])
    sha = paths.store_bytes(root, _json_bytes(bundle))
    store.add_artifact(conn, record_uid, "paper_manifest", sha, "pandoc", converter_version,
                       source_version=source["version"])
    bundle["_manifest_sha256"] = sha
    return bundle


def locator(root, work_key: str, bundle: dict, remote_version, target: Path) -> dict:
    """行号从最终字节计算，图件映射只读本清单。投影失败不影响权威路径。"""
    sha = bundle["doc"]["sha256"]
    doc_path = paths.byte_path(root, sha)
    text = doc_path.read_text(encoding="utf-8")
    local_version = bundle["source"]["version"]
    warnings = list(bundle["warnings"])
    stale = local_version is not None and remote_version is not None and local_version != remote_version
    if stale:
        warnings.append("正文版本 " + local_version + " 落后于元数据版本 " + remote_version
                        + "；read --refresh true 可更新")
    elif local_version is None or remote_version is None:
        warnings.append("来源版本未知，不能据 stale=false 判定正文为最新版")
    doc = {"path": str(doc_path), "sha256": sha, "size": doc_path.stat().st_size,
           "artifact_kind": "paper_md", "converter_version": bundle["converter_version"],
           "source_version": local_version, "remote_version": remote_version, "stale": stale}
    projected = [(sha, "paper.md"), (bundle["captions_sha256"], "figures/captions.json")]
    projected += [(item["sha256"], item["ref"]) for item in bundle["figures"]]
    try:
        copied = False
        for member_sha, ref in projected:
            if not paths.project_bytes(root, member_sha, target / ref):
                copied = True
        doc["work_path"] = str(target / "paper.md")
        if copied:
            warnings.append("文件系统未使用硬链接，投影已降级为副本")
    except OSError as exc:
        warnings.append("投影不可用，使用 bytes 路径读取：" + str(exc))
    anchors = []
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith(("```", "~~~")):
            fenced = not fenced
        match = ANCHOR.match(line) if not fenced else None
        if match:
            anchors.append({"kind": match[1], "label": match[2], "line": number})
    figures = [{**item, "path": str(paths.byte_path(root, item["sha256"]))}
               for item in bundle["figures"]]
    data = {"work_key": work_key, "doc": doc, "figures": figures, "anchors": anchors,
            "front_matter": bundle["front_matter"]}
    if warnings:
        data["warnings"] = list(dict.fromkeys(warnings))
    return data
