/** Voice READY: the store is allowed voice AND the whole voice pack is on this PC.
 *
 * The owner (19 Oct 2026): nothing voice before the pack is downloaded. Until both are
 * true there is no floating voice button / bar, pages take no spoken commands, no
 * shortcut badges or voice hints show, and nothing is sent to the voice service. The
 * pack is downloaded in Settings > Assistant only; once it is in, this flips by itself
 * (the pack is asked again every second while it installs), so voice comes on without
 * a restart.
 *
 * The same two answers the engine's voice_ready() uses (core/voice_pack.py):
 * /api/voice/enabled (the admin panel's switch) and /api/voice/pack (`installed` is
 * true only for a whole pack).
 */
import { useSyncExternalStore } from 'react'
import { checkVoiceEnabled, voiceSwitch, whenVoiceChecked, subscribeVoiceSwitch } from './voiceEnabled'
import { refreshVoicePack, subscribeVoicePack, voicePackView } from './voicePack'

export type VoiceReady = {
  ready: boolean
  /** The store's switch is on (admin panel). */
  allowed: boolean
  /** A whole pack is installed on this PC. */
  installed: boolean
  /** Both answers are in (or failed): before that, nothing shows. */
  checked: boolean
}

const PACK_RECHECK_MS = 5 * 60 * 1000
const FOCUS_GAP_MS = 5000

let packAsked: Promise<unknown> | null = null
let packCheckedOnce = false
let lastPackAt = 0
let started = false

function compute(): VoiceReady {
  const sw = voiceSwitch()
  const pack = voicePackView()
  const installed = pack.pack?.installed === true
  return {
    ready: sw.enabled && installed,
    allowed: sw.enabled,
    installed,
    checked: sw.checked && packCheckedOnce,
  }
}

let snap: VoiceReady = compute()
const listeners = new Set<() => void>()

function update() {
  const next = compute()
  if (
    next.ready === snap.ready && next.allowed === snap.allowed && next.installed === snap.installed &&
    next.checked === snap.checked
  ) return
  snap = next
  for (const l of listeners) l()
}

function askPack(): Promise<unknown> {
  lastPackAt = Date.now()
  if (!packAsked) {
    packAsked = refreshVoicePack().finally(() => {
      packAsked = null
      packCheckedOnce = true
      update()
    })
  }
  return packAsked
}

function onFocus() {
  if (Date.now() - lastPackAt < FOCUS_GAP_MS) return
  void askPack()
  void checkVoiceEnabled()
}

function startWatching() {
  if (started) return
  started = true
  subscribeVoiceSwitch(update)
  subscribeVoicePack(update)
  window.addEventListener('focus', onFocus)
  void whenVoiceChecked()
  void askPack()
  // The pack can be removed / copied in from outside the app: look again now and then.
  window.setInterval(() => void askPack(), PACK_RECHECK_MS)
}

/** Voice is ready right now (no waiting). */
export const isVoiceReady = () => snap.ready
export const voiceReadyNow = () => snap

/** Resolves once both answers are in: true when voice is ready. */
export async function whenVoiceReady(): Promise<boolean> {
  startWatching()
  const allowed = await whenVoiceChecked()
  if (!allowed) return false
  if (!packCheckedOnce) await askPack()
  update()
  return snap.ready
}

function subscribe(l: () => void) {
  listeners.add(l)
  startWatching()
  return () => {
    listeners.delete(l)
  }
}

/** The gate, kept current: re-renders when it flips. */
export function useVoiceReady(): VoiceReady {
  return useSyncExternalStore(subscribe, voiceReadyNow, voiceReadyNow)
}
