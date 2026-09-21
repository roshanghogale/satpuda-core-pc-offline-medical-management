"""Run the Purchase page's REAL TypeScript mapping from Python.

The Tauri Purchase page turns an import's line dicts into its own LineItem with
lineFromPayload(), and back into the save body with tabPayload(). A Python copy
of those would test the copy. So the three functions are cut out of
PurchasePage.tsx as they stand today, compiled with the project's own esbuild,
and run under node: the page's code, not a re-telling of it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TSX = os.path.join(ROOT, "desktop", "src", "pages", "PurchasePage.tsx")
ESBUILD = os.path.join(ROOT, "desktop", "node_modules", ".bin", "esbuild.cmd")


def _cut(src: str, name: str) -> str:
    """One top-level function, from its line to the closing brace at column 0."""
    m = re.search(rf"^function {name}\b", src, re.M)
    if not m:
        raise RuntimeError(f"{name} not found in PurchasePage.tsx")
    end = src.index("\n}\n", m.start())
    return src[m.start():end + 3]


_JS = None


def _compiled() -> str:
    global _JS
    if _JS:
        return _JS
    src = open(TSX, encoding="utf-8").read()
    body = "\n".join(_cut(src, n) for n in ("isStripType", "lineFromPayload", "tabPayload"))
    ts = (
        "type PurchaseRuntimePrefs = any; type PurchaseLinePayload = any;\n"
        "type LineItem = any; type PurchaseTab = any;\n"
        + body
        + "\nconst input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        "const lines = input.items.map(lineFromPayload);\n"
        "const tab = Object.assign({ items: lines }, input.tab);\n"
        "process.stdout.write(JSON.stringify({ lines, payload: tabPayload(tab) }));\n"
    )
    tmp = tempfile.mkdtemp(prefix="ts_bridge_")
    entry = os.path.join(tmp, "bridge.ts")
    out = os.path.join(tmp, "bridge.js")
    open(entry, "w", encoding="utf-8").write(ts)
    subprocess.run([ESBUILD, entry, "--platform=node", f"--outfile={out}", "--log-level=error"],
                   check=True, shell=False)
    _JS = out
    return out


def page_roundtrip(items: list[dict], tab: dict) -> dict:
    """items -> lineFromPayload -> (page) -> tabPayload -> the body the page saves."""
    js = _compiled()
    r = subprocess.run(["node", js], input=json.dumps({"items": items, "tab": tab}),
                       capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(r.stdout)
