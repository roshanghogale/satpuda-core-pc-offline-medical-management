@echo off
title Satpuda Core - Folder Builds (Win10/11 + Win8)
color 0A

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "PATH=%PATH%;C:\Users\rosha\AppData\Local\Programs\Python\Python313;C:\Users\rosha\AppData\Local\Programs\Python\Python313\Scripts;C:\nvm4w\nodejs;C:\Users\rosha\AppData\Roaming\npm"

echo.
echo ============================================================
echo   Satpuda Core - Folder Builds
echo   [1] dist\SatpudaCore_Win10  - Windows 10 / 11 (64-bit)
echo   [2] dist\SatpudaCore_Win8   - Windows 8 / 8.1 (32-bit)
echo ============================================================
echo.

REM ── Shared prep ─────────────────────────────────────────────────────────────
echo [1/5] Installing Win10/11 build packages (Python 3.13)...
pip install --upgrade pyinstaller cryptography reportlab openpyxl ttkbootstrap pillow ^
  google-api-python-client google-auth google-cloud-firestore ^
  pdfplumber pypdfium2 google-generativeai google-genai ^
  faster-whisper sounddevice numpy pyttsx3 pywin32 "qrcode[pil]" >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: pip install failed for Python 3.13.
    pause
    exit /b 1
)
echo       Done.
echo.

if not exist "%ROOT%config\firebase_service_account.json" (
    echo ERROR: config\firebase_service_account.json is missing.
    pause
    exit /b 1
)

if exist "%ROOT%config\store_backup.build" (
    echo [1b/5] Embedding store backup config...
    python "%ROOT%embed_store_backup.py"
    if %errorlevel% neq 0 (
        echo ERROR: embed_store_backup.py failed.
        pause
        exit /b 1
    )
)
echo.

echo [2/5] Building web app...
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
if not exist "%ROOT%config\expiry.dat" python "%ROOT%make_demo_expiry.py" >nul 2>&1
echo       Done.
echo.

echo [3/5] Cleaning previous folder outputs...
if not exist "%ROOT%dist" mkdir "%ROOT%dist"
if exist "%ROOT%dist\SatpudaCore_Win10" rmdir /s /q "%ROOT%dist\SatpudaCore_Win10"
if exist "%ROOT%dist\SatpudaCore_Win8"  rmdir /s /q "%ROOT%dist\SatpudaCore_Win8"
if exist "%ROOT%build\VeterinaryApp_Folder"     rmdir /s /q "%ROOT%build\VeterinaryApp_Folder" 2>nul
if exist "%ROOT%build\VeterinaryApp_Win8_Folder" rmdir /s /q "%ROOT%build\VeterinaryApp_Win8_Folder" 2>nul
echo       Done.
echo.

REM ── Win10/11 folder (Python 3.13, full features) ─────────────────────────────
echo [4/5] Building SatpudaCore_Win10 folder (Windows 10 / 11)...
echo       Voice + Gemini + Firebase sync included. This may take 3-6 minutes...
pyinstaller "%ROOT%VeterinaryApp_Folder.spec" --noconfirm
if %errorlevel% neq 0 (
    echo ERROR: Win10/11 folder build failed.
    echo Check build\VeterinaryApp_Folder\warn-VeterinaryApp_Folder.txt
    pause
    exit /b 1
)
echo       Done: dist\SatpudaCore_Win10\SatpudaCore_Win10.exe
echo.

REM ── Win8 folder (Python 3.8-32, lite features) ─────────────────────────────
set "WIN8_PY="
py -3.8-32 --version > "%TEMP%\py38check.txt" 2>&1
if exist "%TEMP%\py38check.txt" (
    findstr /i "3.8" "%TEMP%\py38check.txt" >nul 2>&1
    if not errorlevel 1 set "WIN8_PY=py -3.8-32"
    del "%TEMP%\py38check.txt" >nul 2>&1
)
if "%WIN8_PY%"=="" (
    py -3.8 --version > "%TEMP%\py38check.txt" 2>&1
    if exist "%TEMP%\py38check.txt" (
        findstr /i "3.8" "%TEMP%\py38check.txt" >nul 2>&1
        if not errorlevel 1 set "WIN8_PY=py -3.8"
        del "%TEMP%\py38check.txt" >nul 2>&1
    )
)
if "%WIN8_PY%"=="" (
    if exist "C:\Python38-32\python.exe" set "WIN8_PY=C:\Python38-32\python.exe"
)
if "%WIN8_PY%"=="" (
    if exist "C:\Python38\python.exe" set "WIN8_PY=C:\Python38\python.exe"
)

