"""bytes/ 与 works/ 路径拼装、硬链接与只读、引用计数 GC。

契约：docs/接口/存储与迁移.md §2/§4、ADR-0033/0034。
路径一律现场拼，绝不落库（本模块不把路径交给 store）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from pathlib import Path
from typing import Iterable, Iterator

CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_hex_sha256(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def bytes_dir(root: str | Path) -> Path:
    return Path(root) / "bytes"


def byte_path(root: str | Path, sha256: str) -> Path:
    """bytes/<sha 前2位>/<sha256>。"""
    return bytes_dir(root) / sha256[:2] / sha256


def works_dir(root: str | Path) -> Path:
    return Path(root) / "works"


def work_dir(root: str | Path, work_key: str) -> Path:
    return works_dir(root) / work_key


def _chmod_readonly(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass


def make_writable(path: Path) -> None:
    if path.exists():
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


def store_bytes(root: str | Path, data: bytes, *, readonly: bool = True) -> str:
    """内容寻址落盘；相同字节只存一份。返回 sha256。"""
    digest = sha256_bytes(data)
    dest = byte_path(root, digest)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / (".tmp-" + os.urandom(6).hex())
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    if readonly:
        _chmod_readonly(dest)
    return digest


def store_file(root: str | Path, src: str | Path, *, readonly: bool = True) -> str:
    """把已有文件按内容寻址存进 bytes/（流式，不整份读入内存）。"""
    src_path = Path(src)
    hasher = hashlib.sha256()
    with src_path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            hasher.update(chunk)
    digest = hasher.hexdigest()
    dest = byte_path(root, digest)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / (".tmp-" + os.urandom(6).hex())
        shutil.copyfile(src_path, tmp)
        os.replace(tmp, dest)
    if readonly:
        _chmod_readonly(dest)
    return digest


def hardlink_or_copy(src: str | Path, dest: str | Path) -> bool:
    """优先硬链接；跨卷或不支持时降级为副本并返回 False。"""
    src_path, dest_path = Path(src), Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        make_writable(dest_path)
        dest_path.unlink()
    try:
        os.link(src_path, dest_path)
        return True
    except OSError:
        shutil.copyfile(src_path, dest_path)
        return False


def project_bytes(root: str | Path, sha256: str, dest: str | Path) -> bool:
    """把 bytes/ 里的实体投影到 works/（硬链接）。"""
    return hardlink_or_copy(byte_path(root, sha256), dest)


def same_volume(first: str | Path, second: str | Path) -> bool:
    try:
        a, b = Path(first), Path(second)
        a.mkdir(parents=True, exist_ok=True)
        b.mkdir(parents=True, exist_ok=True)
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def hardlink_supported(directory: str | Path) -> bool:
    base = Path(directory)
    base.mkdir(parents=True, exist_ok=True)
    probe = base / (".hardlink-probe-" + os.urandom(6).hex())
    link = base / (".hardlink-probe-link-" + os.urandom(6).hex())
    try:
        probe.write_bytes(b"x")
        os.link(probe, link)
        return True
    except OSError:
        return False
    finally:
        for p in (probe, link):
            if p.exists():
                make_writable(p)
                try:
                    p.unlink()
                except OSError:
                    pass


def iter_bytes(root: str | Path) -> Iterator[Path]:
    """产出 bytes/ 下的实体文件（跳过临时文件）。"""
    base = bytes_dir(root)
    if not base.is_dir():
        return
    for shard in sorted(base.iterdir()):
        if not shard.is_dir():
            continue
        for entity in sorted(shard.iterdir()):
            if entity.is_file() and not entity.name.startswith(".tmp-"):
                yield entity


def verify_bytes(root: str | Path, sample: int = 20, *, rng=None) -> list[dict]:
    """抽样重算 sha256 并与文件名比对；只读、不修复。"""
    entities = list(iter_bytes(root))
    if sample and sample > 0 and sample < len(entities):
        chooser = rng if rng is not None else __import__("random")
        entities = chooser.sample(entities, sample)
    mismatches: list[dict] = []
    for entity in entities:
        hasher = hashlib.sha256()
        with entity.open("rb") as handle:
            while True:
                chunk = handle.read(CHUNK)
                if not chunk:
                    break
                hasher.update(chunk)
        actual = hasher.hexdigest()
        if actual != entity.name:
            mismatches.append(
                {
                    "path": str(entity),
                    "expected": entity.name,
                    "actual": actual,
                    "sha256": entity.name,
                }
            )
    return mismatches


def gc_bytes(root: str | Path, referenced: Iterable[str], *, dry_run: bool = False) -> list[str]:
    """按引用计数 GC：referenced 之外的实体一律删除（先去掉只读）。"""
    keep = set(referenced)
    removed: list[str] = []
    for entity in iter_bytes(root):
        if entity.name in keep:
            continue
        removed.append(str(entity))
        if not dry_run:
            make_writable(entity)
            try:
                entity.unlink()
            except OSError:
                pass
    return removed
