"""Windows capture devices via MMDevice API — same list as Settings > Sound > Input."""
from __future__ import annotations

import sys
from typing import List


def list_windows_capture_devices() -> List[dict]:
    """
    Active recording endpoints exactly as Windows Settings shows them.

    Returns dicts: name, id, is_default, index
    """
    if sys.platform != "win32":
        return []
    try:
        return _list_via_ctypes()
    except Exception:
        return []


def _list_via_ctypes() -> List[dict]:
    import ctypes
    from ctypes import POINTER, byref, c_int, c_ulong, c_void_p, c_wchar_p, cast, wintypes

    ole32 = ctypes.windll.ole32
    try:
        ole32.CoInitialize(None)
    except Exception:
        pass

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

        @classmethod
        def from_str(cls, s: str):
            g = cls()
            hr = ole32.CLSIDFromString(ctypes.c_wchar_p(s), byref(g))
            if hr != 0:
                raise OSError("CLSIDFromString failed for %s" % s)
            return g

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", wintypes.USHORT),
            ("wReserved1", wintypes.USHORT),
            ("wReserved2", wintypes.USHORT),
            ("wReserved3", wintypes.USHORT),
            ("data", c_void_p),
        ]

    CLSCTX_ALL = 23
    eCapture = 1
    eConsole = 0
    DEVICE_STATE_ACTIVE = 1
    STGM_READ = 0
    VT_LPWSTR = 31

    clsid = GUID.from_str("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    iid_enum = GUID.from_str("{A95664D2-9614-4F35-A746-DE8DB63617E6}")

    pkey = PROPERTYKEY()
    pkey.fmtid = GUID.from_str("{A45C254E-DF1C-4EFD-8020-67D146A850E0}")
    pkey.pid = 14  # PKEY_Device_FriendlyName

    enumerator = c_void_p()
    hr = ole32.CoCreateInstance(
        byref(clsid), None, CLSCTX_ALL, byref(iid_enum), byref(enumerator),
    )
    if hr != 0 or not enumerator:
        raise OSError("CoCreateInstance failed: 0x%08X" % (hr & 0xFFFFFFFF))

    def vtable(obj):
        return cast(cast(obj, POINTER(c_void_p))[0], POINTER(c_void_p))

    vt_enum = vtable(enumerator)
    EnumAudioEndpoints = ctypes.WINFUNCTYPE(
        ctypes.HRESULT, c_void_p, c_int, c_ulong, POINTER(c_void_p),
    )(vt_enum[3])
    GetDefaultAudioEndpoint = ctypes.WINFUNCTYPE(
        ctypes.HRESULT, c_void_p, c_int, c_int, POINTER(c_void_p),
    )(vt_enum[4])

    collection = c_void_p()
    hr = EnumAudioEndpoints(enumerator, eCapture, DEVICE_STATE_ACTIVE, byref(collection))
    if hr != 0 or not collection:
        raise OSError("EnumAudioEndpoints failed: 0x%08X" % (hr & 0xFFFFFFFF))

    vt_col = vtable(collection)
    GetCount = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(c_ulong))(vt_col[3])
    Item = ctypes.WINFUNCTYPE(
        ctypes.HRESULT, c_void_p, c_ulong, POINTER(c_void_p),
    )(vt_col[4])

    count = c_ulong(0)
    GetCount(collection, byref(count))

    default_id = None
    default_dev = c_void_p()
    if GetDefaultAudioEndpoint(enumerator, eCapture, eConsole, byref(default_dev)) == 0 and default_dev:
        GetId = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, c_void_p, POINTER(c_wchar_p),
        )(vtable(default_dev)[5])
        pid = c_wchar_p()
        if GetId(default_dev, byref(pid)) == 0 and pid:
            default_id = pid.value
            ole32.CoTaskMemFree(pid)

    devices = []
    for i in range(int(count.value)):
        device = c_void_p()
        if Item(collection, i, byref(device)) != 0 or not device:
            continue
        vt_dev = vtable(device)
        OpenPropertyStore = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, c_void_p, c_ulong, POINTER(c_void_p),
        )(vt_dev[4])
        GetId = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, c_void_p, POINTER(c_wchar_p),
        )(vt_dev[5])

        store = c_void_p()
        if OpenPropertyStore(device, STGM_READ, byref(store)) != 0 or not store:
            continue

        GetValue = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT),
        )(vtable(store)[5])

        pv = PROPVARIANT()
        name = None
        if GetValue(store, byref(pkey), byref(pv)) == 0:
            if pv.vt == VT_LPWSTR and pv.data:
                name = ctypes.wstring_at(pv.data)
            try:
                ole32.PropVariantClear(byref(pv))
            except Exception:
                pass

        dev_id = None
        pid = c_wchar_p()
        if GetId(device, byref(pid)) == 0 and pid:
            dev_id = pid.value
            ole32.CoTaskMemFree(pid)

        if not name:
            continue
        devices.append({
            "name": name.strip(),
            "id": dev_id or "",
            "is_default": bool(default_id and dev_id == default_id),
            "index": i,
        })

    return devices


if __name__ == "__main__":
    for d in list_windows_capture_devices():
        mark = " *" if d.get("is_default") else ""
        print(d["name"] + mark)
