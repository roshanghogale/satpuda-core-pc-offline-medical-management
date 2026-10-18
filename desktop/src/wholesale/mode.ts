/** Is the open store a wholesale store? (core/wholesale_mode.py, D:\projects\WHOLESALE_DESIGN.md)
 *
 * Wholesale is a SEPARATE store (its own medicines, rates, purchases, sales and stock).
 * GET /api/wholesale/state answers {allowed, mode, store_name, companion_name, margins}: `mode`
 * is the open store's kind, `allowed` says the switch is on offer (the open store is wholesale,
 * or retail with a wholesale companion), `companion_name` the store the switch opens.
 * Until the first answer the store is neither (`ready` false); an engine that never answers
 * reads as retail.
 *
 *   const { allowed, mode, isWholesale, ready } = useBusinessMode()
 *   const st = await fetchBusinessMode()          // ask the engine now
 *   const st = await fetchBusinessMode(false)     // the last answer if there is one
 *
 * Re-checked every 5 minutes, when the window comes back into focus, and whenever
 * BUSINESS_MODE_EVENT is dispatched on window (a switch dispatches it).
 */
import { useSyncExternalStore } from 'react'
import { getApiBase } from '../api'

export type BusinessMode = 'retail' | 'wholesale'

export type WholesaleMargins = { A: number; B: number; C: number }

export type BusinessModeState = {
  /** The server lets this store switch to wholesale. */
  allowed: boolean
  /** The store's mode now ('retail' whenever not allowed). */
  mode: BusinessMode
  isWholesale: boolean
  /** Rate A / B / C margin % over the purchase rate (settings ws_margin_a/b/c). */
  margins: WholesaleMargins
  /** The open store's name and the other store the switch opens ("" when none). */
  storeName: string
  companionName: string
  /** The first check has finished (answered or failed). */
  checked: boolean
  /** Same as `checked` (false only until the first answer arrives). */
  ready?: boolean
}

/** Dispatched on window after the mode is switched, so every screen reads it again. */
export const BUSINESS_MODE_EVENT = 'satpuda:business-mode-changed'

export const DEFAULT_MARGINS: WholesaleMargins = { A: 8, B: 10, C: 12 }

const RECHECK_MS = 5 * 60 * 1000
const RETRY_MS = 20 * 1000
const FIRST_RETRY_MS = 1500
const FIRST_TRIES = 4
const FOCUS_GAP_MS = 5000
const TIMEOUT_MS = 6000

let state: BusinessModeState = {
  allowed: false,
  mode: 'retail',
  isWholesale: false,
  margins: DEFAULT_MARGINS,
  storeName: '',
  companionName: '',
  checked: false,
  ready: false,
}
const listeners = new Set<() => void>()
let inflight: Promise<BusinessModeState> | null = null
let timer = 0
let lastCheckAt = 0
let started = false
let failures = 0

function publish(next: BusinessModeState) {
  const same =
    next.allowed === state.allowed &&
    next.mode === state.mode &&
    next.checked === state.checked &&
    next.storeName === state.storeName &&
    next.companionName === state.companionName &&
    next.margins.A === state.margins.A &&
    next.margins.B === state.margins.B &&
    next.margins.C === state.margins.C
  if (same) return
  state = next
  for (const l of listeners) l()
}

function num(v: unknown, fallback: number): number {
  const n = Number(v)
  return Number.isFinite(n) && n >= 0 ? n : fallback
}

export function parseBusinessMode(v: unknown): BusinessModeState {
  const o = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>
  const allowed = o.allowed === true
  // The open store's kind: a wholesale store is wholesale whatever else the answer says.
  const mode: BusinessMode = o.mode === 'wholesale' ? 'wholesale' : 'retail'
  const m = (o.margins && typeof o.margins === 'object' ? o.margins : {}) as Record<string, unknown>
  return {
    allowed,
    mode,
    isWholesale: mode === 'wholesale',
    margins: {
      A: num(m.A, DEFAULT_MARGINS.A),
      B: num(m.B, DEFAULT_MARGINS.B),
      C: num(m.C, DEFAULT_MARGINS.C),
    },
    storeName: typeof o.store_name === 'string' ? o.store_name : '',
    companionName: typeof o.companion_name === 'string' ? o.companion_name : '',
    checked: true,
    ready: true,
  }
}

function schedule(ms: number) {
  if (timer) window.clearTimeout(timer)
  timer = window.setTimeout(() => void fetchBusinessMode(true), ms)
}

/** The store's mode, asked of the engine now (`force` false: the last answer when there is
 *  one). Never throws; any failure reads as retail. */
