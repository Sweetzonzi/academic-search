@echo off
rem academic-search launcher (Windows)。契约见仓库 docs/接口/交付物-skill打包.md。
rem 仓库根：--root 参数 → ACADEMIC_SEARCH_ROOT → 本 bundle 的 root.txt → 脚本往上三级（= 仓库根）。
setlocal
set "HERE=%~dp0"
set "ROOT=%ACADEMIC_SEARCH_ROOT%"
if not defined ROOT (
  if exist "%HERE%..\root.txt" (
    for /f "usebackq delims=" %%i in ("%HERE%..\root.txt") do set "ROOT=%%i"
  )
)
if not defined ROOT set "ROOT=%HERE%..\..\.."
if not exist "%ROOT%\src\academic_search\__init__.py" (
  echo academic-search: 解析出的仓库根不像仓库: %ROOT% 1>&2
  echo   请设 ACADEMIC_SEARCH_ROOT，或把仓库路径写进 "%HERE%..\root.txt" 1>&2
  exit /b 1
)
set "ACADEMIC_SEARCH_ROOT=%ROOT%"
set "PY=%ACADEMIC_SEARCH_PYTHON%"
if not defined PY where /q python && set "PY=python"
if not defined PY where /q py && set "PY=py"
if not defined PY (
  echo academic-search: 找不到 Python，请设 ACADEMIC_SEARCH_PYTHON 1>&2
  exit /b 1
)
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 (
  echo academic-search: 解释器 %PY% 不可用 ^(Windows 上裸 python 可能是应用商店存根^) 1>&2
  echo   请设 ACADEMIC_SEARCH_PYTHON 指向真正的解释器 1>&2
  exit /b 1
)
set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%"
"%PY%" -m academic_search %*
