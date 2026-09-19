# Server Sync Code Audit Report

**Date:** 2026-07-12  
**Scope:** Windows Desktop (mac2) + Android (Satpuda Core)  
**Mode:** Read-only scan — no code modified  
**Cross-check against:** Cursor canvas `server-sync-architecture.canvas.tsx` ("Server Sync Architecture")

Paths:
- Desktop: `E:\download 07-07-2026\mac2\mac2`
- Android: `E:\download 07-07-2026\Satpuda Core (2)\Satpuda Core`

---

## Canvas cross-check (doc vs code)

| Canvas claim | Code verdict |
|--------------|--------------|
| PC bootstrap = push all → pull all | **MATCH** — `run_bootstrap` calls `push_all_local` then `sync_down_all` |
| Android bootstrap = download first → selective upload | **MATCH** — `runBootstrapMerge` |
| Sync is store-wise, not device-wise | **MATCH** |
| Medicine alone uses `synced_at` merge | **MATCH** (pull path); note every push still *writes* a `synced_at` field on the Server doc |
| Sale-first day-to-day push | **MATCH** on PC `after_sale_saved` and Android `BillingService.saveBill` |
| Android Sale/Purchase saves twice | **PARTIAL MISMATCH** — Purchase is double-pushed; Sale is **not** (see §7) |
| Desktop return-delete not wired | **MATCH** |
| Settings realtime-pulled on PC | Canvas says push-heavy / PC rarely pulls — **MATCH** (PC listeners omit `settings`) |
| Android SyncBootstrap KDoc "push-all + pull-all" | **MISMATCH with Android code** — comment at `SyncBootstrap.kt:65-66` is stale; implementation is download→selective upload |

**Additional code nuance not in canvas:** On PC, bootstrap UI job and `start_online_sync` (listeners) can run **in parallel** — listeners are not strictly sequenced after bootstrap completes (`main.py` ~1842-1846).

---

## 1. PC bootstrap: Offline → Online → push all → pull all

### Trigger chain

1. Admin sets Online in Settings → `set_sync_mode('online')`  
   - File: `core/sync_prefs.py`  
   - Lines: **40-53** — if previous was offline, calls `mark_pending_bootstrap()`
2. Pending flag file: `server_bootstrap_pending_{store_id}.txt`  
   - File: `core/sync_bootstrap.py` lines **34-37**, **26-27**
3. UI asks for restart (database tab); on next launch:
   - `main.py` **354** → `after(2500, _start_server_sync_if_online)`
   - `main.py` **1785-1792** → if online + server supported → `_start_server_sync`
   - `main.py` **1842-1846** → if `should_run_bootstrap(self.conn)` → progress job; then always `start_online_sync(...)`

### Bootstrap decision

- File: `core/sync_bootstrap.py`  
- `should_run_bootstrap` **86-93**: online AND (`is_pending_bootstrap()` OR (not done AND `local_db_has_business_data`))

### Bootstrap execution

| Step | File | Function | Lines |
|------|------|----------|-------|
| Coordinator entry | `core/sync_coordinator.py` | `run_bootstrap_if_needed` | **480-493** |
| Core bootstrap | `core/server_entity_sync.py` | `run_bootstrap` | **1167-1185** |
| Push all | `core/server_entity_sync.py` | `push_all_local` | **1110-1164** |
| Pull all | `core/server_entity_sync.py` | `sync_down_all` | **1095-1107** |
| Mark done | `core/sync_coordinator.py` | `mark_bootstrap_done()` after ok | **492** |
| Then listeners | `core/sync_coordinator.py` | `start_online_sync` → `start_background` → `start_listeners` | **496-512**, `server_entity_sync.py` **1231-1238**, **1190-1218** |

`run_bootstrap` order (confirmed):

```
ensure_credentials → verify_server_access → push_all_local → sync_down_all
```

### What "push all" uploads (`push_all_local`)

For each local row `id`, calls the matching push helper (Server path `stores/{store_id}/{collection}/{id}` via `_col_ref` at **195-196**).

| Order | Local SQLite table | Push function | Server collection |
|-------|--------------------|---------------|----------------------|
| 1 | `customers` | `push_customer` | `customers` |
| 2 | `suppliers` | `push_supplier` | `suppliers` |
| 3 | `medicines` | `push_medicine` | `medicines` |
| 4 | `doctors` | `push_doctor` | `doctors` |
| 5 | `sales` | `push_sale` | `sales` (embeds `items[]` from `sales_items`) |
| 6 | `purchases` | `push_purchase` | `purchases` (embeds `items[]` from `purchase_items`) |
| 7 | `customer_payments` | `push_customer_payment` | `customer_payments` |
| 8 | `supplier_payments` | `push_supplier_payment` | `supplier_payments` |
| 9 | `sales_returns` | `push_sales_return` | `sales_returns` (+ items) |
| 10 | `purchase_returns` | `push_purchase_return` | `purchase_returns` (+ items) |
| 11 | (settings) | `push_pharmacy_profile` | `settings/pharmacy_profile` |
| 12 | (settings) | `push_dropdowns` | `settings/dropdowns` |

