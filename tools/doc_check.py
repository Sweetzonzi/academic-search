#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""文档检查器 —— academic-search 文档的引用完整性与自包含性。

对应 AGENTS.md 第 4 节。用法（在 academic-search/ 下）：

    $env:PYTHONIOENCODING='utf-8'
    & 'D:\anaconda3\python.exe' tools\doc_check.py          # A+B+C+D 全部检查
    & 'D:\anaconda3\python.exe' tools\doc_check.py --refs   # 只查 A 链接 / B 引用 / D 路径
    & 'D:\anaconda3\python.exe' tools\doc_check.py --self   # 只查 C 自包含性

体系（docs/00-文档体系.md）：
  - 每篇文档在 front-matter 里声明 doc-id 与 kind；
  - 跨文档引用写成 「DOCID §x.y」，或裸的 ADR-NNNN / EX-YYYYMMDD-...；
  - 追加式记录（kind = decision / experiment）允许历史措辞，
    自包含性检查只覆盖规范与过程类。

能证明什么：链接是否解析、DOCID §x.y 是否指向存在的小节、文档点名的 .py 是否在、缺陷词汇出现在哪。
证明不了什么（必须人读）：语义漂移、框架是否仍在、结论是否有实测支撑。
退出码：有硬错误（断链 / 悬空引用）时 1，否则 0。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent          # academic-search/
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"

# 扫“被删除的上一版”留下的措辞。刻意宁可多报 —— 需要人判定。
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
LEGIT_CONTEXT = re.compile(r"否决|风险登记册|迁移|备选|踩过的坑|判据|记录类")

# 只有规范 / 过程类做自包含性检查；决策与实验是追加式记录，允许历史措辞。
C_KINDS = {"normative", "runbook"}

FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
REF = re.compile(r"(?<![A-Za-z0-9-])([A-Za-z][A-Za-z0-9._-]*)\s*§\s*(\d+(?:\.\d+)*)")
BARE = re.compile(r"(?<![A-Za-z0-9-])(ADR-\d{4}|EX-\d{4}-\d{2}-\d{2}-[A-Za-z0-9-]+)(?![A-Za-z0-9-])")

# D 段：文档里点名的仓库内 .py 路径（行内代码、代码块、正文都算）
PYREF = re.compile(r"(?<![\w./\\:-])((?:[\w.-]+[/\\])+[\w.-]+\.py)(?![\w])")
URLRE = re.compile(r"https?://\S+")


def markdown_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for p in sorted(DOCS.rglob("*.md")):
        if ARCHIVE in p.parents:
            continue
        out.append(p)
    for name in ("AGENTS.md", "README.md"):
        p = ROOT / name
        if p.exists():
            out.append(p)
    return out


def load(p: pathlib.Path) -> tuple[str, dict[str, str]]:
    text = p.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    m = FM.match(text)
    if m:
        for line in m.group(1).split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"').strip("'")
    return text, meta


def section_ids(text: str) -> set[str]:
    return set(re.findall(r"^#{2,4}\s+(\d+(?:\.\d+)*)\.?\s", text, re.M))


def rel(p: pathlib.Path) -> str:
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def check_links(files: list[pathlib.Path]) -> int:
    print("=" * 88)
    print("A. 相对链接")
    print("=" * 88)
    bad = 0
    for doc in files:
        for i, line in enumerate(doc.read_text(encoding="utf-8").split("\n"), 1):
            for m in re.finditer(r"!?\[[^\]]*\]\(([^)]+)\)", line):
                target = m.group(1)
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                path_part = urllib.parse.unquote(target.split("#")[0])
                if not path_part or path_part.startswith("#"):
                    continue
                if not (doc.parent / path_part).resolve().exists():
                    if path_part.startswith("figures/"):
                        print(f"  ~ {rel(doc)}:{i}  {target}  （正文示例，非仓库链接）")
                        continue
                    bad += 1
                    print(f"  ✗ 断链 {rel(doc)}:{i}  {target}")
    print(f"  → 断链 {bad} 处")
    return bad


