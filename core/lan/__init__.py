"""Two or more PCs of one shop on a LAN (owner, 23 Oct 2026): "two computers connected with an
ethernet switch, same store -- when one adds a sale or purchase it instantly shows in the other,
without internet".

One PC is the MAIN PC, the others are COUNTER PCs.

  * Every PC keeps its own engine and its own offline-first copy of the shop (core/offline_first),
    exactly as before. The desktop UI on every PC still talks only to its own engine on
    127.0.0.1 -- nothing in the UI had to learn about the LAN.
  * On the Main PC a second, small listener answers on the LAN (main_server.py, port 8775 by
    default). It accepts paired counters only: a counter enters the Main PC's 6-digit pairing
    code once and gets a device token; every LAN request must carry it. The listener passes the
    request on to the Main PC's own engine on 127.0.0.1 -- the engine API itself never listens
    on the LAN.
  * On a Counter PC the engine forwards the UI's requests to the Main PC (counter.py), so both
    PCs work on the SAME data at the same moment. What belongs to this PC stays here: printing
    (the bill is printed on this PC's own printer, from the Main PC's bill copied into this
    PC's copy), files (exports and PDFs made by the Main PC are written into this PC's own
    folders), the screen's own settings, the licence and the sync status.
  * Change hints: every save on any PC is noted on the Main PC (hints.py); counters ask for them
    every second and their screens refresh, so a sale on one shows on the other at once.
  * Main PC off or the cable out: the counter's engine answers from its OWN copy (offline-first,
    its own id range and bill-number blocks), and the screen says so. When the Main PC is back,
    the counter hands the bills it made to the Main PC over the LAN (bridge.py); the Main PC
    writes them into its copy as if they had come from the server and sends them on to the
    cloud. The server keeps every event once (by device and event id), so a bill that reaches
    it twice -- from the counter itself and through the Main PC -- is counted once.
  * The Main PC's own changes are copied into the counter's copy over the LAN as well (the
    "feed"), so the counter's copy is fresh for a failover even with no internet at all.
"""
