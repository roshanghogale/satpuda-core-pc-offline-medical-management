"""A PC activated by the installer saved paired_at as "...+00:00"; seven days later, when
its token ran out, the re-pair check threw "can't subtract offset-naive and offset-aware
datetimes" on every server call (a shop, 2026-10-13)."""
import datetime as dt

from core import server_api


def test_installer_paired_at_is_read():
    now = dt.datetime.now(dt.timezone.utc)
    assert server_api._paired_within({"paired_at": now.isoformat()}, 10) is True
    old = now - dt.timedelta(days=7)
    assert server_api._paired_within({"paired_at": old.isoformat()}, 10) is False


def test_pairing_paired_at_still_read():
    now = dt.datetime.utcnow()
    assert server_api._paired_within({"paired_at": now.isoformat() + "Z"}, 10) is True
    assert server_api._paired_within({"paired_at": ""}, 10) is False
    assert server_api._paired_within({"paired_at": "junk"}, 10) is False


def test_expired_installer_session_pairs_again(monkeypatch):
    stale = {"token": "x.y.z", "paired_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)).isoformat()}
    saved = {}
    monkeypatch.setattr(server_api, "load_session", lambda k: dict(saved.get(k) or stale))
    monkeypatch.setattr(server_api, "save_session", lambda k, d: saved.__setitem__(k, d))
    monkeypatch.setattr(server_api, "_token_exp", lambda t: 0.0)
    monkeypatch.setattr(server_api, "get_license", lambda t: (_ for _ in ()).throw(
        server_api.ServerHttpError(401, "expired")))
    monkeypatch.setattr(server_api, "pair_store", lambda **kw: {"token": "new", "store": {"store_id": "s1"}})
    out = server_api.ensure_store_session(store_key="K", android_key="SC-1", store_name="Shop")
    assert out["token"] == "new"
