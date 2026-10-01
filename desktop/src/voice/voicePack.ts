/** The voice pack: the voice service and its model, downloaded by the store that wants voice.
 *
 * The app no longer ships the voice service. The ENGINE (the app's own sidecar, the
 * same base as /api/voice/enabled) downloads, checks, unpacks and starts it:
 * GET /api/voice/pack answers where that stands, and check / install / cancel /
 * remove / start each answer with the same status. Install runs in the background,
 * so while the pack is busy it is asked again every second; it stops once idle,
 * ready or failed.
 *
 * One copy is kept here and shared: the voice bar's download card and the
 * Settings > Voice block show the same state.
 */
import { useSyncExternalStore } from 'react'
import { getApiBase } from '../api'

export type PackState =
  | 'idle' | 'checking' | 'downloading' | 'verifying' | 'extracting' | 'starting' | 'ready' | 'error'

export type VoicePack = {
  state: PackState
  /** Bytes while downloading / verifying, files while extracting. */
  done: number
  total: number
  error: string
  available: { version: string; size: number; notes: string } | null
  installed: boolean
  version: string | null
  running: boolean
  path: string
  free_gb: number | null
  need_gb: number
}

export type PackAction = 'check' | 'install' | 'cancel' | 'remove' | 'start'

/** Where the pack stands; `failed` when the engine did not answer (an older engine has no pack API). */
export type PackView = { pack: VoicePack | null; failed: string }

const STATES: readonly PackState[] = [
  'idle', 'checking', 'downloading', 'verifying', 'extracting', 'starting', 'ready', 'error',
]
const BUSY = new Set<PackState>(['checking', 'downloading', 'verifying', 'extracting', 'starting'])
const POLL_MS = 1000
const TIMEOUT_MS = 8000
/** Shown until the manifest says otherwise. */
export const PACK_SIZE_GUESS = '1.6 GB'

export const packBusy = (p: VoicePack | null) => !!p && BUSY.has(p.state)

const num = (v: unknown) => (Number.isFinite(Number(v)) ? Number(v) : 0)

function parse(v: unknown): VoicePack {
  const o = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>
  const st = String(o.state ?? 'idle') as PackState
  const av = o.available && typeof o.available === 'object' ? (o.available as Record<string, unknown>) : null
  return {
    state: STATES.includes(st) ? st : 'idle',
    done: num(o.done),
    total: num(o.total),
    error: String(o.error ?? ''),
    available: av ? { version: String(av.version ?? ''), size: num(av.size), notes: String(av.notes ?? '') } : null,
    installed: o.installed === true,
    version: o.version == null || o.version === '' ? null : String(o.version),
    running: o.running === true,
    path: String(o.path ?? ''),
    free_gb: o.free_gb == null || !Number.isFinite(Number(o.free_gb)) ? null : Number(o.free_gb),
    need_gb: num(o.need_gb),
  }
}

let view: PackView = { pack: null, failed: '' }
const listeners = new Set<() => void>()
let pollTimer = 0

function publish(next: PackView) {
  view = next
  for (const l of listeners) l()
}

/** Keep asking every second while the pack is busy; stop when it is not. */
function follow() {
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = 0
  if (packBusy(view.pack)) pollTimer = window.setTimeout(() => void refreshVoicePack(), POLL_MS)
}

async function call(path: string, method: 'GET' | 'POST'): Promise<VoicePack | null> {
  const ctrl = new AbortController()
  const t = window.setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  try {
    const res = await fetch(`${getApiBase()}${path}`, {
      method,
      headers: method === 'POST' ? { 'Content-Type': 'application/json' } : undefined,
      body: method === 'POST' ? '{}' : undefined,
      signal: ctrl.signal,
    })
    const data = (await res.json().catch(() => null)) as Record<string, unknown> | null
    // A refusal still carries the status (with its error); anything else is a failed call.
    if (!data || typeof data.state !== 'string') throw new Error(String(data?.error || `HTTP ${res.status}`))
    const pack = parse(data)
    publish({ pack, failed: '' })
    return pack
  } catch (e) {
    publish({ pack: view.pack, failed: e instanceof Error ? e.message : String(e) })
    return null
  } finally {
    window.clearTimeout(t)
    follow()
  }
}

/** Ask the engine where the pack stands. Never throws. */
export const refreshVoicePack = () => call('/api/voice/pack', 'GET')

/** check: read the online manifest; install: download + check + unpack + start (also updates);
 *  cancel; remove: stop the service and delete the pack; start: start an installed pack. */
export const voicePackAction = (action: PackAction) => call(`/api/voice/pack/${action}`, 'POST')

function subscribe(l: () => void) {
  listeners.add(l)
  return () => {
    listeners.delete(l)
  }
}
const current = () => view

/** The pack, kept current while anyone shows it. */
export function useVoicePack(): PackView {
  return useSyncExternalStore(subscribe, current, current)
}

// ── Words for the screen ──

const mb = (bytes: number) => Math.round(bytes / (1024 * 1024)).toLocaleString('en-IN')

/** "1.6 GB" / "640 MB" from bytes. */
export function sizeText(bytes: number): string {
  if (!bytes) return PACK_SIZE_GUESS
  const gb = bytes / (1024 * 1024 * 1024)
  return gb >= 1 ? `${Math.round(gb * 10) / 10} GB` : `${mb(bytes)} MB`
}

/** The download's size: the manifest's, else the usual one. */
export const packSizeText = (p: VoicePack | null) => sizeText(p?.available?.size || 0)

/** 0..1 for the progress bar, or null when there is nothing to count. */
export function packProgress(p: VoicePack | null): number | null {
  if (!p || !p.total || p.total <= 0) return null
  return Math.max(0, Math.min(1, p.done / p.total))
}

/** The state as one line: "Download hot aahe… 640 / 1,560 MB". */
export function packStateLine(p: VoicePack): string {
  const counted = p.total > 0
  switch (p.state) {
    case 'checking': return 'Tapasat aahe…'
    case 'downloading': return `Download hot aahe…${counted ? ` ${mb(p.done)} / ${mb(p.total)} MB` : ''}`
    case 'verifying': return 'Tapasat aahe… (download barobar aahe ka)'
    case 'extracting': return `Extract hot aahe…${counted ? ` ${p.done.toLocaleString('en-IN')} / ${p.total.toLocaleString('en-IN')} files` : ''}`
    case 'starting': return 'Voice suru hot aahe — pahilya veli ~1 minute'
    case 'ready': return 'Voice tayar'
    case 'error': return p.error || 'Kahi tari chukla'
    default: return p.installed ? 'Voice install aahe' : 'Voice ya PC var nahi'
  }
}

/** Not enough free disk for the pack: the warning, else ''. */
export function diskWarning(p: VoicePack | null): string {
  if (!p || p.free_gb == null || !p.need_gb || p.free_gb >= p.need_gb) return ''
  return `PC var jaga kami aahe: ${Math.round(p.free_gb * 10) / 10} GB mokli, ${Math.round(p.need_gb * 10) / 10} GB lagte`
}
