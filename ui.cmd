@echo off
rem L2C Review - Windows equivalent of `make ui`.
rem Usage:  ui.cmd [PORT]      (default 8501)
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\streamlit.exe" (
    echo Environment not installed - run install.cmd first.
    exit /b 1
)

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8501"

echo Dashboard: http://localhost:%PORT%   (Ctrl+C to stop)
".venv\Scripts\streamlit.exe" run app\streamlit_app.py --server.port %PORT%