**Not uploaded by push_all:** `medicines_master` (listed in `COLLECTIONS` but absent from `push_all_local` tables tuple **1115-1126**).

Push uses `set(..., merge=True)` and always stamps doc field `synced_at` (`push_doc` **203-212**).

### What "pull all" overwrites locally (`sync_down_all` → `sync_down_doc`)

`sync_down_all` **1095-1107**: streams **every** doc in `COLLECTIONS` (**22-26**), including `medicines_master`.

Per-doc apply: `sync_down_doc` **723-1052**.

| Server collection | Local write behavior |
|----------------------|----------------------|
| `customers` | `INSERT OR REPLACE INTO customers` — full row overwrite (**727-740**) |
| `medicines` | **Merge** via `merge_medicine_pull`; if `needs_repush`, only update stock/hidden/`synced_at` then re-push; else upsert full medicine row (**741-808**) |
| `sales` | Stub customer if needed; **DELETE** `sales_items` for sale; `INSERT OR REPLACE` sale; re-insert items; stub medicines from line metadata (**809-886**) |
| `purchases` | **DELETE** `purchase_items`; `INSERT OR REPLACE` purchase; re-insert items (**887-926**) |
| `suppliers` | `INSERT OR REPLACE` (**927-940**) |
| `doctors` | `INSERT OR REPLACE` (**941-950**) |
| `customer_payments` | `INSERT OR REPLACE` (**951-968**) |
| `supplier_payments` | `INSERT OR REPLACE` (**969+**) |
| `sales_returns` | DELETE return items; REPLACE header; re-insert items (**~990-1017**) |
| `purchase_returns` | Same pattern (**1018-1048**) |
| `medicines_master` | **No branch** in `sync_down_doc` — streamed count increments but **no local table write** |

**Important:** Pull does **not** clear tables first. It upserts by id. Local rows whose ids are **absent** from cloud remain locally after bootstrap pull. Remote deletes are only applied later via listener `REMOVED` → `delete_down_doc` (**1055-1092**).

---

## 2. Android bootstrap: download first → selective upload

### Trigger chain

1. Online activation or Offline→Online in Settings → `SyncBootstrap.markPending(db)`  
   - `ActivationViewModel.kt` ~72; `SettingsViewModel.kt` ~617  
2. `ServerManager.start()` **48-84**:
   - Skip if not Online  
   - `initServer` → `verifyServerAccess` → `flushSyncQueue`  
   - If `SyncBootstrap.shouldRunBootstrap` → `runBootstrapMerge` → `markDone`  
   - Then `startRealtimeListeners()`

### Bootstrap decision

- File: `core/sync/SyncBootstrap.kt`  
- `shouldRunBootstrap` **67-73**: Online AND (pending OR (not done AND db file exists AND business data))

### Bootstrap execution

| Step | File | Function | Lines |
|------|------|----------|-------|
| Entry | `ServerManager.kt` | `start` | **68-71** |
| Merge | `ServerManager.kt` | `runBootstrapMerge` | **426-444** |
| Download all | `ServerManager.kt` | `syncDownAll` | **177-195** |
| Page fetch | `ServerManager.kt` | `syncDownCollection` | **212-250** (`Source.SERVER`, pages of 250) |
| Apply doc | `ServerSyncHelper.kt` | `syncDownDoc` | **110+** |
| Selective upload | `ServerManager.kt` | `runSelectiveUpload` | **508-654** |

`runBootstrapMerge` order (confirmed):

```
verifyServerAccess → syncDownAll → runSelectiveUpload
```

### What download applies

Collections downloaded (**180-184**):  
`customers`, `suppliers`, `medicines`, `sales`, `purchases`, `customer_payments`, `supplier_payments`, `doctors`, `sales_returns`, `purchase_returns`, `medicines_master`, `settings`

Apply rules (`ServerSyncHelper.syncDownDoc`):
- Non-medicine entities: Room `insert` overwrite by PK (last applied wins).
- Sales/purchases/returns: delete child items then reinsert from embedded `items[]`.
- Medicines: `MedicineSyncMerge.merge` / `apply`; may `needsRepush` → immediate `syncMedicine`.
- `medicines_master` / `settings`: handled in helper (**378+**, **394+**) — Android **does** apply these; PC does not for master.

