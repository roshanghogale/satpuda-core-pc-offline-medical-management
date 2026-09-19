"""A missing row is asked for once, not four hundred times.

A shop PC asked /api/sync/medicines/<id> 423 times in a single run and was
answered 404 every single time -- the same few ids, each one a round trip to the
VPS before the caller fell back to its own stub. The rows were medicines the
server genuinely does not have (deleted, or never pushed), so every one of those
requests was answered the same way it had been a second earlier.

The answer is remembered for a minute now, and thrown away the moment anything
is written to that collection or the catalogue is refreshed -- another device can
create the id at any time, and the shop must see it when it does.

Patched client; nothing reaches a server.
"""
from __future__ import annotations

import unittest
from unittest import mock

from core import online_catalog, server_crud  # noqa: E402


class _NotFound(Exception):
    status = 404


class TheRealClientPath(unittest.TestCase):
    """Through the REAL pull_doc, with only the HTTP call faked.

    The first version of this memory passed its tests and remembered nothing in
    the field: the tests mocked pull_doc to RAISE a 404, while the real pull_doc
    catches the 404 and returns None. This case keeps the real pull_doc and fakes
    only _request, so it fails if that ever drifts apart again.
    """

    def setUp(self):
        server_crud.forget_missing()
        self.addCleanup(server_crud.forget_missing)
        self.requests = 0
        from core import server_api

        def fake_request(method, path, **_kw):
            self.requests += 1
            raise server_api.ServerHttpError(404, "medicines/24 not found")

        for p in (
            mock.patch.object(server_crud, "_token", lambda: "t"),
            mock.patch("core.server_api._request", side_effect=fake_request),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_fifty_asks_for_a_missing_row_make_one_request(self):
        for _ in range(50):
            self.assertIsNone(server_crud.get_doc("medicines", 24))
        self.assertEqual(self.requests, 1, f"{self.requests} requests reached the server")


class AMissingRow(unittest.TestCase):
    def setUp(self):
        server_crud.forget_missing()
        self.addCleanup(server_crud.forget_missing)
        self.asked: list[tuple] = []

        def pull_doc(_token, collection, local_id, **_kw):
            self.asked.append((collection, int(local_id)))
            if int(local_id) == 7:
                return {"id": 7, "name": "DOLO 650"}
            raise _NotFound()

        api = mock.MagicMock()
        api.pull_doc.side_effect = pull_doc
        api.ServerHttpError = _NotFound
        self.api = api
        # No patch.dict on sys.modules: restoring it drops modules imported during
        # the test, the next import builds a second core.server_api, and its
        # ServerHttpError is a different class that pull_doc no longer catches.
        patches = [
            mock.patch.object(server_crud, "_token", lambda: "t"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self._api_patch = mock.patch("core.server_api.pull_doc", side_effect=pull_doc)
        self._api_patch.start()
        self.addCleanup(self._api_patch.stop)
        self._err_patch = mock.patch("core.server_api.ServerHttpError", _NotFound)
        self._err_patch.start()
        self.addCleanup(self._err_patch.stop)

    def test_it_is_asked_once_however_often_it_is_wanted(self):
        for _ in range(50):
            self.assertIsNone(server_crud.get_doc("medicines", 404))
        self.assertEqual(len(self.asked), 1, f"asked {len(self.asked)} times")

    def test_a_row_that_exists_is_never_cached_away(self):
        for _ in range(3):
            self.assertEqual(server_crud.get_doc("medicines", 7)["name"], "DOLO 650")
        self.assertEqual(len(self.asked), 3, "an existing row must be fetched every time")

    def test_writing_to_the_collection_makes_it_ask_again(self):
        server_crud.get_doc("medicines", 404)
        server_crud.forget_missing("medicines")
        server_crud.get_doc("medicines", 404)
        self.assertEqual(len(self.asked), 2)

    def test_one_collection_is_forgotten_without_forgetting_the_others(self):
        server_crud.get_doc("medicines", 404)
        server_crud.get_doc("customers", 404)
        server_crud.forget_missing("medicines")
        server_crud.get_doc("medicines", 404)
        server_crud.get_doc("customers", 404)
        self.assertEqual(self.asked.count(("medicines", 404)), 2)
        self.assertEqual(self.asked.count(("customers", 404)), 1)

    def test_the_memory_lapses_on_its_own(self):
        server_crud.get_doc("medicines", 404)
        with mock.patch("time.time", return_value=__import__("time").time() + server_crud._MISSING_TTL + 1):
            server_crud.get_doc("medicines", 404)
        self.assertEqual(len(self.asked), 2)

    def test_refreshing_the_catalogue_clears_it(self):
        server_crud.get_doc("medicines", 404)
        with mock.patch.object(online_catalog, "_persist_snapshot", lambda: None):
            online_catalog.invalidate("medicines")
        server_crud.get_doc("medicines", 404)
        self.assertEqual(len(self.asked), 2)


if __name__ == "__main__":
    unittest.main()
