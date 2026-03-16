@echo off
setlocal

set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

pushd "%REPO_ROOT%"
uv sync
if errorlevel 1 (
  set "EXITCODE=%errorlevel%"
  popd
  echo.
  echo [ERROR] uv sync failed with exit code: %EXITCODE%
  echo [TIP] Close GUI/terminal processes that may lock .venv files, then retry.
  echo [TIP] Press any key to close.
  pause >nul
  exit /b %EXITCODE%
)

uv run gui.py
set "EXITCODE=%errorlevel%"
popd

if "%EXITCODE%"=="0" (
  echo.
  echo [INFO] GUI launcher finished. Press any key to close.
) else (
  echo.
  echo [ERROR] Launcher failed with exit code: %EXITCODE%
  echo [TIP] Run "uv run gui.py" in repository root for full logs.
  echo [TIP] Press any key to close.
)

pause >nul
exit /b %EXITCODE%
