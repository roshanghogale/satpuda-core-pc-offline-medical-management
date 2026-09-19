"""App theme + mode prefs — no Tkinter (safe for SatpudaEngine / Tauri sidecar)."""
from __future__ import annotations

import os
import sys

AVAILABLE_THEMES = {
    'steel-dark':    'Dark  — Steel Blue',
    'charcoal-dark': 'Dark  — Charcoal Grey',
    'crimson-dark':  'Dark  — Crimson Red',
    'rose-dark':     'Dark  — Rose Pink',
    'navy-dark':     'Dark  — Navy Blue',
    'forest-dark':   'Dark  — Forest Green',
    'midnight-dark': 'Dark  — Midnight Violet',
    'amber-dark':    'Dark  — Amber Gold',
    'teal-dark':     'Dark  — Teal Cyan',
    'violet-dark':   'Dark  — Violet Purple',
    'steel-light':    'Light — Steel Blue',
    'charcoal-light': 'Light — Charcoal Grey',
    'crimson-light':  'Light — Crimson Red',
    'rose-light':     'Light — Rose Pink',
    'navy-light':     'Light — Navy Blue',
    'forest-light':   'Light — Forest Green',
    'midnight-light': 'Light — Midnight Violet',
    'amber-light':    'Light — Amber Gold',
    'teal-light':     'Light — Teal Cyan',
    'violet-light':   'Light — Violet Purple',
}


def _theme_config_path() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp', 'theme_config.txt')
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', 'config', 'theme_config.txt')


def load_theme() -> str:
    try:
        path = _theme_config_path()
        if os.path.exists(path):
            t = open(path, encoding='utf-8').read().strip()
            if t in AVAILABLE_THEMES:
                return t
    except Exception:
        pass
    return 'navy-light'


def save_theme(theme: str) -> bool:
    try:
        theme = (theme or "").strip()
        if theme not in AVAILABLE_THEMES:
            return False
        path = os.path.normpath(_theme_config_path())
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(theme)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            with open(_theme_config_path(), "w", encoding="utf-8") as f:
                f.write(theme)
            return True
        except Exception:
            return False


def _app_mode_path() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp', 'app_mode.txt')
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', 'config', 'app_mode.txt')


def load_app_mode() -> str:
    """Return 'medical' or 'veterinary'."""
    try:
        path = _app_mode_path()
        if os.path.exists(path):
            m = open(path, encoding='utf-8').read().strip().lower()
            if m in ('medical', 'veterinary'):
                return m
    except Exception:
        pass
    return 'medical'


def save_app_mode(mode: str) -> None:
    try:
        with open(_app_mode_path(), 'w', encoding='utf-8') as f:
            f.write(mode)
    except Exception:
        pass
