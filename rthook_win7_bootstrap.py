"""Win7 runtime hook: system DLLs before bundled PATH; pywin32 on PATH."""
import os
import sys

if sys.platform != 'win32' or not getattr(sys, 'frozen', False) or not hasattr(sys, '_MEIPASS'):
    raise SystemExit(0)

base = os.path.abspath(sys._MEIPASS)
exe_dir = os.path.dirname(os.path.abspath(sys.executable))
system_root = os.environ.get('SystemRoot', r'C:\Windows')
syswow64 = os.path.join(system_root, 'SysWOW64')
system32 = os.path.join(system_root, 'System32')

path_parts = []
for folder in (syswow64, system32, base, exe_dir):
    if os.path.isdir(folder) and folder not in path_parts:
        path_parts.append(folder)
for part in os.environ.get('PATH', '').split(os.pathsep):
    if part and '_MEI' not in part.upper() and part not in path_parts:
        path_parts.append(part)
os.environ['PATH'] = os.pathsep.join(path_parts)

pywin32_sys = os.path.join(base, 'pywin32_system32')
if os.path.isdir(pywin32_sys):
    if pywin32_sys not in sys.path:
        sys.path.append(pywin32_sys)
    if pywin32_sys not in path_parts:
        os.environ['PATH'] = pywin32_sys + os.pathsep + os.environ.get('PATH', '')
