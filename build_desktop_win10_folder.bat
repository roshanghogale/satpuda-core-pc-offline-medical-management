@echo off

REM Short Rust target path: MSVC link.exe cannot take a 32K command line.
REM Set here (not in .cargo/config.toml) so non-Windows builds still work.
set "CARGO_TARGET_DIR=C:\satpuda-rs"

title Satpuda Core - Tauri Desktop Win10 Standalone Folder Build
color 0B
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: python not found on PATH. Install Python 3.13+.
    pause
    exit /b 1
)

where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: npm not found on PATH. Install Node.js 18+.
    pause
    exit /b 1
)

where cargo >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: cargo not found on PATH. Install Rust toolchain.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Satpuda Core - Tauri Desktop Win10 Standalone Build
echo   Output: dist\SatpudaCore_Desktop_Win10\
echo ============================================================
echo.

REM The Firebase service account is no longer required. Nothing in the shipped
REM engine opens it -- the whole import graph from run_desktop_api.py, 175
REM modules, never touches firestore or firebase_admin. Sync is the Satpuda Core
REM Server. Gating the build on a key that is only there to be leaked was
REM keeping it in every release.

echo [1/6] Installing Python build packages for data engine...
pip install --upgrade pyinstaller cryptography reportlab openpyxl pillow ^
  google-api-python-client google-auth google-cloud-firestore ^
  pdfplumber pypdfium2 google-generativeai google-genai ^
  pywin32 qrcode certifi >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: pip install failed for Python engine build.
    pause
    exit /b 1
)
echo       Done.
echo.

if exist "%ROOT%config\store_backup.build" (
    echo [1b/6] Embedding store backup config...
    python "%ROOT%embed_store_backup.py"
    if %errorlevel% neq 0 (
        echo ERROR: embed_store_backup.py failed.
        pause
        exit /b 1
    )
)

if not exist "%ROOT%config\master_medicine.db" (
    echo ERROR: config\master_medicine.db is missing - required for medical mode.
    pause
    exit /b 1
)
echo       master_medicine.db present.
echo.

echo [2/6] Building web app ^(mobile purchase entry^)...
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

echo [3/6] Building bundled data engine ^(PyInstaller folder^)...
if exist "%ROOT%dist\SatpudaEngine" rmdir /s /q "%ROOT%dist\SatpudaEngine"
if exist "%ROOT%build\SatpudaEngine_Folder" rmdir /s /q "%ROOT%build\SatpudaEngine_Folder" 2>nul
python -m PyInstaller "%ROOT%SatpudaEngine_Folder.spec" --noconfirm
if %errorlevel% neq 0 (
    echo ERROR: SatpudaEngine folder build failed.
    echo Check build\SatpudaEngine_Folder\warn-SatpudaEngine_Folder.txt
    pause
    exit /b 1
)
if not exist "%ROOT%dist\SatpudaEngine\SatpudaEngine.exe" (
    echo ERROR: dist\SatpudaEngine\SatpudaEngine.exe not found after PyInstaller.
    pause
    exit /b 1
)
if not exist "%ROOT%dist\SatpudaEngine\_internal\config\master_medicine.db" (
    if not exist "%ROOT%dist\SatpudaEngine\config\master_medicine.db" (
        echo ERROR: master_medicine.db was not bundled into SatpudaEngine.
        pause
        exit /b 1
    )
)
REM Same reason: a clean release deliberately leaves it out.
echo       Done: dist\SatpudaEngine\SatpudaEngine.exe

REM Tauri bundles ..\engine as a resource, so it must exist before [5/6].
echo [3b/6] Staging engine for Tauri ^(desktop\engine^)...
if exist "%ROOT%desktop\engine" rmdir /s /q "%ROOT%desktop\engine"
mkdir "%ROOT%desktop\engine"
xcopy /Y /E /Q /I "%ROOT%dist\SatpudaEngine\*" "%ROOT%desktop\engine\" >nul
if not exist "%ROOT%desktop\engine\SatpudaEngine.exe" (
    echo ERROR: could not stage desktop\engine for the Tauri bundle.
    pause
    exit /b 1
)
echo       Done.
echo.

echo [4/6] npm install ^(Tauri frontend^)...
cd /d "%ROOT%desktop"
call npm install
if %errorlevel% neq 0 (
    echo ERROR: npm install failed.
    pause
    exit /b 1
)
echo       Done.
echo.

echo [5/6] tauri build ^(may take 5-15 minutes on first run^)...
call npm run tauri:build
if %errorlevel% neq 0 (
    echo ERROR: tauri build failed.
    pause
    exit /b 1
)
echo       Done.
echo.

REM CARGO_TARGET_DIR is redirected above, so cargo does not write under src-tauri.
set "TDIR=%CARGO_TARGET_DIR%"
if "%TDIR%"=="" set "TDIR=%ROOT%desktop\src-tauri\target"
set "EXE=%TDIR%\release\Satpuda Core.exe"
if not exist "%EXE%" set "EXE=%TDIR%\release\app.exe"
if not exist "%EXE%" (
    echo ERROR: Built exe not found in %TDIR%\release\
    pause
    exit /b 1
)

