"""Offline-first sync for the PC (owner's design doc "Satpuda Core: Offline-First Server Design",
7 Oct 2026; server side in server-live/src/services/syncV2.js).

The PC always works on its own SQLite copy of the shop and keeps it in step with the server in
the background:

  * schema   -- the PC's own bookkeeping tables and the triggers that note every change to a
                synced table (any code path) and every stock movement, so nothing is missed;
  * ids      -- records this PC creates take ids from its own range (device_no * 1e9 up);
  * numbers  -- bill / purchase numbers from blocks reserved on the server in advance;
  * outbox   -- noted changes become numbered events (1, 2, 3 ...) kept until the server
                confirms them;
  * pull     -- other devices' changes written into the local copy; stock set to the
                server's figure plus this PC's own movements the server does not have yet;
  * worker   -- the background loop that pushes and pulls.
"""

# v1.0.11 (22 Oct 2026) moved every Online PC onto offline-first by itself on its first start.
# Owner, 30 Oct 2026: the shop chooses -- Offline, Offline-first and Online are all in Settings
# (desktop_settings_service._sync_mode_options); Online -> Offline-first runs the same switch-over
# (the copy, then registration) when it is chosen. A PC already on offline-first stays on it.
AUTO_ACTIVATE = False