def check_sections(files: list[pathlib.Path]) -> int:
    print()
    print("=" * 88)
    print("B. 跨文档引用（DOCID §x.y 与 ADR / EX 编号）")
    print("=" * 88)

    by_id: dict[str, tuple[pathlib.Path, set[str]]] = {}
    for d in files:
        _text, meta = load(d)
        did = meta.get("doc-id")
        if did:
            by_id[did] = (d, section_ids(_text))

    bad = 0
    checked = 0
    for doc in files:
        text = doc.read_text(encoding="utf-8")
        for i, line in enumerate(text.split("\n"), 1):
            for m in REF.finditer(line):
                did, sec = m.group(1), m.group(2)
                checked += 1
                if did not in by_id:
                    bad += 1
                    print(f"  ✗ {rel(doc)}:{i}  «{m.group(0)}»  文档 ID {did} 不存在")
                elif sec not in by_id[did][1]:
                    bad += 1
                    print(f"  ✗ {rel(doc)}:{i}  «{m.group(0)}»  {rel(by_id[did][0])} 里没有 §{sec}")
            for m in BARE.finditer(line):
                checked += 1
                if m.group(1) not in by_id:
                    bad += 1
                    print(f"  ✗ {rel(doc)}:{i}  «{m.group(1)}»  没有对应的决策/实验文档")
    print(f"  → 检查 {checked} 条，悬空 {bad} 条")
    return bad


def top_dirs() -> set[str]:
    """本仓库的顶层目录。运行期目录见 README「目录约定」，可能尚未创建，固定列入。"""
    runtime = {"data", "bytes", "works"}
    return runtime | {p.name for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")}


def py_paths_in(line: str) -> list[str]:
    """一行里应当存在于本仓库的 .py 路径（剔除 URL、模板写法与外部仓库的路径）。"""
    tops = top_dirs()
    out: list[str] = []
    for m in PYREF.finditer(URLRE.sub(" ", line)):
        token = m.group(1)
        if any(c in token for c in "<>*?${}"):
            continue
        if token.replace("\\", "/").split("/")[0] not in tops:
            continue              # 首段不是本仓库的顶层目录 —— 指的是别的仓库
        out.append(token)
    return out


def check_py_paths(files: list[pathlib.Path]) -> int:
    print()
    print("=" * 88)
    print("D. 文档点名的 .py 路径")
    print("=" * 88)
    bad = 0
    checked = 0
    for doc in files:
        for i, line in enumerate(doc.read_text(encoding="utf-8").split("\n"), 1):
            for token in py_paths_in(line):
                checked += 1
                if not (ROOT / token.replace("\\", "/")).exists():
                    bad += 1
                    print(f"  ✗ {rel(doc)}:{i}  {token}  不存在")
    print(f"  → 检查 {checked} 条路径，不存在 {bad} 条")
    return bad


def check_selfcontainment(files: list[pathlib.Path]) -> int:
    print()
    print("=" * 88)
    print("C. 自包含性候选（只查规范/过程类；命中需逐条人读）")
    print("=" * 88)
    hits = 0
    for doc in files:
        text, meta = load(doc)
        if meta.get("kind") not in C_KINDS:
            continue
        rows = []
        for i, line in enumerate(text.split("\n"), 1):
            for pat, why in SELF_PATTERNS:
                for m in re.finditer(pat, line):
                    s, e = max(0, m.start() - 45), min(len(line), m.end() + 45)
                    legit = "合法历史?" if LEGIT_CONTEXT.search(line) else ""
                    rows.append((i, m.group(0), why, line[s:e].strip(), legit))
        if rows:
            print(f"\n  {rel(doc)}")
            for i, hit, why, ctx, legit in rows:
                hits += 1
                print(f"    [{i:>4}] «{hit}» ({why}) {legit}")
                print(f"           …{ctx}…")
    print(f"\n  → 候选 {hits} 处。**全部需人读**，不要机械清零。")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="academic-search 文档检查器")
    ap.add_argument("--refs", action="store_true", help="只查链接、引用与路径")
    ap.add_argument("--self", dest="selfonly", action="store_true", help="只查自包含性")
    args = ap.parse_args()

    files = markdown_files()
    if not files:
        print(f"找不到文档；DOCS 解析为 {DOCS}", file=sys.stderr)
        return 2

    errors = 0
    if not args.selfonly:
        errors += check_links(files)
        errors += check_sections(files)
        errors += check_py_paths(files)
    if not args.refs:
        check_selfcontainment(files)

    print()
    if errors:
        print(f"✗ 硬错误 {errors} 处（断链 / 悬空引用 / 路径不存在）")
    else:
        print("✓ 链接、引用与脚本路径全部解析")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
