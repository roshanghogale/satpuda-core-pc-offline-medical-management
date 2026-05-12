@echo off
cd /d "%~dp0"
echo Starting Veterinary Management System...
python main.py
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Could not start. Make sure Python is installed and in PATH.
    pause
)