### What selective upload uploads (`runSelectiveUpload`)

Prefetches remote timestamp map per collection (`synced_at` | `last_updated` | `created_at` — **512-525**).

`shouldUpload` (**528-536**): upload if cloud missing doc, OR local ts ≥ remote ts; **if local ts is null → do not upload when remote exists**.

| Entity | Local timestamp used | Effect |
|--------|----------------------|--------|
| customers | `lastUpdated` | Compared |
| medicines | `syncedAt` | Compared |
| sales | `createdAt` | Compared |
| purchases | `createdAt` | Compared |
| customer_payments | `createdAt` ?: `paymentDate` | Compared |
| supplier_payments | `createdAt` ?: `paymentDate` | Compared |
| sales_returns / purchase_returns | `createdAt` ?: `returnDate` | Compared |
| suppliers | **always `null`** | Only upload if **missing** remotely (**555**) |
| doctors | **always `null`** | Only upload if **missing** remotely (**621**) |
| pharmacy profile + dropdowns | always at end | Always pushed (**649-653**) |

This is **not** blind push-all (unlike PC).

### Sync Now (non-bootstrap)

`SyncCoordinator.syncServerNow` **123-158**: if bootstrap due → `runBootstrapMerge`; else `flushSyncQueue` → `syncDownAll` → `uploadNewerLocal` (= selective upload).

---

## 3. Collection paths ↔ local tables

Root: `stores/{store_id}/{collection}/{doc_id}`  
Settings: `stores/{store_id}/settings/{doc_id}`  
Link registry (top-level, **not** under store): `store_keys/{SC-XXXXXXXX}`

| Server path | Desktop SQLite | Android Room | Notes |
|----------------|----------------|--------------|-------|
| `…/customers/{id}` | `customers` | `CustomerEntity` | |
| `…/suppliers/{id}` | `suppliers` | `SupplierEntity` | |
| `…/medicines/{id}` | `medicines` | `MedicineEntity` | |
| `…/sales/{id}` | `sales` + embedded → `sales_items` | `SaleEntity` + `SaleItemEntity` | Items not a separate collection |
| `…/purchases/{id}` | `purchases` + `purchase_items` | `PurchaseEntity` + items | Same |
| `…/customer_payments/{id}` | `customer_payments` | `CustomerPaymentEntity` | |
| `…/supplier_payments/{id}` | `supplier_payments` | `SupplierPaymentEntity` | |
| `…/doctors/{id}` | `doctors` | `DoctorEntity` | |
| `…/sales_returns/{id}` | `sales_returns` + `sales_return_items` | return + items | |
| `…/purchase_returns/{id}` | `purchase_returns` + `purchase_return_items` | return + items | |
| `…/medicines_master/{id}` | *(no PC apply)* | master batch docs | PC listens/streams only |
| `…/settings/pharmacy_profile` | `settings` / profile fields | `PharmacyProfile` | |
| `…/settings/dropdowns` | villages/doctors lists etc. | dropdown settings | |
| `…/settings/store_link` | pushed by PC | — | Android link metadata mirror |
| `store_keys/{SC-…}` | published by PC | validated by Android | Holds `store_id`, `store_name`, `pc_stores` |

`COLLECTIONS` constant (PC listeners + pull): `core/server_entity_sync.py` **22-26**.

---

## 4. `store_id` derivation and SC- linking

### Desktop `store_id`

- File: `core/server_entity_sync.py` `get_store_id` **126-134**  
- Source: `get_active_store_key()` or `'Store_Default'`  
- Normalize: `.lower()` then `re.sub(r'[^a-z0-9_]', '_', sid)` → fallback `store_default`  
- Same helper for registry rows: `store_link.store_id_from_key` **97-102**

### Android `store_id`

- File: `StoreIdProvider.kt` **13-21**  
  1. If not offline-only and `ActivationManager.getServerStoreId()` non-blank → **use that exact string from PC key doc**  
  2. Else normalize local store key same regex rule → `store_default`

### SC- key publish (PC)

- File: `core/store_link.py`  
- Generate/persist: `ensure_android_store_key` **42-54** → `SC-{8 hex}`  
- Publish: `publish_store_key` **57-78** → `store_keys/{key}` with `{store_id, store_name, app_mode, updated_at}` + `settings/store_link`  
- Called from `start_online_sync` via `ensure_online_store_link` (`sync_coordinator.py` **462-471**, **508-510**)

