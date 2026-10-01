/** Talks to the local voice service (voice/voice_service.py on 127.0.0.1).
 *
 * The service owns the microphone and the Whisper model; the screen only says
 * "start", "stop" and "what does this text mean". Nothing here leaves the PC.
 */

import { whenVoiceChecked } from './voiceEnabled'

export const VOICE_BASE = 'http://127.0.0.1:47811'

export type VoiceCommand = {
  intent: string
  args: Record<string, any>
  heard: string
  text: string
  confidence: number
  needs_confirm: boolean
  say: string
}

export type VoiceReply = {
  ok: boolean
  heard?: string
  command?: VoiceCommand
  /** Everything one sentence asked for ("Ramesh la don Dolo, paachshe cash"), in order. */
  commands?: VoiceCommand[]
  timing?: { audio_s: number; asr_s: number; parse_ms: number }
  error?: string
  note?: string
  /** Which understanding answered (a newer service); absent from an older one. */
  engine?: VoiceEngine
  llm_ms?: number
  /** Compare mode: the other engine's answer to the same words. */
  alt?: VoiceAlt
  /** What the wake word / speaker gates made of it (null: that gate is off). */
  gate?: VoiceGate
}

export type VoiceGate = { wake: boolean | null; speaker: number | null; passed: boolean }

export type VoiceHealth = {
  ok: boolean
  ready: boolean
  model: string
  model_error: string
  language: string
  device: number | null
  recording: boolean
  medicines: number
  customers: number
  doctors: number
  vocab_error: string
}

export type VoiceDevice = { index: number; name: string; default: boolean }

/** A voice-opened dropdown: its name and the rows it shows now (pending "dropdown"). */
export type FieldContext = { field?: string; options?: string[] }

/** Thrown instead of calling the service while the store's voice switch is off. */
export class VoiceOffError extends Error {
  constructor() {
    super('voice band aahe (admin panel)')
    this.name = 'VoiceOffError'
  }
}

type CallOpts = {
  /** Go through even with the store's switch off (only: closing the mic on the way out). */
  always?: boolean
  /** A newer endpoint: an HTTP error (404 from an older service) throws instead of being read. */
  strict?: boolean
}

