"""M1.5 spike / step 3: run pandoc over the downloaded papers and log everything.

Produces, per paper, under out/<id>/:
    paper.gfm.md          LaTeX source -> GFM
    paper.md.md           LaTeX source -> pandoc markdown (math-safe dialect)
    html.gfm.md           arXiv HTML     -> GFM
    media/                --extract-media drop zone
    <name>.stderr.txt     pandoc's stderr, always written (empty file = clean run)
A JSON-ish log line per run goes to stdout and to out/runlog.txt.
"""
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import pypandoc  # noqa: E402

FILES = os.path.join(os.path.dirname(pypandoc.__file__), "files")
PANDOC = os.path.join(FILES, "pandoc.exe" if os.name == "nt" else "pandoc")
OUT = os.path.join(BASE, "out")
LOG = os.path.join(OUT, "runlog.txt")


def run(name: str, args: list, cwd: str, dest: str) -> dict:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    t0 = time.time()
    p = subprocess.run([PANDOC, *args], cwd=cwd, capture_output=True)
    dt = time.time() - t0
    err = p.stderr.decode("utf-8", "replace")
    if p.returncode == 0 and p.stdout:
        open(dest, "wb").write(p.stdout)
    open(dest + ".stderr.txt", "w", encoding="utf-8").write(err)
    rec = {
        "paper": os.path.basename(cwd),
        "run": name,
        "rc": p.returncode,
        "sec": round(dt, 2),
        "out_kb": round(len(p.stdout) / 1024, 1) if p.returncode == 0 else None,
        "stderr_head": " | ".join(err.strip().splitlines()[:3]),
        "cmd": "pandoc " + " ".join(args),
    }
    print(json.dumps(rec, ensure_ascii=False))
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def convert_paper(paper_dir: str, tex: str) -> None:
    o = os.path.join(OUT, os.path.basename(paper_dir))
    media = os.path.join(o, "media")
    common = ["--wrap=none", f"--resource-path={paper_dir}", "--extract-media=" + media]
    # 1) LaTeX -> GFM  (what an LLM/other tool would read)
    run("tex->gfm", [tex, "-f", "latex", "-t", "gfm", *common], paper_dir,
        os.path.join(o, "paper.gfm.md"))
    # 2) LaTeX -> pandoc markdown (math/attribute-safe dialect)
    run("tex->md", [tex, "-f", "latex", "-t", "markdown+tex_math_dollars+pipe_tables"
                    "+footnotes+implicit_figures", *common], paper_dir,
        os.path.join(o, "paper.md.md"))
    # 3) arXiv HTML -> GFM  (the fallback when there is no usable LaTeX)
    html = os.path.join(paper_dir, "arxiv.html")
    if os.path.exists(html):
        run("html->gfm", [html, "-f", "html", "-t", "gfm", *common], paper_dir,
            os.path.join(o, "html.gfm.md"))
        run("html->md", [html, "-f", "html", "-t", "markdown+tex_math_dollars"
                         "+pipe_tables+footnotes+implicit_figures", *common], paper_dir,
            os.path.join(o, "html.md.md"))


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    open(LOG, "a", encoding="utf-8").write(f"\n===== run {time.strftime('%F %T')} =====\n")
    for d in sorted(os.listdir(BASE)):
        pdir = os.path.join(BASE, d)
        if not os.path.isdir(pdir) or not d.startswith("26"):
            continue
        print("=" * 100)
        print(f"### {d}")
        tex = None
        for cand in ("main.tex", "Manuscript_arxiv.tex"):
            if os.path.exists(os.path.join(pdir, cand)):
                tex = cand
        if tex is None:
            hits = [f for f in os.listdir(pdir) if f.endswith(".tex")]
            tex = hits[0] if hits else None
        if tex is None:
            print("  no .tex found, skipping")
            continue
        convert_paper(pdir, tex)


if __name__ == "__main__":
    main()
