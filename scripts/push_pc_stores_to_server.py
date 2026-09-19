"""Push all local Mac2 stores from this PC to Satpuda Core Server.

Usage (from mac2 folder or with PYTHONPATH=mac2):
  python scripts/push_pc_stores_to_server.py
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    from core import server_api as api
    from core import server_sync

    print(f"API: {api.api_base()}")
    print("Health:", api.health())

    def progress(msg: str) -> None:
        print(msg, flush=True)

    total, messages = server_sync.push_all_local_stores_to_server(progress_cb=progress)
    print()
    print("=== Done ===")
    for m in messages:
        print(m)
    print(f"Total upserted: {total:,}")
    return 0 if not any("FAILED" in m for m in messages) else 1


if __name__ == "__main__":
    raise SystemExit(main())
