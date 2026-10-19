const SUPPLIERS_KEY   = 'vet_suppliers'
const CONFIGS_KEY     = 'vet_supplier_configs'
const SCHEDULES_KEY   = 'vet_schedules'
const MED_TYPES_KEY   = 'vet_med_types'
const API_BASE_KEY    = 'vet_api_base'

const DEFAULT_SCHEDULES = ['', 'H', 'H1', 'X', 'G', 'K', 'C', 'C1', 'P', 'N', 'M']
const DEFAULT_MED_TYPES = [
  'Bolus', 'Bolus Pack', 'Capsule', 'Cream', 'Drops', 'Ear Drops', 'Eye Drops',
  'Feed Supplement', 'Gel', 'Granules', 'Inhaler', 'Injection', 'Injection - Vial',
  'Instrument / Medical Device', 'Liniment', 'Liquid', 'Lotion', 'Nasal Drops',
  'Ointment', 'Others', 'Powder', 'Sachet', 'Shampoo', 'Soap', 'Spray',
  'Suspension', 'Syrup', 'Tablet', 'Tablet Pack', 'Vaccine',
]

export function getApiBase() {
  try {
    const stored = localStorage.getItem(API_BASE_KEY)
    if (stored) return stored.replace(/\/$/, '')
    if (typeof window !== 'undefined' && window.location.protocol.startsWith('http')) {
      return window.location.origin.replace(/\/$/, '')
    }
  } catch { /* ignore */ }
  return ''
}

function normalizeList(raw, defaults) {
  if (!Array.isArray(raw) || raw.length === 0) return [...defaults]
  return raw
}

function catalogUrl() {
  const base = (typeof import.meta !== 'undefined' && import.meta.env?.BASE_URL) || './'
  const ts = Date.now()
  return `${base}catalog.json?t=${ts}`.replace(/([^:]\/)\/+/g, '$1')
}

/**
 * Load suppliers + schedules + types from catalog.json (written by desktop app on open).
 * Same idea as types/schedules from layout — suppliers included in that snapshot.
 */
export async function loadRuntimeCatalog() {
  try {
    const res = await fetch(catalogUrl(), { cache: 'no-store' })
    if (res.ok) {
      const data = await res.json()
      const suppliers = Array.isArray(data.suppliers) ? data.suppliers : []
      const schedules = normalizeList(data.schedules, DEFAULT_SCHEDULES)
      const medTypes = normalizeList(data.med_types, DEFAULT_MED_TYPES)
      localStorage.setItem(SUPPLIERS_KEY, JSON.stringify(suppliers))
      localStorage.setItem(SCHEDULES_KEY, JSON.stringify(schedules))
      localStorage.setItem(MED_TYPES_KEY, JSON.stringify(medTypes))
      return {
        connected: true,
        suppliers,
        schedules,
        medTypes,
        supplierCount: suppliers.length,
        ...storeInfo(data),
      }
    }
  } catch (e) {
    console.warn('catalog.json load failed:', e)
  }
  return loadAllCatalogsFromApi()
}

/**
 * Who served this page and for which kind of store.
 *
 * The PC's Mobile Import receiver (core/mobile_import_server.py) serves this same page to a
 * phone that scanned its QR, and its catalog says so: source 'mobile_import', the address to
 * send bills to, and store_kind -- 'wholesale' when the PC has a wholesale store open, which
 * turns the bill into strips + purchase rate per strip + Rate A / B / C (Rate A required).
 * The Web Entry server on the PC itself says nothing of the kind: retail, Save All as before.
 */
export function storeInfo(data) {
  const kind = String(data?.store_kind || '').toLowerCase() === 'wholesale' ? 'wholesale' : 'retail'
  const m = data?.margins || {}
  const num = (v, d) => (Number.isFinite(Number(v)) && v !== '' && v != null ? Number(v) : d)
  return {
    phone: data?.source === 'mobile_import',
    postTo: String(data?.post_to || '/mobile-import'),
    storeKind: kind,
    storeName: String(data?.store_name || ''),
    margins: { A: num(m.A, 8), B: num(m.B, 10), C: num(m.C, 12) },
  }
}

