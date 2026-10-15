"""M1.5 spike / step 4: quality audit of the converted Markdown.

For every output file it reports, in one table:
  math      display $$...$$ / inline $...$ counts, and how many of them still
            contain raw LaTeX (which is what pandoc emits when it cannot parse
            a construct -- the question is whether the *content* survives)
  leak      leftover structural LaTeX (\\begin{, \\cite, \\ref, \\label, ...)
  struct    headings / tables / images / list items / words
  media     image links, and whether the target file actually exists on disk
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "out")

DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.S)
# inline math: $...$ not part of $$ and not escaped; conservative on purpose
INLINE = re.compile(r"(?<!\$)(?<!\\)\$(?!\$)([^$\n]{1,400}?)(?<!\\)\$(?!\$)")
IMG = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
LEAKS = {
    r"\\begin\{": "env",
    r"\\end\{": "env-end",
    r"\\cite[a-z]*\{": "cite",
    r"\\ref\{|\\eqref\{|\\autoref\{": "ref",
    r"\\label\{": "label",
    r"\\includegraphics": "includegraphics",
    r"\\begin\{tikzpicture\}": "tikz",
    r"\\newcommand|\\def\\": "macrodef",
    r"\\documentclass|\\usepackage": "preamble",
    r"%\s*TODO|FIXME": "todo",
}


def audit(path: str) -> dict:
    txt = open(path, encoding="utf-8", errors="replace").read()
    disp = DISPLAY.findall(txt)
    # strip display blocks before looking for inline math, to avoid double counting
    rest = DISPLAY.sub(" ", txt)
    inl = INLINE.findall(rest)
    imgs = IMG.findall(txt)
    missing = [p for p in imgs
               if not p.startswith("http") and not os.path.exists(
                   os.path.normpath(os.path.join(os.path.dirname(path), p)))]
    leaks = {label: len(re.findall(pat, txt)) for pat, label in LEAKS.items()}
    leaks = {k: v for k, v in leaks.items() if v}
    return {
        "kb": round(len(txt.encode("utf-8")) / 1024, 1),
        "disp": len(disp),
        "disp_raw": sum(1 for d in disp if "\\" in d),
        "inl": len(inl),
        "inl_raw": sum(1 for d in inl if "\\" in d),
        "h": len(re.findall(r"^#{1,6} ", txt, re.M)),
        "tables": len(re.findall(r"^\|.*\|\s*$", txt, re.M)),
        "imgs": len(imgs),
        "img_missing": len(missing),
        "words": len(txt.split()),
        "leaks": leaks,
    }


def main() -> None:
    rows = []
    for paper in sorted(os.listdir(OUT)):
        pdir = os.path.join(OUT, paper)
        if not os.path.isdir(pdir):
            continue
        for f in sorted(os.listdir(pdir)):
            if not f.endswith(".md"):
                continue
            rows.append((paper, f, audit(os.path.join(pdir, f))))
    hdr = (f"{'paper':14s} {'file':14s} {'KB':>6s} {'disp':>5s} {'raw':>4s} "
           f"{'inl':>5s} {'raw':>4s} {'head':>4s} {'tbl':>4s} {'img':>4s} {'miss':>4s} {'words':>6s}  leaks")
    print(hdr)
    print("-" * len(hdr))
    for paper, f, a in rows:
        print(f"{paper:14s} {f:14s} {a['kb']:6.1f} {a['disp']:5d} {a['disp_raw']:4d} "
              f"{a['inl']:5d} {a['inl_raw']:4d} {a['h']:4d} {a['tables']:4d} "
              f"{a['imgs']:4d} {a['img_missing']:4d} {a['words']:6d}  {a['leaks']}")


if __name__ == "__main__":
    main()
