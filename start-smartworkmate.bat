@echo off
setlocal

set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

set "ARG1=%~1"
set "ARG2=%~2"
if /I "%ARG1%"=="--help" set "ARG1=help"
if /I "%ARG1%"=="--dry-run" set "ARG1=dry-run"

powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\start-smartworkmate.ps1" "%ARG1%" "%ARG2%"
exit /b %errorlevel%
