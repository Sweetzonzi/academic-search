#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""自检：AGENTS.md 是否遵守它自己定的规则？

R1  不含"版本相对框架"措辞（✗/✓ 举例行、「」引号内引用、下定义行除外）
R2  它引用的仓库内文件都存在
R3  它让你跑的脚本都存在
R4  结构正常（代码围栏成对、标题不跳级、§1 的子节数）
R5  文中"第 N 节"自指真实存在

用法：& 'D:\anaconda3\python.exe' tools\selfcheck.py
退出码：有 ✗ 时 1。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent          # academic-search/
F = ROOT / "AGENTS.md"

DEFECT_WORDS = ["推翻", "上一版", "旧版", "更正", "修订建议", "此前", "第一版", "起因："]
# 下定义 / 解释机制的行必然会写出缺陷词 —— 那是说明，不是框架
DEFN = ("**长什么样**", "**为什么是缺陷**", "**改法**", "**衡量标准**",
        "**判据**", "**人读的验收问法**", "- **框架是否仍在**",
        "**不要**在正文里留", "误报")


def main() -> int:
    if not F.exists():
        print(f"AGENTS.md 不存在：{F}", file=sys.stderr)
        return 2
    t = F.read_text(encoding="utf-8")
    lines = t.split("\n")
    failed = 0

    print("R1  版本相对框架（排除举例、引号内引用、下定义行）")
    bad = 0
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith(("- ✗", "- ✓", "| ✗", "| ✓")) or any(d in s for d in DEFN):
            continue
        stripped = re.sub(r"「[^」]*」", "", line)
        stripped = re.sub(r"“[^”]*”", "", stripped)
        stripped = re.sub(r"`[^`]*`", "", stripped)
        if any(p in stripped for p in DEFECT_WORDS):
            bad += 1
            print(f"     ✗ [{i}]  {s[:90]}")
    print(f"     → {bad} 处（应为 0）")
    failed += bad
    print("     注：本检查只识别'缺陷词汇'。谁是缺陷、谁是定义，最终仍需人读——")
    print("         这正是 AGENTS.md 第 4 节自己承认的局限。")

    print("\nR2  AGENTS.md 引用的仓库内文件")
    # 先剔除 ``` 围栏内的内容，否则反引号会跨围栏配对，把整段命令当成文件名
    prose = re.sub(r"```.*?```", "", t, flags=re.S)
    refs = set()
    for m in re.finditer(r"`([^`\n]+)`", prose):
        s = m.group(1).strip()
        if re.fullmatch(r"[\w./\\-]+\.(md|py|json|db)", s) or \
           re.fullmatch(r"(docs|data|src|skills|tools)/[\w./*-]*", s):
            refs.add(s)
    for r in sorted(refs):
        hit = (ROOT / r).exists() or bool(list(ROOT.rglob(pathlib.Path(r).name)))
        print(f"     {'✓' if hit else '✗'} {r}")
        failed += 0 if hit else 1

    print("\nR3  它让你跑的脚本")
    scripts = sorted({m for m in re.findall(r"tools[\\/][\w.]+\.py", t)} |
                     {m for m in re.findall(r"data[\\/][\w.\-/]+\.py", t)})
    if not scripts:
        print("     ✗ 没提到任何脚本")
        failed += 1
    for s in scripts:
        p = ROOT / s.replace("\\", "/")
        print(f"     {'✓' if p.exists() else '✗'} {s}")
        failed += 0 if p.exists() else 1

    print("\nR4  结构")
    fences = t.count("```")
    print(f"     代码围栏 ``` 出现 {fences} 次 → {'✓ 成对' if fences % 2 == 0 else '✗ 不成对'}")
    failed += 0 if fences % 2 == 0 else 1
    prev = 0
    for h, title in re.findall(r"^(#{1,3})\s+(.*)$", t, re.M):
        lvl = len(h)
        flag = "" if lvl <= prev + 1 else "  ✗ 跳级"
        if flag:
            failed += 1
        print(f"       {'  ' * (lvl - 1)}{title}{flag}")
        prev = lvl
    nsec = len(re.findall(r"^###\s+\d\.\d", t, re.M))
    print(f"     §1 子节 {nsec} 个 → {'✓' if nsec == 4 else '✗ 应为 4'}")
    failed += 0 if nsec == 4 else 1
    # 顶层小节编号必须连续 0..N
    tops = [int(n) for n in re.findall(r"^##\s+(\d+)\.", t, re.M)]
    expect = list(range(len(tops)))
    print(f"     顶层小节 {tops} → {'✓ 连续' if tops == expect else f'✗ 应为 {expect}'}")
    failed += 0 if tops == expect else 1

    print("\nR5  自指「第 N 节」")
    for n in sorted(set(re.findall(r"第\s*(\d)\s*节", t))):
        ok = bool(re.search(rf"^##\s+{n}\.", t, re.M))
        print(f"     {'✓' if ok else '✗'} 第 {n} 节")
        failed += 0 if ok else 1

    print()
    print("✓ AGENTS.md 自检通过" if not failed else f"✗ 自检失败 {failed} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
