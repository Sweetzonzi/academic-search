#!/bin/sh
# academic-search launcher (POSIX)。契约见仓库 docs/接口/交付物-skill打包.md。
# 仓库根：--root 参数 → ACADEMIC_SEARCH_ROOT → 本 bundle 的 root.txt → 脚本往上三级（= 仓库根）。
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${ACADEMIC_SEARCH_ROOT:-}
if [ -z "$ROOT" ] && [ -f "$HERE/../root.txt" ]; then
  ROOT=$(head -n 1 "$HERE/../root.txt")
fi
if [ -z "$ROOT" ]; then
  ROOT=$(CDPATH= cd -- "$HERE/../../.." && pwd)
fi
if [ ! -f "$ROOT/src/academic_search/__init__.py" ]; then
  echo "academic-search: 解析出的仓库根不像仓库: $ROOT" >&2
  echo "  请设 ACADEMIC_SEARCH_ROOT，或把仓库路径写进 $HERE/../root.txt" >&2
  exit 1
fi
PY=${ACADEMIC_SEARCH_PYTHON:-}
if [ -z "$PY" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY=$candidate; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "academic-search: 找不到 Python，请设 ACADEMIC_SEARCH_PYTHON" >&2
  exit 1
fi
ACADEMIC_SEARCH_ROOT=$ROOT PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m academic_search "$@"
