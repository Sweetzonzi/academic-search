#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""盘点 data/spike-m1.5/ 的遗留物。

问题：
  1. 有没有脚本从没被任何东西引用过（孤儿）？
  2. 有没有同一逻辑的两个版本（编号重复 / 内容近似）？
  3. 有没有文档引用了但已不存在的脚本？
  4. 各脚本的输入输出目录是否都还在？
输出：待清理候选 + 依据。不做任何删除。
"""
from __future__ import annotations

import difflib
import hashlib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent      # academic-search/
SPIKE = ROOT / "data" / "spike-m1.5"
DOCS = [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md")) + [ROOT / "AGENTS.md"]

doc_text = "\n".join(d.read_text(encoding="utf-8") for d in DOCS if d.exists())

scripts = sorted(p.name for p in SPIKE.glob("*.py"))
print(f"脚本 {len(scripts)} 个\n")

# --- 1. 谁被引用 ---
print("=" * 84)
print("1. 脚本被引用情况（引用 = 出现在文档里）")
print("=" * 84)
orphans = []
for s in scripts:
    cited_doc = s in doc_text
    # 脚本之间互相引用（import / subprocess 调用 / 注释提及）
    cited_code = []
    for other in scripts:
        if other == s:
            continue
        t = (SPIKE / other).read_text(encoding="utf-8", errors="replace")
        if s in t:
            cited_code.append(other)
    tag = []
    if cited_doc:
        tag.append("文档")
    if cited_code:
        tag.append("脚本:" + ",".join(cited_code))
    if not tag:
        orphans.append(s)
    print(f"  {'○' if tag else '⚠ 孤儿'}  {s:<28} {' '.join(tag) if tag else '（无任何引用）'}")

# --- 2. 编号重复 ---
print()
print("=" * 84)
print("2. 编号重复与内容近似")
print("=" * 84)
bynum: dict[str, list[str]] = {}
for s in scripts:
    m = re.match(r"(\d+)_", s)
    if m:
        bynum.setdefault(m.group(1), []).append(s)
for num, group in sorted(bynum.items()):
    if len(group) > 1:
        print(f"  ⚠ 编号 {num} 重复：{group}")
        a, b = (SPIKE / group[0]).read_text(encoding="utf-8", errors="replace"), \
               (SPIKE / group[1]).read_text(encoding="utf-8", errors="replace")
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        print(f"      体积 {len(a)} vs {len(b)} 字节；相似度 {ratio:.0%}")
        print(f"      {group[0]} 首行: {a.splitlines()[0][:70] if a.splitlines() else ''}")
        print(f"      {group[1]} 首行: {b.splitlines()[0][:70] if b.splitlines() else ''}")

# --- 3. 文档引用但不存在的脚本 ---
print()
print("=" * 84)
print("3. 文档提到但目录里没有的脚本")
print("=" * 84)
mentioned = set(re.findall(r"`?(\d\d_[a-z_0-9]+\.py)`?", doc_text))
missing = sorted(m for m in mentioned if m not in scripts)
print("  " + ("无" if not missing else "\n  ".join(missing)))

# --- 4. 脚本引用的输入 ---
print()
print("=" * 84)
print("4. 目录内容")
print("=" * 84)
for d in sorted(SPIKE.iterdir()):
    if d.is_dir():
        n = sum(1 for _ in d.rglob("*") if _.is_file())
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        print(f"  {d.name:<28} {n:>5} 文件  {size/1024/1024:>8.1f} MB")
    else:
        print(f"  {d.name:<28}       {d.stat().st_size/1024:>8.1f} KB")

print()
print(f"→ 孤儿脚本：{orphans if orphans else '无'}")
