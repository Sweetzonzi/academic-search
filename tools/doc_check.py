#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""文档检查器 —— academic-search 文档的自包含性与引用完整性。

对应 AGENTS.md 第 4 节。用法（在 academic-search/ 下）：

    $env:PYTHONIOENCODING='utf-8'
    & 'D:\anaconda3\python.exe' tools\doc_check.py          # 全部检查
    & 'D:\anaconda3\python.exe' tools\doc_check.py --refs   # 只查引用
    & 'D:\anaconda3\python.exe' tools\doc_check.py --self   # 只查自包含性

**能证明什么**：链接是否解析、`NN §x.y` 引用的小节是否存在、缺陷词汇出现在哪。

**证明不了什么**（必须人读）：
  - 语义漂移 —— 小节存在，但内容已不是当初引用的那个；
  - 缺陷词汇 vs. 定义 —— 见下方 SELF_PATTERNS 的说明；
  - 结论是否有实测支撑。

退出码：有硬错误（断链 / 悬空 § 引用）时 1，否则 0。自包含性命中只是**复核队列**，不影响退出码。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent          # academic-search/
DOCS = [ROOT / "README.md"] + sorted((ROOT / "docs").glob("0*.md")) + [
    ROOT / "docs" / "参考资料-公开API事实.md"
]
# docs/archive/ 故意排除：它保留的是当时的样子（AGENTS.md 第 2 节）

# 扫“被删除的上一版”留下的措辞。刻意宁可多报 —— 需要人判定。
# 例：本表报出的「本版」多半是「文本版」的一部分；「推翻」在
# “否决某方案”的语境里是合法的。工具只负责把候选行摆到人面前。
SELF_PATTERNS: list[tuple[str, str]] = [
    (r"(?<!文)本版|上一版|前一版|前版|旧版|早期版本", "版本间指代"),
    (r"原\s*[DQ]\d+|原方案|原设计", "提名了可能已不存在的编号/方案"),
    (r"推翻|已在同日|已被删", "声称推翻了某个先前结论"),
    (r"修订建议|口径的更正|勘误|修正\s*§|旧记录", "对旧文本的更正"),
    (r"起因[:：]|讨论中|对话里提到|我们(?:之前|讨论|决定)|你(?:说|提到|问)过", "指代未记录的对话"),
    (r"第一版|第二版|上一轮|这一轮(?!的)|本轮之前", "指代迭代轮次"),
    (r"此前(?![的])", "指代未记录的时序"),
]

# 这些是“应当保留的历史”，出现缺陷词也合法（AGENTS.md 第 2 节）
LEGIT_CONTEXT = re.compile(r"否决|风险登记册|迁移|备选|踩过的坑|判据")


def markdown_files() -> list[pathlib.Path]:
    return [d for d in DOCS if d.exists()]


def check_links() -> int:
    """相对链接是否解析。返回错误数。"""
    print("=" * 88)
    print("A. 相对链接")
    print("=" * 88)
    bad = 0
    for doc in markdown_files():
        for i, line in enumerate(doc.read_text(encoding="utf-8").split("\n"), 1):
            for m in re.finditer(r"!?\[[^\]]*\]\(([^)]+)\)", line):
                target = m.group(1)
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                path_part = urllib.parse.unquote(target.split("#")[0])
                if not path_part or path_part.startswith("#"):
                    continue
                if not (doc.parent / path_part).resolve().exists():
                    # 正文里展示的产物片段（如 paper.md 里的 figures/x.png）不是仓库链接
                    if path_part.startswith("figures/"):
                        print(f"  ~ {doc.name}:{i}  {target}  （正文示例，非仓库链接）")
                        continue
                    bad += 1
                    print(f"  ✗ 断链 {doc.name}:{i}  {target}")
    print(f"  → 断链 {bad} 处")
    return bad


def check_sections() -> int:
    """`NN §x.y` 引用的小节是否存在于目标文档。返回错误数。"""
    print()
    print("=" * 88)
    print("B. 跨文档 § 引用")
    print("=" * 88)

    by_num: dict[str, tuple[pathlib.Path, set[str]]] = {}
    for d in markdown_files():
        m = re.match(r"(\d\d)-", d.name)
        if m:
            ids = set(re.findall(r"^#{2,4}\s+(\d+(?:\.\d+)*)\.?\s", d.read_text(encoding="utf-8"), re.M))
            by_num[m.group(1)] = (d, ids)

    bad = 0
    checked = 0
    for doc in markdown_files():
        for i, line in enumerate(doc.read_text(encoding="utf-8").split("\n"), 1):
            for m in re.finditer(r"(?<![\dA-Za-z])(\d{2})[\s、]{0,2}§\s*(\d+(?:\.\d+)*)", line):
                dnum, sec = m.group(1), m.group(2)
                checked += 1
                if dnum not in by_num:
                    bad += 1
                    print(f"  ✗ {doc.name}:{i}  «{m.group(0)}»  文档号 {dnum} 不存在")
                elif sec not in by_num[dnum][1]:
                    bad += 1
                    print(f"  ✗ {doc.name}:{i}  «{m.group(0)}»  {by_num[dnum][0].name} 里没有 §{sec}")
    print(f"  → 检查 {checked} 条，悬空 {bad} 条")
    return bad


def check_selfcontainment() -> int:
    """缺陷词汇复核队列。返回命中数（非错误）。"""
    print()
    print("=" * 88)
    print("C. 自包含性候选（缺陷词汇命中；需逐条人读判定）")
    print("=" * 88)
    hits = 0
    for doc in markdown_files():
        rows = []
        for i, line in enumerate(doc.read_text(encoding="utf-8").split("\n"), 1):
            for pat, why in SELF_PATTERNS:
                for m in re.finditer(pat, line):
                    s, e = max(0, m.start() - 45), min(len(line), m.end() + 45)
                    legit = "合法历史?" if LEGIT_CONTEXT.search(line) else ""
                    rows.append((i, m.group(0), why, line[s:e].strip(), legit))
        if rows:
            print(f"\n  {doc.name}")
            for i, hit, why, ctx, legit in rows:
                hits += 1
                print(f"    [{i:>4}] «{hit}» ({why}) {legit}")
                print(f"           …{ctx}…")
    print(f"\n  → 候选 {hits} 处。**全部需人读**，不要机械清零。")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="academic-search 文档检查器")
    ap.add_argument("--refs", action="store_true", help="只查链接与 § 引用")
    ap.add_argument("--self", dest="selfonly", action="store_true", help="只查自包含性")
    args = ap.parse_args()

    if not markdown_files():
        print(f"找不到文档；ROOT 解析为 {ROOT}", file=sys.stderr)
        return 2

    errors = 0
    if not args.selfonly:
        errors += check_links()
        errors += check_sections()
    if not args.refs:
        check_selfcontainment()

    print()
    if errors:
        print(f"✗ 硬错误 {errors} 处（断链 / 悬空引用）")
    else:
        print("✓ 链接与 § 引用全部解析")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
