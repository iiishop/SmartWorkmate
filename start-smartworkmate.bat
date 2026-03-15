@echo off
setlocal

set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

set "MODE=execute"
set "USER=iiishop"

if /I "%~1"=="dry-run" set "MODE=dry-run"
if /I "%~1"=="--dry-run" set "MODE=dry-run"
if /I "%~1"=="help" goto :help
if /I "%~1"=="--help" goto :help

if not "%~2"=="" set "USER=%~2"

where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] uv is not installed or not in PATH.
  echo Install uv first, then rerun this script.
  exit /b 1
)

echo [INFO] Repo root: %REPO_ROOT%
echo [INFO] Syncing dependencies...
call uv sync
if errorlevel 1 (
  echo [ERROR] uv sync failed.
  exit /b 1
)

if /I "%MODE%"=="dry-run" (
  echo [INFO] Starting SmartWorkmate in dry-run once mode...
  call uv run python -m smartworkmate.cli start --root "%REPO_ROOT%" --dry-run --once --user "%USER%"
) else (
  echo [INFO] Starting SmartWorkmate in execute mode...
  call uv run python -m smartworkmate.cli start --root "%REPO_ROOT%" --execute --user "%USER%"
)

if errorlevel 1 (
  echo [ERROR] SmartWorkmate exited with an error.
  exit /b 1
)

echo [INFO] SmartWorkmate finished.
exit /b 0

:help
echo Usage:
echo   start-smartworkmate.bat [dry-run^|--dry-run] [username]
echo.
echo Examples:
echo   start-smartworkmate.bat
echo   start-smartworkmate.bat dry-run
echo   start-smartworkmate.bat dry-run iiishop
exit /b 0
