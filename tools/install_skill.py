#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""把 skills/academic-search/ 链接到宿主的 skill 目录。

安装的是薄 bundle：`SKILL.md`、`references/` 与 `scripts/`；代码与数据仍在仓库
（规则见 skills/academic-search/references/setup.md §1）。复制模式另写本机绑定 `root.txt`。
默认链接整个 bundle，仓库更新直接可见；--mode copy 用于不支持链接的宿主。

用法（在 academic-search/ 下）：

    $env:PYTHONIOENCODING='utf-8'
    & 'D:\anaconda3\python.exe' tools\install_skill.py --dest <安装目录>
    & 'D:\anaconda3\python.exe' tools\install_skill.py --dest <安装目录> --check
    & 'D:\anaconda3\python.exe' tools\install_skill.py --dest <安装目录> --dry-run

安装者按宿主实际扫描机制指定 --dest，值是完整的技能安装目录（含 academic-search），
不是它的父目录。安装器不选择宿主或默认目录。

能证明什么：安装目录链接到本仓库；复制模式下受管文件一致、绑定正确。
证明不了什么：宿主是否扫描该目录，以及当前会话是否已刷新技能目录。

退出码：0 = 已一致 / 已更新；1 = --check 发现漂移、绑定冲突或出错。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import pathlib
import shutil
import subprocess
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


def backup_dir(dest: pathlib.Path) -> pathlib.Path:
    """备份放在扫描根之外的 skills.backup/，避免被当成 skill。"""
    return dest.parent.parent / (dest.parent.name + ".backup")


def fresh_backup(dest: pathlib.Path, stamp: str) -> pathlib.Path:
    """同一秒内多次同步时不覆盖前一份备份。"""
    base = backup_dir(dest) / (dest.name + "-" + stamp)
    if not os.path.lexists(base):
        return base
    n = 2
    while True:
        candidate = base.with_name(base.name + "-" + str(n))
        if not os.path.lexists(candidate):
            return candidate
        n += 1


def report(case: str, rel: str) -> None:
    print("  " + case + "：" + rel)


def is_link(path: pathlib.Path) -> bool:
    """Python 3.11 没有 Path.is_junction；同时识别符号链接与 Windows 联接。"""
    if not os.path.lexists(path):
        return False
    return path.is_symlink() or getattr(path.lstat(), "st_reparse_tag", 0) == 0xA0000003


def validate_dest(dest: pathlib.Path) -> None:
    # 仅解析父目录，保留末级链接身份，避免把安装操作写进仓库源目录。
    if dest == SOURCE or dest in SOURCE.parents or SOURCE in dest.parents:
        raise ValueError("安装目录不能是技能源目录、其祖先或其子目录：" + str(dest))


def create_link(dest: pathlib.Path) -> None:
    if os.name == "nt":
        # Junction 不依赖符号链接权限；路径通过环境传入，避免 PowerShell -Command
        # 把 argv 重新解析为代码后拆开含空格、引号或 $ 的路径。
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:ACADEMIC_SEARCH_LINK_DEST "
             "-Target $env:ACADEMIC_SEARCH_LINK_SOURCE -ErrorAction Stop | Out-Null"],
            env=dict(os.environ, ACADEMIC_SEARCH_LINK_DEST=str(dest),
                     ACADEMIC_SEARCH_LINK_SOURCE=str(SOURCE)), capture_output=True,
        )
        if result.returncode:
            raise OSError("创建目录联接失败：" + result.stderr.decode(errors="replace"))
    else:
        dest.symlink_to(SOURCE, target_is_directory=True)


def run_check(dest: pathlib.Path, mode: str = "link") -> int:
    if mode == "link":
        print("源    ：" + str(SOURCE))
        print("安装  ：" + str(dest))
        if is_link(dest) and dest.resolve() == SOURCE and (dest / "SKILL.md").is_file():
            print("结论  ：已链接，仓库更新直接可见")
            return 0
        print("结论  ：未链接到本仓库，运行不带 --check 的命令安装")
        return 1
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
    if not (changed or missing or extra) and bound and pathlib.Path(bound) == ROOT:
        print("结论  ：一致（" + str(len(source)) + " 个受管文件）")
        return 0
    print("结论  ：有漂移，运行不带 --check 的命令同步")
    return 1


