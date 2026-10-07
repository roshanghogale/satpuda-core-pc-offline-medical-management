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

# This build moves an Online PC onto offline-first by itself on its first start (the screen
# shows the copy being made). Offline-mode PCs switch from Settings (their own data goes up
# first through the usual Offline -> Online push).
AUTO_ACTIVATE = True
