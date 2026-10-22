#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""把 skills/academic-search/ 同步到宿主的 skill 目录（安装与更新）。

安装的是薄 bundle：代码与数据仍在仓库，宿主的 skill 目录里只有
`SKILL.md`、`references/`、`scripts/` 与本机绑定 `root.txt`
（规则见 skills/academic-search/references/setup.md §1）。
本脚本只同步前三项，**保留**安装副本里的 `root.txt`。

用法（在 academic-search/ 下）：

    $env:PYTHONIOENCODING='utf-8'
    & 'D:\anaconda3\python.exe' tools\install_skill.py            # 安装或更新
    & 'D:\anaconda3\python.exe' tools\install_skill.py --check    # 只查漂移，不写
    & 'D:\anaconda3\python.exe' tools\install_skill.py --dry-run  # 只说要做什么
    & 'D:\anaconda3\python.exe' tools\install_skill.py --dest <目录>

默认安装位置：$DSH_HOME/skills/academic-search；未设该变量时用 ~/.dsh/skills/academic-search。

能证明什么：受管文件与仓库逐字节一致、`root.txt` 指向本仓库、旧副本已备份。
证明不了什么：宿主是否扫描该目录（那是宿主配置）、agent 何时重新加载（正文每次读取，目录看 frontmatter 变化）。

退出码：0 = 已一致 / 已更新；1 = --check 发现漂移、绑定冲突或出错。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent          # academic-search/
SOURCE = ROOT / "skills" / "academic-search"
SUBDIRS = ("references", "scripts")
BINDING = "root.txt"


