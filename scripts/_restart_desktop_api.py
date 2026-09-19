"""Kill stale API on 8765 and start current revision."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request

out = subprocess.check_output(["netstat", "-ano", "-p", "TCP"], text=True, errors="ignore")
pids = set()
for line in out.splitlines():
    if "127.0.0.1:8765" in line and "LISTENING" in line:
        pids.add(line.split()[-1])
for pid in pids:
    subprocess.run(
        ["taskkill", "/PID", pid, "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("killed", pid)

time.sleep(0.6)
subprocess.Popen(
    ["python", "run_desktop_api.py"],
    cwd=r"C:\Users\win10\Downloads\mac2 (2)\mac2",
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

for _ in range(50):
    time.sleep(0.3)
    try:
        h = json.loads(
            urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=1)
            .read()
            .decode()
        )
        print("health", h.get("revision"), h.get("db_path"))
        if int(h.get("revision") or 0) >= 7:
            inv = json.loads(
                urllib.request.urlopen("http://127.0.0.1:8765/api/inventory")
                .read()
                .decode()
            )
            print(
                "inv",
                len(inv.get("rows") or []),
                "types",
                len(inv.get("types") or []),
                "cols",
                inv.get("columns"),
            )
            sh = json.loads(
                urllib.request.urlopen("http://127.0.0.1:8765/api/sales/history")
                .read()
                .decode()
            )
            print("sales", len(sh.get("rows") or []), sh.get("columns")[:5])
            sf = json.loads(
                urllib.request.urlopen("http://127.0.0.1:8765/api/sales/form")
                .read()
                .decode()
            )
            print(
                "form customers",
                len(sf.get("customers") or []),
                "doctors",
                len(sf.get("doctors") or []),
                "medicines",
                len(sf.get("medicines") or []),
            )
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as exc:
        print("wait", exc)

raise SystemExit("API did not reach revision 7")
