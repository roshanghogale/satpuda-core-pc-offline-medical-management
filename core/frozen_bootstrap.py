"""
Frozen EXE startup and restart helpers.

Fixes tkinter failing after restart/update when stale TCL_LIBRARY / PATH entries
from a parent PyInstaller process point at deleted _MEI folders, or when Tcl/Tk
DLLs are not on the Windows DLL search path before _tkinter loads.
"""
from __future__ import annotations

import os
import sys


def prepare_frozen_runtime() -> None:
    """Register bundled Tcl/Tk DLLs and script dirs. Safe to call multiple times."""
    if not getattr(sys, 'frozen', False) or not hasattr(sys, '_MEIPASS'):
        return

    base = os.path.abspath(sys._MEIPASS)
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))

    for key in ('TCL_LIBRARY', 'TK_LIBRARY'):
        old = os.environ.get(key, '')
        if old and ('_MEI' in old or '_MEIPASS' in old):
            if not os.path.isdir(old):
                os.environ.pop(key, None)

    if hasattr(os, 'add_dll_directory'):
        for folder in (base, exe_dir, os.path.join(base, 'DLLs')):
            if folder and os.path.isdir(folder):
                try:
                    os.add_dll_directory(folder)
                except (OSError, AttributeError):
                    pass

    path_parts: list[str] = []
    for folder in (base, exe_dir):
        if folder and folder not in path_parts:
            path_parts.append(folder)

    for part in os.environ.get('PATH', '').split(os.pathsep):
        if not part:
            continue
        if '_MEI' in part.upper():
            continue
        if part not in path_parts:
            path_parts.append(part)
    os.environ['PATH'] = os.pathsep.join(path_parts)

    tcldir = os.path.join(base, '_tcl_data')
    tkdir = os.path.join(base, '_tk_data')
    if os.path.isdir(tcldir):
        os.environ['TCL_LIBRARY'] = tcldir
    if os.path.isdir(tkdir):
        os.environ['TK_LIBRARY'] = tkdir

    if sys.platform == 'win32':
        try:
            import ctypes
            for name in (
                'tcl86t.dll', 'tk86t.dll', 'tcl86.dll', 'tk86.dll',
                'sqlite3.dll', 'python313.dll',
            ):
                dll_path = os.path.join(base, name)
                if os.path.isfile(dll_path):
                    ctypes.WinDLL(dll_path)
        except OSError:
            pass


def clean_env_for_child_process() -> dict:
    """Drop stale PyInstaller paths before spawning a restarted EXE."""
    env = os.environ.copy()
    for key in (
        'TCL_LIBRARY',
        'TK_LIBRARY',
        'PYTHONHOME',
        'PYTHONPATH',
        'PYTHONEXECUTABLE',
        '_MEIPASS',
        '_MEIPASS2',
    ):
        env.pop(key, None)

    cleaned: list[str] = []
    for part in env.get('PATH', '').split(os.pathsep):
        if not part:
            continue
        upper = part.upper()
        if '_MEI' in upper or '_MEIPASS' in upper:
            continue
        if part not in cleaned:
            cleaned.append(part)
    env['PATH'] = os.pathsep.join(cleaned)
    return env


def relaunch_executable(root=None) -> None:
    """Restart the running EXE with a clean environment and correct working directory."""
    if not getattr(sys, 'frozen', False):
        import subprocess

        args = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
        cwd = os.path.dirname(os.path.abspath(sys.argv[0]))
        try:
            if root is not None:
                try: root.quit()
                except Exception: pass
                try: root._style = type('_S', (), {'instance': None})()
                except Exception: pass
                try: root.destroy()
                except Exception: pass
            subprocess.Popen(args, cwd=cwd)
        except Exception:
            pass
        sys.exit(0)

    # Frozen EXE: replace the current process (no parallel extraction/locks).
    exe_path = os.path.abspath(sys.executable)
    args = [exe_path] + sys.argv[1:]
    env = clean_env_for_child_process()

    if root is not None:
        try: root.quit()
        except Exception: pass
        try: root._style = type('_S', (), {'instance': None})()
        except Exception: pass
        try: root.destroy()
        except Exception: pass

    # os.execve does not spawn a new process; it avoids PyInstaller temp extraction races.
    os.execve(exe_path, args, env)


def build_update_batch(
    app_dir: str,
    exe_name: str,
    staging_name: str,
    bat_path: str,
) -> str:
    """Write a robust swap-and-restart script for in-app EXE updates."""
    app_dir = os.path.abspath(app_dir)
    exe_path = os.path.join(app_dir, exe_name)
    staging_path = os.path.join(app_dir, staging_name)
    backup_path = exe_path + '.bak'
    bat_path = os.path.abspath(bat_path)

    lines = [
        '@echo off',
        'setlocal',
        f'cd /d "{app_dir}"',
        'set TCL_LIBRARY=',
        'set TK_LIBRARY=',
        'set PYTHONHOME=',
        'if not exist "{staging}" ('.format(staging=staging_path),
        '  exit /b 1',
        ')',
        ':wait_old',
        'timeout /t 2 /nobreak >nul',
        f'copy /Y "{staging_path}" "{exe_path}" >nul 2>&1',
        'if errorlevel 1 goto wait_old',
        f'if exist "{backup_path}" del /f /q "{backup_path}"',
        f'if exist "{staging_path}" del /f /q "{staging_path}"',
        f'start "" /D "{app_dir}" "{exe_path}"',
        f'del /f /q "{bat_path}"',
    ]
    return '\r\n'.join(lines) + '\r\n'