def managed(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """受管文件 → 仓库内绝对路径。刻意不含 `root.txt`：那是本机绑定，不属于仓库。"""
    out: dict[str, pathlib.Path] = {}
    skill = root / "SKILL.md"
    if not skill.exists():
        raise SystemExit("仓库里找不到 " + str(skill))
    out["SKILL.md"] = skill
    for name in SUBDIRS:
        base = root / name
        if not base.exists():
            raise SystemExit("仓库里找不到 " + str(base))
        for path in sorted(base.rglob("*")):
            if path.is_file():
                out[path.relative_to(root).as_posix()] = path
    return out


def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def managed_names() -> list[str]:
    names = ["SKILL.md"]
    for name in SUBDIRS:
        base = SOURCE / name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                names.append(path.relative_to(SOURCE).as_posix())
    return names


def diff(source: dict[str, pathlib.Path], dest: pathlib.Path) -> tuple[list[str], list[str], list[str]]:
    """返回 (内容不同, 缺失, 多余)。多余只按受管名单判断，`root.txt` 不算多余。"""
    changed: list[str] = []
    missing: list[str] = []
    names = managed_names()
    for rel in names:
        target = dest / rel
        if not target.is_file():
            missing.append(rel)
        elif digest(target) != digest(source[rel]):
            changed.append(rel)
    extra: list[str] = []
    if dest.exists():
        for name in SUBDIRS:
            base = dest / name
            if not base.exists():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(dest).as_posix()
                    if rel not in source:
                        extra.append(rel)
    return changed, missing, extra


def read_binding(dest: pathlib.Path) -> str | None:
    path = dest / BINDING
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            return line.strip()
    return ""


def default_dest() -> pathlib.Path:
    home = os.environ.get("DSH_HOME") or str(pathlib.Path.home() / ".dsh")
    return pathlib.Path(home) / "skills" / "academic-search"


def backup_dir(dest: pathlib.Path) -> pathlib.Path:
    """备份放在扫描根之外：宿主只扫 `<dshHome>/skills`，备份落在 `skills.backup/` 不会被当成 skill。"""
    return dest.parent.parent / (dest.parent.name + ".backup")


def fresh_backup(dest: pathlib.Path, stamp: str) -> pathlib.Path:
    """同一秒内多次同步时不覆盖前一份备份。"""
    base = backup_dir(dest) / (dest.name + "-" + stamp)
    if not base.exists():
        return base
    n = 2
    while True:
        candidate = base.with_name(base.name + "-" + str(n))
        if not candidate.exists():
            return candidate
        n += 1


def report(case: str, rel: str) -> None:
    print("  " + case + "：" + rel)


def run_check(dest: pathlib.Path) -> int:
    source = managed(SOURCE)
    changed, missing, extra = diff(source, dest)
    bound = read_binding(dest)
    print("源    ：" + str(SOURCE))
    print("安装  ：" + str(dest) + ("" if dest.exists() else "（不存在）"))
    print("绑定  ：" + (bound if bound else "（无 root.txt）"))
    if not dest.exists():
        print("结论  ：未安装，运行不带 --check 的命令安装")
        return 1
    for rel in changed:
        report("内容不同", rel)
    for rel in missing:
        report("缺失", rel)
    for rel in extra:
        report("多余", rel)
    if bound and pathlib.Path(bound) != ROOT:
        print("  " + "绑定指向别的仓库：" + bound + "（换绑要显式改 root.txt）")
    if not (changed or missing or extra) and (not bound or pathlib.Path(bound) == ROOT):
        print("结论  ：一致（" + str(len(source)) + " 个受管文件）")
        return 0
    print("结论  ：有漂移，运行不带 --check 的命令同步")
    return 1


def run_install(dest: pathlib.Path, *, allow_rebind: bool, keep_backup: bool, dry_run: bool) -> int:
    source = managed(SOURCE)
    changed, missing, extra = diff(source, dest)
    bound = read_binding(dest)
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    backup = fresh_backup(dest, stamp)

    print("源    ：" + str(SOURCE))
    print("安装  ：" + str(dest))
    bound_note = bound if bound else "（将写入 " + str(ROOT) + "）"
    if bound and pathlib.Path(bound) != ROOT:
        bound_note += "（将改绑为 " + str(ROOT) + "）" if allow_rebind else "（绑定异常）"
    print("绑定  ：" + bound_note)
    for rel in changed:
        report("更新", rel)
    for rel in missing:
        report("新增", rel)
    for rel in extra:
        report("删除", rel)
    if not bound:
        report("新增", BINDING)
    if bound and pathlib.Path(bound) != ROOT and not allow_rebind:
        print(
            "错误  ：安装副本绑定到另一个仓库（" + bound + "）。\n"
            "        确认要改绑时加 --allow-rebind，或直接改该副本的 root.txt。",
            file=sys.stderr,
        )
        return 1
    if not (changed or missing or extra) and bound == str(ROOT):
        print("结论  ：已一致，无需写入")
        return 0
    if dry_run:
        print("结论  ：--dry-run，未写入")
        return 0

    staging = pathlib.Path(tempfile.mkdtemp(prefix="academic-search-skill-"))
    try:
        for rel, path in source.items():
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        (staging / BINDING).write_text(str(ROOT) + "\n", encoding="utf-8", newline="\n")
        if dest.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                shutil.rmtree(backup)
            shutil.move(str(dest), str(backup))
            print("备份  ：" + str(backup))
        shutil.move(str(staging), str(dest))
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    if not keep_backup and backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
        print("备份  ：已按 --no-backup 删除")
    print("结论  ：已同步 " + str(len(source)) + " 个受管文件")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_skill.py",
        description="把 skills/academic-search/ 同步到宿主的 skill 目录（保留 root.txt 绑定）。",
    )
    parser.add_argument("--dest", type=pathlib.Path, default=None, help="安装目录，默认 $DSH_HOME/skills/academic-search")
    parser.add_argument("--check", action="store_true", help="只比差异，不写；一致时退出 0")
    parser.add_argument("--dry-run", action="store_true", help="只打印将发生的增删改，不写")
    parser.add_argument("--allow-rebind", action="store_true", help="安装副本绑定到别的仓库时仍改绑到本仓库")
    parser.add_argument("--no-backup", action="store_true", help="同步成功后删除旧副本备份")
    args = parser.parse_args(argv)

    dest = (args.dest or default_dest()).expanduser().resolve()
    if args.check:
        return run_check(dest)
    return run_install(
        dest, allow_rebind=args.allow_rebind, keep_backup=not args.no_backup, dry_run=args.dry_run
    )


if __name__ == "__main__":
    # 中文文件名与输出在 Windows 控制台不因编码失败
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
