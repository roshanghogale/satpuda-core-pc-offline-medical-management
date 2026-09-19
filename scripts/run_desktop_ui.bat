@echo off

REM Short Rust target path: MSVC link.exe cannot take a 32K command line.
REM Set here (not in .cargo/config.toml) so non-Windows builds still work.
set "CARGO_TARGET_DIR=C:\satpuda-rs"

setlocal
cd /d "%~dp0.."

echo Starting Satpuda desktop (local data engine starts with the window)...
cd desktop
call npm run tauri:dev
endlocal