echo [6/6] Assembling dist\SatpudaCore_Desktop_Win10\...
set "OUT=%ROOT%dist\SatpudaCore_Desktop_Win10"
if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%"
copy /Y "%EXE%" "%OUT%\SatpudaCore_Desktop.exe" >nul

if not exist "%OUT%\engine" mkdir "%OUT%\engine"
xcopy /Y /E /Q /I "%ROOT%dist\SatpudaEngine\*" "%OUT%\engine\" >nul

if exist "%ROOT%tools\SumatraPDF64.exe" (
    if not exist "%OUT%\tools" mkdir "%OUT%\tools"
    copy /Y "%ROOT%tools\SumatraPDF64.exe" "%OUT%\tools\" >nul
)
if exist "%ROOT%tools\SumatraPDF32.exe" (
    if not exist "%OUT%\tools" mkdir "%OUT%\tools"
    copy /Y "%ROOT%tools\SumatraPDF32.exe" "%OUT%\tools\" >nul
)

REM The Tauri MSI is still produced -- it just does not belong INSIDE the folder
REM that gets zipped and published. It was 229 MB of the 473 MB download, an
REM installer riding inside the payload that the installer downloads, and every
REM shop paid for it on a shop's internet line. It now lands in a SIBLING folder
REM under dist\ so that zipping %OUT% cannot pick it up by accident.
set "MSIOUT=%ROOT%dist\SatpudaCore_Desktop_Win10_MSI"
if exist "%MSIOUT%" rmdir /s /q "%MSIOUT%"
if exist "%TDIR%\release\bundle\msi" (
    mkdir "%MSIOUT%"
    xcopy /Y /E /Q "%TDIR%\release\bundle\msi\*" "%MSIOUT%\" >nul 2>&1
)

REM engine\config is where the shell used to drop desktop_api.log while the app
REM ran. A log written on THIS machine was being zipped and shipped to every
REM shop, and on an in-place update the running engine held it open, which is
REM what turned an upgrade into Access Denied. The shell now logs to
REM %%LOCALAPPDATA%%\VeterinaryApp\logs (see desktop\src-tauri\src\lib.rs);
REM this line clears anything an older build left behind, so a folder assembled
REM on a machine that has run the old shell still ships clean.
if exist "%OUT%\engine\config" rmdir /s /q "%OUT%\engine\config"

(
echo Satpuda Core Desktop - Windows 10 / 11 ^(standalone^)
echo Built: %DATE% %TIME%
echo.
echo Run: SatpudaCore_Desktop.exe
echo.
echo Folder layout:
echo   SatpudaCore_Desktop.exe  - Tauri UI shell
echo   engine\SatpudaEngine.exe - Local data engine ^(no Python install needed^)
echo   tools\                    - SumatraPDF for silent bill printing
echo.
echo Store databases, settings and logs live in:
echo   %%LOCALAPPDATA%%\VeterinaryApp\
echo   %%LOCALAPPDATA%%\VeterinaryApp\logs\desktop_api.log  - engine log
echo ^(same as classic SatpudaCore_Win10 — nothing is written beside the EXE,
echo  so an in-place update never has to replace a file that is held open^).
echo.
echo Zip this entire folder for distribution. The Tauri MSI is NOT part of it -
echo it is built to dist\SatpudaCore_Desktop_Win10_MSI\ alongside this folder.
) > "%OUT%\README.txt"

cd /d "%ROOT%"

REM Say what this build is about to hand to a shop. Runs from the repo root
REM so it can import core.*; a clean build STOPS if it carries a credential
REM or another shop's data.
echo [6b/6] Auditing what the build carries...
python "%ROOT%scripts\audit_release_folder.py" "%OUT%"
if %errorlevel% neq 0 (
    echo.
    echo ERROR: this build carries a live credential or another shop's data.
    echo        Fix the list above, or build a paired copy on purpose with:
    echo            set SATPUDA_BUILD=paired
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   TAURI DESKTOP STANDALONE BUILD COMPLETE
echo ============================================================
echo   [OK] %OUT%\SatpudaCore_Desktop.exe
echo   [OK] %OUT%\engine\SatpudaEngine.exe
if exist "%OUT%\tools\SumatraPDF64.exe" echo   [OK] %OUT%\tools\SumatraPDF64.exe
if exist "%MSIOUT%" echo   [OK] %MSIOUT%\  ^(MSI - kept OUT of the zipped folder^)
echo.
echo   Zip dist\SatpudaCore_Desktop_Win10 for distribution.
echo   Do NOT copy the MSI into it - that is 229 MB of installer inside the
echo   payload the installer downloads.
echo ============================================================
pause
endlocal

