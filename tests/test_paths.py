"""paths：内容寻址、硬链接投影、抽样校验与引用计数 GC。"""

from __future__ import annotations

import pytest

from academic_search import paths


def _unlock(root):
    for entity in list(paths.iter_bytes(root)):
        paths.make_writable(entity)


def test_byte_path_layout(tmp_path):
    sha = "ab" + "0" * 62
    assert paths.byte_path(tmp_path, sha) == tmp_path / "bytes" / "ab" / sha


def test_store_bytes_dedupes(tmp_path):
    a = paths.store_bytes(tmp_path, b"hello", readonly=False)
    b = paths.store_bytes(tmp_path, b"hello", readonly=False)
    assert a == b
    assert len(list(paths.iter_bytes(tmp_path))) == 1


def test_store_bytes_returns_sha(tmp_path):
    import hashlib

    digest = paths.store_bytes(tmp_path, b"hello", readonly=False)
    assert digest == hashlib.sha256(b"hello").hexdigest()


def test_store_file_matches_store_bytes(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    assert paths.store_file(tmp_path, src, readonly=False) == paths.store_bytes(
        tmp_path, b"payload", readonly=False
    )


def test_store_bytes_readonly_then_writable(tmp_path):
    digest = paths.store_bytes(tmp_path, b"x", readonly=True)
    entity = paths.byte_path(tmp_path, digest)
    paths.make_writable(entity)
    entity.unlink()
    assert not entity.exists()


def test_project_bytes_hardlinks(tmp_path):
    digest = paths.store_bytes(tmp_path, b"figure", readonly=False)
    dest = paths.work_dir(tmp_path, "wk") / "figures" / "f.png"
    linked = paths.project_bytes(tmp_path, digest, dest)
    assert dest.read_bytes() == b"figure"
    if linked:
        # 硬链接：改写副本即改写实体
        dest.write_bytes(b"changed")
        assert paths.byte_path(tmp_path, digest).read_bytes() == b"changed"


def test_verify_bytes_detects_tamper(tmp_path):
    digest = paths.store_bytes(tmp_path, b"good", readonly=False)
    assert paths.verify_bytes(tmp_path, sample=0) == []
    entity = paths.byte_path(tmp_path, digest)
    paths.make_writable(entity)
    entity.write_bytes(b"evil")
    bad = paths.verify_bytes(tmp_path, sample=0)
    assert len(bad) == 1
    assert bad[0]["expected"] == digest
    assert bad[0]["actual"] != digest


def test_verify_reports_missing_when_none(tmp_path):
    assert paths.verify_bytes(tmp_path, sample=0) == []


def test_gc_bytes_removes_unreferenced(tmp_path):
    keep = paths.store_bytes(tmp_path, b"keep", readonly=False)
    drop = paths.store_bytes(tmp_path, b"drop", readonly=False)
    removed = paths.gc_bytes(tmp_path, {keep})
    assert len(removed) == 1
    assert paths.byte_path(tmp_path, keep).exists()
    assert not paths.byte_path(tmp_path, drop).exists()


def test_gc_dry_run_keeps_files(tmp_path):
    drop = paths.store_bytes(tmp_path, b"drop", readonly=False)
    removed = paths.gc_bytes(tmp_path, set(), dry_run=True)
    assert len(removed) == 1
    assert paths.byte_path(tmp_path, drop).exists()
    paths.make_writable(paths.byte_path(tmp_path, drop))


def test_hardlink_supported(tmp_path):
    assert paths.hardlink_supported(tmp_path) is True


def test_is_hex_sha256():
    assert paths.is_hex_sha256("a" * 64) is True
    assert paths.is_hex_sha256("a" * 63) is False
    assert paths.is_hex_sha256("z" * 64) is False
