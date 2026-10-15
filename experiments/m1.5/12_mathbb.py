"""M1.5 spike / step 13: does the HTML route lose blackboard-bold symbols?

LaTeXML renders \\mathbb{R} as a literal double-struck character carried by a CSS
class rather than a MathML mathvariant attribute.  If pandoc cannot see the
variant, the symbol is dropped and `\\mathbb{R}^n` degrades to `{}^n`.
"""
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
RS = "\u211d"          # ℝ
EMPTY_POW = re.compile(r"\{\}\s*\^")
LATEX_MB = re.compile(r"\\mathbb\{")


def main() -> None:
    print(f"{'paper':15s} {'route':14s} {'\\mathbb{':>9s} {'Unicode ℝ':>10s} "
          f"{'{}^ (空底)':>11s}  KB")
    for tag in sorted(d for d in os.listdir(BASE) if d.startswith("26")
                      and os.path.isdir(os.path.join(BASE, d))):
        for root in ("textified", "textified-html", "textified-html-clean"):
            p = os.path.join(BASE, root, tag, "paper.md")
            if not os.path.exists(p):
                continue
            t = open(p, encoding="utf-8").read()
            print(f"{tag:15s} {root:14s} {len(LATEX_MB.findall(t)):9d} "
                  f"{t.count(RS):10d} {len(EMPTY_POW.findall(t)):11d}  "
                  f"{len(t)/1024:.1f}")
        print()

    # concrete side-by-side of one formula that contains R^n
    tag = "2610.18675v1"
    print("=" * 100)
    print("具体对照（2610.18675，含 R^n 的一句）：")
    for root in ("textified", "textified-html"):
        p = os.path.join(BASE, root, tag, "paper.md")
        t = open(p, encoding="utf-8").read()
        i = t.find("is the state and")
        seg = t[max(0, i - 160):i + 60].replace("\n", " | ")
        print(f"\n-- {root}:\n   {seg}")


if __name__ == "__main__":
    main()
