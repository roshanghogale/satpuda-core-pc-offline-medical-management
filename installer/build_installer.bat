@echo off
REM ---------------------------------------------------------------------------
REM  Compile the Satpuda Core installer.
REM  Run this on the Windows build PC, from this folder.
REM  Result: Output\SatpudaCoreInstaller.exe
REM ---------------------------------------------------------------------------
setlocal

set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if not defined ISCC (
    echo.
    echo   Inno Setup 6 was not found.
    echo.
    echo   Download Inno Setup 6.3 or newer from https://jrsoftware.org/isdl.php
    echo   ^(innosetup-6.5.x.exe^), install it with the default options, then run
    echo   this file again.
    echo.
    echo   6.3 is the minimum - the script uses ArchitecturesAllowed=x64compatible,
    echo   which older versions reject.
    echo.
    exit /b 1
)

REM  The wizard header logo. Three BMPs, referenced from [Setup]. ISCC's own
REM  message for a missing one is a bare "file not found", so check for them
REM  here where the fix can be spelled out.
for %%B in (wizard_logo.bmp wizard_logo_150.bmp wizard_logo_200.bmp) do (
    if not exist "%~dp0%%B" (
        echo.
        echo   Missing %%B
        echo.
        echo   The wizard header logo is built from satpuda.ico. Regenerate all
        echo   three sizes with:  python make_wizard_logo.py
        echo   ^(needs Pillow^), or copy the .bmp files from the repository.
        echo.
        exit /b 1
    )
)

echo Using: %ISCC%
"%ISCC%" "%~dp0SatpudaCore.iss"
if errorlevel 1 (
    echo.
    echo   COMPILE FAILED - see the messages above.
    exit /b 1
)

echo.
echo   Built: %~dp0Output\SatpudaCoreInstaller.exe
for %%F in ("%~dp0Output\SatpudaCoreInstaller.exe") do echo   Size:  %%~zF bytes
echo.
endlocal
