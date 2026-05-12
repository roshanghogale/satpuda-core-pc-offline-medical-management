const SUPPLIERS_KEY   = 'vet_suppliers'
const CONFIGS_KEY     = 'vet_supplier_configs'
const SCHEDULES_KEY   = 'vet_schedules'
const MED_TYPES_KEY   = 'vet_med_types'
const MED_NAMES_KEY   = 'vet_medicine_names'  // injected by desktop launcher

const DEFAULT_SCHEDULES = ['', 'H', 'H1', 'X', 'G', 'K', 'C', 'C1', 'P', 'N', 'M']
const DEFAULT_MED_TYPES = [
  'Tablet','Syrup','Injection','Injection - Vial',
  'Ointment','Powder','Bolus','Liquid','Liniment','Gel','Vaccine','Granules'
]

export function getSchedules() {
  try { return JSON.parse(localStorage.getItem(SCHEDULES_KEY) || 'null') || DEFAULT_SCHEDULES }
  catch { return DEFAULT_SCHEDULES }
}

export function getMedTypes() {
  try { return JSON.parse(localStorage.getItem(MED_TYPES_KEY) || 'null') || DEFAULT_MED_TYPES }
  catch { return DEFAULT_MED_TYPES }
}

/**
 * getMedicineNames — returns the medicine name list injected by the desktop
 * launcher (up to 400k names stored as a JSON array in localStorage).
 * Falls back to an empty array if not injected yet.
 */
export function getMedicineNames() {
  try {
    const raw = localStorage.getItem(MED_NAMES_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

/**
 * searchMedicineNames — fast prefix+contains filter over the in-memory array.
 * Returns at most `limit` results. Prefix matches come first.
 */
export function searchMedicineNames(query, limit = 50) {
  const names = getMedicineNames()
  if (!query || query.length === 0) {
    // No text typed — return first `limit` names from master
    return names.slice(0, limit)
  }
  const q = query.toLowerCase()
  const prefix   = []
  const contains = []
  for (const name of names) {
    const n = name.toLowerCase()
    if (n.startsWith(q))    { prefix.push(name);   if (prefix.length >= limit) break }
    else if (n.includes(q)) { contains.push(name) }
    if (prefix.length + contains.length >= limit * 2) break
  }
  return [...prefix, ...contains].slice(0, limit)
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
  { key: 'schedule',           label: 'Schedule',           type: 'text',   required: false },
  { key: 'content_drug',       label: 'Content/Drug',       type: 'text',   required: false },
  { key: 'quantity_value',     label: "Pack Size (e.g. 6'S, 120ml, 1kg)", type: 'text', required: false },
]

export const DEFAULT_FIELD_ORDER = ALL_MEDICINE_FIELDS.map(f => f.key)

// Dynamic — reads from localStorage so it reflects what was saved in Settings
export function getMedicineTypes() {
  return getMedTypes()
}
export const MEDICINE_TYPES = getMedTypes()

// ── Suppliers ──────────────────────────────────────────────────────────────

export function getSuppliers() {
  try { return JSON.parse(localStorage.getItem(SUPPLIERS_KEY) || '[]') }
  catch { return [] }
}

export function saveSuppliers(list) {
  localStorage.setItem(SUPPLIERS_KEY, JSON.stringify(list))
}

export function upsertSupplier(supplier) {
  const list = getSuppliers()
  const idx  = list.findIndex(s => s.name === supplier.name)
  if (idx >= 0) list[idx] = supplier
  else list.push(supplier)
  saveSuppliers(list)
}

// ── Supplier field-order configs ───────────────────────────────────────────

export function getConfigs() {
  try { return JSON.parse(localStorage.getItem(CONFIGS_KEY) || '{}') }
  catch { return {} }
}

export function saveConfig(supplierName, fieldOrder) {
  const configs = getConfigs()
  configs[supplierName] = fieldOrder
  localStorage.setItem(CONFIGS_KEY, JSON.stringify(configs))
}

export function getFieldOrderForSupplier(supplierName) {
  const configs = getConfigs()
  const order = configs[supplierName] || DEFAULT_FIELD_ORDER
  // Remove tablets_per_stripe if still present from old saved configs
  return order.filter(k => k !== 'tablets_per_stripe')
}