export function fetchBusinessMode(force = true): Promise<BusinessModeState> {
  if (!force && state.checked) return Promise.resolve(state)
  if (inflight) return inflight
  lastCheckAt = Date.now()
  inflight = (async () => {
    const ctrl = new AbortController()
    const t = window.setTimeout(() => ctrl.abort(), TIMEOUT_MS)
    let ok = false
    try {
      const res = await fetch(`${getApiBase()}/api/wholesale/state`, { signal: ctrl.signal })
      if (res.ok) {
        publish(parseBusinessMode(await res.json()))
        ok = true
      }
    } catch {
      /* engine not up yet, or an older engine: retail */
    } finally {
      window.clearTimeout(t)
    }
    if (ok) {
      failures = 0
    } else if (!state.checked) {
      // Not answered yet: a wholesale store must not show the retail screens for a moment,
      // so wait a few quick retries before reading it as retail.
      failures += 1
      if (failures >= FIRST_TRIES) {
        publish({ ...state, allowed: false, mode: 'retail', isWholesale: false, checked: true, ready: true })
      }
    }
    // An answer once had keeps standing through a failed re-check: the open store's kind
    // does not change without a switch, and a switch restarts the app.
    schedule(ok ? RECHECK_MS : state.checked ? RETRY_MS : FIRST_RETRY_MS)
    inflight = null
    return state
  })()
  return inflight
}

export type StoreSwitchAnswer = {
  ok: boolean
  /** The app restarts after it (the other store is open). */
  reload: boolean
  mode: BusinessMode
  store_name: string
  /** The other store's own offline-first copy is being made: follow `progress`. */
  running?: boolean
  progress?: string
  unchanged?: boolean
}

/** POST /api/wholesale/mode: OPEN the other store (the retail store's wholesale companion, or
 *  back). Throws with the engine's message (e.g. no wholesale store for this one). */
export async function switchStore(mode: BusinessMode): Promise<StoreSwitchAnswer> {
  const res = await fetch(`${getApiBase()}/api/wholesale/mode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  const d = await res.json().catch(() => ({}))
  if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
  return {
    ok: true,
    reload: d.reload === true,
    mode: d.mode === 'wholesale' ? 'wholesale' : 'retail',
    store_name: String(d.store_name || ''),
    running: d.running === true,
    progress: typeof d.progress === 'string' ? d.progress : undefined,
    unchanged: d.unchanged === true,
  }
}

export type SwitchProgress = { running: boolean; steps: string[]; error: string | null }

/** The copy job's progress (GET /api/offline-first/activate/progress). */
export async function fetchSwitchProgress(path = '/api/offline-first/activate/progress'): Promise<SwitchProgress> {
  const res = await fetch(`${getApiBase()}${path}`)
  const d = await res.json().catch(() => ({}))
  return {
    running: d.running === true,
    steps: Array.isArray(d.steps) ? d.steps.map((s: unknown) => String(s)) : [],
    error: d.error ? String(d.error) : null,
  }
}

/** GET /api/wholesale/last-rates: the newest Rate A / B / C a medicine was given. */
export async function fetchLastRates(
  name: string,
  medicineId?: number | null,
): Promise<{ found: boolean; purchase_rate?: number; rate_a?: number; rate_b?: number; rate_c?: number }> {
  const qs = new URLSearchParams({ name: name || '' })
  if (medicineId) qs.set('medicine_id', String(medicineId))
  try {
    const res = await fetch(`${getApiBase()}/api/wholesale/last-rates?${qs}`)
    const d = await res.json()
    return d && d.found ? d : { found: false }
  } catch {
    return { found: false }
  }
}

/** Rate A / B / C = purchase rate + margin %, to 2 decimals. */
export function suggestedRates(purchaseRate: number, margins: WholesaleMargins = state.margins) {
  const pr = Number(purchaseRate) || 0
  const r = (pct: number) => (pr > 0 ? Math.round(pr * (1 + pct / 100) * 100) / 100 : 0)
  return { rate_a: r(margins.A), rate_b: r(margins.B), rate_c: r(margins.C) }
}

function onModeEvent() {
  // Another screen asked for a fresh read.
  if (Date.now() - lastCheckAt > 500) void fetchBusinessMode(true)
}

function onFocus() {
  if (Date.now() - lastCheckAt > FOCUS_GAP_MS) void fetchBusinessMode(true)
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  if (!started) {
    started = true
    void fetchBusinessMode(true)
    window.addEventListener('focus', onFocus)
    window.addEventListener(BUSINESS_MODE_EVENT, onModeEvent)
  }
  return () => {
    listeners.delete(cb)
  }
}

function snapshot() {
  return state
}

/** {allowed, mode, isWholesale, margins, checked} -- re-renders when it changes. */
export function useBusinessMode(): BusinessModeState {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}
