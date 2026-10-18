/** Is this store wholesale right now? (core/wholesale_mode.py, D:\projects\WHOLESALE_DESIGN.md)
 *
 * GET /api/wholesale/state answers {allowed, mode, margins}: `allowed` is the admin panel's
 * per-store switch, `mode` the store setting business_mode ('retail' | 'wholesale') and reads
 * 'retail' whenever the server does not allow wholesale. Until the first answer, and when the
 * engine does not answer, the store is retail.
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
const FOCUS_GAP_MS = 5000
const TIMEOUT_MS = 6000

let state: BusinessModeState = {
  allowed: false,
  mode: 'retail',
  isWholesale: false,
  margins: DEFAULT_MARGINS,
  checked: false,
  ready: false,
}
const listeners = new Set<() => void>()
let inflight: Promise<BusinessModeState> | null = null
let timer = 0
let lastCheckAt = 0
let started = false

function publish(next: BusinessModeState) {
  const same =
    next.allowed === state.allowed &&
    next.mode === state.mode &&
    next.checked === state.checked &&
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
  const mode: BusinessMode = allowed && o.mode === 'wholesale' ? 'wholesale' : 'retail'
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
    if (!ok) {
      publish({ ...state, allowed: false, mode: 'retail', isWholesale: false, checked: true, ready: true })
    }
    schedule(ok ? RECHECK_MS : RETRY_MS)
    inflight = null
    return state
  })()
  return inflight
}

/** POST /api/wholesale/mode. Throws with the engine's message (e.g. not allowed). */
export async function setBusinessMode(mode: BusinessMode): Promise<BusinessModeState> {
  const res = await fetch(`${getApiBase()}/api/wholesale/mode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  const d = await res.json().catch(() => ({}))
  if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
  const next = parseBusinessMode(d)
  publish(next)
  try {
    window.dispatchEvent(new CustomEvent(BUSINESS_MODE_EVENT, { detail: next }))
  } catch {
    /* no window events (tests) */
  }
  return next
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
  // setBusinessMode already published its answer; another dispatcher gets a fresh read.
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
