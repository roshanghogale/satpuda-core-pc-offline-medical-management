@echo off
title Satpuda Core - Win10/11 Folder Build (dist only)
color 0A

set "ROOT=%~dp0"
cd /d "%ROOT%"

REM Prefer current machine Python/Node on PATH (no hardcoded user paths).
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: python not found on PATH.
    if /i not "%SATPUDA_BUILD_NOPAUSE%"=="1" pause
    exit /b 1
)

echo.
echo ============================================================
echo   Satpuda Core - Windows 10 / 11 Folder Build
echo   Output: dist\SatpudaCore_Win10\
echo ============================================================
echo.

if not exist "%ROOT%config\firebase_service_account.json" (
    echo ERROR: config\firebase_service_account.json is missing.
    if /i not "%SATPUDA_BUILD_NOPAUSE%"=="1" pause
    exit /b 1
)

echo [1/5] Ensuring Win10/11 build packages (Python 3.13)...
echo       Prefer binary wheels; log: build_win10_pip.log
echo       (not a full --upgrade — that was hanging silently on old pdfplumber)
set "PIP_LOG=%ROOT%build_win10_pip.log"
if exist "%ROOT%requirements.txt" (
    pip install --prefer-binary -r "%ROOT%requirements.txt" > "%PIP_LOG%" 2>&1
) else (
    pip install --prefer-binary pyinstaller cryptography reportlab openpyxl "ttkbootstrap>=1.10.1,<2.0" pillow ^
      google-api-python-client google-auth google-cloud-firestore ^
      "pdfplumber>=0.10.0" pypdfium2 google-generativeai google-genai ^
      faster-whisper sounddevice numpy pyttsx3 pywin32 qrcode certifi > "%PIP_LOG%" 2>&1
)
if %errorlevel% neq 0 (
    echo ERROR: pip install failed for Python 3.13.
    echo Last lines of %PIP_LOG%:
    powershell -NoProfile -Command "Get-Content -LiteralPath '%PIP_LOG%' -Tail 40"
    if /i not "%SATPUDA_BUILD_NOPAUSE%"=="1" pause
    exit /b 1
)
echo       Done.
echo.

if exist "%ROOT%config\store_backup.build" (
    echo [1b/5] Embedding store backup config...
    python "%ROOT%embed_store_backup.py"
    if %errorlevel% neq 0 (
        echo ERROR: embed_store_backup.py failed.
        if /i not "%SATPUDA_BUILD_NOPAUSE%"=="1" pause
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

echo [3/5] Cleaning previous Win10 folder output...
if not exist "%ROOT%dist" mkdir "%ROOT%dist"
if exist "%ROOT%dist\SatpudaCore_Win10" rmdir /s /q "%ROOT%dist\SatpudaCore_Win10"
if exist "%ROOT%build\VeterinaryApp_Folder" rmdir /s /q "%ROOT%build\VeterinaryApp_Folder" 2>nul
if exist "%ROOT%Build_Windows_10_11\Portable" (
    echo       Removing old Portable build copy...
    rmdir /s /q "%ROOT%Build_Windows_10_11\Portable"
)
echo       Done.
echo.

echo [4/5] Building SatpudaCore_Win10 folder (Windows 10 / 11)...
echo       Voice + Gemini + Firebase sync included. This may take 3-8 minutes...
python -m PyInstaller "%ROOT%VeterinaryApp_Folder.spec" --noconfirm
if %errorlevel% neq 0 (
    echo ERROR: Win10/11 folder build failed.
    echo Check build\VeterinaryApp_Folder\warn-VeterinaryApp_Folder.txt
    if /i not "%SATPUDA_BUILD_NOPAUSE%"=="1" pause
    exit /b 1
)
echo       Done: dist\SatpudaCore_Win10\SatpudaCore_Win10.exe
echo.

echo [5/5] Copying SumatraPDF tools...
if exist "%ROOT%tools\SumatraPDF64.exe" (
    if not exist "%ROOT%dist\SatpudaCore_Win10\tools" mkdir "%ROOT%dist\SatpudaCore_Win10\tools"
    copy /Y "%ROOT%tools\SumatraPDF64.exe" "%ROOT%dist\SatpudaCore_Win10\tools\" >nul
)
if exist "%ROOT%tools\SumatraPDF32.exe" (
    if not exist "%ROOT%dist\SatpudaCore_Win10\tools" mkdir "%ROOT%dist\SatpudaCore_Win10\tools"
    copy /Y "%ROOT%tools\SumatraPDF32.exe" "%ROOT%dist\SatpudaCore_Win10\tools\" >nul
)
echo       Done.
echo.

if not exist "%ROOT%Build_Windows_10_11" mkdir "%ROOT%Build_Windows_10_11"
(
echo ============================================================
echo Satpuda Core - Windows 10 / 11 Build
echo ============================================================
echo Folder: dist\SatpudaCore_Win10\SatpudaCore_Win10.exe
echo Python 3.13 ^| 64-bit ^| Full features
echo ============================================================
) > "%ROOT%Build_Windows_10_11\BUILD_REPORT.txt"

echo ============================================================
echo   WIN10 FOLDER BUILD COMPLETE
echo ============================================================
if exist "%ROOT%dist\SatpudaCore_Win10\SatpudaCore_Win10.exe" (
    echo   [OK] dist\SatpudaCore_Win10\
) else (
    echo   [FAIL] dist\SatpudaCore_Win10\ missing.
)
echo.
echo   Zip dist\SatpudaCore_Win10 for distribution / GitHub release.
echo ============================================================
if /i not "%SATPUDA_BUILD_NOPAUSE%"=="1" pause
