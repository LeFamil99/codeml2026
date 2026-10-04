@echo off
rem L2C Review - Windows equivalent of `make install`.
rem Creates .venv (Python 3.13 via uv) and installs the project editable with the
rem dashboard, OCR and test extras.
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv not found - install it from https://docs.astral.sh/uv/ then rerun.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    uv venv --python 3.13 .venv
    if errorlevel 1 exit /b 1
)

uv pip install --python ".venv\Scripts\python.exe" -e ".[app,da,dev]"
if errorlevel 1 exit /b 1

echo.
echo ready - try 'ui.cmd'
