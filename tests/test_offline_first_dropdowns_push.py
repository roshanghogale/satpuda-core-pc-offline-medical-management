"""Offline-first: villages / medicine types saved on one PC go to the server (audit 25 Oct 2026)."""
import sqlite3

from core import sync_coordinator as sc


def _run(monkeypatch, mode, fail=False):
    sent = []

    def fake_push(conn):
        if fail:
            raise OSError("no internet")
        sent.append(conn)
        return True

    monkeypatch.setattr("core.sync_prefs.get_sync_mode", lambda: mode)
    monkeypatch.setattr(sc, "is_online_mode", lambda: False)
    monkeypatch.setattr("core.server_live.push_dropdowns", fake_push)
    conn = sqlite3.connect(":memory:")
    sc.after_villages_saved(conn)
    sc.after_layout_saved(conn)
    return sent


def test_offline_first_sends_villages_and_layout(monkeypatch):
    from core.sync_prefs import MODE_OFFLINE_FIRST

    assert len(_run(monkeypatch, MODE_OFFLINE_FIRST)) == 2


def test_plain_offline_sends_nothing(monkeypatch):
    from core.sync_prefs import MODE_OFFLINE

    assert _run(monkeypatch, MODE_OFFLINE) == []


def test_no_internet_does_not_break_the_save(monkeypatch):
    from core.sync_prefs import MODE_OFFLINE_FIRST

    assert _run(monkeypatch, MODE_OFFLINE_FIRST, fail=True) == []