def remove_installation(path: pathlib.Path) -> None:
    """只删除安装项本身；目录联接与符号链接绝不递归进入目标。"""
    if path.is_symlink():
        path.unlink()
    elif is_link(path):
        path.rmdir()
    else:
        shutil.rmtree(path)


def run_install(dest: pathlib.Path, *, allow_rebind: bool, keep_backup: bool,
                dry_run: bool, mode: str = "link") -> int:
    validate_dest(dest)
    source = managed(SOURCE)
    bound = read_binding(dest)
    print("源    ：" + str(SOURCE))
    print("安装  ：" + str(dest))
    print("模式  ：" + mode)
    linked_elsewhere = is_link(dest) and dest.resolve() != SOURCE
    wrong_binding = bool(bound and pathlib.Path(bound).resolve() != ROOT)
    if (linked_elsewhere or wrong_binding) and not allow_rebind:
        print(
            "错误  ：现有链接或 root.txt 指向别的仓库；确认改绑时加 --allow-rebind。",
            file=sys.stderr,
        )
        return 1
    if mode == "link":
        if is_link(dest) and dest.resolve() == SOURCE:
            print("结论  ：已链接，无需写入")
            return 0
    elif dest.is_dir() and not is_link(dest):
        changed, missing, extra = diff(source, dest)
        if not (changed or missing or extra) and bound and not wrong_binding:
            print("结论  ：复制模式已一致，无需写入")
            return 0
    if dry_run:
        print("结论  ：--dry-run，未写入")
        return 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    # staging、安装项、备份位于同一卷；rename 只移动目录项，失败时可恢复。
    stage_root = pathlib.Path(tempfile.mkdtemp(prefix=".academic-search-", dir=dest.parent))
    staging = stage_root / dest.name
    backup = fresh_backup(dest, datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S"))
    try:
        if mode == "link":
            create_link(staging)
        else:
            staging.mkdir()
            for rel, path in source.items():
                target = staging / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            (staging / BINDING).write_text(str(ROOT) + "\n", encoding="utf-8", newline="\n")
        if os.path.lexists(dest):
            backup.parent.mkdir(parents=True, exist_ok=True)
            dest.rename(backup)
            print("备份  ：" + str(backup))
        try:
            staging.rename(dest)
        except OSError:
            if os.path.lexists(backup):
                backup.rename(dest)
            raise
    finally:
        if os.path.lexists(staging):
            remove_installation(staging)
        stage_root.rmdir()

    if not keep_backup and os.path.lexists(backup):
        # fresh_backup 的路径固定在扫描根的同级 skills.backup 下。
        if backup.parent.resolve() != backup_dir(dest).resolve():
            raise ValueError("备份路径超出预期目录")
        remove_installation(backup)
        print("备份  ：已按 --no-backup 删除")
    print("结论  ：已链接，仓库更新直接可见" if mode == "link" else
          "结论  ：已同步 " + str(len(source)) + " 个受管文件")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_skill.py",
        description="把 skills/academic-search/ 链接到宿主；--mode copy 可安装独立薄副本。",
    )
    parser.add_argument("--mode", choices=("link", "copy"), default="link", help="安装形态，默认 link")
    parser.add_argument("--dest", type=pathlib.Path, required=True,
                        help="完整的技能安装目录（含 academic-search），由安装者按宿主扫描位置指定")
    parser.add_argument("--check", action="store_true", help="只查链接目标或复制文件差异，不写")
    parser.add_argument("--dry-run", action="store_true", help="只打印将发生的增删改，不写")
    parser.add_argument("--allow-rebind", action="store_true", help="安装副本绑定到别的仓库时仍改绑到本仓库")
    parser.add_argument("--no-backup", action="store_true", help="同步成功后删除旧副本备份")
    args = parser.parse_args(argv)

    requested = args.dest.expanduser().absolute()
    dest = requested.parent.resolve() / requested.name
    try:
        validate_dest(dest)
        if args.check:
            return run_check(dest, args.mode)
        return run_install(
            dest, allow_rebind=args.allow_rebind, keep_backup=not args.no_backup,
            dry_run=args.dry_run, mode=args.mode,
        )
    except (OSError, ValueError) as exc:
        print("错误  ：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    # 中文文件名与输出在 Windows 控制台不因编码失败
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