### SC- key validate (Android)

- File: `StoreLinkValidator.kt` **19-68**  
- Requires key starts with `SC-`, doc exists, **store name matches**, `store_id` non-blank  
- `ActivationManager.linkToPc` **134-158** saves `KEY_SERVER_STORE_ID = info.storeId`

### Do both devices always resolve the same `store_id` before sync?

| Condition | Same bucket? |
|-----------|--------------|
| Android linked with valid SC- key; PC Online on that store | **Yes** — Android uses PC published `store_id` string |
| Android Online but prefs missing `server_store_id` | **Maybe** — falls back to local key normalization; matches only if store keys normalize identically |
| Different store names / wrong key / Offline | **No** — different buckets or no Server |
| PC multi-store: only **active** store `get_store_id()` is used | Android must link to that store key/name |

There is **no** runtime handshake that re-validates equality on every push; equality depends on link + active store.

---

## 5. Conflict handling by entity

### Medicine — only `synced_at` merge on pull

| Platform | Merge entry |
|----------|-------------|
| Desktop | `core/medicine_sync_merge.py` `merge_medicine_pull` (~**40+**); used from `sync_down_doc` **741-764** |
| Android | `MedicineSyncMerge.kt`; used from `ServerSyncHelper.syncDownDoc` medicines branch **139-146** |

Rules (both): compare local vs remote `synced_at`; newer wins stock/`is_hidden`; local-newer → `needs_repush`; equal → max stock / may repush. Android `apply` keeps local metadata when local newer.

Desktop also bumps local `synced_at` on medicine edits via SQLite triggers (`ensure_medicine_sync_triggers`).

### All other entities — last-write-wins, no timestamp compare on pull

| Entity | Desktop pull | Android pull | Separate items collection? |
|--------|--------------|--------------|----------------------------|
| Sales | `INSERT OR REPLACE` header; wipe+rewrite `sales_items` | Room insert + delete/reinsert items | **No** — embedded `items[]` |
| Sales items | Replaced with parent sale doc | Same | N/A |
| Purchases | Same pattern | Same | No |
| Purchase items | Same | Same | N/A |
| Customers | `INSERT OR REPLACE` | `insert` overwrite | — |
| Suppliers | `INSERT OR REPLACE` | `insert` overwrite | — |
| Customer / supplier payments | `INSERT OR REPLACE` | `insert` overwrite | — |
| Sales / purchase returns | REPLACE + wipe items | Same | Embedded items |
| Doctors | `INSERT OR REPLACE` | `insert` | — |

**Nuance:** Server `set(merge=True)` on push means cloud field merge, but local pull still fully replaces the row/items for that id. No LWW timestamp gate except medicines (and Android selective **upload** timestamp gate, which is upload-side only).

---

## 6. Realtime listeners

### Desktop

| Item | Detail |
|------|--------|
| Attach | `start_listeners` `server_entity_sync.py` **1190-1218**; started from `start_background` / `start_online_sync` |
| Collections | Same as `COLLECTIONS` (**22-26**) — **includes** `medicines_master`, **excludes** `settings` |
| Events | `ADDED`/`MODIFIED` → `sync_down_doc`; `REMOVED` → `delete_down_doc`; then `on_change(collection)` |
| Detach | `stop_listeners` **1221-1228**; `stop_online_sync` |
| UI | `main.py` `_on_server_data_changed` debounced ~450ms |

**Reconnect / missed docs:** Google Server `on_snapshot` delivers a **full current snapshot** of matching docs on (re)attach as ADDED (and subsequent diffs). That effectively **replays current cloud state** into `sync_down_doc` for every doc — similar to a bulk apply for that collection, but **not** the same code path as `sync_down_all`. Orphan local-only rows (never in cloud) can linger after reconnect until wipe/manual cleanup.

### Android

| Item | Detail |
|------|--------|
| Attach | `startRealtimeListeners` `ServerManager.kt` **115-158** after bootstrap attempt in `start()` |
| Collections | Same business list **plus** `settings` (**116-120**) |
| Events | ADDED/MODIFIED → `syncDownDoc` (+ medicine repush); REMOVED → `deleteDownDoc` |
| Detach | Not clearly unsubscribe-on-Offline in scanned `start()` path; mode switch / process lifecycle dependent |

**Reconnect:** Same Server snapshot semantics — initial snapshot reapplies current docs. Separately, **Sync Now** / bootstrap can explicitly `syncDownAll` (`Source.SERVER`) = true bulk pull.

---

## 7. Known gaps — confirmed detail

