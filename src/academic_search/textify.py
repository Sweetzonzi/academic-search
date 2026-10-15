"""文本化流水线：LaTeX 源码 → 可读 Markdown（含图件与锚点）。

契约：docs/02-设计说明书.md §4.10、[ADR-0056](../决策/ADR-0056.md) 决策 3、
经验数字见 docs/实验/EX-2026-10-14-文本化.md。spike 在 experiments/m1.5/05_textify.py。

本模块只用标准库（pandoc / pdftocairo 是外部可执行文件，缺失即降级）；
不改库、不拼 bytes/ 路径（那是 paths.py 与 service 的事）。
"""

from __future__ import annotations

import glob as _glob
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser

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


def converter_version(pandoc: str) -> str:
    return pandoc_version(pandoc) + "|" + WRITER


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


def find_bibliography(directory: str) -> str | None:
    matches = sorted(_glob.glob(os.path.join(directory, "*.bib")))
    return matches[0] if matches else None


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
            if not pdftocairo:
                stats["render_skipped"] += 1
            else:
                rendered = pdf_to_png(raw, stem, pdftocairo)
                if rendered:
                    stats["rendered_png"] += 1
                    rel = "figures/" + os.path.basename(rendered)
                    figmap[os.path.basename(raw)] = rel
                    return rel
                stats["render_failed"] += 1
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
        for image in parser.images:
            alt = image["alt"] or os.path.splitext(os.path.basename(image["src"]))[0]
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

_HEAD_ATTR = re.compile(r"^(#{1,6}) +(.+?)(?: +\{#([^}]+)\})? *$")
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


def inject_anchors(text: str) -> tuple[str, dict, dict]:
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
            counters["sec"][level - 1] += 1
            heads[level - 1] += 1
            for k in range(level, 6):
                counters["sec"][k] = 0
            parts = counters["sec"][:level]
            start = next((i for i, value in enumerate(parts) if value), 0)
            number = ".".join(str(c) for c in parts[start:])
            label = head.group(3) or take_id()
            if label:
                refmap[label] = ("sec", number)
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
    }
    return "\n".join(out), stats, refmap


# --------------------------------------------------------------------------
# 交叉引用解析
# --------------------------------------------------------------------------

KIND_PREFIX = {
    "eq": "eq. ", "fig": "Fig. ", "tbl": "Table ", "sec": "sec. ",
    "env": "", "alg": "Algorithm ",
}

_REF_ANY = re.compile(r"\[(?:\\\[([^\]]+)\\\]|\[?([^\]]*?)\]?)\]\(#([^)]+)\)")

#: 参考文献条目链接（citeproc 产生）：本来就有编号，不改写、不计未解析。
_BIB_TARGET = re.compile(r"^(?:bib|cite|ref)[.:]", re.I)


def resolve_refs(text: str, refmap: dict, source_refmap: dict | None = None) -> tuple[str, dict]:
    """把 [\\[label\\]](#label) / [[1]](#bib.x) 之类改写成带编号的链接。

    source_refmap 是 LaTeX 源码的兜底表（EX §7 第 2 条：没有 div id 的表格）。
    """
    stats = {"resolved": 0, "unresolved": 0, "bibliography": 0}
    unresolved: list[str] = []
    table = dict(refmap)
    if source_refmap:
        for key, value in source_refmap.items():
            table.setdefault(key, value)

    def repl(match):
        target = match.group(3)
        if _BIB_TARGET.match(target):
            stats["bibliography"] += 1
            return match.group(0)
        keys = [k for k in target.split(",") if k in table]
        if not keys:
            stats["unresolved"] += 1
            unresolved.append(target)
            return match.group(0)
        stats["resolved"] += 1
        label = ", ".join(KIND_PREFIX.get(table[k][0], "") + table[k][1] for k in keys)
        return "[" + label + "](#" + target + ")"

    result = _REF_ANY.sub(repl, text)
    stats["unresolved_targets"] = unresolved
    return result, stats


_SPAN_ATTR = re.compile(
    r"\{(?=[^{}]*\b(?:reference-type|reference|acronym-label|acronym-form|"
    r"\.unnumbered|\.definition|\.example|\.theorem|\.lemma|\.proposition|"
    r"\.corollary|\.remark|\.assumption|\.claim|\.proof)\b)[^{}]*\}"
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
_TOKEN_RE = re.compile(
    r"\\label\{([^}]*)\}|\\begin\{([A-Za-z*]+)\}|\\end\{([A-Za-z*]+)\}"
    r"|\\(part|chapter|section|subsection|subsubsection)\*?\{"
)


def source_refmap(tex_text: str) -> dict:
    """从 LaTeX 源码按出现顺序建 label → (kind, number)（兜底用）。

    对没有 div id 的表格（EX §7 第 2 条）：源码里的第 N 个 table 环境对应
    输出里的第 N 个表格，因此按顺序对齐即可，不猜文本。
    """
    refmap: dict[str, tuple[str, str]] = {}
    counters: dict[str, int] = {}
    section = [0, 0, 0]
    pending_section: str | None = None
    stack: list[tuple[str, str] | None] = []

    for match in _TOKEN_RE.finditer(tex_text):
        label, begin, end, section_kind = match.groups()
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
                     "subsection": 1, "subsubsection": 2}[section_kind]
            section[level] += 1
            for k in range(level + 1, 3):
                section[k] = 0
            pending_section = ".".join(str(c) for c in section[: level + 1])
            continue
        if label:
            if stack and stack[-1]:
                refmap[label] = stack[-1]
            elif pending_section:
                refmap[label] = ("sec", pending_section)
            pending_section = None
    return refmap


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
    text, anchor_stats, refmap = inject_anchors(text)
    fallback = source_refmap(tex_text) if tex_text else {}
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
    root_tex: str, out_media_dir: str, *, pandoc: str, bibliography: str | None = None,
    source_dir: str | None = None, timeout: int = 300,
) -> str:
    """调 pandoc 产出 Markdown 原文；失败抛 TextifyError。"""
    command = [
        pandoc, os.path.basename(root_tex), "-f", "latex", "-t", WRITER, "--wrap=none",
        "--extract-media=" + out_media_dir,
    ]
    if bibliography:
        command += ["--citeproc", "--bibliography=" + os.path.basename(bibliography)]
    try:
        proc = subprocess.run(
            command, cwd=source_dir or os.path.dirname(root_tex),
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
    return (proc.stdout or b"").decode("utf-8", "replace")
