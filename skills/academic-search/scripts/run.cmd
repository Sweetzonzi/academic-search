@echo off
rem academic-search launcher (Windows)。契约见仓库 docs/接口/交付物-skill打包.md。
rem 仓库根：--root 参数 → ACADEMIC_SEARCH_ROOT → 本 bundle 的 root.txt → 脚本往上三级（= 仓库根）。
rem 解释器：ACADEMIC_SEARCH_PYTHON → python → py；验证版本与核心模块。
setlocal DisableDelayedExpansion
set "HERE=%~dp0"
set "ROOT=%ACADEMIC_SEARCH_ROOT%"
set "DIAG="
rem SHIFT 不影响 %%*；只解析子命令之前的选项，原参数仍传给 CLI。
:options
if "%~1"=="" goto resolve
if "%~1"=="--diagnose" (
  set "DIAG=1"
  shift
  goto options
)
if "%~1"=="--root" (
  if "%~2"=="" goto missing_root
  set "ROOT=%~2"
  shift
  shift
  goto options
)
set "ARG=%~1"
if "%ARG:~0,7%"=="--root=" (
  set "ROOT=%ARG:~7%"
  if not defined ROOT goto missing_root
  shift
  goto options
)
if defined DIAG goto diagnose_usage
goto resolve

:diagnose_usage
echo academic-search: --diagnose 单独调用，不与子命令混用。1>&2
exit /b 1

:missing_root
echo academic-search: --root 需要一个仓库路径；参见 "%HERE%..\references\setup.md" 1>&2
exit /b 1

:resolve
if not defined ROOT (
  if exist "%HERE%..\root.txt" (
    set /p "ROOT=" < "%HERE%..\root.txt"
  )
)
if not defined ROOT set "ROOT=%HERE%..\..\.."
for %%i in ("%ROOT%") do set "ROOT=%%~fi"
set "ROOT_OK=false"
if exist "%ROOT%\src\academic_search\__init__.py" set "ROOT_OK=true"
if "%ROOT_OK%"=="false" if not defined DIAG goto bad_root
set "ACADEMIC_SEARCH_ROOT=%ROOT%"

set "PY="
if defined ACADEMIC_SEARCH_PYTHON (
  call :probe "%ACADEMIC_SEARCH_PYTHON%"
  if not errorlevel 1 set "PY=%ACADEMIC_SEARCH_PYTHON%"
  if not defined PY echo academic-search: 指定解释器无法启动、低于 Python 3.11 或缺 sqlite3/tomllib，改试 python / py 1>&2
)
if not defined PY (
  rem 同一个名字在 PATH 里可能有多份：应用商店的 python.exe 存根常排在真解释器前面，
  rem 所以逐个 `where` 命中都 probe，取第一个能跑的。
  for %%c in (python py) do (
    if not defined PY (
      for /f "delims=" %%p in ('where %%c 2^>nul') do (
        if not defined PY (
          call :probe "%%p"
          if not errorlevel 1 set "PY=%%p"
        )
      )
    )
  )
)
if defined DIAG goto diagnose
if not defined PY goto bad_python
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%"
"%PY%" -m academic_search %*
exit /b %errorlevel%

:diagnose
echo root="%ROOT%"
echo root_valid=%ROOT_OK%
echo data="%ROOT%\data"
if defined PY (
  call "%PY%" -c "import sys,sqlite3; print('python=' + sys.executable); print('python_version=' + sys.version.split()[0]); print('sqlite_version=' + sqlite3.sqlite_version)"
) else (
  echo python=unavailable
)
if defined OPENALEX_API_KEY (echo openalex_key=present) else (echo openalex_key=missing)
echo database=not_checked
echo network=not_checked
if "%ROOT_OK%"=="false" (
  echo stage=root_error
  goto bad_root
)
if not defined PY (
  echo stage=python_error
  goto bad_python
)
echo stage=launcher_ready
exit /b 0

:bad_root
echo academic-search: 解析出的仓库根不像仓库: "%ROOT%" 1>&2
echo   用 --root、ACADEMIC_SEARCH_ROOT 或 bundle/root.txt 指向含 src/academic_search 的仓库。1>&2
echo   参见 "%HERE%..\references\setup.md" 1>&2
exit /b 1

:bad_python
echo academic-search: 找不到 Python 3.11+ 且含 sqlite3/tomllib 的解释器。1>&2
echo   已尝试 ACADEMIC_SEARCH_PYTHON / python / py；请指定真实解释器。1>&2
echo   参见 "%HERE%..\references\setup.md" 1>&2
exit /b 1

:probe
rem 用 call：候选也可能是 .cmd/.bat，不 call 会把控制权交出去、父脚本不再继续。
call %1 -c "import sys; sys.version_info >= (3, 11) or sys.exit(1); import sqlite3, tomllib" >nul 2>&1
exit /b %errorlevel%
