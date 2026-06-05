"""
Tcl/Tk files for PyInstaller — fixes:
  ImportError: DLL load failed while importing _tkinter
  Failed to extract _tcl_data\\auto.tcl\\auto.tcl (wrong dest = file path, not folder)

PyInstaller 6.x tcltk_info.data_files uses (dest_path, src_path, 'DATA') where dest_path
includes the filename. Analysis datas need (src, dest_dir) with dest_dir = parent folder only.
"""
import os
import sys


def _normalize_data_dest(src: str, dest: str) -> str:
    """Turn '_tcl_data/auto.tcl' into '_tcl_data' for single-file datas tuples."""
    dest = dest.replace('\\', '/')
    src_base = os.path.basename(src)
    if dest.endswith('/' + src_base) or dest == src_base:
        parent = os.path.dirname(dest)
        return parent if parent else '.'
    return dest


def tcl_tk_datas_and_binaries():
    datas = []
    binaries = []
    try:
        from PyInstaller.utils.hooks.tcl_tk import tcltk_info
        if tcltk_info.available:
            for entry in tcltk_info.data_files:
                if len(entry) == 3:
                    dest, src, _typ = entry
                    datas.append((src, _normalize_data_dest(src, dest)))
                elif len(entry) == 2:
                    a, b = entry
                    # Support both (src, dest) and (dest, src)
                    if os.path.isfile(a):
                        src, dest = a, b
                    elif os.path.isfile(b):
                        dest, src = a, b
                    else:
                        src, dest = a, b
                    datas.append((src, _normalize_data_dest(src, dest)))
    except Exception:
        pass

    dll_dir = os.path.join(sys.base_prefix, 'DLLs')
    for dll_name in ('tcl86t.dll', 'tk86t.dll', 'tcl86.dll', 'tk86.dll'):
        src = os.path.join(dll_dir, dll_name)
        if os.path.isfile(src):
            binaries.append((src, '.'))

    try:
        import _tkinter
        ext = getattr(_tkinter, '__file__', None)
        if ext and os.path.isfile(ext):
            binaries.append((ext, '.'))
    except ImportError:
        pass

    tcl_root = os.path.join(sys.base_prefix, 'tcl')
    for sub, dll_name in (('dde1.4', 'tcldde14.dll'), ('reg1.3', 'tclreg13.dll')):
        src = os.path.join(tcl_root, sub, dll_name)
        if os.path.isfile(src):
            binaries.append((src, os.path.join('tcl', sub).replace('\\', '/')))

    return datas, binaries
