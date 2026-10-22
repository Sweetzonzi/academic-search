"""文本化流水线：LaTeX 源码 → 可读 Markdown（含图件与锚点）。

契约：docs/02-设计说明书.md §4.10、[ADR-0056](../决策/ADR-0056.md) 决策 3、
经验数字见 docs/实验/EX-2026-10-14-文本化.md。spike 在 experiments/m1.5/05_textify.py。

本模块只用标准库（pandoc / pdftocairo 是外部可执行文件，PyMuPDF 可选，缺失即降级）；
不改库、不拼 bytes/ 路径（那是 paths.py 与 service 的事）。
"""

from __future__ import annotations

import glob as _glob
import gzip
import io
import os
import re
import shutil
import subprocess
import tarfile
from html.parser import HTMLParser

from . import latex

#: pandoc 输出方言：固定值；gfm 会把行间公式变成代码块，markdown 会丢表格（EX §4.2）。
WRITER = "commonmark_x+tex_math_dollars+pipe_tables+footnotes+implicit_figures"

PDF_RENDER_DPI = 200

#: 根 .tex 的候选名（ADR-0056 决策 3）。
ROOT_TEX_CANDIDATES = ("main.tex", "ms.tex", "paper.tex", "Manuscript_arxiv.tex")
TEX_SUFFIXES = (".tex", ".ltx")


class TextifyError(Exception):
    """文本化失败；reason 进 counts.failures[]。"""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason


# --------------------------------------------------------------------------
# 外部工具定位
# --------------------------------------------------------------------------

def _first_existing(paths):
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


def find_pandoc(root=None, env=None) -> str | None:
    """ACADEMIC_SEARCH_PANDOC → vendor/pandoc/ → PATH。"""
    environ = os.environ if env is None else env
    candidates = [environ.get("ACADEMIC_SEARCH_PANDOC")]
    if root:
        names = ("pandoc.exe", "pandoc") if os.name == "nt" else ("pandoc", "pandoc.exe")
        candidates += [os.path.join(root, "vendor", "pandoc", n) for n in names]
    found = _first_existing(candidates)
    if found:
        return found
    return shutil.which("pandoc")


def find_pdftocairo(root=None, env=None) -> str | None:
    """PATH → ACADEMIC_SEARCH_PDFTOCAIRO → D:\\texlive\\*\\bin\\windows（ADR-0056）。"""
    environ = os.environ if env is None else env
    found = shutil.which("pdftocairo")
    if found:
        return found
    found = _first_existing([environ.get("ACADEMIC_SEARCH_PDFTOCAIRO")])
    if found:
        return found
    pattern = os.path.join("D:" + os.sep, "texlive", "*", "bin", "windows", "pdftocairo.exe")
    matches = sorted(_glob.glob(pattern))
    return matches[-1] if matches else None


