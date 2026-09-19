# Option B sync (revision + WebSocket)

## Live path (default)
- **USE_REVISION_SYNC=true** (default on Mac2 and Android)
- Server: monotonic `head_revision` + `sync_changes`
- WebSocket `/ws/sync`: `sync_hint` only (revision number)
- Clients: pull `GET /api/sync/changes/full`, apply to SQLite, UI reads SQLite
- Safety poll `GET /api/sync/status` every 45s if WS drops

## Pull from Server
**Disaster recovery / full replace only.**  
Day-to-day sync must not require Pull. Use Pull when:
- This PC/phone is badly out of date
- Recovering after DB corruption or wrong store
- Explicit admin “replace local from server”

Pull clears local business rows then downloads a clean copy (`replace_local=True`).

## Rollback to legacy watermark poller
Mac2:
- Env: `USE_REVISION_SYNC=0` (or `SYNC_ENGINE_V2=0`)
- Or AppData file `use_revision_sync.txt` containing `0`

Android:
- SharedPreferences `satpuda_sync_engine` → `use_revision_sync=false`

Push helpers (`push_*` / `syncSaleBundle`) stay unchanged either way.

## Watermarks
`sync_watermarks.py` / `SyncWatermarks.kt` remain for:
- Manual full Pull merge
- Rollback poller only

They are **not** the live primary mechanism when USE_REVISION_SYNC is on.

## B4 additions
- **client_uuid** on sales / purchases / medicines (generated on device before first push; unique per store)
- **stock_operations** append-only delta log (`op_uuid` idempotent); preferred over blind `stock_qty` LWW when `stock_ops` present
- Sale/purchase bundles attach `stock_ops` on medicine docs (Mac2 + Android)
- Absolute stock patches are audit-only (`op=set`) — **not** changelogged (avoids double-apply)
- **Admin → Store → Sync**: `head_revision`, device `last_ack_revision` / lag, last 50 `sync_changes`
- Clients `POST /api/sync/ack` (or WS `{type:'ack'}`) after applying revisions
- Mac2 `sync_outbox` retries failed sale/purchase pushes (Android parity)
