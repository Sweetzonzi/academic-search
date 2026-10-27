#!/bin/sh
# academic-search launcher (POSIX)。契约见仓库 docs/接口/交付物-skill打包.md。
# 仓库根：--root 参数 → ACADEMIC_SEARCH_ROOT → 本 bundle 的 root.txt → 解析链接后的脚本往上三级。
# 解释器：ACADEMIC_SEARCH_PYTHON → python3 → python；验证版本与核心模块。
set -u
HERE=$(CDPATH= cd -P -- "$(dirname -- "$0")" && pwd -P)
ROOT=${ACADEMIC_SEARCH_ROOT:-}
DIAG=
# 函数内 shift 不改变主入口的参数；只解析子命令之前的选项。
parse_options() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --diagnose) DIAG=1; shift ;;
      --root)
        if [ "$#" -lt 2 ] || [ -z "$2" ]; then
          echo "academic-search: --root 需要一个仓库路径；参见 $HERE/../references/setup.md" >&2
          exit 1
        fi
        ROOT=$2; shift 2 ;;
      --root=*)
        ROOT=${1#--root=}; shift
        if [ -z "$ROOT" ]; then
          echo "academic-search: --root 需要一个仓库路径" >&2
          exit 1
        fi ;;
      *)
        if [ -n "$DIAG" ]; then
          echo "academic-search: --diagnose 单独调用，不与子命令混用。" >&2
          exit 1
        fi
        break ;;
    esac
  done
}
parse_options "$@"
if [ -z "$ROOT" ] && [ -f "$HERE/../root.txt" ]; then
  ROOT=$(head -n 1 "$HERE/../root.txt" | tr -d '\r')
fi
if [ -z "$ROOT" ]; then
  ROOT=$(CDPATH= cd -- "$HERE/../../.." && pwd)
fi
if [ -d "$ROOT" ]; then ROOT=$(CDPATH= cd -- "$ROOT" && pwd); fi
ROOT_OK=false
if [ -f "$ROOT/src/academic_search/__init__.py" ]; then ROOT_OK=true; fi
bad_root() {
  echo "academic-search: 解析出的仓库根不像仓库: $ROOT" >&2
  echo "  用 --root、ACADEMIC_SEARCH_ROOT 或 bundle/root.txt 指向含 src/academic_search 的仓库。" >&2
  echo "  参见 $HERE/../references/setup.md" >&2
  exit 1
}
if [ "$ROOT_OK" = false ] && [ -z "$DIAG" ]; then bad_root; fi

usable() {
  [ -n "$1" ] && command -v "$1" >/dev/null 2>&1 && "$1" -c "import sys; sys.version_info >= (3, 11) or sys.exit(1); import sqlite3, tomllib" >/dev/null 2>&1
}

PY=${ACADEMIC_SEARCH_PYTHON:-}
if [ -n "$PY" ] && ! usable "$PY"; then
  echo "academic-search: 指定解释器无法启动、低于 Python 3.11 或缺 sqlite3/tomllib，改试 python3 / python" >&2
  PY=
fi
if [ -z "$PY" ]; then
  for candidate in python3 python; do
    if usable "$candidate"; then PY=$candidate; break; fi
  done
fi
bad_python() {
  echo "academic-search: 找不到 Python 3.11+ 且含 sqlite3/tomllib 的解释器。" >&2
  echo "  已尝试 ACADEMIC_SEARCH_PYTHON / python3 / python；请指定真实解释器。" >&2
  echo "  参见 $HERE/../references/setup.md" >&2
  exit 1
}
if [ -n "$DIAG" ]; then
  printf 'root=%s\nroot_valid=%s\ndata=%s/data\n' "$ROOT" "$ROOT_OK" "$ROOT"
  if [ -n "$PY" ]; then
    "$PY" -c "import sys,sqlite3; print('python=' + sys.executable); print('python_version=' + sys.version.split()[0]); print('sqlite_version=' + sqlite3.sqlite_version)"
  else
    echo 'python=unavailable'
  fi
  if [ -n "${OPENALEX_API_KEY:-}" ]; then echo 'openalex_key=present'; else echo 'openalex_key=missing'; fi
  # 本启动器不查询操作系统的凭据存储（POSIX 没有等价的用户级变量库），如实标 not_applicable。
  echo 'openalex_key_user_scope=not_applicable'
  printf 'database=not_checked\nnetwork=not_checked\n'
  if [ "$ROOT_OK" = false ]; then echo 'stage=root_error'; bad_root; fi
  if [ -z "$PY" ]; then echo 'stage=python_error'; bad_python; fi
  echo 'stage=launcher_ready'
  exit 0
fi
if [ -z "$PY" ]; then bad_python; fi
ACADEMIC_SEARCH_ROOT=$ROOT PYTHONIOENCODING=${PYTHONIOENCODING:-utf-8} PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m academic_search "$@"
