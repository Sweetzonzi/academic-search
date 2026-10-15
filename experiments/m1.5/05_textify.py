"""M1.5 spike / step 6: prototype textifier  (design doc section 4.10, crude edition).

Pipeline:  arXiv LaTeX source --pandoc(commonmark_x)--> Markdown
           --> normalise math (unwrap \\begin{equation}-style envs, lift \\label)
           --> normalise figures (raw <figure>/<embed> HTML -> Markdown images,
               PDF figures rendered to PNG with pdftocairo)
           --> inject numbered anchors (<!-- sec: -->, <!-- eq: -->, <!-- fig: -->)
           --> resolve cross-references against those anchors

This is a spike, not the deliverable: it exists to find out which parts of the
"native text first" claim actually hold, and which need real engineering.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

from lxml import html as LH

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import pypandoc  # noqa: E402

PANDOC = os.path.join(os.path.dirname(pypandoc.__file__), "files",
                      "pandoc.exe" if os.name == "nt" else "pandoc")
PDFTOCAIRO = r"D:\texlive\2024\bin\windows\pdftocairo.exe"
WRITER = "commonmark_x+tex_math_dollars+pipe_tables+footnotes+implicit_figures"
OUTROOT = os.path.join(BASE, "textified")

# ---------------------------------------------------------------- math shapes

MATH_ENV = {                       # LaTeX top-level env -> env legal inside $$
    "equation": None, "equation*": None, "displaymath": None,
    "multline": None, "multline*": None,
    "align": "aligned", "align*": "aligned", "flalign": "aligned",
    "flalign*": "aligned", "alignat": "aligned", "alignat*": "aligned",
    "eqnarray": "aligned", "eqnarray*": "aligned", "split": "aligned",
    "gather": "gathered", "gather*": "gathered",
}
DISP_ENV = re.compile(
    r"\$\$[ \t]*\r?\n?[ \t]*\\begin\{(?P<env>[A-Za-z]+\*?)\}(?P<body>.*?)"
    r"\\end\{(?P=env)\}[ \t]*\r?\n?[ \t]*\$\$", re.S)
DISP_PLAIN = re.compile(r"\$\$(?P<body>.+?)\$\$", re.S)
LABEL = re.compile(r"\\label\{([^}]*)\}")
# a display-math block whose content starts with `|` must not be read as a table


def normalise_math(txt: str) -> tuple:
    stats = {"env_unwrapped": 0, "env_wrapped": 0, "labels_lifted": 0,
             "kept_raw_env": 0}

    def emit(body: str, label, wrap=None) -> str:
        body = body.strip()
        if wrap:
            body = "\\begin{%s}\n%s\n\\end{%s}" % (wrap, body, wrap)
        head = f"<!-- id:{label} -->\n" if label else ""
        return f"\n\n{head}$$\n{body}\n$$\n\n"

    def repl(m):
        env, body = m.group("env"), m.group("body")
        labels = LABEL.findall(body)
        body = LABEL.sub("", body)
        body = re.sub(r"\\(?:nonumber|notag)\b", "", body)
        wrap = MATH_ENV.get(env, None)
        if env not in MATH_ENV:
            stats["kept_raw_env"] += 1
            wrap = "aligned"
        elif wrap:
            stats["env_wrapped"] += 1
        else:
            stats["env_unwrapped"] += 1
        stats["labels_lifted"] += len(labels)
        return emit(body, labels[0] if labels else None, wrap)

    out = DISP_ENV.sub(repl, txt)

    def repl_plain(m):
        body = m.group("body")
        if "\\begin{" in body:      # already a legal inner environment
            labs = LABEL.findall(body)
            return emit(LABEL.sub("", body), labs[0] if labs else None)
        labs = LABEL.findall(body)
        stats["labels_lifted"] += len(labs)
        return emit(LABEL.sub("", body), labs[0] if labs else None)

    out = DISP_PLAIN.sub(repl_plain, out)
    return out, stats


# ------------------------------------------------------------------- figures

FIG_OPEN = re.compile(r"<figure\b", re.I)
FIG_CLOSE = re.compile(r"</figure>", re.I)


def _figure_spans(txt: str):
    i = 0
    while True:
        m = FIG_OPEN.search(txt, i)
        if not m:
            return
        depth, pos = 0, m.start()
        while True:
            o, c = FIG_OPEN.search(txt, pos), FIG_CLOSE.search(txt, pos)
            if not c:
                return
            if o and o.start() < c.start():
                depth, pos = depth + 1, o.end()
            else:
                depth, pos = depth - 1, c.end()
                if depth == 0:
                    yield m.start(), c.end()
                    break
        i = pos


def _pdf_to_png(pdf: str, png_stem: str) -> bool:
    if not os.path.exists(PDFTOCAIRO):
        return False
    try:
        p = subprocess.run([PDFTOCAIRO, "-png", "-r", "200", "-singlefile",
                            pdf, png_stem], capture_output=True, timeout=180)
        return p.returncode == 0 and os.path.exists(png_stem + ".png")
    except Exception:
        return False


def normalise_figures(txt: str, media_dir: str, fig_dir: str) -> tuple:
    stats = {"figures": 0, "images": 0, "rendered_png": 0, "render_failed": 0,
             "missing_src": 0}
    os.makedirs(fig_dir, exist_ok=True)
    figmap = {}

    def localise(src: str) -> str:
        src = (src or "").strip()
        if src.startswith("file:///"):
            src = src[8:]
        if not os.path.isabs(src):
            cand = os.path.normpath(os.path.join(media_dir, src))
            src = cand if os.path.exists(cand) else src
        if not os.path.exists(src):
            stats["missing_src"] += 1
            return src.replace("\\", "/")
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(src))
        figmap.setdefault(os.path.basename(src), "figures/" + base)
        if src.lower().endswith(".pdf"):
            stem = os.path.join(fig_dir, os.path.splitext(base)[0])
            if _pdf_to_png(src, stem):
                stats["rendered_png"] += 1
                rel = "figures/" + os.path.basename(stem) + ".png"
                figmap[os.path.basename(src)] = rel
                return rel
            stats["render_failed"] += 1
        dst = os.path.join(fig_dir, base)
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
        return "figures/" + base

    replacements = []
    for s, e in _figure_spans(txt):
        block = txt[s:e]
        try:
            root = LH.fromstring("<div>" + block + "</div>")
        except Exception:
            continue
        imgs = root.xpath(".//*[local-name()='embed' or local-name()='img'"
                          " or local-name()='object']")
        caps = [" ".join(x.text_content().split())
                for x in root.xpath(".//*[local-name()='figcaption']")]
        if not imgs:
            continue
        stats["figures"] += 1
        ids = root.xpath(".//*[@id]")
        lines = [f"<!-- id:{ids[0].get('id')} -->"] if ids else []
        for im in imgs:
            src = im.get("src") or im.get("data") or ""
            alt = ""
            parent = im.getparent()
            if parent is not None:
                pc = parent.xpath(".//*[local-name()='figcaption']")
                if pc:
                    alt = " ".join(pc[0].text_content().split())
            alt = alt or os.path.splitext(os.path.basename(src))[0]
            lines.append(f"![{alt}]({localise(src)})")
            stats["images"] += 1
        big = caps[-1] if caps else ""
        if big and len(caps) > 1:
            lines += ["", f"*{big}*"]
        replacements.append((s, e, "\n".join(lines)))
    for s, e, new in reversed(replacements):
        txt = txt[:s] + new + txt[e:]
    return txt, stats, figmap


# ------------------------------------------------------------------- anchors

HEAD_ATTR = re.compile(r"^(#{1,6}) +(.+?)(?: +\{#([^}]+)\})? *$")
ANCHOR_ID = re.compile(r"^<!-- id:(\S+) -->$")
FENCE_DIV = re.compile(r"^:::\s*(?:\{([^}]*)\})?\s*$")
DIV_ID = re.compile(r"#(\S+)")
DIV_CLASS = re.compile(r"\.([A-Za-z0-9_-]+)")
THM_BOLD = re.compile(r"^\*\*(Theorem|Lemma|Proposition|Corollary|Definition"
                      r"|Assumption|Remark|Example|Claim)\s+([0-9]+(?:\.[0-9]+)?)\*\*")
TBL_ROW = re.compile(r"^\|.*\|\s*$")
REF_LINK = re.compile(r"\[(?P<text>[^\]]*?)\]\(#(?P<target>[^)]+)\)")


def inject_anchors(txt: str) -> tuple:
    """Number sections/equations/figures/tables and build label -> number."""
    counters = {"sec": [0] * 6, "eq": 0, "fig": 0, "tbl": 0}
    heads = [0] * 6
    refmap = {}
    out = []
    in_math = False
    pending_id = None
    prev_was_row = False
    div_stack = []                 # frames: {"id":..., "bound":bool}
    lines = txt.split("\n")

    def take_id():
        """The label this numbered item should carry, if any.

        Two sources, in order of precision:
          * an `<!-- id:X -->` comment emitted by normalise_math / figures
          * the id of the innermost enclosing fenced div that has not yet been
            bound to a numbered item (this is how arXiv's LaTeXML HTML ids and
            pandoc's `::: {#tab:...}` table divs get attached)
        """
        nonlocal pending_id
        if pending_id:
            got, pending_id = pending_id, None
            return got
        for fr in reversed(div_stack):
            if fr["id"] and not fr["bound"]:
                fr["bound"] = True
                return fr["id"]
        return None

    for i, line in enumerate(lines):
        s = line.strip()

        m = ANCHOR_ID.match(s)
        if m:
            pending_id = m.group(1)
            out.append(line)
            continue

        if s == "$$":
            if not in_math:
                counters["eq"] += 1
                got = take_id()
                if got:
                    refmap[got] = ("eq", counters["eq"])
                out.append(f"<!-- eq: {counters['eq']} -->")
            in_math = not in_math
            out.append(line)
            prev_was_row = False
            continue

        if in_math:
            out.append(line)
            continue

        h = HEAD_ATTR.match(line)
        if h:
            lvl = len(h.group(1))
            counters["sec"][lvl - 1] += 1
            heads[lvl - 1] += 1
            for k in range(lvl, 6):
                counters["sec"][k] = 0
            num = ".".join(str(c) for c in counters["sec"][:lvl])
            label = h.group(3) or take_id()
            if label:
                refmap[label] = ("sec", num)
            tag = f"<!-- sec: {num}" + (f" id:{label}" if label else "") + " -->"
            out.append(tag)
            out.append(f"{h.group(1)} {h.group(2)}")
            prev_was_row = False
            continue

        fd = FENCE_DIV.match(s)
        if fd:
            attrs = fd.group(1)
            if attrs is None:             # bare `:::` closes the innermost frame
                if div_stack:
                    div_stack.pop()
                else:
                    out.append(line)
                prev_was_row = False
                continue
            did = DIV_ID.search(attrs)
            cls = DIV_CLASS.search(attrs)
            if cls:
                out.append(f"<!-- env: {cls.group(1)} -->")
            else:
                out.append(line)
            div_stack.append({"id": did.group(1) if did else None,
                              "bound": False, "env": bool(cls)})
            if cls and did:               # theorem number comes from pandoc itself
                for ahead in lines[i + 1:i + 7]:
                    tb = THM_BOLD.match(ahead.strip())
                    if tb:
                        refmap[did.group(1)] = ("env",
                                                f"{tb.group(1)} {tb.group(2)}")
                        div_stack[-1]["bound"] = True
                        break
            prev_was_row = False
            continue

        if s.startswith("!["):
            counters["fig"] += 1
            got = take_id()
            if got:
                refmap[got] = ("fig", counters["fig"])
            out.append(f"<!-- fig: {counters['fig']} -->")
            out.append(line)
            prev_was_row = False
            continue

        is_row = bool(TBL_ROW.match(s))
        if is_row and not prev_was_row:
            counters["tbl"] += 1
            got = take_id()
            if got:
                refmap[got] = ("tbl", counters["tbl"])
            out.append(f"<!-- tbl: {counters['tbl']} -->")
        out.append(line)
        prev_was_row = is_row
    return "\n".join(out), {"heads": heads, "eq": counters["eq"],
                            "fig": counters["fig"], "tbl": counters["tbl"],
                            "labels": len(refmap)}, refmap


KIND_PREFIX = {"eq": "eq. ", "fig": "Fig. ", "tbl": "Table ", "sec": "sec. ",
               "env": ""}


REF_ANY = re.compile(r"\[(?:\\\[([^\]]+)\\\]|([^\]]*))\]\(#([^)]+)\)")


def resolve_refs(txt: str, refmap: dict) -> tuple:
    stats = {"resolved": 0, "unresolved": 0}

    def repl(m):
        target = m.group(3)
        keys = [k for k in target.split(",") if k in refmap]
        if not keys:
            stats["unresolved"] += 1
            return m.group(0)
        stats["resolved"] += 1
        lab = ", ".join(f"{KIND_PREFIX[refmap[k][0]]}{refmap[k][1]}" for k in keys)
        return f"[{lab}](#{target})"

    return REF_ANY.sub(repl, txt), stats


# pandoc span attributes that commonmark has no syntax for; dropping them is safe
SPAN_ATTR = re.compile(
    r"\{(?=[^{}]*\b(?:reference-type|reference|acronym-label|acronym-form|"
    r"\.unnumbered|\.definition|\.example|\.theorem|\.lemma|\.proposition|"
    r"\.corollary|\.remark|\.assumption|\.claim|\.proof)\b)[^{}]*\}")
RAW_ATTR = re.compile(r"\{=[A-Za-z]+\}")


def polish(txt: str) -> tuple:
    stats = {"attrs_dropped": 0}
    txt, n1 = SPAN_ATTR.subn("", txt)
    txt, n2 = RAW_ATTR.subn("", txt)
    stats["attrs_dropped"] = n1 + n2
    txt = re.sub(r"[ \t]+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt, stats


# ---------------------------------------------------------------------- main

def textify(paper_dir: str, tag: str) -> dict:
    outdir = os.path.join(OUTROOT, tag)
    figdir = os.path.join(outdir, "figures")
    os.makedirs(outdir, exist_ok=True)
    tex = None
    for cand in ("main.tex", "Manuscript_arxiv.tex"):
        if os.path.exists(os.path.join(paper_dir, cand)):
            tex = cand
    if tex is None:
        tex = next((f for f in os.listdir(paper_dir) if f.endswith(".tex")), None)
    bib = next((f for f in os.listdir(paper_dir) if f.endswith(".bib")), None)
    cmd = [PANDOC, tex, "-f", "latex", "-t", WRITER, "--wrap=none",
           "--extract-media=" + os.path.join(outdir, "_media")]
    if bib:
        cmd += ["--citeproc", "--bibliography=" + bib]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=paper_dir, capture_output=True)
    dt = time.time() - t0
    if proc.returncode != 0:
        return {"tag": tag, "error": proc.stderr.decode()[:400]}
    raw = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    txt, mstats = normalise_math(raw)
    txt, fstats, figmap = normalise_figures(
        txt, os.path.join(outdir, "_media"), figdir)
    txt, astats, refmap = inject_anchors(txt)
    txt, rstats = resolve_refs(txt, refmap)
    txt, pstats = polish(txt)
    media_abs = os.path.join(outdir, "_media")
    for pat in (media_abs.replace("\\", "/"), media_abs):
        txt = txt.replace(pat, "figures")
    head = (f"<!-- source: arxiv:{tag}  route=latex->pandoc  pipeline=m1.5-spike -->\n"
            f"<!-- generated: {time.strftime('%F %T')} -->\n\n")
    open(os.path.join(outdir, "paper.md"), "w", encoding="utf-8",
         newline="\n").write(head + txt)
    warn = [l for l in proc.stderr.decode("utf-8", "replace").splitlines()
            if "WARNING" in l]
    meta = {"tag": tag, "route": "latex", "writer": WRITER,
            "sec_convert": round(dt, 2),
            "md_kb": round(len(txt.encode()) / 1024, 1),
            "math": mstats, "figure": fstats, "anchors": astats,
            "refs": rstats, "polish": pstats, "pandoc_warnings": len(warn),
            "warning_kinds": sorted({re.sub(r"^\[WARNING\] ", "", w)[:60]
                                     for w in warn})[:6],
            "figmap": figmap}
    open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8").write(
        json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def main() -> None:
    tags = sys.argv[1:] or sorted(d for d in os.listdir(BASE) if d.startswith("26")
                                  and os.path.isdir(os.path.join(BASE, d)))
    rows = []
    for tag in tags:
        pdir = os.path.join(BASE, tag)
        if os.path.isdir(pdir):
            rows.append(textify(pdir, tag))
    print("=" * 108)
    print(f"{'paper':15s} {'s':>5s} {'KB':>7s} {'rawEnv':>6s} {'unw':>4s} {'wrp':>4s} "
          f"{'id':>4s} {'fig':>4s} {'img':>4s} {'png':>4s} {'fail':>4s} {'miss':>4s} "
          f"{'h1':>3s} {'h2':>3s} {'eq':>4s} {'fig#':>5s} {'tbl':>4s} {'ref':>5s} "
          f"{'unres':>5s} {'warn':>4s}")
    for m in rows:
        if "error" in m:
            print(f"{m['tag']:15s} ERROR {m['error'][:80]}")
            continue
        a, f, mm = m["anchors"], m["figure"], m["math"]
        print(f"{m['tag']:15s} {m['sec_convert']:5.2f} {m['md_kb']:7.1f} "
              f"{mm['kept_raw_env']:6d} {mm['env_unwrapped']:4d} {mm['env_wrapped']:4d} "
              f"{mm['labels_lifted']:4d} {f['figures']:4d} {f['images']:4d} "
              f"{f['rendered_png']:4d} {f['render_failed']:4d} {f['missing_src']:4d} "
              f"{a['heads'][0]:3d} {a['heads'][1]:3d} {a['eq']:4d} {a['fig']:5d} "
              f"{a['tbl']:4d} {m['refs']['resolved']:5d} {m['refs']['unresolved']:5d} "
              f"{m['pandoc_warnings']:4d}")
    for m in rows:
        if "error" not in m and m["warning_kinds"]:
            print(f"\n{m['tag']} warnings:")
            for w in m["warning_kinds"]:
                print(f"  - {w}")


if __name__ == "__main__":
    main()
