@echo off
cd /d "%~dp0purchase-entry-web"

REM ── Ensure npm/node are on PATH (NVM vars don’t expand on double-click) ───────
set "PATH=%PATH%;C:\nvm4w\nodejs;C:\Users\rosha\AppData\Roaming\npm"

set "SILENT=%~1"

if /i "%SILENT%"=="silent" goto :build

echo ============================================
echo  Purchase Entry Web App - Build
echo ============================================
echo.

:build
if not exist "node_modules" (
    if /i not "%SILENT%"=="silent" echo [1/4] Installing dependencies (first time only)...
    npm install
    if %errorlevel% neq 0 (
        if /i not "%SILENT%"=="silent" echo ERROR: npm install failed. Make sure Node.js is installed.
        if /i not "%SILENT%"=="silent" pause
        exit /b 1
    )
    if /i not "%SILENT%"=="silent" echo Done.
)

if /i not "%SILENT%"=="silent" echo [2/4] Building web app...
npm run build
if %errorlevel% neq 0 (
    if /i not "%SILENT%"=="silent" echo ERROR: Build failed.
    if /i not "%SILENT%"=="silent" pause
    exit /b 1
)
if /i not "%SILENT%"=="silent" echo Done.

if /i not "%SILENT%"=="silent" echo [3/4] Copying to web_app folder...
copy /Y "dist\index.html" "..\web_app\index.html" >nul
if exist "dist\assets" (
    if not exist "..\web_app\assets" mkdir "..\web_app\assets"
    xcopy /Y /E /Q "dist\assets\*" "..\web_app\assets\" >nul
)
if /i not "%SILENT%"=="silent" echo Done.

if /i not "%SILENT%"=="silent" (
    echo [4/4] Opening web app in browser...
    start "" "%~dp0..\web_app\index.html"
    echo.
    echo ============================================
    echo  Build complete! web_app\index.html is ready.
    echo  Now rebuild the exe: build_exe.bat
    echo ============================================
    echo.
    pause
)
