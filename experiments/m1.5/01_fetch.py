"""M1.5 spike / step 2: download arXiv e-print (LaTeX source) + HTML for candidates.

Checks, per paper:
  - e-print available? what's inside? (single .tex? tarball? dvi?)
  - does the source use TikZ / custom macros / \\input?
  - is the arXiv HTML (ar5iv-style) version available?
  - size of each route

arXiv asks for >= 3 s between requests on a single connection; we sleep 3.5 s.
"""
import gzip
import io
import os
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "academic-search-spike/0.1 (M1.5 textification test; contact: local user)"}
SLEEP = 3.5


def get(url: str, tries: int = 3):
    """Return (bytes, content_type) or (None, reason)."""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(3 * (i + 1))
    return None, last


def extract_eprint(blob: bytes, dest: str) -> str:
    """Save and unpack the e-print. Returns a human-readable description."""
    os.makedirs(dest, exist_ok=True)
    if blob[:2] == b"\x1f\x8b":
        raw = gzip.decompress(blob)
        if raw[:5] == b"ustar" or len(raw) > 512 and raw[257:262] == b"ustar":
            with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
                members = tf.getmembers()
                tf.extractall(dest, filter="data")
            return f"tar.gz -> {len(members)} files"
        open(os.path.join(dest, "main.tex"), "wb").write(raw)
        return "gzip(single .tex) -> main.tex"
    if blob[:5] == b"%PDF-":
        open(os.path.join(dest, "main.pdf"), "wb").write(blob)
        return "PDF only (no LaTeX source)"
    if blob.lstrip()[:1] == b"%":  # starts with a TeX comment -> plain .tex
        open(os.path.join(dest, "main.tex"), "wb").write(blob)
        return "plain .tex -> main.tex"
    open(os.path.join(dest, "main.bin"), "wb").write(blob)
    return f"unknown format, magic={blob[:8]!r}"


def probe(dest: str) -> dict:
    """Scan extracted sources for the things that decide our pipeline."""
    tex, figs, other = [], [], []
    for root, _dirs, files in os.walk(dest):
        for f in files:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, dest).replace("\\", "/")
            if f.lower().endswith((".tex", ".ltx", ".bbl")):
                tex.append(rel)
            elif f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg", ".pgf", ".ps")):
                figs.append((rel, os.path.getsize(p)))
            else:
                other.append(rel)
    body = ""
    for rel in tex:
        if rel.endswith((".tex", ".ltx")):
            body += open(os.path.join(dest, rel), encoding="utf-8", errors="replace").read()

    def count(pat):
        return len(re.findall(pat, body))

    return {
        "tex_files": tex,
        "figure_files": figs,
        "other": other[:20],
        "tikzpicture": count(r"\\begin\{tikzpicture\}"),
        "pgfplots": count(r"\\begin\{axis\}"),
        "includegraphics": count(r"\\includegraphics"),
        "newcommand": count(r"\\(?:re)?newcommand"),
        "def_macro": count(r"\\def\\"),
        "inputs": re.findall(r"\\(?:input|include)\{([^}]+)\}", body)[:15],
        "equations": count(r"\\begin\{(?:equation|align|gather|eqnarray|multline)"),
        "inline_math_hint": count(r"\$"),
        "documentclass": (re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", body) or [None, "?"])[1],
        "bibliography": re.findall(r"\\(?:bibliography|addbibresource)\{([^}]+)\}", body)[:5],
    }


def main() -> None:
    ids = sys.argv[1:]
    for aid in ids:
        short = aid.replace("/", "_")
        dest = os.path.join(BASE, short)
        print("=" * 100)
        print(f"### {aid}")
        if os.path.exists(os.path.join(dest, ".done")):
            print("  (already fetched)")
        else:
            blob, ct = get(f"https://arxiv.org/e-print/{aid}")
            time.sleep(SLEEP)
            if blob is None:
                print(f"  e-print: FAILED ({ct})")
            else:
                print(f"  e-print: {len(blob)/1024:.1f} KB, {ct} -> {extract_eprint(blob, dest)}")
            html, ct2 = get(f"https://arxiv.org/html/{aid}")
            time.sleep(SLEEP)
            if html is None:
                print(f"  html:    FAILED ({ct2})")
            else:
                open(os.path.join(dest, "arxiv.html"), "wb").write(html)
                print(f"  html:    {len(html)/1024:.1f} KB, {ct2} -> arxiv.html")
            open(os.path.join(dest, ".done"), "w").write("ok")
        p = probe(dest)
        print(f"  documentclass={p['documentclass']}  equations={p['equations']}  "
              f"includegraphics={p['includegraphics']}")
        print(f"  tikzpicture={p['tikzpicture']}  pgfplots={p['pgfplots']}  "
              f"newcommand={p['newcommand']}  def={p['def_macro']}")
        print(f"  inputs={p['inputs']}  bib={p['bibliography']}")
        print(f"  tex={p['tex_files']}")
        print(f"  figures({len(p['figure_files'])})=" +
              ", ".join(f"{n}({s/1024:.0f}KB)" for n, s in p["figure_files"][:12]))
        if p["other"]:
            print(f"  other[:20]={p['other']}")


if __name__ == "__main__":
    main()
