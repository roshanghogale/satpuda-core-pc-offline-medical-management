@echo off
title Satpuda Core — Build Script
color 0A
cd /d "%~dp0"

echo.
echo ============================================================
echo   Satpuda Core — EXE Builder
echo   Billing. Management. Simplified.
echo ============================================================
echo.

REM ── Step 1: Install / upgrade required packages ──────────────────────────────
echo [1/5] Installing required packages...
pip install --upgrade pyinstaller cryptography reportlab openpyxl ttkbootstrap pillow google-api-python-client google-auth
if %errorlevel% neq 0 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)
echo       Done.
echo.

REM ── Step 2: Build web app (purchase entry) ──────────────────────────────────
echo [2/5] Building web app (purchase entry)...
call "%~dp0run_web.bat" silent
if %errorlevel% neq 0 (
    echo WARNING: Web app build failed. Continuing with EXE build...
)
echo       Done.
echo.

REM ── Step 3: Clean previous builds ────────────────────────────────────────────
echo [3/5] Cleaning previous build output...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
echo       Done.
echo.

REM ── Step 4: Build Windows 10/11 EXE (64-bit, current Python) ─────────────────
echo [4/5] Building SatpudaCore.exe (Windows 10/11, 64-bit)...
echo       This may take 3-5 minutes...
pyinstaller VeterinaryApp.spec --noconfirm
if %errorlevel% neq 0 (
    echo ERROR: Windows 10/11 build failed.
    pause
    exit /b 1
)
echo       Done: dist\SatpudaCore.exe
echo.

REM ── Step 5: Build Windows 7 EXE (Python 3.8 32-bit if available) ─────────────
echo [5/5] Building SatpudaCore_Win7.exe (Windows 7/8/8.1, 32-bit)...
echo       Requires Python 3.8 32-bit installed. Checking...

py -3.8-32 --version >nul 2>&1
if %errorlevel% equ 0 (
    echo       Python 3.8 32-bit found. Building...
    py -3.8-32 -m pip install --upgrade pyinstaller cryptography ttkbootstrap pillow openpyxl google-api-python-client google-auth >nul 2>&1
    py -3.8-32 -m PyInstaller VeterinaryApp_Win7.spec --noconfirm
    if %errorlevel% neq 0 (
        echo WARNING: Windows 7 build failed. Skipping.
    ) else (
        echo       Done: dist\SatpudaCore_Win7.exe
    )
) else (
    py -3.8 --version >nul 2>&1
    if %errorlevel% equ 0 (
        echo       Python 3.8 found. Building...
        py -3.8 -m pip install --upgrade pyinstaller cryptography ttkbootstrap pillow openpyxl google-api-python-client google-auth >nul 2>&1
        py -3.8 -m PyInstaller VeterinaryApp_Win7.spec --noconfirm
        if %errorlevel% neq 0 (
            echo WARNING: Windows 7 build failed. Skipping.
        ) else (
            echo       Done: dist\SatpudaCore_Win7.exe
        )
    ) else (
        echo       Python 3.8 not found — skipping Windows 7 build.
        echo       To build Win7 EXE: install Python 3.8 32-bit from python.org
    )
)
echo.

REM ── Summary ───────────────────────────────────────────────────────────────────
echo ============================================================
echo   BUILD COMPLETE — Satpuda Core
echo ============================================================
echo.
if exist dist\SatpudaCore.exe (
    echo   dist\SatpudaCore.exe        — Windows 10 / 11  (64-bit)
)
if exist dist\SatpudaCore_Win7.exe (
    echo   dist\SatpudaCore_Win7.exe   — Windows 7 / 8 / 8.1  (32-bit)
)
echo.
echo ============================================================
pause
