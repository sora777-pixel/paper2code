@echo off
chcp 65001 >nul
rem ===========================================================================
rem  paper2code 一键启动（本地 Web UI）
rem
rem  用法：
rem      start_demo.bat               默认端口 8000
rem      start_demo.bat 8080          指定端口
rem      start_demo.bat --offline     离线模式（零 Key、不联网、不花钱）
rem
rem  特点：自动按序探测「真的装了 fastapi/uvicorn 的解释器」；本机若存在 WorkBuddy
rem        内置 Python（带依赖）会自动借用它的 site-packages；一个都没找到时给出
rem        一条明确的安装命令并停住，而不是窗口一闪而过。
rem ===========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PORT=8000"
set "OFFLINE="
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--offline" (set "OFFLINE=1") else (set "PORT=%~1")
shift
goto parse
:parsed
if defined P2C_PORT set "PORT=%P2C_PORT%"

set "PY="
set "PYRUN="
set "OLD_PYTHONPATH=%PYTHONPATH%"
set "WBROOT=%USERPROFILE%\.workbuddy\binaries\python"

rem -- 1) 仓库内虚拟环境（推荐） ------------------------------------------- #
call :probe "%~dp0.venv\Scripts\python.exe"
if defined PY goto found
call :probe "%~dp0venv\Scripts\python.exe"
if defined PY goto found

rem -- 2) 用户显式指定的解释器 --------------------------------------------- #
if defined P2C_PYTHON call :probe "%P2C_PYTHON%"
if defined PY goto found

rem -- 3) PATH 上的解释器 -------------------------------------------------- #
call :probe_bare "py -3"
if defined PY goto found
call :probe_bare "python"
if defined PY goto found

rem -- 4) 本机 WorkBuddy 内置依赖（存在才用） ------------------------------- #
set "SP=%WBROOT%\envs\default\Lib\site-packages"
if exist "%SP%" (
  call :probe_sp "%WBROOT%\versions\3.13.12\python.exe" "%SP%"
  if defined PY goto found
  call :probe_sp "%WBROOT%\versions\current\current\python.exe" "%SP%"
  if defined PY goto found
  call :probe_sp "%WBROOT%\versions\current\python.exe" "%SP%"
  if defined PY goto found
  call :probe_bare_sp "py -3" "%SP%"
  if defined PY goto found
  call :probe_bare_sp "python" "%SP%"
  if defined PY goto found
)

echo.
echo [错误] 没找到装了 fastapi / uvicorn 的解释器。
echo        请任选一种方式装依赖，然后重新双击本文件：
echo          a. 复用已有解释器:  python -m pip install -r requirements.txt
echo          b. 建干净虚拟环境:  python -m venv .venv
echo                              .venv\Scripts\pip install -r requirements.txt
echo          c. 直接指定解释器:  set P2C_PYTHON=D:\path\to\python.exe
echo.
pause
exit /b 3

:found
if defined OFFLINE set "P2C_LLM_MODE=offline"
echo.
echo   解释器   : %PY%
if defined PYTHONPATH echo   依赖路径 : %PYTHONPATH%
if defined OFFLINE echo   运行模式 : offline 模式，不联网、不花钱
if not defined OFFLINE echo   运行模式 : 按 configs/default.yaml 与 api_keys.yaml
echo   访问地址 : http://127.0.0.1:%PORT%
echo   停止服务 : 在本窗口按 Ctrl+C
echo.
%PYRUN% -m paper2code serve --port %PORT%
echo.
echo 服务已退出，退出码 %errorlevel%。
pause
exit /b 0

rem ---------------------------------------------------------------------------
rem  探测子过程：只有能 import fastapi + uvicorn 才算「可用」
rem ---------------------------------------------------------------------------
:probe
set "CAND=%~1"
if not defined CAND exit /b 1
if not exist "%CAND%" exit /b 1
"%CAND%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 exit /b 1
set "PY=%CAND%"
set PYRUN="%CAND%"
exit /b 0

:probe_bare
set "CMD=%~1"
%CMD% -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 exit /b 1
set "PY=%CMD%"
set "PYRUN=%CMD%"
exit /b 0

:probe_sp
set "CAND=%~1"
set "SPATH=%~2"
if not defined CAND exit /b 1
if not exist "%CAND%" exit /b 1
if not exist "%SPATH%" exit /b 1
set "PYTHONPATH=%SPATH%"
"%CAND%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 goto probe_sp_fail
set "PY=%CAND%"
set PYRUN="%CAND%"
exit /b 0
:probe_sp_fail
set "PYTHONPATH=%OLD_PYTHONPATH%"
exit /b 1

:probe_bare_sp
set "CMD=%~1"
set "SPATH=%~2"
if not exist "%SPATH%" exit /b 1
set "PYTHONPATH=%SPATH%"
%CMD% -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 goto probe_bare_sp_fail
set "PY=%CMD%"
set "PYRUN=%CMD%"
exit /b 0
:probe_bare_sp_fail
set "PYTHONPATH=%OLD_PYTHONPATH%"
exit /b 1
