"""M1.5 spike / step 5: pick the best output dialect.

Runs the same LaTeX source through several pandoc writers and reports, for each,
how it encodes the five constructs that decide readability for an LLM/human:
display math, inline math, figures, tables, theorem-like environments.
"""
import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import pypandoc  # noqa: E402

PANDOC = os.path.join(os.path.dirname(pypandoc.__file__), "files",
                      "pandoc.exe" if os.name == "nt" else "pandoc")
PAPER = os.path.join(BASE, "2610.18675v1")
TEX = "main.tex"

WRITERS = [
    ("gfm", "gfm"),
    ("gfm-tex_math_dollars", "gfm-tex_math_dollars"),
    ("gfm+tex_math_dollars", "gfm+tex_math_dollars"),
    ("commonmark_x", "commonmark_x+tex_math_dollars+pipe_tables+footnotes+implicit_figures"),
    ("md-pipe", "markdown+tex_math_dollars+pipe_tables+footnotes+implicit_figures"
                "-raw_tex-raw_attribute-grid_tables"),
    ("md-grid", "markdown+tex_math_dollars+pipe_tables+footnotes+implicit_figures"),
]


def sample(pat: str, txt: str, n: int = 70) -> str:
    m = re.search(pat, txt, re.S | re.M)
    if not m:
        return "—none—"
    s = m.group(0).replace("\n", "⏎")
    return s[:n]


def main() -> None:
    rows = []
    for label, writer in WRITERS:
        p = subprocess.run(
            [PANDOC, TEX, "-f", "latex", "-t", writer, "--wrap=none"],
            cwd=PAPER, capture_output=True)
        if p.returncode != 0:
            print(f"{label:22s} FAILED: {p.stderr.decode()[:200]}")
            continue
        # pandoc writes CRLF on Windows; normalise so that ^...$ anchors behave
        t = p.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        rows.append({
            "dialect": label,
            "disp": sample(r"^\$\$.*?\$\$|^``` *math.*?^```", t),
            "inl": sample(r"\$`[^`]+`\$|\$[^$\n]{2,30}\$", t),
            "fig": sample(r"<figure.*?</figure>|!\[[^\]]*\]\([^)]*\)", t),
            "tbl": sample(r"^\|.*\|$|^\+[-=+]+$", t),
            "thm": sample(r"^:::.*$|^<div class=\"definition\">.*$", t),
            "counts": {
                "$$": t.count("$$"),
                "```math": t.count("``` math"),
                "$`": t.count("$`"),
                "<figure": t.count("<figure"),
                "<embed": t.count("<embed"),
                "pipe_rows": len(re.findall(r"^\|.*\|$", t, re.M)),
                "grid": len(re.findall(r"^\+[-=+]{4,}\+$", t, re.M)),
                "span_attr": len(re.findall(r"\]\{[.#]", t)),
                "raw_env": len(re.findall(r"\\begin\{", t)),
            },
        })
    for r in rows:
        print("=" * 110)
        print(f"### {r['dialect']}")
        print(f"  display : {r['disp']}")
        print(f"  inline  : {r['inl']}")
        print(f"  figure  : {r['fig']}")
        print(f"  table   : {r['tbl']}")
        print(f"  theorem : {r['thm']}")
        print(f"  counts  : {r['counts']}")


if __name__ == "__main__":
    main()
