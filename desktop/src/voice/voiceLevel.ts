/** The voice service's level (tier), understanding engine and gates, shared.
 *
 * The bar (wake word hint, compare lines) and both "Voice level" panels read the
 * same copy, so a change made in one shows in the others at once. `cfg` stays
 * null while the service has not answered GET /voice/config -- an older
 * service without it just shows none of the level UI.
 */
import { useSyncExternalStore } from 'react'
import { voiceGetLevel, voiceListenPause, voiceSetLevel, type VoiceLevelConfig, type VoiceLevelPatch } from './voiceClient'
import { sayKey, type Cue } from './sayClip'
import { isHandsFreeOn, isListeningHeld } from './voiceMode'

/** The bar's "Uttar bolun sanga" switch (VoiceBar keeps it under this key). */
const TALK_BACK_KEY = 'satpuda.voice.talkBack'
function talkBackOn(): boolean {
  try {
    return window.localStorage.getItem(TALK_BACK_KEY) !== '0'
  } catch {
    return true
  }
}

/** Say a fixed phrase from a settings panel or the wizard. With Voice ON and spoken replies on,
 *  the mic is paused for it (so it does not hear itself) unless `pauseMic` is false --
 *  the wizard holds the mic paused on its own. */
export function sayLevelKey(key: string, fallback: string, opts: { cue?: Cue; pauseMic?: boolean } = {}) {
  // Spoken replies off: only a short sound plays, and nothing pauses the mic.
  const pause = opts.pauseMic !== false && talkBackOn()
  sayKey(
    key,
    pause
      ? {
          start: () => {
            if (isHandsFreeOn()) void voiceListenPause(true).catch(() => {})
          },
          done: () => {
            if (isHandsFreeOn() && !isListeningHeld()) void voiceListenPause(false).catch(() => {})
          },
        }
      : undefined,
    { fallback, cue: opts.cue, silent: !talkBackOn() },
  )
}

export type VoiceLevelState = { cfg: VoiceLevelConfig | null; loading: boolean; error: string }

let state: VoiceLevelState = { cfg: null, loading: false, error: '' }
const listeners = new Set<() => void>()
let inflight: Promise<VoiceLevelConfig | null> | null = null

function publish(patch: Partial<VoiceLevelState>) {
  state = { ...state, ...patch }
  for (const l of listeners) l()
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Read the config again (one call at a time). Never throws; null when the service has none. */
export function reloadVoiceLevel(): Promise<VoiceLevelConfig | null> {
  if (inflight) return inflight
  publish({ loading: true })
  inflight = voiceGetLevel()
    .then((cfg) => {
      publish({ cfg, loading: false, error: '' })
      return cfg
    })
    .catch((e) => {
      publish({ cfg: null, loading: false, error: errText(e) })
      return null
    })
    .finally(() => {
      inflight = null
    })
  return inflight
}

/** Change some settings; the service's answer replaces the copy. Throws when it was not saved. */
export async function saveVoiceLevel(patch: VoiceLevelPatch): Promise<VoiceLevelConfig> {
  const cfg = await voiceSetLevel(patch)
  publish({ cfg, error: '' })
  return cfg
}

/** Put a copy the service sent back some other way (after enrollment). */
export function takeVoiceLevel(cfg: VoiceLevelConfig) {
  publish({ cfg, error: '' })
}

const get = () => state
function subscribe(l: () => void) {
  listeners.add(l)
  return () => {
    listeners.delete(l)
  }
}

export function useVoiceLevel(): VoiceLevelState {
  return useSyncExternalStore(subscribe, get, get)
}

/** "AI", "Rules", "Donhi": how an engine is written on screen. */
export function engineWord(engine: unknown): string {
  switch (String(engine || '').toLowerCase()) {
    case 'llm': return 'AI'
    case 'hybrid': return 'Donhi'
    case 'rules': return 'Rules'
    default: return String(engine || '')
  }
}

/** 1200 -> "1.2s", 40 -> "0.04s". */
export function msText(ms: unknown): string {
  const n = Number(ms)
  if (!Number.isFinite(n) || n < 0) return ''
  return n >= 100 ? `${(n / 1000).toFixed(1)}s` : `${(n / 1000).toFixed(2)}s`
}
