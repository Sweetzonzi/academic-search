@echo off
rem academic-search launcher (Windows)。契约见仓库 docs/接口/交付物-skill打包.md。
rem 仓库根：--root 参数 → ACADEMIC_SEARCH_ROOT → 本 bundle 的 root.txt → 脚本往上三级（= 仓库根）。
rem 解释器：ACADEMIC_SEARCH_PYTHON → python → py；每个候选都先 probe `import sys`，
rem 失败换下一个（应用商店的 python.exe 存根能被 where 命中，但不能执行）。
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

set "PY="
if defined ACADEMIC_SEARCH_PYTHON (
  call :probe "%ACADEMIC_SEARCH_PYTHON%"
  if not errorlevel 1 set "PY=%ACADEMIC_SEARCH_PYTHON%"
  if not defined PY echo academic-search: ACADEMIC_SEARCH_PYTHON 指向的解释器不可用，改试 python / py 1>&2
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
if not defined PY (
  echo academic-search: 找不到可用的 Python（试过 ACADEMIC_SEARCH_PYTHON / python / py） 1>&2
  echo   请设 ACADEMIC_SEARCH_PYTHON 指向真正的解释器 1>&2
  exit /b 1
)
set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%"
"%PY%" -m academic_search %*
exit /b %errorlevel%

:probe
rem %1 是带引号的候选；能执行 import sys 才算可用。
rem 用 call：候选也可能是 .cmd/.bat，不 call 会把控制权交出去、父脚本不再继续。
call %1 -c "import sys" >nul 2>&1
exit /b %errorlevel%