if "%WIN8_PY%"=="" (
    echo ERROR: Python 3.8 not found. Install Python 3.8 (32-bit) for the Win8 build.
    pause
    exit /b 1
)

echo [5/5] Building SatpudaCore_Win8 folder (Windows 8 / 8.1)...
echo       Using %WIN8_PY% — no voice/Gemini (Win8-compatible). May take 3-6 minutes...
%WIN8_PY% -m pip install --upgrade pyinstaller cryptography ttkbootstrap pillow openpyxl reportlab ^
  google-api-python-client google-auth google-cloud-firestore ^
  pdfplumber pypdfium2 pywin32 "qrcode[pil]" >nul 2>&1
%WIN8_PY% -m PyInstaller "%ROOT%VeterinaryApp_Win8_Folder.spec" --noconfirm
if %errorlevel% neq 0 (
    echo ERROR: Win8 folder build failed.
    echo Check build\VeterinaryApp_Win8_Folder\warn-VeterinaryApp_Win8_Folder.txt
    pause
    exit /b 1
)
echo       Done: dist\SatpudaCore_Win8\SatpudaCore_Win8.exe
echo.

REM ── SumatraPDF for silent printing ──────────────────────────────────────────
if not exist "%ROOT%dist\tools" mkdir "%ROOT%dist\tools"
if exist "%ROOT%tools\SumatraPDF64.exe" copy /Y "%ROOT%tools\SumatraPDF64.exe" "%ROOT%dist\tools\" >nul
if exist "%ROOT%tools\SumatraPDF32.exe" copy /Y "%ROOT%tools\SumatraPDF32.exe" "%ROOT%dist\tools\" >nul
if exist "%ROOT%tools\SumatraPDF.exe"   copy /Y "%ROOT%tools\SumatraPDF.exe"   "%ROOT%dist\tools\" >nul

if exist "%ROOT%dist\SatpudaCore_Win10" (
    if not exist "%ROOT%dist\SatpudaCore_Win10\tools" mkdir "%ROOT%dist\SatpudaCore_Win10\tools"
    if exist "%ROOT%tools\SumatraPDF64.exe" copy /Y "%ROOT%tools\SumatraPDF64.exe" "%ROOT%dist\SatpudaCore_Win10\tools\" >nul
    if exist "%ROOT%tools\SumatraPDF32.exe" copy /Y "%ROOT%tools\SumatraPDF32.exe" "%ROOT%dist\SatpudaCore_Win10\tools\" >nul
)

if exist "%ROOT%dist\SatpudaCore_Win8" (
    if not exist "%ROOT%dist\SatpudaCore_Win8\tools" mkdir "%ROOT%dist\SatpudaCore_Win8\tools"
    if exist "%ROOT%tools\SumatraPDF32.exe" copy /Y "%ROOT%tools\SumatraPDF32.exe" "%ROOT%dist\SatpudaCore_Win8\tools\" >nul
    if exist "%ROOT%tools\SumatraPDF.exe"   copy /Y "%ROOT%tools\SumatraPDF.exe"   "%ROOT%dist\SatpudaCore_Win8\tools\" >nul
)

echo ============================================================
echo   FOLDER BUILDS COMPLETE
echo ============================================================
if exist "%ROOT%dist\SatpudaCore_Win10\SatpudaCore_Win10.exe" (
    echo   [OK] dist\SatpudaCore_Win10\  - Windows 10 / 11
) else (
    echo   [FAIL] SatpudaCore_Win10 folder missing.
)
if exist "%ROOT%dist\SatpudaCore_Win8\SatpudaCore_Win8.exe" (
    echo   [OK] dist\SatpudaCore_Win8\   - Windows 8 / 8.1
) else (
    echo   [FAIL] SatpudaCore_Win8 folder missing.
)
echo.
echo   Zip each folder separately for distribution.
echo ============================================================
pause
