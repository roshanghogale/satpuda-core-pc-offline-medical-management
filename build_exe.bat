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
echo [1/6] Installing required packages (voice + PDF/Gemini import)...
pip install --upgrade pyinstaller cryptography reportlab openpyxl ttkbootstrap pillow ^
  google-api-python-client google-auth google-cloud-firestore ^
  pdfplumber pypdfium2 google-generativeai google-genai ^
  faster-whisper sounddevice numpy pyttsx3 pywin32 >nul 2>&1
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
    call npm install >nul 2>&1
    if %errorlevel% neq 0 (
        echo       WARNING: npm install failed. Skipping web app build.
        goto :web_done
    )
)
call npm run build >nul 2>&1
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

REM ── Step 2b: Firebase service account (required for online sync) ─────────────
if not exist "%ROOT%config\firebase_service_account.json" (
    echo       ERROR: config\firebase_service_account.json is missing.
    echo       Copy your Firebase service account JSON there, then rebuild.
    pause
    exit /b 1
)
echo [2b/6] Firebase credentials found — will bundle in both EXEs.
echo.

REM ── Step 2c: Bundle expiry.dat (edit with set_expiry.py before build if needed) ─
echo [2c/6] Ensuring config\expiry.dat exists for EXE bundle...
if not exist "%ROOT%config\expiry.dat" (
    python "%ROOT%make_demo_expiry.py"
    echo       Created demo expiry.dat ^(today + 5 days^).
) else (
    echo       Using existing config\expiry.dat
)
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
echo       (Gemini for bill photos — no local EasyOCR/torch in EXE)
echo.

REM ── Step 4b: Windows 10/11 folder build (full features — voice + Gemini) ─────
echo [4b/7] Building SatpudaCore_Win10 folder (Windows 10/11, full features)...
echo       Recommended for daily use — fastest startup.
pyinstaller "%ROOT%VeterinaryApp_Folder.spec" --noconfirm >nul 2>&1
if %errorlevel% neq 0 (
    echo       WARNING: Win10/11 folder build failed.
) else (
    echo       Done: dist\SatpudaCore_Win10\SatpudaCore_Win10.exe
)
echo.

REM ── Step 5: Windows 8 folder + legacy Win7 builds ───────────────────────────
echo [5/7] Building SatpudaCore_Win8 folder (Windows 8 / 8.1)...

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
echo       Installing packages for Python 3.8 (PDF/Excel + Firebase — no voice/Gemini)...
%WIN7_PY% -m pip install --upgrade pyinstaller cryptography ttkbootstrap pillow openpyxl reportlab ^
  google-api-python-client google-auth google-cloud-firestore ^
  pdfplumber pypdfium2 pywin32 "qrcode[pil]" >nul 2>&1
echo       Building Win8 folder. This may take 3-5 minutes...
%WIN7_PY% -m PyInstaller "%ROOT%VeterinaryApp_Win8_Folder.spec" --noconfirm >nul 2>&1
if %errorlevel% neq 0 (
    echo       ERROR: Win8 folder build failed.
    echo       Check build\VeterinaryApp_Win8_Folder\warn-VeterinaryApp_Win8_Folder.txt
) else (
    echo       Done: dist\SatpudaCore_Win8\SatpudaCore_Win8.exe
)
echo       Building Win7 folder (legacy). This may take 3-5 minutes...
%WIN7_PY% -m PyInstaller "%ROOT%VeterinaryApp_Win7_Folder.spec" --noconfirm >nul 2>&1
if %errorlevel% neq 0 (
    echo       ERROR: Win7 folder build failed.
    echo       Check build\VeterinaryApp_Win7_Folder\warn-VeterinaryApp_Win7_Folder.txt
) else (
    echo       Done: dist\SatpudaCore_Win7\SatpudaCore_Win7.exe
)
echo       Building Win7 single-file EXE...
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

REM ── Step 6: Copy bundled SumatraPDF to dist (silent printing) ─────────────────
echo [6/6] Copying SumatraPDF tools to dist...
set "SUMATRA_COPIED=0"
if exist "%ROOT%tools\SumatraPDF64.exe" set "SUMATRA_COPIED=1"
if exist "%ROOT%tools\SumatraPDF32.exe" set "SUMATRA_COPIED=1"
if exist "%ROOT%tools\SumatraPDF.exe" set "SUMATRA_COPIED=1"

