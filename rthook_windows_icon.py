"""
PyInstaller runtime hook — runs before main.py.
Sets Windows AppUserModelID so the taskbar uses SatpudaCore.exe's embedded icon.
"""
import sys

if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            'SatpudaMedical.SatpudaCore.1',
        )
    except Exception:
        pass
