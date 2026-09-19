"""UPI payment QR settings for printed bills."""
from __future__ import annotations

import os
import sys

AMOUNT_TOTAL = 'total'
AMOUNT_TOTAL_PLUS_PREV_DUE = 'total_plus_prev_due'
_AMOUNT_MODES = (AMOUNT_TOTAL, AMOUNT_TOTAL_PLUS_PREV_DUE)


def _config_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp',
        )
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'config',
    )


def load_upi_qr_enabled() -> bool:
    path = os.path.join(_config_dir(), 'upi_qr_enabled.txt')
    try:
        if os.path.exists(path):
            raw = open(path, encoding='utf-8').read().strip().lower()
            return raw in ('1', 'true', 'yes', 'on')
    except Exception:
        pass
    return False


def save_upi_qr_enabled(enabled: bool) -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    with open(os.path.join(_config_dir(), 'upi_qr_enabled.txt'), 'w', encoding='utf-8') as f:
        f.write('1' if enabled else '0')


def load_upi_id() -> str:
    path = os.path.join(_config_dir(), 'upi_id.txt')
    try:
        if os.path.exists(path):
            return open(path, encoding='utf-8').read().strip()
    except Exception:
        pass
    return ''


def save_upi_id(upi_id: str) -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    cleaned = (upi_id or '').strip().lower().replace(' ', '')
    with open(os.path.join(_config_dir(), 'upi_id.txt'), 'w', encoding='utf-8') as f:
        f.write(cleaned)


def load_upi_qr_amount_mode() -> str:
    path = os.path.join(_config_dir(), 'upi_qr_amount_mode.txt')
    try:
        if os.path.exists(path):
            raw = open(path, encoding='utf-8').read().strip().lower()
            if raw in _AMOUNT_MODES:
                return raw
    except Exception:
        pass
    return AMOUNT_TOTAL


def save_upi_qr_amount_mode(mode: str) -> None:
    val = (mode or '').strip().lower()
    if val not in _AMOUNT_MODES:
        val = AMOUNT_TOTAL
    os.makedirs(_config_dir(), exist_ok=True)
    with open(os.path.join(_config_dir(), 'upi_qr_amount_mode.txt'), 'w', encoding='utf-8') as f:
        f.write(val)


def upi_amount_mode_label(mode: str) -> str:
    if mode == AMOUNT_TOTAL_PLUS_PREV_DUE:
        return 'Bill total + previous due'
    return 'Bill total only'


def upi_amount_mode_from_label(label: str) -> str:
    if 'previous due' in (label or '').lower():
        return AMOUNT_TOTAL_PLUS_PREV_DUE
    return AMOUNT_TOTAL
