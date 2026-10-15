"""M1.5 spike / step 15: which LaTeX commands does each route emit?

A quick fairness check on the HTML route: collect every \\command that appears
inside maths in each route's paper.md and diff the alphabets.  Anything the HTML
route emits but the LaTeX route never does is a candidate for "no renderer will
draw this".
"""
import collections
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
CMD = re.compile(r"\\([A-Za-z]{2,})")
# maths regions only: $...$ and $$...$$ fenced blocks
MATH = re.compile(r"\$\$.*?\$\$|\$`[^`]*`\$|\$[^$\n]{1,600}?\$", re.S)


def commands(path: str) -> collections.Counter:
    t = open(path, encoding="utf-8").read()
    c = collections.Counter()
    for m in MATH.finditer(t):
        c.update(CMD.findall(m.group(0)))
    return c


def main() -> None:
    tag = "2610.18675v1"
    la = commands(os.path.join(BASE, "textified", tag, "paper.md"))
    hh = commands(os.path.join(BASE, "textified-html", tag, "paper.md"))
    only_html = {k: v for k, v in hh.items() if k not in la}
    only_latex = {k: v for k, v in la.items() if k not in hh}
    print(f"### {tag}")
    print(f"   distinct commands: latex={len(la)}  html={len(hh)}")
    print(f"   只在 html 路线出现的命令 ({len(only_html)} 种，共 {sum(only_html.values())} 次):")
    for k, v in sorted(only_html.items(), key=lambda kv: -kv[1])[:25]:
        print(f"      {v:4d}  \\{k}")
    print(f"   只在 latex 路线出现的命令 ({len(only_latex)} 种，共 {sum(only_latex.values())} 次):")
    for k, v in sorted(only_latex.items(), key=lambda kv: -kv[1])[:25]:
        print(f"      {v:4d}  \\{k}")


if __name__ == "__main__":
    main()