### (a) Android double-push — **Purchase yes; Sale no** (canvas partial mismatch)

**Purchase double-push (confirmed):**

1. `PurchaseService.savePurchase` **269** → `ServerSyncHelper.syncPurchase` (+ medicines, dropdowns)  
2. `PurchaseViewModel.kt` **590**, **674** → `syncCoordinator.afterPurchaseSaved(purchaseId)`  
3. `afterPurchaseSaved` (`SyncCoordinator.kt` **53-62**) pushes supplier → medicines → purchase **again**

Payment/full edit paths in `PurchaseService` (**309**, **452**) push once from service; ViewModel may still call `afterPurchaseSaved` depending on UI path — create path is definitely double.

**Sale double-push: NOT confirmed**

- Live create path: `BillingService.saveBill` **165-170** only (`syncSale` → customer → medicines).  
- `SyncCoordinator.afterSaleSaved` **42-50** exists but **has zero call sites** in the Android tree (grep: definition only).  
- Canvas claim "Sale/Purchase saves twice" should be corrected to **Purchase (create) double-push; Sale single-push**.

### (b) Desktop return-delete not wired (confirmed)

- Helpers exist: `after_sales_return_deleted` / `after_purchase_return_deleted` — `sync_coordinator.py` **373-416**  
- Grep across repo: **only definitions**, no UI callers  
- Save paths **are** wired:  
  - `ui/returns/sales_return.py` **812-813** → `after_sales_return_saved`  
  - `ui/returns/purchase_return.py` **994-995** → `after_purchase_return_saved`  
- Therefore deleting a return locally does **not** delete the Server return doc (other devices keep it until manual cloud wipe / conflicting overwrite).

---

## 8. Other full collection / full table / full document-set operations

Besides bootstrap:

| Operation | Platform | Location | What it does |
|-----------|----------|----------|--------------|
| `sync_down_all` / `syncDownAll` | Both | PC **1095-1107**; Android **177-195** | Stream/page **entire** collections into local DB |
| `push_all_local` | PC | **1110-1164** | Upload **every** local business row + profile/dropdowns |
| `runSelectiveUpload` / `uploadNewerLocal` | Android | **508-654**, Sync Now | Read **entire** remote collection for ts map; upload subset |
| `syncDownBills` | Android | **198-209** | Bulk download subset: customers, suppliers, medicines, sales, purchases, returns |
| `runFullSync` | Android | **447-464** | Flush + download all + selective upload |
| `wipe_store_data` / `wipeStoreData` | Both | PC **231-258**; Android **467-494** | Delete **all** docs in store collections (batched); Android also `syncQueueDao.deleteAll` |
| PC Settings wipe UI | Desktop | `database_tab.py` ~**2158-2160** | Calls `wipe_store_data()` |
| Android Settings wipe | Android | `SettingsViewModel.kt` ~**197** | Calls `wipeStoreData()` |
| `flushSyncQueue` | Android | ServerManager queue section | Replays all pending queue rows |
| Listener initial snapshot | Both | on attach | Effectively applies **current full collection** as ADDED events |
| `after_save(..., 'all')` | PC | `sync_coordinator.py` **29-30** | Could call `push_all_local` — appears unused by UI |
| Drive backup/restore | Both (Offline) | Drive managers | Full **SQLite file** backup — not Server collection sync |

---

## Quick reference: critical function index

| Concern | Desktop | Android |
|---------|---------|---------|
| Mode switch Offline→Online | `sync_prefs.set_sync_mode` 40-53 | `ActivationManager.linkToPc` / Settings + `SyncBootstrap.markPending` |
| Bootstrap | `server_sync.run_bootstrap` 1167-1185 | `ServerManager.runBootstrapMerge` 426-444 |
| Push all | `push_all_local` 1110-1164 | *(selective only)* `runSelectiveUpload` 508-654 |
| Pull all | `sync_down_all` 1095-1107 | `syncDownAll` 177-195 |
| Listeners | `start_listeners` 1190-1218 | `startRealtimeListeners` 115-158 |
| store_id | `get_store_id` 126-134 | `StoreIdProvider.getStoreId` 13-21 |
| SC- link | `store_link.publish_store_key` 57-78 | `StoreLinkValidator.validate` 19-68 |
| Medicine merge | `medicine_sync_merge.merge_medicine_pull` | `MedicineSyncMerge` |
| Sale push order | `after_sale_saved` 37-72 | `BillingService.saveBill` 165-170 |

---

## End of report

No fixes proposed (per request). Use this file as the source of truth for subsequent prompts; prefer it over the canvas where the canvas conflicts (especially §7a Sale double-push).