async function call<T>(path: string, body?: unknown, timeoutMs = 30000, opts: CallOpts = {}): Promise<T> {
  // The store's switch comes first: with voice off nothing is sent to the service.
  if (!opts.always && !(await whenVoiceChecked())) throw new VoiceOffError()
  const ctrl = new AbortController()
  const timer = window.setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(`${VOICE_BASE}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal,
    })
    if (opts.strict && !res.ok) throw new Error(`HTTP ${res.status}`)
    return (await res.json()) as T
  } finally {
    window.clearTimeout(timer)
  }
}

export const voiceHealth = () => call<VoiceHealth>('/health', undefined, 3000)
export const voiceDevices = () =>
  call<{ ok: boolean; devices: VoiceDevice[]; selected: number | null }>('/devices', undefined, 5000)
export const voiceStart = () => call<{ ok: boolean; error?: string }>('/start', {}, 5000)
export const voiceCancel = () => call<{ ok: boolean }>('/cancel', {}, 5000)
/** The page's tab or panel when it has one ("customer" on Payments, "sales" on Returns); sent only when set. */
const sectionOf = (section: string) => (section ? { section } : {})
export const voiceStop = (page: string, pending: string, field: FieldContext = {}, section = '') =>
  call<VoiceReply>('/stop', { page, ...sectionOf(section), pending, ...field }, 60000)
export const voiceParse = (text: string, page: string, pending: string, field: FieldContext = {}, section = '') =>
  call<VoiceReply>('/parse', { text, page, ...sectionOf(section), pending, ...field }, 10000)
export const voiceConfig = (patch: Record<string, unknown>) =>
  call<{ ok: boolean }>('/config', patch, 10000)
export const voiceRefreshVocab = () =>
  call<{ ok: boolean; medicines: number; customers: number; error?: string }>('/vocab/refresh', {}, 30000)

// ── Hands-free listening ──
// With Voice ON the service keeps the mic open, cuts speech into utterances on
// its own and queues what it understood; the screen only polls the queue.

/** One utterance the service heard and parsed while listening hands-free. */
export type ListenItem = {
  seq: number
  heard?: string
  command?: VoiceCommand
  commands?: VoiceCommand[]
  timing?: { audio_s: number; asr_s: number; parse_ms: number }
  error?: string
  note?: string
  engine?: VoiceEngine
  llm_ms?: number
  gate?: VoiceGate
  alt?: VoiceAlt
}

export type ListenNext = {
  ok: boolean
  listening: boolean
  paused: boolean
  /** Newest seq the service has handed out. */
  seq: number
  /** Mic level, 0..1, for the meter. */
  level: number
  items: ListenItem[]
  error?: string
  /** Segments the wake word / speaker gates dropped since listening started. */
  ignored?: number
  /** The owner said only "Satpuda": the next segment is the command. */
  waiting_for_command?: boolean
}

export const voiceListenStart = (page: string, pending: string, field: FieldContext = {}, section = '') =>
  call<{ ok: boolean; error?: string; seq?: number }>('/listen/start', { page, ...sectionOf(section), pending, ...field }, 8000)
// Closing the mic goes through even as the store's switch turns voice off.
export const voiceListenStop = () => call<{ ok: boolean }>('/listen/stop', {}, 5000, { always: true })
export const voiceListenContext = (page: string, pending: string, field: FieldContext = {}, section = '') =>
  call<{ ok: boolean }>('/listen/context', { page, ...sectionOf(section), pending, ...field }, 3000)
export const voiceListenPause = (paused: boolean) =>
  call<{ ok: boolean }>('/listen/pause', { paused }, 3000)
export const voiceListenNext = (after: number) =>
  call<ListenNext>(`/listen/next?after=${encodeURIComponent(String(after))}`, undefined, 3000)

// ── Voice level (tiers), understanding engine and the two gates ──
// Newer service only: every call below is strict, and a caller treats a throw
// as "this service does not have it" and hides that part of the screen.

export type VoiceEngine = 'rules' | 'llm' | 'hybrid'
export type VoiceTier = 1 | 2 | 3

/** The other engine's answer, in compare mode. */
export type VoiceAlt = { engine: string; command?: VoiceCommand; commands?: VoiceCommand[]; ms?: number }

export type VoiceLevelConfig = {
  tier: VoiceTier
  tier_auto: VoiceTier
  /** The admin's cap: "auto" = no cap set. */
  tier_cap: 'auto' | '1' | '2' | '3'
  understanding: VoiceEngine
  wake_word: boolean
  wake_words: string[]
  speaker_check: boolean
  speaker_enrolled: boolean
  speaker_samples: number
  speaker_threshold: number
  llm_model: string | null
  llm_ready: boolean
  whisper_model: string
  hardware: { ram_gb?: number; cores?: number; threads?: number; gpu?: string | boolean | null }
  compare: boolean
}

export type VoiceLevelPatch = Partial<
  Pick<VoiceLevelConfig, 'understanding' | 'tier' | 'wake_word' | 'speaker_check' | 'compare' | 'speaker_threshold'>
>

const ENGINES: readonly VoiceEngine[] = ['rules', 'llm', 'hybrid']
const tierOf = (v: unknown, dflt: VoiceTier): VoiceTier => {
  const n = Number(v)
  return n === 1 || n === 2 || n === 3 ? n : dflt
}
const numOr = (v: unknown): number | undefined =>
  v != null && v !== '' && Number.isFinite(Number(v)) ? Number(v) : undefined

/** The service's answer, cleaned: null when it is not a config at all. */
function cleanLevel(v: unknown): VoiceLevelConfig | null {
  if (!v || typeof v !== 'object') return null
  const o = v as Record<string, any>
  if (o.tier == null && o.understanding == null) return null
  const cap = String(o.tier_cap ?? 'auto')
  const hw = o.hardware && typeof o.hardware === 'object' ? o.hardware : {}
  return {
    tier: tierOf(o.tier, 1),
    tier_auto: tierOf(o.tier_auto, tierOf(o.tier, 1)),
    tier_cap: cap === '1' || cap === '2' || cap === '3' ? cap : 'auto',
    understanding: ENGINES.includes(o.understanding) ? o.understanding : 'rules',
    wake_word: o.wake_word === true,
    wake_words: Array.isArray(o.wake_words) ? o.wake_words.map((w: unknown) => String(w ?? '').trim()).filter(Boolean) : [],
    speaker_check: o.speaker_check === true,
    speaker_enrolled: o.speaker_enrolled === true,
    speaker_samples: Number(o.speaker_samples) || 0,
    speaker_threshold: numOr(o.speaker_threshold) ?? 0,
    llm_model: o.llm_model ? String(o.llm_model) : null,
    llm_ready: o.llm_ready === true,
    whisper_model: String(o.whisper_model ?? ''),
    hardware: {
      ram_gb: numOr(hw.ram_gb),
      cores: numOr(hw.cores),
      threads: numOr(hw.threads),
      gpu: hw.gpu ?? null,
    },
    compare: o.compare === true,
  }
}

export async function voiceGetLevel(): Promise<VoiceLevelConfig> {
  const c = cleanLevel(await call<unknown>('/voice/config', undefined, 5000, { strict: true }))
  if (!c) throw new Error('voice config nahi')
  return c
}

export async function voiceSetLevel(patch: VoiceLevelPatch): Promise<VoiceLevelConfig> {
  const r = await call<Record<string, unknown>>('/voice/config', patch, 15000, { strict: true })
  const c = cleanLevel(r)
  if (!c) throw new Error(String(r?.error || 'voice config save zala nahi'))
  return c
}

// Speaker enrollment: one sample is recorded between start and stop.
export type SpeakerStatus = { enrolled: boolean; samples: number; threshold: number }

export const voiceEnrollStart = () =>
  call<{ ok: boolean; error?: string }>('/speaker/enroll/start', {}, 5000, { strict: true })
export const voiceEnrollStop = () =>
  call<{ ok: boolean; samples?: number; error?: string }>('/speaker/enroll/stop', {}, 20000, { strict: true })
export const voiceSpeakerReset = () =>
  call<{ ok: boolean; error?: string }>('/speaker/reset', {}, 5000, { strict: true })
export async function voiceSpeakerStatus(): Promise<SpeakerStatus> {
  const r = await call<Record<string, unknown>>('/speaker/status', undefined, 5000, { strict: true })
  return {
    enrolled: r?.enrolled === true,
    samples: Number(r?.samples) || 0,
    threshold: Number(r?.threshold) || 0,
  }
}

// ── Numbered shortcuts: "doctor 2" sets the 2nd doctor; in an open list "two" picks it ──

export type ShortcutKind = 'doctor' | 'customer' | 'supplier' | 'village'
export const SHORTCUT_KINDS: readonly ShortcutKind[] = ['doctor', 'customer', 'supplier', 'village']
/** kind -> "n" -> name. */
export type ShortcutMap = Record<ShortcutKind, Record<string, string>>

export const MAX_SHORTCUTS = 30
/** Fired on window after the shortcuts changed, so open pages re-read them. */
export const SHORTCUTS_EVENT = 'satpuda-voice-shortcuts'

export const emptyShortcuts = (): ShortcutMap => ({ doctor: {}, customer: {}, supplier: {}, village: {} })

export function isShortcutKind(v: unknown): v is ShortcutKind {
  return SHORTCUT_KINDS.includes(String(v || '').toLowerCase() as ShortcutKind)
}

function cleanShortcuts(v: unknown): ShortcutMap {
  const out = emptyShortcuts()
  const src = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>
  for (const kind of SHORTCUT_KINDS) {
    const m = src[kind]
    if (!m || typeof m !== 'object') continue
    for (const [n, name] of Object.entries(m as Record<string, unknown>)) {
      const s = String(name ?? '').trim()
      if (s && /^\d+$/.test(n)) out[kind][String(Number(n))] = s
    }
  }
  return out
}

// The last map the service gave, for lists that must order themselves at once
// (a voice-opened dropdown). null: never read, or the service is not running.
let cachedShortcuts: ShortcutMap | null = null
let cachedAt = 0
let inflight: Promise<unknown> | null = null

export const cachedShortcutMap = () => cachedShortcuts

const names = (v: unknown) =>
  Array.isArray(v) ? v.map((d) => String(d ?? '').trim()).filter(Boolean) : []

export async function voiceShortcuts(): Promise<{ shortcuts: ShortcutMap; doctors: string[]; suppliers: string[] }> {
  const r = await call<{ ok: boolean; shortcuts?: unknown; doctors?: unknown; suppliers?: unknown; error?: string }>(
    '/shortcuts', undefined, 5000,
  )
  if (!r?.ok) throw new Error(r?.error || 'shortcuts not read')
  const shortcuts = cleanShortcuts(r.shortcuts)
  cachedShortcuts = shortcuts
  cachedAt = Date.now()
  return { shortcuts, doctors: names(r.doctors), suppliers: names(r.suppliers) }
}

/** Re-read the map when the cached one is older than `maxAgeMs` (never throws). */
export function refreshShortcutCache(maxAgeMs = 15000): Promise<ShortcutMap | null> {
  if (cachedShortcuts && Date.now() - cachedAt < maxAgeMs) return Promise.resolve(cachedShortcuts)
  if (!inflight) {
    inflight = voiceShortcuts()
      .catch(() => null)
      .finally(() => {
        inflight = null
      })
  }
  return inflight.then(() => cachedShortcuts)
}

async function postShortcuts(body: unknown): Promise<ShortcutMap> {
  const r = await call<{ ok: boolean; shortcuts?: unknown; error?: string }>('/shortcuts', body, 8000)
  if (!r?.ok) throw new Error(r?.error || 'shortcut not saved')
  const m = cleanShortcuts(r.shortcuts)
  cachedShortcuts = m
  cachedAt = Date.now()
  window.dispatchEvent(new CustomEvent(SHORTCUTS_EVENT, { detail: m }))
  return m
}

/** Set one number (name "" clears it; a name keeps only one number). */
export const voiceSetShortcut = (kind: ShortcutKind, n: number, name: string) =>
  postShortcuts({ kind, n, name })
/** Replace every shortcut. */
export const voiceSaveShortcuts = (shortcuts: ShortcutMap) => postShortcuts({ shortcuts })

/** "Doctor", "Grahak", "Supplier", "Gaon": how a kind is written on screen. */
export function shortcutKindWord(kind: unknown): string {
  switch (String(kind || '').toLowerCase()) {
    case 'customer': return 'Grahak'
    case 'supplier': return 'Supplier'
    case 'village': return 'Gaon'
    default: return 'Doctor'
  }
}

/** "②" for 1..20, "#21" past that. */
export function shortcutBadge(n: number | string): string {
  const k = Number(n)
  return k >= 1 && k <= 20 ? String.fromCodePoint(0x2460 + k - 1) : `#${n}`
}
