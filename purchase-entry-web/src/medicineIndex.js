/** Built-in medicine list (medicines.json generated at npm build). */

let _names = []
let _ready = false
let _loadPromise = null

export function isMedicineIndexReady() {
  return _ready
}

export function getMedicineIndexCount() {
  return _names.length
}

function assetUrl(file) {
  const base = (typeof import.meta !== 'undefined' && import.meta.env?.BASE_URL) || './'
  return `${base}${file}`.replace(/\/+/g, '/').replace(':/', '://')
}

function mergeUniqueNames(baseList, extraList) {
  const seen = new Set()
  const out = []
  for (const n of [...(extraList || []), ...(baseList || [])]) {
    const s = (n || '').trim()
    if (!s) continue
    const key = s.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(s)
  }
  out.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
  return out
}

/** Load medicines.json + inventory names from catalog.json (written when desktop opens web entry). */
export function loadBuiltInMedicines() {
  if (_loadPromise) return _loadPromise
  _loadPromise = (async () => {
    let builtIn = []
    try {
      const res = await fetch(assetUrl('medicines.json'), { cache: 'no-store' })
      if (res.ok) {
        const data = await res.json()
        builtIn = Array.isArray(data?.names) ? data.names : (Array.isArray(data) ? data : [])
      }
    } catch (e) {
      console.warn('medicines.json not loaded:', e)
    }

    let inventory = []
    try {
      const res = await fetch(assetUrl(`catalog.json?t=${Date.now()}`), { cache: 'no-store' })
      if (res.ok) {
        const cat = await res.json()
        if (Array.isArray(cat.inventory_medicine_names)) {
          inventory = cat.inventory_medicine_names
        }
      }
    } catch { /* catalog optional */ }

    _names = mergeUniqueNames(builtIn, inventory)
    _ready = true
    return _names.length
  })()
  return _loadPromise
}

/** Call after Save All so new names appear without reloading the page. */
export function addMedicineNamesToIndex(names) {
  if (!Array.isArray(names) || !names.length) return
  _names = mergeUniqueNames(_names, names)
}

/** Same as addMedicineNamesToIndex — merge stock/custom names from catalog or save API. */
export function mergeInventoryNames(names) {
  addMedicineNamesToIndex(names)
}

/** Reload built-in list + catalog.json (e.g. after reopening web entry). */
export function reloadMedicineIndex() {
  _ready = false
  _loadPromise = null
  return loadBuiltInMedicines()
}

export function getLoadedMedicineCount() {
  return _names.length
}

/** Fast prefix-then-contains search over the built-in list (no API). */
export function searchBuiltInMedicines(query, limit = 200) {
  if (!_ready || !_names.length) return []
  const q = (query || '').trim().toLowerCase()
  if (!q) return _names.slice(0, limit)

  const out = []
  for (let i = 0; i < _names.length; i++) {
    const n = _names[i]
    if (n.toLowerCase().startsWith(q)) {
      out.push(n)
      if (out.length >= limit) return out
    }
  }
  for (let i = 0; i < _names.length; i++) {
    const n = _names[i]
    const low = n.toLowerCase()
    if (!low.startsWith(q) && low.includes(q)) {
      out.push(n)
      if (out.length >= limit) return out
    }
  }
  return out
}
