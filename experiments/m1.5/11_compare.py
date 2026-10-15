"""M1.5 spike / step 12: head-to-head on what actually matters to a control paper.

Volume was never the only axis.  This compares the two routes on:
  * math fidelity      -- \\mathbb / \\mathcal / \\dot survive or not
  * structural noise   -- LaTeXML wraps every equation in a layout <table>,
                          which our table detector then reports as a real table
  * disk footprint     -- source + figures + paper.md, per paper and per route
"""
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
TAGS = ["2610.17916v1", "2610.17929v1", "2610.18290v1", "2610.18311v1",
        "2610.18361v1", "2610.18630v1", "2610.18675v1", "2610.18758v1"]
ROUTES = [("latex", "textified"), ("html", "textified-html"),
          ("html-clean", "textified-html-clean")]

MACROS = [r"\\mathbb\{", r"\\mathcal\{", r"\\mathbf\{", r"\\dot\{",
          r"\\boldsymbol\{", r"\\succeq|\\preceq", r"\\top\b", r"\\ker\b"]


def dir_size(p: str) -> int:
    tot = 0
    for r, _d, fs in os.walk(p):
        for f in fs:
            tot += os.path.getsize(os.path.join(r, f))
    return tot


def main() -> None:
    print("=" * 108)
    print("A) 公式保真度：原始 LaTeX 命令在两个路线里还剩多少（数出的是 paper.md 中的出现次数）")
    print(f"{'paper':15s} " + " ".join(f"{m.strip(chr(92)+'{}'):>10s}" for m in MACROS))
    for tag in TAGS:
        print(f"--- {tag}")
        for route, root in ROUTES:
            p = os.path.join(BASE, root, tag, "paper.md")
            if not os.path.exists(p):
                continue
            t = open(p, encoding="utf-8").read()
            cnt = [len(re.findall(m, t)) for m in MACROS]
            print(f"    {route:11s} " + " ".join(f"{c:10d}" for c in cnt))

    print("\n" + "=" * 108)
    print("B) 结构噪音：检测到的\"表格\"有多少是真表格")
    print(f"{'paper':15s} {'latex tbl':>9s} {'html tbl':>9s} {'html-clean':>10s} "
          f"{'真表格(src)':>11s}")
    for tag in TAGS:
        src = os.path.join(BASE, tag)
        n_real = 0
        for r, _d, fs in os.walk(src):
            for f in fs:
                if f.endswith((".tex", ".ltx")):
                    body = open(os.path.join(r, f), encoding="utf-8",
                                errors="replace").read()
                    n_real += len(re.findall(r"\\begin\{table\*?\}", body))
        row = []
        for _route, root in ROUTES:
            p = os.path.join(BASE, root, tag, "paper.md")
            if os.path.exists(p):
                t = open(p, encoding="utf-8").read()
                row.append(len(re.findall(r"^<!-- tbl: \d+ -->$", t, re.M)))
            else:
                row.append(-1)
        print(f"{tag:15s} {row[0]:9d} {row[1]:9d} {row[2]:10d} {n_real:11d}")

    print("\n" + "=" * 108)
    print("C) 磁盘占用（每篇：论文源码/HTML + 图件 + paper.md）")
    hdr = (f"{'paper':15s} {'src KB':>8s} "
           f"{'latex md':>9s} {'latex fig':>10s} {'latex 总':>9s} "
           f"{'html md':>8s} {'html fig':>9s} {'html 总':>9s}")
    print(hdr)
    tot = dict.fromkeys(["src", "lmd", "lfig", "lall", "hmd", "hfig", "hall"], 0)
    for tag in TAGS:
        src = dir_size(os.path.join(BASE, tag))
        lmd = os.path.getsize(os.path.join(BASE, "textified", tag, "paper.md"))
        lfig = dir_size(os.path.join(BASE, "textified", tag, "figures"))
        hmd = os.path.getsize(os.path.join(BASE, "textified-html", tag, "paper.md"))
        hfig = dir_size(os.path.join(BASE, "textified-html", tag, "figures"))
        for k, v in zip(("src", "lmd", "lfig", "lall", "hmd", "hfig", "hall"),
                        (src, lmd, lfig, lmd + lfig, hmd, hfig, hmd + hfig)):
            tot[k] += v
        print(f"{tag:15s} {src/1024:8.0f} {lmd/1024:9.1f} {lfig/1024:10.1f} "
              f"{(lmd+lfig)/1024:9.1f} {hmd/1024:8.1f} {hfig/1024:9.1f} "
              f"{(hmd+hfig)/1024:9.1f}")
    print(f"{'TOTAL':15s} {tot['src']/1024/1024:7.1f}M {tot['lmd']/1024:9.1f} "
          f"{tot['lfig']/1024/1024:9.1f}M {tot['lall']/1024/1024:8.1f}M "
          f"{tot['hmd']/1024:8.1f} {tot['hfig']/1024/1024:8.1f}M "
          f"{tot['hall']/1024/1024:8.1f}M")


if __name__ == "__main__":
    main()
