"""安装器的文件系统验收：链接、复制迁移、备份与失败恢复。"""

import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "install_skill", Path(__file__).resolve().parents[1] / "tools/install_skill.py"
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "repo with spaces"
    source = root / "skills/academic-search"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("skill v1", encoding="utf-8")
    for name in installer.SUBDIRS:
        (source / name).mkdir()
        (source / name / "example.txt").write_text(name, encoding="utf-8")
    monkeypatch.setattr(installer, "ROOT", root)
    monkeypatch.setattr(installer, "SOURCE", source)
    return source, tmp_path / "host/skills/academic-search"


def test_link_observes_atomic_edits_and_added_files_without_reinstall(installation):
    source, dest = installation
    assert installer.main(["--dest", str(dest)]) == 0
    assert installer.is_link(dest)
    assert dest.resolve() == source
    assert not (source / "root.txt").exists()
    replacement = source / "replacement.md"
    replacement.write_text("skill v2", encoding="utf-8")
    replacement.replace(source / "SKILL.md")
    (source / "references/new.md").write_text("new", encoding="utf-8")
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "skill v2"
    assert (dest / "references/new.md").is_file()
    assert installer.main(["--dest", str(dest), "--check"]) == 0
    assert installer.main(["--dest", str(dest)]) == 0
    assert not installer.backup_dir(dest).exists()


def test_copy_migration_keeps_backup_and_checks_installation_type(installation):
    source, dest = installation
    assert installer.main(["--dest", str(dest), "--mode", "copy"]) == 0
    (dest / "local-notes.txt").write_text("keep me", encoding="utf-8")
    assert installer.main(["--dest", str(dest), "--mode", "copy", "--check"]) == 0
    assert installer.main(["--dest", str(dest), "--check"]) == 1
    assert installer.main(["--dest", str(dest)]) == 0
    backups = list(installer.backup_dir(dest).iterdir())
    assert len(backups) == 1
    assert (backups[0] / "local-notes.txt").read_text(encoding="utf-8") == "keep me"
    assert dest.resolve() == source


def test_switching_link_to_copy_does_not_delete_source(installation):
    source, dest = installation
    assert installer.main(["--dest", str(dest)]) == 0
    assert installer.main(["--dest", str(dest), "--mode", "copy", "--no-backup"]) == 0
    assert not installer.is_link(dest)
    assert (source / "SKILL.md").read_text(encoding="utf-8") == "skill v1"
    assert installer.main(["--dest", str(dest), "--mode", "copy", "--check"]) == 0


def test_dry_run_and_check_do_not_create_directories(installation):
    _, dest = installation
    assert installer.main(["--dest", str(dest), "--check"]) == 1
    assert installer.main(["--dest", str(dest), "--dry-run"]) == 0
    assert not dest.parent.exists()


@pytest.mark.parametrize("relative", [".", "..", "references/nested"])
def test_rejects_destination_overlapping_source(installation, relative):
    source, _ = installation
    assert installer.main(["--dest", str(source / relative)]) == 1
    assert (source / "SKILL.md").is_file()


def test_other_binding_requires_explicit_rebind(installation):
    source, dest = installation
    dest.mkdir(parents=True)
    (dest / "root.txt").write_text(str(source.parent / "another-repo"), encoding="utf-8")
    assert installer.main(["--dest", str(dest)]) == 1
    assert not installer.is_link(dest)
    assert installer.main(["--dest", str(dest), "--allow-rebind"]) == 0
    assert dest.resolve() == source


def test_other_link_requires_explicit_rebind(installation, monkeypatch):
    source, dest = installation
    other = source.parent / "other"
    other.mkdir()
    dest.parent.mkdir(parents=True)
    with monkeypatch.context() as patch:
        patch.setattr(installer, "SOURCE", other)
        installer.create_link(dest)
    assert installer.main(["--dest", str(dest)]) == 1
    assert dest.resolve() == other
    assert installer.main(["--dest", str(dest), "--allow-rebind", "--no-backup"]) == 0
    assert other.is_dir()
    assert (source / "SKILL.md").is_file()


def test_failed_replacement_restores_old_installation(installation, monkeypatch):
    _, dest = installation
    assert installer.main(["--dest", str(dest), "--mode", "copy"]) == 0
    original_rename = Path.rename

    def fail_publish(path, target):
        if path.parent.name.startswith(".academic-search-"):
            raise OSError("simulated publish failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_publish)
    assert installer.main(["--dest", str(dest)]) == 1
    assert not installer.is_link(dest)
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "skill v1"
    assert not list(dest.parent.glob(".academic-search-*"))


def test_creation_failure_preserves_old_copy(installation, monkeypatch):
    _, dest = installation
    assert installer.main(["--dest", str(dest), "--mode", "copy"]) == 0

    def fail_create(path):
        raise OSError("simulated link permission failure")

    monkeypatch.setattr(installer, "create_link", fail_create)
    assert installer.main(["--dest", str(dest)]) == 1
    assert not installer.is_link(dest)
    assert (dest / "SKILL.md").is_file()


def test_destination_is_required_before_any_installation(installation, capsys):
    source, dest = installation
    with pytest.raises(SystemExit) as error:
        installer.main([])
    assert error.value.code == 2
    assert "--dest" in capsys.readouterr().err
    assert not dest.parent.exists()
    assert (source / "SKILL.md").is_file()
