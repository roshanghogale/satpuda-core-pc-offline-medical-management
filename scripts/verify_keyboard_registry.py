#!/usr/bin/env python3
"""Verify KeyboardRegistry installs and all bind sequences are Tk-valid."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INVALID_BIND_RE = (
    '<KeyPress-asciigrave>',
    '<Key-asciigrave>',
    '<asciigrave>',
    'KeyPress-asciigrave',
    'KeyPress-backquote',
    'KeyPress-quoteleft',
)


def scan_invalid_keysyms() -> list[str]:
    issues = []
    for py in ROOT.rglob('*.py'):
        if 'venv' in py.parts or '__pycache__' in py.parts:
            continue
        if py.name == 'verify_keyboard_registry.py':
            continue
        try:
            text = py.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for pat in INVALID_BIND_RE:
            if pat in text:
                issues.append(f'{py.relative_to(ROOT)}: uses invalid bind {pat!r}')
    return issues


def test_registry_install() -> list[str]:
    import tkinter as tk
    from core.keyboard_registry import KeyboardRegistry

    errors: list[str] = []
    root = tk.Tk()
    root.withdraw()

    class FakeApp:
        active_nav = None
        main_frame = root

        def nav_click(self, *a):
            pass

    handlers = {
        '`': lambda: None,
        '0': lambda: None,
        '1': lambda: None,
        '2': lambda: None,
        '3': lambda: None,
        '4': lambda: None,
        '5': lambda: None,
        '6': lambda: None,
        '7': lambda: None,
    }
    KeyboardRegistry.configure_navigation(handlers)
    try:
        KeyboardRegistry.install(root, FakeApp())
    except Exception as exc:
        errors.append(f'KeyboardRegistry.install failed: {exc}')
        root.destroy()
        return errors

    if not KeyboardRegistry._installed:
        errors.append('KeyboardRegistry._installed is False after install')
    if len(KeyboardRegistry._page_digit_handlers) < 9:
        errors.append(
            f'Expected 9 page digit handlers, got {len(KeyboardRegistry._page_digit_handlers)}'
        )
    if not KeyboardRegistry._bound_sequences:
        errors.append('No bind_all sequences recorded')

    for seq in KeyboardRegistry._bound_sequences:
        for bad in _INVALID_BIND_KEYSYMS:
            if bad in seq.lower():
                errors.append(f'Invalid bound sequence: {seq}')

    root.destroy()
    return errors


def print_audit_table():
    from core.keyboard_registry import KeyboardRegistry

    rows = KeyboardRegistry.audit_bindings()
    print('\nShortcut Registry Audit')
    print('-' * 90)
    print(f'{"Shortcut":<18} {"Handler":<36} {"Page":<22} Status')
    print('-' * 90)
    for row in rows:
        print(
            f'{row["shortcut"]:<18} '
            f'{row["handler"]:<36} '
            f'{row["page"]:<22} '
            f'{row["status"]}'
        )
    print('-' * 90)
    print(f'Total global shortcuts documented: {len(rows)}')


_INVALID_BIND_KEYSYMS = frozenset({'asciigrave', 'backquote', 'quoteleft'})


def main():
    print('Keyboard Registry Verification')
    print('=' * 40)

    scan_issues = scan_invalid_keysyms()
    install_issues = test_registry_install()

    all_issues = scan_issues + install_issues
    if all_issues:
        print('ISSUES:')
        for issue in all_issues:
            print(' -', issue)
    else:
        print('Scan: no invalid keysyms in codebase')
        print('Install: KeyboardRegistry OK')

    print_audit_table()

    # can_process_global_shortcut smoke test
    import tkinter as tk
    from core.keyboard_registry import KeyboardRegistry

    root = tk.Tk()
    entry = tk.Entry(root)
    entry.pack()
    KeyboardRegistry._root = root
    entry.focus_force()
    root.update()
    blocked = not KeyboardRegistry.can_process_global_shortcut(root)
    root.focus_force()
    root.update()
    allowed = KeyboardRegistry.can_process_global_shortcut(root)
    root.destroy()
    print(f'\ncan_process_global_shortcut: entry focused blocked={blocked}, blur allowed={allowed}')
    if not blocked or not allowed:
        all_issues.append('can_process_global_shortcut: unexpected focus detection')

    return 1 if all_issues else 0


if __name__ == '__main__':
    raise SystemExit(main())
