#!/usr/bin/env python3
"""Simulate startup alerts dismiss + page navigation keys."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tkinter as tk
from core.keyboard_registry import KeyboardRegistry, PageBindings


def main():
    nav_log = []

    root = tk.Tk()
    root.geometry('400x300')

    class App:
        active_nav = '🏠 Home'
        main_frame = None
        nav_frame = None
        _home_keyboard_bindings = None
        _home_inner_frame = None
        _returns_show = None
        _settings_page = None

        def nav_click(self, cmd, text):
            nav_log.append(text)
            cmd()

    app = App()
    mf = tk.Frame(root)
    nf = tk.Frame(root)
    mf.pack(fill=tk.BOTH, expand=True)
    nf.pack()
    app.main_frame = mf
    app.nav_frame = nf

    inner = tk.Frame(mf)
    inner.pack(fill=tk.BOTH, expand=True)
    btn = tk.Button(inner, text='New Bill')
    btn.pack()
    app._home_inner_frame = inner

    def open_sales():
        app.active_nav = 'Sales'

    KeyboardRegistry.configure_navigation({
        '0': lambda e=None: nav_log.append('Home') or app.nav_click(lambda: None, '🏠 Home'),
        '1': lambda e=None: (app.nav_click(open_sales, 'Sales'), nav_log.append('digit1')),
        '2': lambda e=None: nav_log.append('Purchase'),
    })
    KeyboardRegistry.install(root, app)
    KeyboardRegistry.wire_shell(root, mf, nf)

    hb = PageBindings(
        page_id='home',
        sub_keys={'b': lambda: nav_log.append('Bill')},
    )
    app._home_keyboard_bindings = hb
    KeyboardRegistry.register_page(inner, hb)

    # Simulate modal grab like startup alerts
    alert = tk.Toplevel(root)
    alert.grab_set()
    root.update()

    alert.destroy()
    root.update()
    KeyboardRegistry.finish_modal_session()
    root.update()

    assert KeyboardRegistry._nav_mode is True, 'nav_mode should be True after finish_modal_session'

    # Digit navigation on home button area
    btn.focus_set()
    root.update()
    btn.event_generate('<KeyPress-1>')
    root.update()
    print('nav_log after 1:', nav_log)
    assert 'digit1' in nav_log or 'Sales' in nav_log, nav_log

    nav_log.clear()
    KeyboardRegistry.set_nav_mode(True)
    btn.event_generate('<KeyPress-b>')
    root.update()
    print('nav_log after b:', nav_log)
    assert 'Bill' in nav_log, nav_log

    print('ALERT_KEYBOARD_TEST_OK')
    root.destroy()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
