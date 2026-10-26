"""Compare two --dump folders of time_pages.py (before / after a change): same answers?

usage: python tests/perf/compare_answers.py DIR_A DIR_B
Prints the first differing paths per answer. Fields that are the clock (generated / checked at)
are ignored.
"""
from __future__ import annotations

import json
import os
import sys

VOLATILE = {"generated_at", "checked_at", "now", "server_time", "elapsed_ms", "took_ms", "ms", "seconds",
            "t", "ts", "last_checked", "computed_at", "built_at", "age_s", "db_path",
            "device_key_path", "port", "api_port", "print_log_path"}


def diff(a, b, path="", out=None, limit=8):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in VOLATILE:
                continue
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'B' if k not in a else 'A'}")
                continue
            diff(a[k], b[k], f"{path}/{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} vs {len(b)} items")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, limit)
            if len(out) >= limit:
                break
    elif a != b:
        out.append(f"{path}: {str(a)[:80]!r} vs {str(b)[:80]!r}")
    return out


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    da, db = argv[0], argv[1]
    bad = 0
    for name in sorted(os.listdir(da)):
        pa, pb = os.path.join(da, name), os.path.join(db, name)
        if not os.path.exists(pb):
            print(f"{name}: missing in B")
            bad += 1
            continue
        ra, rb = open(pa, "rb").read(), open(pb, "rb").read()
        if ra == rb:
            continue
        try:
            ja, jb = json.loads(ra), json.loads(rb)
        except ValueError:
            print(f"{name}: bytes differ (not JSON)")
            bad += 1
            continue
        d = diff(ja, jb)
        if d:
            bad += 1
            print(f"{name}: DIFF")
            for line in d:
                print("   ", line)
    print("same" if not bad else f"{bad} answer(s) differ")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