if "%SUMATRA_COPIED%"=="0" (
    echo       WARNING: No SumatraPDF in tools\ — silent print will fail until you add:
    echo         tools\SumatraPDF64.exe  and/or  tools\SumatraPDF32.exe
) else (
    if not exist "%ROOT%dist\tools" mkdir "%ROOT%dist\tools"
    if exist "%ROOT%tools\SumatraPDF64.exe" (
        copy /Y "%ROOT%tools\SumatraPDF64.exe" "%ROOT%dist\tools\" >nul
        echo       Copied SumatraPDF64.exe -^> dist\tools\
    )
    if exist "%ROOT%tools\SumatraPDF32.exe" (
        copy /Y "%ROOT%tools\SumatraPDF32.exe" "%ROOT%dist\tools\" >nul
        echo       Copied SumatraPDF32.exe -^> dist\tools\
    )
    if exist "%ROOT%tools\SumatraPDF.exe" (
        copy /Y "%ROOT%tools\SumatraPDF.exe" "%ROOT%dist\tools\" >nul
        echo       Copied SumatraPDF.exe -^> dist\tools\
    )
    if exist "%ROOT%dist\SatpudaCore_Win10" (
        if not exist "%ROOT%dist\SatpudaCore_Win10\tools" mkdir "%ROOT%dist\SatpudaCore_Win10\tools"
        if exist "%ROOT%tools\SumatraPDF64.exe" copy /Y "%ROOT%tools\SumatraPDF64.exe" "%ROOT%dist\SatpudaCore_Win10\tools\" >nul
        if exist "%ROOT%tools\SumatraPDF32.exe" copy /Y "%ROOT%tools\SumatraPDF32.exe" "%ROOT%dist\SatpudaCore_Win10\tools\" >nul
        if exist "%ROOT%tools\SumatraPDF.exe" copy /Y "%ROOT%tools\SumatraPDF.exe" "%ROOT%dist\SatpudaCore_Win10\tools\" >nul
        echo       Copied Sumatra tools -^> dist\SatpudaCore_Win10\tools\
    )
    if exist "%ROOT%dist\SatpudaCore_Win8" (
        if not exist "%ROOT%dist\SatpudaCore_Win8\tools" mkdir "%ROOT%dist\SatpudaCore_Win8\tools"
        if exist "%ROOT%tools\SumatraPDF32.exe" copy /Y "%ROOT%tools\SumatraPDF32.exe" "%ROOT%dist\SatpudaCore_Win8\tools\" >nul
        if exist "%ROOT%tools\SumatraPDF.exe" copy /Y "%ROOT%tools\SumatraPDF.exe" "%ROOT%dist\SatpudaCore_Win8\tools\" >nul
        echo       Copied Sumatra tools -^> dist\SatpudaCore_Win8\tools\
    )
    if exist "%ROOT%dist\SatpudaCore_Win7" (
        if not exist "%ROOT%dist\SatpudaCore_Win7\tools" mkdir "%ROOT%dist\SatpudaCore_Win7\tools"
        if exist "%ROOT%tools\SumatraPDF32.exe" copy /Y "%ROOT%tools\SumatraPDF32.exe" "%ROOT%dist\SatpudaCore_Win7\tools\" >nul
        if exist "%ROOT%tools\SumatraPDF.exe" copy /Y "%ROOT%tools\SumatraPDF.exe" "%ROOT%dist\SatpudaCore_Win7\tools\" >nul
        echo       Copied Sumatra tools -^> dist\SatpudaCore_Win7\tools\
    )
)
echo.

REM ── Summary ───────────────────────────────────────────────────────────────────
echo ============================================================
echo   BUILD COMPLETE - Satpuda Core
echo ============================================================
echo.

if exist "%ROOT%dist\SatpudaCore.exe" (
    echo   [OK] dist\SatpudaCore.exe        - Windows 8 / 10 / 11  (64-bit, portable)
) else (
    echo   [FAIL] SatpudaCore.exe was NOT produced.
)

if exist "%ROOT%dist\SatpudaCore_Win10\SatpudaCore_Win10.exe" (
    echo   [OK] dist\SatpudaCore_Win10\          - Win10/11 folder ^(voice + Gemini^)
) else (
    echo   [SKIP] SatpudaCore_Win10 folder build not produced.
)

if exist "%ROOT%dist\SatpudaCore_Win8\SatpudaCore_Win8.exe" (
    echo   [OK] dist\SatpudaCore_Win8\           - Win8 folder ^(no voice/Gemini^)
) else (
    echo   [SKIP] SatpudaCore_Win8 folder — Python 3.8 not found or build failed.
)

if exist "%ROOT%dist\SatpudaCore_Win7.exe" (
    echo   [OK] dist\SatpudaCore_Win7.exe   - Win7 single-file ^(optional^)
) else (
    echo   [SKIP] SatpudaCore_Win7.exe single-file not produced.
)

echo.
echo ============================================================
echo   Press any key to close this window...
echo ============================================================
pause
