@echo off
title Satpuda Core - Release folder builds
set "ROOT=%~dp0"
cd /d "%ROOT%"
echo [prep]
if not exist config\expiry.dat python make_demo_expiry.py >nul 2>&1
echo [1/2] Win10/11 release folder...
pip install pyinstaller cryptography reportlab openpyxl ttkbootstrap pillow pdfplumber pypdfium2 pywin32 certifi >nul 2>&1
if exist dist\SatpudaCore_Win10 rmdir /s /q dist\SatpudaCore_Win10
pyinstaller VeterinaryApp_Release_Win10_Folder.spec --noconfirm
if errorlevel 1 exit /b 1
echo [2/2] Win7 release folder...
set WIN7_PY=py -3.8-32
%WIN7_PY% patch_pyinstaller_win7.py
if exist dist\SatpudaCore_Win7 rmdir /s /q dist\SatpudaCore_Win7
%WIN7_PY% -m PyInstaller VeterinaryApp_Win7_Folder.spec --noconfirm
if errorlevel 1 exit /b 1
%WIN7_PY% patch_pyinstaller_win7.py --restore
echo [post] Bundle runtime DLLs + Sumatra tools (no zip)...
python bundle_portable_runtime.py
if errorlevel 1 exit /b 1
echo Done: release_zips\
