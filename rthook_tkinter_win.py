"""
PyInstaller runtime hook — runs before main.py and before tkinter import.

Fixes on Windows (Python 3.8+):
  ImportError: DLL load failed while importing _tkinter
  The specified module could not be found.

Frozen apps must put sys._MEIPASS on the DLL search path so _tkinter.pyd
can load tcl86t.dll / tk86t.dll bundled next to it.
"""
import os
import sys

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    base = os.path.abspath(sys._MEIPASS)

    if hasattr(os, 'add_dll_directory'):
        try:
            os.add_dll_directory(base)
        except (OSError, AttributeError):
            pass

    path = os.environ.get('PATH', '')
    if base not in path.split(os.pathsep):
        os.environ['PATH'] = base + os.pathsep + path

    tcldir = os.path.join(base, '_tcl_data')
    tkdir = os.path.join(base, '_tk_data')
    if os.path.isdir(tcldir):
        os.environ['TCL_LIBRARY'] = tcldir
    if os.path.isdir(tkdir):
        os.environ['TK_LIBRARY'] = tkdir
