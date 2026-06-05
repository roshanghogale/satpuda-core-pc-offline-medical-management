"""
PyInstaller runtime hook — runs before main.py and before tkinter import.
"""
import os
import sys

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    base = os.path.abspath(sys._MEIPASS)
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))

    for key in ('TCL_LIBRARY', 'TK_LIBRARY'):
        old = os.environ.get(key, '')
        if old and '_MEI' in old and not os.path.isdir(old):
            os.environ.pop(key, None)

    if hasattr(os, 'add_dll_directory'):
        for folder in (base, exe_dir):
            try:
                os.add_dll_directory(folder)
            except (OSError, AttributeError):
                pass

    path_parts = [base, exe_dir]
    for part in os.environ.get('PATH', '').split(os.pathsep):
        if not part or '_MEI' in part.upper():
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

    try:
        import ctypes
        for name in ('tcl86t.dll', 'tk86t.dll', 'tcl86.dll', 'tk86.dll'):
            dll_path = os.path.join(base, name)
            if os.path.isfile(dll_path):
                ctypes.WinDLL(dll_path)
    except Exception:
        pass