/** Send the bills to the PC that served this page; it shows them for Import there. */
export async function sendToPc(payload, postTo = '/mobile-import') {
  try {
    const res = await fetch(postTo, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || data.ok === false) {
      return { ok: false, error: data.error || `The PC answered ${res.status}` }
    }
    return { ok: true, ...data }
  } catch (e) {
    return { ok: false, error: `Could not reach the PC: ${e?.message || e}` }
  }
}

async function fetchJson(path) {
  const api = getApiBase()
  if (!api) return null
  try {
    const res = await fetch(`${api}${path}`)
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

export function getSchedules() {
  try {
    const raw = JSON.parse(localStorage.getItem(SCHEDULES_KEY) || 'null')
    return normalizeList(raw, DEFAULT_SCHEDULES)
  } catch { return [...DEFAULT_SCHEDULES] }
}

export function getMedTypes() {
  try {
    const raw = JSON.parse(localStorage.getItem(MED_TYPES_KEY) || 'null')
    return normalizeList(raw, DEFAULT_MED_TYPES)
  } catch { return [...DEFAULT_MED_TYPES] }
}

export async function fetchSuppliersFromServer() {
  const data = await fetchJson('/api/suppliers')
  if (!data || data.ok === false || !Array.isArray(data.suppliers)) return null
  localStorage.setItem(SUPPLIERS_KEY, JSON.stringify(data.suppliers))
  return data.suppliers
}

export async function fetchSchedulesFromServer() {
  const data = await fetchJson('/api/schedules')
  if (!data) return null
  const list = normalizeList(data.schedules, DEFAULT_SCHEDULES)
  localStorage.setItem(SCHEDULES_KEY, JSON.stringify(list))
  return list
}

export async function fetchMedTypesFromServer() {
  const data = await fetchJson('/api/med-types')
  if (!data) return null
  const list = normalizeList(data.med_types, DEFAULT_MED_TYPES)
  localStorage.setItem(MED_TYPES_KEY, JSON.stringify(list))
  return list
}

/** Fallback if catalog.json is missing (older install). */
export async function loadAllCatalogsFromApi() {
  const [suppliers, schedules, medTypes] = await Promise.all([
    fetchSuppliersFromServer(),
    fetchSchedulesFromServer(),
    fetchMedTypesFromServer(),
  ])
  const connected = suppliers !== null || schedules !== null || medTypes !== null
  return {
    connected,
    suppliers: suppliers ?? getSuppliers(),
    schedules: schedules ?? getSchedules(),
    medTypes: medTypes ?? getMedicineTypes(),
    supplierCount: (suppliers ?? getSuppliers()).length,
  }
}

export const loadAllCatalogsFromServer = loadRuntimeCatalog

export async function loadBootstrapFromServer() {
  const r = await loadRuntimeCatalog()
  return r.connected
}

export function getCatalogSnapshot() {
  return {
    suppliers: getSuppliers(),
    schedules: getSchedules(),
    medTypes: getMedicineTypes(),
  }
}

export const ALL_MEDICINE_FIELDS = [
  { key: 'medicine_name',      label: 'Medicine Name',      type: 'text',   required: true  },
  { key: 'type',               label: 'Type',               type: 'select', required: true  },
  { key: 'batch_no',           label: 'Batch No',           type: 'text',   required: true  },
  { key: 'expiry_date',        label: 'Expiry (MM/YY)',     type: 'text',   required: true  },
  { key: 'qty',                label: 'Qty',                type: 'number', required: true  },
  { key: 'free_qty',           label: 'Free',               type: 'number', required: false },
  { key: 'rate',               label: 'Rate',               type: 'number', required: true  },
  { key: 'mrp',                label: 'MRP',                type: 'number', required: false },
  { key: 'gst_percent',        label: 'GST%',               type: 'number', required: false },
  { key: 'item_discount',      label: 'Disc%',              type: 'number', required: false },
  { key: 'hsn_code',           label: 'HSN Code',           type: 'text',   required: false },
  { key: 'manufacturer',       label: 'Mfg',                type: 'text',   required: false },
  { key: 'schedule',           label: 'Schedule',           type: 'select', required: false },
  { key: 'content_drug',       label: 'Content/Drug',       type: 'text',   required: false },
  { key: 'quantity_value',     label: "Pack Size (e.g. 6'S, 120ml, 1kg)", type: 'text', required: false },
]

export const DEFAULT_FIELD_ORDER = ALL_MEDICINE_FIELDS.map(f => f.key)

/**
 * A wholesale store's purchase line (WHOLESALE_DESIGN.md): counted in strips (packs) with no
 * loose tablets and no per-tablet rate; the purchase rate is per strip; MRP is optional; Rate
 * A / B / C are per strip, GST extra, and Rate A is required. A fixed order -- the per-supplier
 * layouts on the Settings page were made for the retail columns.
 */
export const WHOLESALE_MEDICINE_FIELDS = [
  { key: 'medicine_name',  label: 'Medicine Name',             type: 'text',   required: true  },
  { key: 'type',           label: 'Type',                      type: 'select', required: true  },
  { key: 'quantity_value', label: 'Pack (e.g. 1x10)',          type: 'text',   required: false },
  { key: 'batch_no',       label: 'Batch No',                  type: 'text',   required: true  },
  { key: 'expiry_date',    label: 'Expiry (MM/YY)',            type: 'text',   required: true  },
  { key: 'qty',            label: 'Strips',                    type: 'number', required: true  },
  { key: 'free_qty',       label: 'Free strips',               type: 'number', required: false },
  { key: 'rate',           label: 'Purchase rate / strip',     type: 'number', required: true  },
  { key: 'mrp',            label: 'MRP (optional)',            type: 'number', required: false },
  { key: 'rate_a',         label: 'Rate A *',                  type: 'number', required: true  },
  { key: 'rate_b',         label: 'Rate B',                    type: 'number', required: false },
  { key: 'rate_c',         label: 'Rate C',                    type: 'number', required: false },
  { key: 'gst_percent',    label: 'GST%',                      type: 'number', required: false },
  { key: 'item_discount',  label: 'Disc%',                     type: 'number', required: false },
  { key: 'hsn_code',       label: 'HSN Code',                  type: 'text',   required: false },
  { key: 'manufacturer',   label: 'Mfg',                       type: 'text',   required: false },
  { key: 'schedule',       label: 'Schedule',                  type: 'select', required: false },
]

export const WHOLESALE_FIELD_ORDER = WHOLESALE_MEDICINE_FIELDS.map(f => f.key)

export function getMedicineTypes() {
  return getMedTypes()
}

export function getSuppliers() {
  try {
    return JSON.parse(localStorage.getItem(SUPPLIERS_KEY) || '[]')
  } catch { return [] }
}

export function upsertSupplier(supplier) {
  const list = getSuppliers()
  const idx = list.findIndex(s => s.name === supplier.name)
  if (idx >= 0) list[idx] = { ...list[idx], ...supplier }
  else list.push(supplier)
  localStorage.setItem(SUPPLIERS_KEY, JSON.stringify(list))
}

/** Persist supplier to SQLite (desktop app database). */
export async function saveSupplierToServer(supplier) {
  const api = getApiBase()
  if (!api) return { ok: false, error: 'API not available' }
  try {
    const res = await fetch(`${api}/api/suppliers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(supplier),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      return { ok: false, error: data.error || res.statusText }
    }
    if (data.ok && data.supplier) {
      upsertSupplier(data.supplier)
    }
    return data
  } catch (e) {
    return { ok: false, error: String(e) }
  }
}

export function getSupplierConfigs() {
  try {
    return JSON.parse(localStorage.getItem(CONFIGS_KEY) || '{}')
  } catch { return {} }
}

export const getConfigs = getSupplierConfigs

export function saveSupplierConfig(name, fieldOrder) {
  const cfg = getSupplierConfigs()
  cfg[name] = fieldOrder
  localStorage.setItem(CONFIGS_KEY, JSON.stringify(cfg))
}

export const saveConfig = saveSupplierConfig

export function saveSuppliers(list) {
  localStorage.setItem(SUPPLIERS_KEY, JSON.stringify(list || []))
}

export function getFieldOrderForSupplier(supplierName) {
  const cfg = getSupplierConfigs()
  return cfg[supplierName] || DEFAULT_FIELD_ORDER
}
