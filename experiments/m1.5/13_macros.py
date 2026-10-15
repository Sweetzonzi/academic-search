"""M1.5 spike / step 14: unexpanded custom macros -- the decisive fidelity gap.

Control/automation papers define a lot of math macros (\\real, \\bH, \\argmin,
\\setKll ...).  The LaTeX route lets pandoc read those definitions and expand
them.  The HTML route cannot: LaTeXML stores the *macro call* in the
`annotation[encoding="application/x-tex"]` node, so pandoc echoes `\\real^{n}`
-- an undefined command that no renderer can draw.

Metric: how many calls to the paper's own \\newcommand macros survive unexpanded.
"""
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
NEWCMD = re.compile(r"\\(?:newcommand|renewcommand|providecommand)\s*\{?\s*\\([A-Za-z@]+)")
DECLARE = re.compile(r"\\DeclareMathOperator\*?\s*\{\s*\\([A-Za-z@]+)")
USE = r"\\{name}\b"


def custom_macros(paper_dir: str) -> set:
    names = set()
    for r, _d, fs in os.walk(paper_dir):
        for f in fs:
            if not f.endswith((".tex", ".ltx", ".sty", ".cls")):
                continue
            txt = open(os.path.join(r, f), encoding="utf-8", errors="replace").read()
            names |= set(NEWCMD.findall(txt)) | set(DECLARE.findall(txt))
    return names


def main() -> None:
    print(f"{'paper':15s} {'自定义宏':>7s} {'latex 未展开':>11s} "
          f"{'html 未展开':>11s} {'html-clean':>11s}")
    tl = th = tc = tn = 0
    for tag in sorted(d for d in os.listdir(BASE) if d.startswith("26")
                      and os.path.isdir(os.path.join(BASE, d))):
        macros = custom_macros(os.path.join(BASE, tag))
        if not macros:
            continue
        row = [len(macros)]
        for root in ("textified", "textified-html", "textified-html-clean"):
            p = os.path.join(BASE, root, tag, "paper.md")
            if not os.path.exists(p):
                row.append(-1)
                continue
            t = open(p, encoding="utf-8").read()
            n = sum(len(re.findall(USE.format(name=re.escape(m)), t)) for m in macros)
            row.append(n)
        tl += max(row[1], 0)
        th += max(row[2], 0)
        tc += max(row[3], 0)
        tn += row[0]
        print(f"{tag:15s} {row[0]:7d} {row[1]:11d} {row[2]:11d} {row[3]:11d}")
    print(f"{'TOTAL':15s} {tn:7d} {tl:11d} {th:11d} {tc:11d}")


if __name__ == "__main__":
    main()
