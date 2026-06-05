@echo off
title Satpuda Core - Build Script
color 0A

set "ROOT=%~dp0"
cd /d "%ROOT%"

REM ── Ensure Python, pip, pyinstaller, node, npm are on PATH ────────────────────
set "PATH=%PATH%;C:\Users\rosha\AppData\Local\Programs\Python\Python313;C:\Users\rosha\AppData\Local\Programs\Python\Python313\Scripts;C:\nvm4w\nodejs;C:\Users\rosha\AppData\Roaming\npm"

echo.
echo ============================================================
echo   Satpuda Core - EXE Builder
echo   Billing. Management. Simplified.
echo ============================================================
echo.

REM ── Step 1: Install required packages ────────────────────────────────────────
echo [1/6] Installing required packages...
pip install --upgrade pyinstaller cryptography reportlab openpyxl ttkbootstrap pillow google-api-python-client google-auth >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ERROR: pip install failed. Make sure Python is in PATH.
    echo.
    pause
    exit /b 1
)
echo       Done.
echo.

REM ── Step 1b: Embed store backup config into config/backup_config.dat ─────────
echo [1b/6] Embedding store backup config for EXE...
if exist "%ROOT%config\store_backup.build" (
    python "%ROOT%embed_store_backup.py"
    if %errorlevel% neq 0 (
        echo       ERROR: embed_store_backup.py failed. Check config\store_backup.build
        pause
        exit /b 1
    )
    echo       Done — backup_config.dat updated from store_backup.build
) else if exist "%ROOT%config\backup_config.dat" (
    echo       Using existing config\backup_config.dat
) else (
    echo       WARNING: No config\store_backup.build or backup_config.dat
    echo       Copy config\store_backup.build.example to store_backup.build and fill in values.
    echo       Or run: python setup_store_backup.py FOLDER_ID "Store Name"
)
echo.

REM ── Step 2: Build web app ─────────────────────────────────────────────────────
echo [2/6] Building web app...
cd /d "%ROOT%purchase-entry-web"
if %errorlevel% neq 0 (
    echo       WARNING: Could not enter purchase-entry-web folder. Skipping.
    goto :web_done
)
if not exist "node_modules" (
    echo       Installing npm dependencies...
    npm install >nul 2>&1
    if %errorlevel% neq 0 (
        echo       WARNING: npm install failed. Skipping web app build.
        goto :web_done
    )
)
npm run build >nul 2>&1
if %errorlevel% neq 0 (
    echo       WARNING: Web app build failed. Continuing anyway...
    goto :web_done
)
copy /Y "dist\index.html" "%ROOT%web_app\index.html" >nul
if exist "dist\medicines.json" copy /Y "dist\medicines.json" "%ROOT%web_app\medicines.json" >nul
if not exist "%ROOT%web_app\medicines.json" (
    echo       Exporting medicines.json...
    python "%ROOT%scripts\export_web_medicines.py" >nul 2>&1
)
if exist "dist\assets" (
    if not exist "%ROOT%web_app\assets" mkdir "%ROOT%web_app\assets"
    xcopy /Y /E /Q "dist\assets\*" "%ROOT%web_app\assets\" >nul
)
echo       Done.

:web_done
cd /d "%ROOT%"
echo.

REM ── Step 3: Clean previous builds ────────────────────────────────────────────
echo [3/6] Cleaning previous build output...
if exist "%ROOT%dist"  rmdir /s /q "%ROOT%dist"
if exist "%ROOT%build" rmdir /s /q "%ROOT%build"
echo       Done.
echo.

REM ── Step 4: Build Windows 8/10/11 EXE ────────────────────────────────────────
echo [4/6] Building SatpudaCore.exe (Windows 8 / 10 / 11, 64-bit)...
echo       This may take 3-5 minutes. Please wait...
pyinstaller "%ROOT%VeterinaryApp.spec" --noconfirm >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Windows 8/10/11 build failed.
    echo Check build\VeterinaryApp\warn-VeterinaryApp.txt for details.
    echo.
    pause
    exit /b 1
)
echo       Done: dist\SatpudaCore.exe
echo.

REM ── Step 5: Build Windows 7 EXE ──────────────────────────────────────────────
echo [5/6] Building SatpudaCore_Win7.exe (Windows 7 / 8 / 8.1)...

set "WIN7_PY="

REM Use a temp file to reliably detect py -3.8-32 without errorlevel redirect issues
py -3.8-32 --version > "%TEMP%\py38check.txt" 2>&1
if exist "%TEMP%\py38check.txt" (
    findstr /i "3.8" "%TEMP%\py38check.txt" >nul 2>&1
    if not errorlevel 1 set "WIN7_PY=py -3.8-32"
    del "%TEMP%\py38check.txt" >nul 2>&1
)

if "%WIN7_PY%"=="" (
    py -3.8 --version > "%TEMP%\py38check.txt" 2>&1
    if exist "%TEMP%\py38check.txt" (
        findstr /i "3.8" "%TEMP%\py38check.txt" >nul 2>&1
        if not errorlevel 1 set "WIN7_PY=py -3.8"
        del "%TEMP%\py38check.txt" >nul 2>&1
    )
)

if "%WIN7_PY%"=="" (
    if exist "C:\Python38-32\python.exe" set "WIN7_PY=C:\Python38-32\python.exe"
)

if "%WIN7_PY%"=="" (
    if exist "C:\Python38\python.exe" set "WIN7_PY=C:\Python38\python.exe"
)

if "%WIN7_PY%"=="" (
    echo       Python 3.8 not found. Skipping Win7 build.
    goto :win7_done
)

echo       Found: %WIN7_PY%
echo       Installing packages for Python 3.8...
%WIN7_PY% -m pip install --upgrade pyinstaller cryptography ttkbootstrap pillow openpyxl reportlab google-api-python-client google-auth >nul 2>&1
echo       Building Win7 EXE. This may take 3-5 minutes. Please wait...
%WIN7_PY% -m PyInstaller "%ROOT%VeterinaryApp_Win7.spec" --noconfirm >nul 2>&1
if %errorlevel% neq 0 (
    echo       ERROR: Win7 build failed.
    echo       Check build\VeterinaryApp_Win7\warn-VeterinaryApp_Win7.txt for details.
) else (
    echo       Done: dist\SatpudaCore_Win7.exe
)

:win7_done
cd /d "%ROOT%"
echo.

REM ── Summary ───────────────────────────────────────────────────────────────────
echo ============================================================
echo   BUILD COMPLETE - Satpuda Core
echo ============================================================
echo.

if exist "%ROOT%dist\SatpudaCore.exe" (
    echo   [OK] dist\SatpudaCore.exe        - Windows 8 / 10 / 11  (64-bit)
) else (
    echo   [FAIL] SatpudaCore.exe was NOT produced.
)

if exist "%ROOT%dist\SatpudaCore_Win7.exe" (
    echo   [OK] dist\SatpudaCore_Win7.exe   - Windows 7 / 8 / 8.1
) else (
    echo   [SKIP] SatpudaCore_Win7.exe - Python 3.8 not found or build failed.
)

echo.
echo ============================================================
echo   Press any key to close this window...
echo ============================================================
pause
