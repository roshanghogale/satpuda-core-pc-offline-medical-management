@echo off
cd /d "%~dp0"

REM ── Ensure Python is on PATH (handles double-click from Explorer) ─────────────
set "PATH=%PATH%;C:\Users\rosha\AppData\Local\Programs\Python\Python313;C:\Users\rosha\AppData\Local\Programs\Python\Python313\Scripts"

echo Starting Veterinary Management System...
python main.py
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Could not start. Make sure Python is installed.
    pause
)