def pandoc_version(pandoc: str) -> str:
    """取 pandoc 版本号；进 converter_version（换版本即触发重转）。"""
    try:
        proc = subprocess.run([pandoc, "--version"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    first = (proc.stdout or b"").decode("utf-8", "replace").splitlines()
    if first:
        return first[0].strip()
    return "unknown"


def _pdftocairo_version(tool: str) -> str | None:
    """取 poppler 版本（pdftocairo -v）；拿不到就用路径里的版本段兜底（ADR-0056 决策 3）。"""
    try:
        proc = subprocess.run([tool, "-v"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        proc = None
    if proc is not None:
        text = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace")
        match = re.search(r"[Vv]ersion\s+([0-9][0-9A-Za-z._-]*)", text)
        if match:
            return match.group(1)
    for part in reversed(os.path.normpath(tool).split(os.sep)):
        if re.fullmatch(r"\d{4}|\d+\.\d+(?:\.\d+)*", part):
            return part
    return None


def pymupdf_version() -> str | None:
    """PyMuPDF 版本；没装返回 None（ADR-0056 决策 3 的第二档渲染器）。"""
    try:
        import fitz  # noqa: PLC0415 - 只有 pdftocairo 缺席时才值得导入
    except Exception:
        return None
    direct = getattr(fitz, "__version__", None) or getattr(fitz, "VersionBind", None)
    if isinstance(direct, str) and direct:
        return direct
    version = getattr(fitz, "version", None)
    if isinstance(version, (list, tuple)) and version and isinstance(version[0], (list, tuple)):
        return str(version[0][0])
    return "unknown"


def renderer_id(tool: str | None) -> str:
    """图渲染器标识，进 converter_version；200 dpi 不是独立参数（ADR-0056 决策 3）。"""
    if tool:
        return "pdftocairo@" + (_pdftocairo_version(tool) or "unknown")
    version = pymupdf_version()
    return ("pymupdf@" + version) if version else "none"


def converter_version(pandoc: str, renderer: str | None = None) -> str:
    """pandoc 版本 + 输出方言 + 图渲染器标识；任一变化即触发重转（ADR-0016、ADR-0056）。"""
    if renderer is None:
        renderer = renderer_id(find_pdftocairo())
    return "|".join([pandoc_version(pandoc), WRITER, renderer])


# --------------------------------------------------------------------------
# 根 .tex 判定
# --------------------------------------------------------------------------

def _contains_documentclass(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            head = handle.read(200000)
    except OSError:
        return False
    return "\\documentclass" in head


def find_root_tex(directory: str, arxiv_id: str | None = None) -> str:
    """按 ADR-0056 决策 3 判根：候选名 → 含 \\documentclass 的文件 → 唯一 .tex。

    含 \\documentclass 的文件多于一个即报错（不猜）。
    """
    names = list(ROOT_TEX_CANDIDATES)
    for suffix in TEX_SUFFIXES:
        if arxiv_id:
            names.append(arxiv_id + suffix)
    for name in names:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    texs = []
    for suffix in TEX_SUFFIXES:
        texs.extend(_glob.glob(os.path.join(directory, "**", "*" + suffix), recursive=True))
    texs = sorted(texs)
    roots = [path for path in texs if _contains_documentclass(path)]
    if len(roots) == 1:
        return roots[0]
    if len(roots) > 1:
        raise TextifyError(
            "ambiguous_root_tex",
            "有多个含 documentclass 的 .tex：" + ", ".join(os.path.basename(p) for p in roots),
        )
    if len(texs) == 1:
        return texs[0]
    raise TextifyError("no_root_tex", "在 " + directory + " 找不到根 .tex")


# --------------------------------------------------------------------------
# 取件：e-print 解包
# --------------------------------------------------------------------------

#: 解包防呆上限（EX §6.2：单篇源码 158–693 KB，留两个数量级余量）。
EPRINT_MAX_UNPACKED = 256 * 1024 * 1024
EPRINT_MAX_MEMBERS = 5000


def _looks_like_tar(raw: bytes) -> bool:
    # tar 的 magic 在偏移 257，不看开头
    return len(raw) > 512 and raw[257:262] == b"ustar"


def _extract_tar(raw: bytes, dest_dir: str) -> None:
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            members = archive.getmembers()
            if len(members) > EPRINT_MAX_MEMBERS:
                raise TextifyError("unpack_failed", "e-print 成员数超过上限：" + str(len(members)))
            total = sum(m.size for m in members if m.isfile())
            if total > EPRINT_MAX_UNPACKED:
                raise TextifyError("unpack_failed", "e-print 解压体积超过上限")
            # data filter 挡绝对路径、.. 与链接（Python 3.12 标准库自带）
            archive.extractall(dest_dir, filter="data")
    except tarfile.TarError as exc:
        raise TextifyError("unpack_failed", "tar 解包失败：" + str(exc)) from exc


def list_files(directory: str) -> list[str]:
    """目录下所有文件的相对路径（POSIX 分隔符），按名排序。"""
    found: list[str] = []
    for base, _dirs, names in os.walk(directory):
        for name in names:
            path = os.path.join(base, name)
            found.append(os.path.relpath(path, directory).replace("\\", "/"))
    return sorted(found)


def _hinted(name_hint: str | None, suffix: str) -> str:
    stem = (name_hint or "main").strip() or "main"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    return stem if stem.lower().endswith(suffix) else stem + suffix


def _write(name: str, dest_dir: str, data: bytes) -> str:
    with open(os.path.join(dest_dir, name), "wb") as handle:
        handle.write(data)
    return name


def unpack_eprint(data: bytes, dest_dir: str, *, name_hint: str | None = None) -> dict:
    """e-print 原始字节 → 解包目录；返回 {kind, files}。

    kind 取 tarball / gzip / tex / pdf / unknown；pdf 表示这条路没有 LaTeX 源码。
    """
    os.makedirs(dest_dir, exist_ok=True)
    if data[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(data)
        except (OSError, EOFError) as exc:
            raise TextifyError("unpack_failed", "gzip 解压失败：" + str(exc)) from exc
        if _looks_like_tar(raw):
            _extract_tar(raw, dest_dir)
            return {"kind": "tarball", "files": list_files(dest_dir)}
        suffix = ".tex" if b"\\documentclass" in raw[:200000] else ".txt"
        return {"kind": "gzip", "files": [_write(_hinted(name_hint, suffix), dest_dir, raw)]}
    if data[:5] == b"%PDF-":
        return {"kind": "pdf", "files": [_write(_hinted(name_hint, ".pdf"), dest_dir, data)]}
    if _looks_like_tar(data):
        _extract_tar(data, dest_dir)
        return {"kind": "tarball", "files": list_files(dest_dir)}
    if data.lstrip()[:1] == b"%" or b"\\documentclass" in data[:200000]:
        return {"kind": "tex", "files": [_write(_hinted(name_hint, ".tex"), dest_dir, data)]}
    return {"kind": "unknown", "files": [_write(_hinted(name_hint, ".bin"), dest_dir, data)]}


# --------------------------------------------------------------------------
# 公式归一化
# --------------------------------------------------------------------------

#: LaTeX 顶层公式环境 → 数学模式内合法环境；None = 拆壳。
MATH_ENV = {
    "equation": None, "equation*": None, "displaymath": None,
    "multline": None, "multline*": None,
    "align": "aligned", "align*": "aligned", "flalign": "aligned",
    "flalign*": "aligned", "alignat": "aligned", "alignat*": "aligned",
    "eqnarray": "aligned", "eqnarray*": "aligned", "split": "aligned",
    "gather": "gathered", "gather*": "gathered",
}
_DISP_ENV = re.compile(
    r"\$\$[ \t]*\r?\n?[ \t]*\\begin\{(?P<env>[A-Za-z]+\*?)\}(?P<body>.*?)"
    r"\\end\{(?P=env)\}[ \t]*\r?\n?[ \t]*\$\$",
    re.S,
)
_DISP_PLAIN = re.compile(r"\$\$(?P<body>.+?)\$\$", re.S)
_LABEL = re.compile(r"\\label\{([^}]*)\}")


def _emit_math(body: str, label: str | None, wrap: str | None) -> str:
    body = body.strip()
    if wrap:
        body = "\\begin{" + wrap + "}\n" + body + "\n\\end{" + wrap + "}"
    head = ("<!-- id:" + label + " -->\n") if label else ""
    return "\n\n" + head + "$$\n" + body + "\n$$\n\n"


def normalise_math(text: str) -> tuple[str, dict]:
    """拆掉 \\begin{equation} 外壳、把 \\label 提成锚点注释。"""
    stats = {"env_unwrapped": 0, "env_wrapped": 0, "labels_lifted": 0, "kept_raw_env": 0}

    def repl_env(match):
        env, body = match.group("env"), match.group("body")
        labels = _LABEL.findall(body)
        body = _LABEL.sub("", body)
        body = re.sub(r"\\(?:nonumber|notag)\b", "", body)
        wrap = MATH_ENV.get(env)
        if env not in MATH_ENV:
            stats["kept_raw_env"] += 1
            wrap = "aligned"
        elif wrap:
            stats["env_wrapped"] += 1
        else:
            stats["env_unwrapped"] += 1
        stats["labels_lifted"] += len(labels)
        return _emit_math(body, labels[0] if labels else None, wrap)

    out = _DISP_ENV.sub(repl_env, text)

    def repl_plain(match):
        body = match.group("body")
        labels = _LABEL.findall(body)
        stats["labels_lifted"] += len(labels)
        return _emit_math(_LABEL.sub("", body), labels[0] if labels else None, None)

    out = _DISP_PLAIN.sub(repl_plain, out)
    return out, stats


# --------------------------------------------------------------------------
# 图件归一化
# --------------------------------------------------------------------------

_FIG_OPEN = re.compile(r"<figure\b", re.I)
_FIG_CLOSE = re.compile(r"</figure>", re.I)


class _FigureHTML(HTMLParser):
    """从 pandoc 吐出的 <figure> 块里按文档序取图与图注。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images: list[dict] = []
        self.captions: list[str] = []
        self.ids: list[str] = []
        self._in_caption = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "figure":
            if values.get("id"):
                self.ids.append(values["id"])
        elif tag in ("img", "embed"):
            self.images.append({"src": (values.get("src") or "").strip(),
                                "alt": (values.get("alt") or "").strip()})
        elif tag == "object":
            self.images.append({"src": (values.get("data") or "").strip(),
                                "alt": (values.get("alt") or "").strip()})
        elif tag == "figcaption":
            self._in_caption = True
            self._buffer = []

    def handle_endtag(self, tag):
        if tag == "figcaption" and self._in_caption:
            self._in_caption = False
            self.captions.append(" ".join("".join(self._buffer).split()))

    def handle_data(self, data):
        if self._in_caption:
            self._buffer.append(data)


def _figure_spans(text: str):
    position = 0
    while True:
        match = _FIG_OPEN.search(text, position)
        if not match:
            return
        depth, cursor = 0, match.start()
        while True:
            open_match = _FIG_OPEN.search(text, cursor)
            close_match = _FIG_CLOSE.search(text, cursor)
            if not close_match:
                return
            if open_match and open_match.start() < close_match.start():
                depth, cursor = depth + 1, open_match.end()
            else:
                depth, cursor = depth - 1, close_match.end()
                if depth == 0:
                    yield match.start(), close_match.end()
                    break
        position = cursor


def _safe_name(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(path))


def pdf_to_png(pdf: str, stem: str, pdftocairo: str | None, dpi: int = PDF_RENDER_DPI) -> str | None:
    """PDF 图 → PNG；失败返回 None（调用方降级为保留 PDF 链接）。"""
    if not pdftocairo:
        return None
    try:
        proc = subprocess.run(
            [pdftocairo, "-png", "-r", str(dpi), "-singlefile", pdf, stem],
            capture_output=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    candidate = stem + ".png"
    if proc.returncode == 0 and os.path.exists(candidate):
        return candidate
    return None


def pdf_to_png_mupdf(pdf: str, stem: str, dpi: int = PDF_RENDER_DPI) -> str | None:
    """第二档渲染器：PyMuPDF（ADR-0056 决策 3）；没装或渲染失败返回 None。"""
    try:
        import fitz  # noqa: PLC0415
    except Exception:
        return None
    try:
        with fitz.open(pdf) as document:
            document.load_page(0).get_pixmap(dpi=dpi).save(stem + ".png")
    except Exception:
        return None
    candidate = stem + ".png"
    return candidate if os.path.exists(candidate) else None


def normalise_figures(
    text: str, media_dir: str, fig_dir: str, pdftocairo: str | None = None
) -> tuple[str, dict, dict]:
    """<figure>/<embed> 原始 HTML → Markdown 图片；PDF 图渲染为 PNG。"""
    stats = {
        "figures": 0, "images": 0, "rendered_png": 0, "render_failed": 0,
        "render_skipped": 0, "missing_src": 0,
    }
    os.makedirs(fig_dir, exist_ok=True)
    figmap: dict[str, str] = {}

    def localise(src: str) -> str:
        raw = (src or "").strip()
        if raw.startswith("file:///"):
            raw = raw[8:]
        if raw and not os.path.isabs(raw):
            candidate = os.path.normpath(os.path.join(media_dir, raw))
            raw = candidate if os.path.exists(candidate) else raw
        if not raw or not os.path.exists(raw):
            stats["missing_src"] += 1
            return raw.replace("\\", "/")
        base = _safe_name(raw)
        figmap.setdefault(os.path.basename(raw), "figures/" + base)
        if raw.lower().endswith(".pdf"):
            stem = os.path.join(fig_dir, os.path.splitext(base)[0])
            rendered = pdf_to_png(raw, stem, pdftocairo)
            if not rendered:
                rendered = pdf_to_png_mupdf(raw, stem)
            if rendered:
                stats["rendered_png"] += 1
                rel = "figures/" + os.path.basename(rendered)
                figmap[os.path.basename(raw)] = rel
                return rel
            if pdftocairo or pymupdf_version():
                stats["render_failed"] += 1
            else:
                stats["render_skipped"] += 1
        destination = os.path.join(fig_dir, base)
        if not os.path.exists(destination):
            shutil.copy2(raw, destination)
        return "figures/" + base

    replacements = []
    for start, end in _figure_spans(text):
        block = text[start:end]
        parser = _FigureHTML()
        try:
            parser.feed(block)
            parser.close()
        except Exception:
            continue
        if not parser.images:
            continue
        stats["figures"] += 1
        lines = []
        if parser.ids:
            lines.append("<!-- id:" + parser.ids[0] + " -->")
        for index, image in enumerate(parser.images):
            alt = image["alt"] or os.path.splitext(os.path.basename(image["src"]))[0]
            if parser.captions and (not image["alt"] or image["alt"].lower() == "refer to caption"):
                alt = parser.captions[min(index, len(parser.captions) - 1)]
            lines.append("![" + alt + "](" + localise(image["src"]) + ")")
            stats["images"] += 1
        if parser.captions and len(parser.captions) > 1:
            lines += ["", "*" + parser.captions[-1] + "*"]
        replacements.append((start, end, "\n".join(lines)))
    for start, end, new in reversed(replacements):
        text = text[:start] + new + text[end:]
    return text, stats, figmap


# --------------------------------------------------------------------------
# 锚点注入
# --------------------------------------------------------------------------

_HEAD_ATTR = re.compile(r"^(#{1,6}) +(.+?)(?: +\{([^}]+)\})? *$")
_ANCHOR_ID = re.compile(r"^<!-- id:(\S+) -->$")
_FENCE_DIV = re.compile(r"^:::\s*(?:\{([^}]*)\})?\s*$")
_DIV_ID = re.compile(r"#(\S+)")
_DIV_CLASS = re.compile(r"\.([A-Za-z0-9_-]+)")
_THM_BOLD = re.compile(
    r"^\*\*(Theorem|Lemma|Proposition|Corollary|Definition|Assumption|Remark"
    r"|Example|Claim)\s+([0-9]+(?:\.[0-9]+)?)\*\*"
)
_TBL_ROW = re.compile(r"^\|.*\|\s*$")

#: 定理类环境：pandoc 会把编号写进粗体，类别进 env 锚点。
THEOREM_CLASSES = {
    "theorem", "lemma", "proposition", "corollary", "definition",
    "assumption", "remark", "example", "claim",
}
#: 伪代码块（EX §7 第 1 条）。
ALGORITHM_CLASSES = {"algorithm", "algorithm2e", "pseudocode"}


def inject_anchors(text: str, source_map: dict | None = None,
                   source_sections: list[dict] | None = None) -> tuple[str, dict, dict]:
    """给节/公式/图/表/伪代码编号，并建立 label → (kind, number)。"""
    counters = {"sec": [0] * 6, "eq": 0, "fig": 0, "tbl": 0, "alg": 0, "env": 0}
    heads = [0] * 6
    refmap: dict[str, tuple[str, str]] = {}
    out: list[str] = []
    in_math = False
    pending_id = None
    prev_was_row = False
    div_stack: list[dict] = []
    lines = text.split("\n")
    source_position = 0
    numbered_ancestors: dict[int, tuple[str, str]] = {}
    unmatched_heads = 0

    def title_matches(source_title, rendered_title, allow_macro):
        words = lambda value: re.findall(r"\w+", value.casefold())
        source_words = words(re.sub(r"\\[A-Za-z@]+\*?", "", source_title))
        rendered_words = words(rendered_title)
        if source_words == rendered_words:
            return True
        if allow_macro and "\\" in source_title:
            # 自定义宏展开后名称可不同；保留源码里的文字必须按原序出现。
            remaining = iter(rendered_words)
            return all(any(word == other for other in remaining) for word in source_words)
        return False

    def source_heading(label, title, level, unnumbered):
        nonlocal source_position
        sections = source_sections or []
        for position in range(source_position, len(sections)):
            entry = sections[position]
            explicit = label is not None and label in entry["labels"]
            allow_macro = position == source_position and not (unnumbered and entry["number"] is not None)
            if explicit or (entry["level"] == level
                            and title_matches(entry["title"], title, allow_macro)):
                source_position = position + 1
                return entry
        return None

    def take_id():
        nonlocal pending_id
        if pending_id:
            got, pending_id = pending_id, None
            return got
        for frame in reversed(div_stack):
            if frame["id"] and not frame["bound"]:
                frame["bound"] = True
                return frame["id"]
        return None

    for index, line in enumerate(lines):
        stripped = line.strip()

        match = _ANCHOR_ID.match(stripped)
        if match:
            pending_id = match.group(1)
            out.append(line)
            continue

        if stripped == "$$":
            if not in_math:
                counters["eq"] += 1
                got = take_id()
                if got:
                    refmap[got] = ("eq", str(counters["eq"]))
                out.append("<!-- eq: " + str(counters["eq"]) + " -->")
            in_math = not in_math
            out.append(line)
            prev_was_row = False
            continue

        if in_math:
            out.append(line)
            continue

        head = _HEAD_ATTR.match(line)
        if head:
            level = len(head.group(1))
            attrs = head.group(3) or ""
            unnumbered = ".unnumbered" in attrs
            if not unnumbered:
                counters["sec"][level - 1] += 1
                for k in range(level, 6):
                    counters["sec"][k] = 0
            heads[level - 1] += 1
            parts = counters["sec"][:level]
            start = next((i for i, value in enumerate(parts) if value), 0)
            number = ".".join(str(c) for c in parts[start:])
            id_match = _DIV_ID.search(attrs)
            label = id_match[1] if id_match else take_id()
            source_entry = (source_map or {}).get(label)
            kind = "sec"
            outline_entry = source_heading(label, head.group(2), level, unnumbered)
            if outline_entry is not None:
                kind = outline_entry["kind"]
                if outline_entry["number"] is not None:
                    number = outline_entry["number"]
                else:
                    parent = outline_entry["parent"]
                    number = (parent + " · " if parent else "") + head.group(2)
                    kind = "env"  # 无编号标题不冒充新的节号。
            elif source_entry and source_entry[0] in ("sec", "app"):
                kind, number = source_entry
            elif unnumbered:
                number = head.group(2)
                kind = "env"
            elif source_sections is not None:
                # 未对齐标题保留可读位置，不能制造与原文不同的编号。
                parent = next((numbered_ancestors[k][1] for k in sorted(numbered_ancestors, reverse=True)
                               if k < level), "")
                number = (parent + " · " if parent else "") + head.group(2)
                kind = "env"
                unmatched_heads += 1
            for ancestor_level in list(numbered_ancestors):
                if ancestor_level >= level:
                    del numbered_ancestors[ancestor_level]
            if kind in ("sec", "app"):
                numbered_ancestors[level] = (kind, number)
            if label:
                refmap[label] = (kind, number)
            tag = "<!-- sec: " + number + (" id:" + label if label else "") + " -->"
            out.append(tag)
            out.append(head.group(1) + " " + head.group(2))
            prev_was_row = False
            continue

        fence = _FENCE_DIV.match(stripped)
        if fence:
            attrs = fence.group(1)
            if attrs is None:
                if div_stack:
                    div_stack.pop()
                else:
                    out.append(line)
                prev_was_row = False
                continue
            div_id = _DIV_ID.search(attrs)
            div_class = _DIV_CLASS.search(attrs)
            classes = set(re.findall(r"\.([A-Za-z0-9_-]+)", attrs))
            theorem = classes & THEOREM_CLASSES
            algorithm = classes & ALGORITHM_CLASSES
            kind = None
            number = None
            if algorithm:
                counters["alg"] += 1
                number = str(counters["alg"])
                kind = "alg"
                out.append("<!-- alg: " + number + " -->")
            elif theorem:
                name = sorted(theorem)[0]
                for ahead in lines[index + 1:index + 8]:
                    bold = _THM_BOLD.match(ahead.strip())
                    if bold:
                        number = bold.group(1) + " " + bold.group(2)
                        break
                if number is None:
                    counters["env"] += 1
                    number = name.capitalize() + " " + str(counters["env"])
                kind = "env"
                out.append("<!-- env: " + ",".join(sorted(theorem)) + " -->")
            elif div_class:
                out.append("<!-- env: " + div_class.group(1) + " -->")
            else:
                out.append(line)
            if div_id and kind is not None:
                refmap[div_id.group(1)] = (kind, number)
            div_stack.append(
                {
                    "id": div_id.group(1) if div_id else None,
                    "bound": kind is not None,
                    "env": bool(div_class),
                }
            )
            if div_id and kind is None:
                for ahead in lines[index + 1:index + 8]:
                    bold = _THM_BOLD.match(ahead.strip())
                    if bold:
                        refmap[div_id.group(1)] = ("env", bold.group(1) + " " + bold.group(2))
                        div_stack[-1]["bound"] = True
                        break
            prev_was_row = False
            continue

        if stripped.startswith("!["):
            counters["fig"] += 1
            got = take_id()
            if got:
                refmap[got] = ("fig", str(counters["fig"]))
            out.append("<!-- fig: " + str(counters["fig"]) + " -->")
            out.append(line)
            prev_was_row = False
            continue

        is_row = bool(_TBL_ROW.match(stripped))
        if is_row and not prev_was_row:
            counters["tbl"] += 1
            got = take_id()
            if got:
                refmap[got] = ("tbl", str(counters["tbl"]))
            out.append("<!-- tbl: " + str(counters["tbl"]) + " -->")
        out.append(line)
        prev_was_row = is_row

    stats = {
        "heads": heads,
        "eq": counters["eq"],
        "fig": counters["fig"],
        "tbl": counters["tbl"],
        "alg": counters["alg"],
        "env": counters["env"],
        "labels": len(refmap),
        "unmatched_heads": unmatched_heads,
    }
    return "\n".join(out), stats, refmap


# --------------------------------------------------------------------------
# 交叉引用解析
# --------------------------------------------------------------------------

KIND_PREFIX = {
    "eq": "eq. ", "fig": "Fig. ", "tbl": "Table ", "sec": "sec. ",
    "app": "Appendix ", "env": "", "alg": "Algorithm ",
}

_REF_ANY = re.compile(r"\[(?:\\\[([^\]]+)\\\]|\[?([^\]]*?)\]?)\]\(#([^)]+)\)")

#: 参考文献条目链接（citeproc 产生）：本来就有编号，不改写、不计未解析。
_BIB_TARGET = re.compile(r"^(?:bib|cite|ref)[.:]", re.I)


def resolve_refs(text: str, refmap: dict, source_refmap: dict | None = None) -> tuple[str, dict]:
    """把 [\\[label\\]](#label) / [[1]](#bib.x) 之类改写成带编号的链接。

    源码提供章节、附录和表格编号；其它类型优先保留 pandoc 输出的编号。
    """
    stats = {"resolved": 0, "unresolved": 0, "bibliography": 0}
    unresolved: list[str] = []
    table = dict(refmap)
    if source_refmap:
        for key, value in source_refmap.items():
            if value[0] in ("sec", "app", "tbl"):
                table[key] = value
            else:
                table.setdefault(key, value)

    def repl(match):
        target = match.group(3)
        if _BIB_TARGET.match(target):
            stats["bibliography"] += 1
            return match.group(0)
        keys = [key.strip() for key in target.split(",")]
        missing = [key for key in keys if key not in table]
        if missing:
            stats["unresolved"] += 1
            unresolved.extend(missing)
        else:
            stats["resolved"] += 1

        def display(key):
            if key in table:
                kind, number = table[key]
                return KIND_PREFIX.get(kind, "") + number
            kind = key.split(":", 1)[0]
            return {"tab": "Table ?", "tbl": "Table ?", "fig": "Fig. ?",
                    "sec": "section ?", "app": "Appendix ?", "eq": "eq. ?"}.get(kind, "reference ?")
        label = ", ".join(display(key) for key in keys)
        return "[" + label + "](#" + target + ")"

    result = _REF_ANY.sub(repl, text)
    stats["unresolved_targets"] = unresolved
    return result, stats


_SPAN_ATTR = re.compile(
    r"\{(?=[^{}]*(?:reference-type|reference|acronym-label|acronym-form|"
    r"\.unnumbered|\.definition|\.example|\.theorem|\.lemma|\.proposition|"
    r"\.corollary|\.remark|\.assumption|\.claim|\.proof|\.nocase|\.uri)\b)[^{}]*\}"
)
_RAW_ATTR = re.compile(r"\{=[A-Za-z]+\}")


def polish(text: str) -> tuple[str, dict]:
    """丢掉 commonmark 表达不了的 pandoc span 属性并折叠多余空行。"""
    text, first = _SPAN_ATTR.subn("", text)
    text, second = _RAW_ATTR.subn("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text, {"attrs_dropped": first + second}


# --------------------------------------------------------------------------
# LaTeX 源码兜底表
# --------------------------------------------------------------------------

_ENV_KIND = {
    "equation": "eq", "equation*": "eq", "align": "eq", "align*": "eq",
    "gather": "eq", "gather*": "eq", "multline": "eq", "multline*": "eq",
    "figure": "fig", "figure*": "fig", "table": "tbl", "table*": "tbl",
    "algorithm": "alg", "algorithm*": "alg",
}
_THM_ENV = {
    "theorem", "lemma", "proposition", "corollary", "definition",
    "assumption", "remark", "example", "claim",
}


def _source_structure(tex_text: str) -> tuple[dict, list[dict]]:
    """从展开的 LaTeX 源码建 label → (kind, number)。

    章节跳过星号形式，appendix 后按字母编号；表格从源码环境取号，
    不依赖 pandoc 是否保留了 table div id。其它类型用于输出缺号时兜底。
    """
    refmap: dict[str, tuple[str, str]] = {}
    counters: dict[str, int] = {}
    section = [0] * 5
    sections: list[dict] = []
    pending_section = None
    ancestors: dict[int, str] = {}
    # article 默认编号到 subsubsection；book/report 默认到 subsection。
    section_depth = 3
    stack: list[tuple[str, str] | None] = []
    appendix = False

    for _, end_position, command in latex.commands(tex_text):
        if command == "appendix":
            appendix = True
            section = [0] * 5
            ancestors.clear()
            pending_section = None
            continue
        section_kind = command.rstrip("*")
        if section_kind not in {"part", "chapter", "section", "subsection", "subsubsection",
                                "paragraph", "subparagraph"}:
            section_kind = None
        option = latex.argument(tex_text, end_position, "[", "]") if section_kind or command == "documentclass" else None
        arg = latex.argument(tex_text, option[1] if option else end_position)
        if arg is None:
            continue
        if command == "documentclass" and arg[0] in ("book", "report"):
            section_depth = 2
        if command == "setcounter" and arg[0] == "secnumdepth":
            value = latex.argument(tex_text, arg[1])
            if value and re.fullmatch(r"-?\d+", value[0].strip()):
                section_depth = int(value[0])
            continue
        label = arg[0] if command == "label" else None
        begin = arg[0] if command == "begin" else None
        end = arg[0] if command == "end" else None
        if begin:
            kind = _ENV_KIND.get(begin)
            if kind is None and begin in _THM_ENV:
                kind = begin
            if kind is None:
                stack.append(None)
                continue
            counters[kind] = counters.get(kind, 0) + 1
            if kind in _THM_ENV:
                stack.append(("env", kind.capitalize() + " " + str(counters[kind])))
            else:
                stack.append((kind, str(counters[kind])))
            continue
        if end:
            if stack:
                stack.pop()
            continue
        if section_kind:
            level = {"part": 0, "chapter": 0, "section": 0,
                     "subsection": 1, "subsubsection": 2, "paragraph": 3,
                     "subparagraph": 4}[section_kind]
            numbered = not command.endswith("*") and level < section_depth
            number = None
            if numbered:
                section[level] += 1
                for k in range(level + 1, len(section)):
                    section[k] = 0
                parts = [str(c) for c in section[: level + 1]]
                if appendix:
                    count, letters = section[0], ""
                    while count:
                        count, digit = divmod(count - 1, 26)
                        letters = chr(65 + digit) + letters
                    parts[0] = letters or "?"
                number = ".".join(parts)
            parent = next((ancestors[k] for k in sorted(ancestors, reverse=True) if k < level), "")
            for ancestor_level in list(ancestors):
                if ancestor_level >= level:
                    del ancestors[ancestor_level]
            if number is not None:
                ancestors[level] = number
            pending_section = {"title": arg[0], "level": level + 1, "number": number,
                               "kind": "app" if appendix else "sec", "parent": parent,
                               "labels": []}
            sections.append(pending_section)
            continue
        if label:
            active = next((frame for frame in reversed(stack) if frame), None)
            if active:
                refmap[label] = active
            elif pending_section is not None:
                pending_section["labels"].append(label)
                if pending_section["number"] is not None:
                    refmap[label] = (pending_section["kind"], pending_section["number"])
    return refmap, sections


def source_refmap(tex_text: str) -> dict:
    """兼容只需引用表的调用者；编号与完整标题大纲共用一处计算。"""
    return _source_structure(tex_text)[0]


# --------------------------------------------------------------------------
# 编排
# --------------------------------------------------------------------------

def normalise_pandoc_output(
    raw: str, media_dir: str, fig_dir: str, *, tex_text: str | None = None,
    pdftocairo: str | None = None,
) -> tuple[str, dict]:
    """pandoc 原始输出 → 可读 Markdown，返回 (markdown, 统计)。"""
    text = raw.replace("\r\n", "\n")
    text, math_stats = normalise_math(text)
    text, figure_stats, figmap = normalise_figures(text, media_dir, fig_dir, pdftocairo)
    fallback, sections = _source_structure(latex.prepare(tex_text)) if tex_text else ({}, None)
    text, anchor_stats, refmap = inject_anchors(text, fallback, sections)
    text, ref_stats = resolve_refs(text, refmap, fallback)
    text, polish_stats = polish(text)
    stats = {
        "math": math_stats,
        "figure": figure_stats,
        "anchors": anchor_stats,
        "refs": ref_stats,
        "polish": polish_stats,
        "figmap": figmap,
    }
    return text, stats


def run_pandoc(
    source_file: str, out_media_dir: str, *, pandoc: str, reader: str = "latex",
    bibliography: str | list[str] | None = None, source_dir: str | None = None, timeout: int = 300,
    diagnostics: list[str] | None = None,
) -> str:
    """调 pandoc 产出 Markdown 原文；reader 取 latex（路线 A）或 html（路线 B）。"""
    os.makedirs(out_media_dir, exist_ok=True)
    command = [
        pandoc, os.path.basename(source_file), "-f", reader, "-t", WRITER, "--wrap=none",
        "--extract-media=" + out_media_dir,
    ]
    if bibliography:
        files = [bibliography] if isinstance(bibliography, str) else bibliography
        command += ["--citeproc"] + ["--bibliography=" + os.path.abspath(file) for file in files]
    try:
        proc = subprocess.run(
            command, cwd=source_dir or os.path.dirname(source_file),
            capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TextifyError("pandoc_failed", "pandoc 无法启动：" + str(exc)) from exc
    if proc.returncode != 0:
        raise TextifyError(
            "pandoc_failed",
            "pandoc 退出码 " + str(proc.returncode) + "：" + (proc.stderr or b"")[:400].decode(
                "utf-8", "replace"
            ),
        )
    if diagnostics is not None:
        messages = (proc.stderr or b"").decode("utf-8", "replace").strip()
        if messages:
            diagnostics.append(messages[:2000])
    return (proc.stdout or b"").decode("utf-8", "replace")
