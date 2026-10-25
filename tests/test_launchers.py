"""启动器的进程级验收：隔离代码副本、环境与数据库，不访问真实网络或凭据。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]
GIT_SH = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
MODES = ["cmd"] if os.name == "nt" else ["sh"]
if os.name == "nt" and GIT_SH.is_file():
    MODES.append("git-sh")


@pytest.fixture(params=MODES)
def launch(request, tmp_path):
    """安装到仓库外，从无关 cwd 调用；不依赖宿主 PATH、root 或 Python 配置。"""
    mode = request.param
    root = tmp_path / "repo with spaces"
    shutil.copytree(REPO / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    bundle = tmp_path / "installed skill"
    shutil.copytree(REPO / "skills/academic-search", bundle,
                    ignore=shutil.ignore_patterns("root.txt", "__pycache__"))
    cwd = tmp_path / "unrelated cwd"
    cwd.mkdir()
    bins = tmp_path / "bin"
    bins.mkdir()
    env = dict(os.environ)
    for name in ("ACADEMIC_SEARCH_ROOT", "ACADEMIC_SEARCH_PYTHON", "PYTHONPATH"):
        env.pop(name, None)
    env.update(OPENALEX_API_KEY="isolated-secret-do-not-print", PYTHONIOENCODING="utf-8",
               PYTHONDONTWRITEBYTECODE="1")
    if mode == "cmd":
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    elif mode == "git-sh":
        env["PATH"] = str(GIT_SH.parent.parent / "usr/bin")
        env["BASH_ENV"] = ""
    else:
        env["PATH"] = "/usr/bin:/bin"
        env.pop("ENV", None)

    def shell_path(path):
        value = str(path).replace("\\", "/")
        if mode == "git-sh" and len(value) > 1 and value[1] == ":":
            return "/" + value[0].lower() + value[2:]
        return value

    env["ACADEMIC_SEARCH_PYTHON"] = shell_path(sys.executable)
    (bundle / "root.txt").write_text(shell_path(root) + "\n", encoding="utf-8")

    def run(*args, **changes):
        current = dict(env)
        for name, value in changes.items():
            if value is None:
                current.pop(name, None)
            else:
                current[name] = str(value)
        if mode == "cmd":
            # cmd /s /c 需要额外一对外层引号，才能同时保留入口与参数中的空格。
            comspec = subprocess.list2cmdline([os.environ.get("COMSPEC", "cmd.exe")])
            invocation = subprocess.list2cmdline([str(bundle / "scripts/run.cmd"), *map(str, args)])
            command = f'{comspec} /d /s /c "{invocation}"'
        else:
            command = [str(GIT_SH) if mode == "git-sh" else "/bin/sh",
                       str(bundle / "scripts/run.sh"), *map(str, args)]
        return subprocess.run(command, cwd=cwd, env=current, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=30)

    def interpreter(name, defect=None):
        # 用真实解释器执行探针，模拟旧版本或缺模块；不按探针字符串返回假成功。
        shim = bins / (name + ".py")
        shim.write_text(
            "import builtins, subprocess, sys\n"
            f"defect = {defect!r}\n"
            "if defect == 'stub': sys.exit(1)\n"
            "if sys.argv[1] == '-c':\n"
            "    if defect == 'old': sys.version_info = (3, 10, 0)\n"
            "    original = builtins.__import__\n"
            "    def limited(name, *args, **kwargs):\n"
            "        if name == defect: raise ImportError(name)\n"
            "        return original(name, *args, **kwargs)\n"
            "    builtins.__import__ = limited\n"
            "    exec(sys.argv[2])\n"
            "else:\n"
            "    sys.exit(subprocess.call([sys.executable, *sys.argv[1:]]))\n",
            encoding="utf-8",
        )
        wrapper = bins / (name + ".cmd" if mode == "cmd" else name)
        if mode == "cmd":
            wrapper.write_bytes((f'@echo off\r\n"{sys.executable}" "{shim}" %*\r\n').encode("utf-8"))
        else:
            wrapper.write_text(f'#!/bin/sh\n"{shell_path(sys.executable)}" "{shell_path(shim)}" "$@"\n',
                               encoding="utf-8")
            wrapper.chmod(0o755)
        return shell_path(wrapper)

    return run, root, bundle, env, shell_path, interpreter, bins


def test_installed_bundle_offline_initialization(launch):
    run, root, _, _, _, _, _ = launch
    assert not (root / "data").exists()
    diagnostic = run("--diagnose")
    assert diagnostic.returncode == 0, diagnostic.stderr
    assert "stage=launcher_ready" in diagnostic.stdout
    assert "database=not_checked" in diagnostic.stdout
    assert "network=not_checked" in diagnostic.stdout
    assert "openalex_key=present" in diagnostic.stdout
    assert "openalex_key_user_scope=" in diagnostic.stdout
    assert "isolated-secret-do-not-print" not in diagnostic.stdout + diagnostic.stderr
    assert not (root / "data").exists()
    result = run("search", "--refresh", "false", "--limit", "1")
    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert payload["ok"] and not payload["data"]["network"]
    assert payload["data"]["items"] == []
    assert (root / "data/sidecar.db").is_file()


@pytest.mark.parametrize("equals", [False, True])
def test_explicit_root_overrides_broken_environment_and_binding(launch, equals):
    run, root, bundle, _, shell_path, _, _ = launch
    (bundle / "root.txt").write_text("/missing-binding\n", encoding="utf-8")
    args = ["--root=" + shell_path(root)] if equals else ["--root", shell_path(root)]
    result = run(*args, "version", ACADEMIC_SEARCH_ROOT="missing-env")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["command"] == "version"


def test_diagnostic_failure_identifies_root_and_exposes_no_key(launch):
    run, root, _, _, _, _, _ = launch
    result = run("--diagnose", ACADEMIC_SEARCH_ROOT="missing-root", OPENALEX_API_KEY=None)
    assert result.returncode == 1
    assert "stage=root_error" in result.stdout
    assert "openalex_key=missing" in result.stdout
    assert "setup.md" in result.stderr
    assert not (root / "data").exists()


def test_diagnostic_separates_process_key_from_user_scope(launch):
    """进程里 missing 不等于没配置：本机用户级可能有同名变量。

    宿主按变量名过滤 `KEY`/`PASSWORD`/`SECRET`/`TOKEN` 时正是这个形态——
    `openalex_key=missing` + `openalex_key_user_scope=present`。
    """
    run, *_ = launch
    diagnostic = run("--diagnose", OPENALEX_API_KEY=None)
    assert diagnostic.returncode == 0, diagnostic.stderr
    fields = dict(line.split("=", 1) for line in diagnostic.stdout.splitlines() if "=" in line)
    assert fields["openalex_key"] == "missing"
    # 只报存在性：取值域固定，任何真实值都不该出现在这一行。
    assert fields["openalex_key_user_scope"] in {"present", "missing", "not_applicable"}


def test_root_requires_value(launch):
    run, *_ = launch
    assert run("--root").returncode == 1
    assert run("--root=").returncode == 1


def test_diagnostic_does_not_silently_skip_command(launch):
    run, *_ = launch
    assert run("--diagnose", "version").returncode == 1


@pytest.mark.parametrize("defect", ["old", "sqlite3", "tomllib", "stub"])
def test_unusable_interpreter_falls_back(launch, defect):
    run, _, _, env, _, interpreter, bins = launch
    rejected = interpreter("bad-python", defect)
    interpreter("python")
    env["PATH"] = str(bins) + os.pathsep + env["PATH"]
    result = run("version", ACADEMIC_SEARCH_PYTHON=rejected)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"]
    assert "3.11" in result.stderr


def test_path_candidate_fallback(launch, request):
    run, _, _, env, _, interpreter, bins = launch
    mode = request.node.callspec.params["launch"]
    interpreter("python" if mode == "cmd" else "python3", "old")
    interpreter("py" if mode == "cmd" else "python")
    env["PATH"] = str(bins) + os.pathsep + env["PATH"]
    result = run("version", ACADEMIC_SEARCH_PYTHON=None)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"]


def test_all_interpreters_unusable(launch):
    run, _, _, env, _, interpreter, bins = launch
    for name in ("python", "python3", "py"):
        interpreter(name, "old")
    env["PATH"] = str(bins) + os.pathsep + env["PATH"]
    result = run("--diagnose", ACADEMIC_SEARCH_PYTHON="missing-python")
    assert result.returncode == 1
    assert "stage=python_error" in result.stdout
    assert "python=unavailable" in result.stdout
    assert "setup.md" in result.stderr


def test_key_is_optional_for_local_use(launch):
    run, *_ = launch
    diagnostic = run("--diagnose", OPENALEX_API_KEY=None)
    assert diagnostic.returncode == 0
    assert "openalex_key=missing" in diagnostic.stdout
    result = run("search", "--refresh", "false", OPENALEX_API_KEY=None)
    assert result.returncode == 0, result.stderr
    assert not json.loads(result.stdout)["data"]["network"]


def test_readonly_database_fails_at_storage_stage(launch):
    run, root, *_ = launch
    assert run("search", "--refresh", "false").returncode == 0
    db = root / "data/sidecar.db"
    db.chmod(stat.S_IREAD)
    try:
        if os.access(db, os.W_OK):
            pytest.skip("当前执行身份可写只读文件，无法验证权限拒绝")
        assert run("--diagnose").returncode == 0
        result = run("search", "--refresh", "false")
        assert result.returncode == 1
        assert json.loads(result.stdout)["error"]["code"] == "data_not_writable"
    finally:
        db.chmod(stat.S_IREAD | stat.S_IWRITE)
