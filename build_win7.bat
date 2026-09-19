@echo off
title Satpuda Core - Windows 7 Build
color 0A

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo.
echo ============================================================
echo   Satpuda Core - Windows 7 Build
echo   Python 3.8 (32-bit) required
echo   Outputs:
echo     dist\SatpudaCore_Win7\SatpudaCore_Win7.exe  (folder)
echo     dist\SatpudaCore_Win7.exe                   (single-file)
echo ============================================================
echo.

REM ── Locate Python 3.8 (32-bit preferred for Win7) ───────────────────────────
set "WIN7_PY="
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
    echo ERROR: Python 3.8 not found.
    echo Install Python 3.8 (32-bit recommended) from python.org, then re-run.
    pause
    exit /b 1
)
echo Using: %WIN7_PY%
%WIN7_PY% --version
echo.

REM ── Prerequisites ─────────────────────────────────────────────────────────────
if not exist "%ROOT%config\firebase_service_account.json" (
    echo ERROR: config\firebase_service_account.json is missing.
    pause
    exit /b 1
)

if exist "%ROOT%config\store_backup.build" (
    echo [1/6] Embedding store backup config...
    %WIN7_PY% "%ROOT%embed_store_backup.py"
    if %errorlevel% neq 0 (
        echo ERROR: embed_store_backup.py failed.
        pause
        exit /b 1
    )
) else (
    echo [1/6] Using existing config\backup_config.dat
)
echo.

echo [2/6] Installing Win7 build packages...
%WIN7_PY% -m pip install --upgrade pip >nul 2>&1
%WIN7_PY% -m pip install --upgrade pyinstaller cryptography ttkbootstrap pillow openpyxl reportlab ^
  google-api-python-client google-auth google-cloud-firestore ^
  pdfplumber pypdfium2 pywin32 "qrcode[pil]" certifi >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: pip install failed for Python 3.8.
    pause
    exit /b 1
)
echo       Done.
echo.

echo [3/6] Building web app (shared with all builds)...
cd /d "%ROOT%purchase-entry-web"
if exist "package.json" (
    if not exist "node_modules" call npm install >nul 2>&1
    call npm run build >nul 2>&1
    if %errorlevel% equ 0 (
        copy /Y "dist\index.html" "%ROOT%web_app\index.html" >nul
        if exist "dist\medicines.json" copy /Y "dist\medicines.json" "%ROOT%web_app\medicines.json" >nul
        if exist "dist\assets" (
            if not exist "%ROOT%web_app\assets" mkdir "%ROOT%web_app\assets"
            xcopy /Y /E /Q "dist\assets\*" "%ROOT%web_app\assets\" >nul
        )
    )
)
cd /d "%ROOT%"
if not exist "%ROOT%config\expiry.dat" %WIN7_PY% "%ROOT%make_demo_expiry.py" >nul 2>&1
echo       Done.
echo.

echo [4/6] Cleaning previous Win7 outputs...
if exist "%ROOT%dist\SatpudaCore_Win7" rmdir /s /q "%ROOT%dist\SatpudaCore_Win7"
if exist "%ROOT%dist\SatpudaCore_Win7.exe" del /f /q "%ROOT%dist\SatpudaCore_Win7.exe"
if exist "%ROOT%build\VeterinaryApp_Win7_Folder" rmdir /s /q "%ROOT%build\VeterinaryApp_Win7_Folder" 2>nul
if exist "%ROOT%build\VeterinaryApp_Win7" rmdir /s /q "%ROOT%build\VeterinaryApp_Win7" 2>nul
echo       Done.
echo.

echo [5/6] Building Win7 folder (recommended — fastest startup)...
echo       No voice / Gemini — Win7-compatible libraries only.
%WIN7_PY% -m PyInstaller "%ROOT%VeterinaryApp_Win7_Folder.spec" --noconfirm
if %errorlevel% neq 0 (
    echo ERROR: Win7 folder build failed.
    echo Check build\VeterinaryApp_Win7_Folder\warn-VeterinaryApp_Win7_Folder.txt
    pause
    exit /b 1
)
echo       Done: dist\SatpudaCore_Win7\SatpudaCore_Win7.exe
echo.

echo [6/6] Building Win7 single-file EXE (portable)...
%WIN7_PY% -m PyInstaller "%ROOT%VeterinaryApp_Win7.spec" --noconfirm
if %errorlevel% neq 0 (
    echo ERROR: Win7 single-file build failed.
    echo Check build\VeterinaryApp_Win7\warn-VeterinaryApp_Win7.txt
    pause
    exit /b 1
)
echo       Done: dist\SatpudaCore_Win7.exe
echo.

REM ── SumatraPDF (32-bit for Win7) ──────────────────────────────────────────────
if exist "%ROOT%dist\SatpudaCore_Win7" (
    if not exist "%ROOT%dist\SatpudaCore_Win7\tools" mkdir "%ROOT%dist\SatpudaCore_Win7\tools"
    if exist "%ROOT%tools\SumatraPDF32.exe" copy /Y "%ROOT%tools\SumatraPDF32.exe" "%ROOT%dist\SatpudaCore_Win7\tools\" >nul
    if exist "%ROOT%tools\SumatraPDF.exe"   copy /Y "%ROOT%tools\SumatraPDF.exe"   "%ROOT%dist\SatpudaCore_Win7\tools\" >nul
    echo       SumatraPDF copied to dist\SatpudaCore_Win7\tools\
)

echo.
echo ============================================================
echo   WINDOWS 7 BUILD COMPLETE
echo ============================================================
if exist "%ROOT%dist\SatpudaCore_Win7\SatpudaCore_Win7.exe" (
    echo   [OK] Folder:  dist\SatpudaCore_Win7\
) else (
    echo   [FAIL] Folder build missing.
)
if exist "%ROOT%dist\SatpudaCore_Win7.exe" (
    echo   [OK] EXE:     dist\SatpudaCore_Win7.exe
) else (
    echo   [FAIL] Single-file EXE missing.
)
echo.
echo   Win7 requirements: Windows 7 SP1 + Platform Update (KB2670838)
echo   Not included: voice assistant, Gemini bill photos
echo   Fallback: PDF / Excel / CSV purchase import still works
echo.
echo   Zip dist\SatpudaCore_Win7\ for distribution (recommended).
echo ============================================================
pause
