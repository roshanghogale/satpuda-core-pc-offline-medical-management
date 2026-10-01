/** Is voice switched on for this store? (the admin panel's per-store switch)
 *
 * The engine answers GET /api/voice/enabled with {enabled, tier, source}. Until
 * it says `enabled: true` the voice bar and the Voice Commands panels are not
 * shown, F1 does nothing and nothing is sent to the voice service (voiceClient
 * waits for this answer before any call). An engine that does not know the
 * endpoint, or does not answer, counts as "off".
 *
 * Re-checked every 5 minutes and when the window comes back into focus. A check
 * that failed (engine still starting) is tried again sooner, so voice does not
 * stay hidden for five minutes after a slow start.
 */
import { useSyncExternalStore } from 'react'
import { getApiBase } from '../api'

export type VoiceTierCap = 'auto' | '1' | '2' | '3'
export type VoiceSwitchSource = 'server' | 'cache' | 'override' | 'default'

export type VoiceSwitch = {
  enabled: boolean
  /** The admin's level cap for this store. */
  tier: VoiceTierCap
  /** Where the answer came from; '' before the first answer or when the engine did not answer. */
  source: VoiceSwitchSource | ''
  /** The first check has finished (answered or failed). */
  checked: boolean
}

const RECHECK_MS = 5 * 60 * 1000
const RETRY_MS = 20 * 1000
const FOCUS_GAP_MS = 5000
const TIMEOUT_MS = 5000

let state: VoiceSwitch = { enabled: false, tier: 'auto', source: '', checked: false }
const listeners = new Set<() => void>()
let inflight: Promise<VoiceSwitch> | null = null
let firstCheck: Promise<VoiceSwitch> | null = null
let timer = 0
let lastCheckAt = 0
let started = false

function publish(next: VoiceSwitch) {
  const same =
    next.enabled === state.enabled && next.tier === state.tier && next.source === state.source &&
    next.checked === state.checked
  if (same) return
  state = next
  for (const l of listeners) l()
}

const TIERS: readonly VoiceTierCap[] = ['auto', '1', '2', '3']
const SOURCES: readonly VoiceSwitchSource[] = ['server', 'cache', 'override', 'default']

function parse(v: unknown): VoiceSwitch {
  const o = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>
  const tier = String(o.tier ?? 'auto') as VoiceTierCap
  const source = String(o.source ?? '') as VoiceSwitchSource
  return {
    enabled: o.enabled === true,
    tier: TIERS.includes(tier) ? tier : 'auto',
    source: SOURCES.includes(source) ? source : 'default',
    checked: true,
  }
}

function schedule(ms: number) {
  if (timer) window.clearTimeout(timer)
  timer = window.setTimeout(() => void checkVoiceEnabled(), ms)
}

/** Ask the engine now (one call at a time). Never throws. */
export function checkVoiceEnabled(): Promise<VoiceSwitch> {
  if (inflight) return inflight
  lastCheckAt = Date.now()
  inflight = (async () => {
    const ctrl = new AbortController()
    const t = window.setTimeout(() => ctrl.abort(), TIMEOUT_MS)
    let ok = false
    try {
      const res = await fetch(`${getApiBase()}/api/voice/enabled`, { signal: ctrl.signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      publish(parse(await res.json()))
      ok = true
    } catch {
      publish({ enabled: false, tier: 'auto', source: '', checked: true })
    } finally {
      window.clearTimeout(t)
    }
    if (started) schedule(ok ? RECHECK_MS : RETRY_MS)
    return state
  })().finally(() => {
    inflight = null
  })
  return inflight
}

/** Resolves once the first check has an answer: true when voice is on. */
export function whenVoiceChecked(): Promise<boolean> {
  if (state.checked) return Promise.resolve(state.enabled)
  if (!firstCheck) firstCheck = checkVoiceEnabled()
  return firstCheck.then((s) => s.enabled)
}

export const isVoiceEnabled = () => state.enabled
export const voiceSwitch = () => state

function onFocus() {
  if (Date.now() - lastCheckAt < FOCUS_GAP_MS) return
  void checkVoiceEnabled()
}

/** Start the 5-minute re-check and the focus re-check (once, for the app's life). */
function startWatching() {
  if (started) return
  started = true
  window.addEventListener('focus', onFocus)
  if (!state.checked && !inflight) void whenVoiceChecked()
  else schedule(state.checked ? RECHECK_MS : RETRY_MS)
}

function subscribe(l: () => void) {
  listeners.add(l)
  startWatching()
  return () => {
    listeners.delete(l)
  }
}

/** The switch, kept current: re-renders when the engine's answer changes. */
export function useVoiceEnabled(): VoiceSwitch {
  return useSyncExternalStore(subscribe, voiceSwitch, voiceSwitch)
}

/** "Chalu — admin panel": where the "on" came from, for the voice settings. */
export function voiceSourceNote(s: VoiceSwitch): string {
  if (!s.enabled) return s.checked ? 'Band — admin panel' : 'Tapasto aahe…'
  switch (s.source) {
    case 'server': return 'Chalu — admin panel'
    case 'cache': return 'Chalu — admin panel (saved copy)'
    case 'override': return 'Chalu — test override'
    default: return 'Chalu — default'
  }
}
