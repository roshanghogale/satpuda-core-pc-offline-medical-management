@echo off
REM ===========================================================================
REM  The build-drift gate, run ON THE BUILD PC. Use this only when the Mac
REM  cannot reach the PC; the normal gate is one command on the Mac:
REM
REM      cd "/Volumes/Extreme SSD/projects/mac2"
REM      python3 tools/verify_build_tree.py check
REM
REM  HOW TO USE THIS ONE
REM    1. On the Mac:  python3 tools/verify_build_tree.py manifest --out mac.manifest
REM    2. Copy mac.manifest to the build PC.
REM    3. Here:        tools\verify_build_tree.cmd C:\path\to\mac.manifest
REM
REM  It hashes THIS tree, compares it with the Mac's, and sets ERRORLEVEL:
REM      0  identical  -- safe to run build_desktop_win10_folder.bat
REM      2  drift      -- DO NOT BUILD; the listed files are not the Mac's
REM      3  could not decide -- also do not build
REM
REM  Why it exists: the 2026-09-14 release shipped widgets\activation_dialog.py
REM  from 2026-08-07, a month older than the Mac's, because this repo is an
REM  incremental copy that nothing verified. Activation has been broken for every
REM  new shop since. Nothing else in the build can see that kind of mistake.
REM
REM  It only reads the tree. The single file it writes is the temporary manifest
REM  in %TEMP%.
REM ===========================================================================
setlocal
set "ROOT=%~dp0.."
set "MAC_MANIFEST=%~1"

if "%MAC_MANIFEST%"=="" (
    echo ERROR: pass the Mac's manifest file.
    echo        tools\verify_build_tree.cmd C:\path\to\mac.manifest
    exit /b 3
)
if not exist "%MAC_MANIFEST%" (
    echo ERROR: no such manifest: %MAC_MANIFEST%
    exit /b 3
)

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: python not found on PATH.
    exit /b 3
)

set "BUILD_MANIFEST=%TEMP%\satpuda_build_tree.manifest"
python "%ROOT%\tools\verify_build_tree.py" manifest --root "%ROOT%" --side build-repo --out "%BUILD_MANIFEST%"
if %errorlevel% neq 0 (
    echo ERROR: could not hash this tree.
    exit /b 3
)

python "%ROOT%\tools\verify_build_tree.py" compare "%MAC_MANIFEST%" "%BUILD_MANIFEST%"
set "GATE=%errorlevel%"
if "%GATE%"=="0" (
    echo.
    echo Build tree verified. Safe to build.
) else (
    echo.
    echo DO NOT BUILD. Fix the files listed above first.
)
exit /b %GATE%
