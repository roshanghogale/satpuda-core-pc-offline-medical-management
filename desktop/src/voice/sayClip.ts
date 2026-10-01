/** What the voice bar says aloud: short Marathi clips recorded ahead of time.
 *
 * Windows only has English voices offline, and they read Marathi-in-Latin like
 * a robot. So every fixed thing the bar says has a key in phrases.json and a
 * recorded clip at /voice-say/<f|m>/<key>.mp3; names, amounts and the like are
 * only shown on screen. A clip that is missing falls back to the old
 * speechSynthesis line, so nothing goes silent while clips are being made.
 *
 * The error / warning sounds live here too: they are played in the same queue,
 * so the mic stays paused (Voice ON) for the sound and the clip after it.
 */

export type Gender = 'f' | 'm'
export type Cue = 'error' | 'warn'
export type SayHooks = { start?: () => void; done?: () => void }

const GENDER_KEY = 'satpuda.voice.gender'

// localStorage can throw (private mode, blocked storage): the voice is a convenience only.
export function getVoiceGender(): Gender {
  try {
    return window.localStorage.getItem(GENDER_KEY) === 'm' ? 'm' : 'f'
  } catch {
    return 'f'
  }
}
export function setVoiceGender(g: Gender) {
  try {
    window.localStorage.setItem(GENDER_KEY, g)
  } catch {
    /* not remembered: fine */
  }
}

// ── speechSynthesis: only the fallback now ──

/** The screen line as something a voice can say: no ×, quotes or symbols. */
export function forSpeech(text: string): string {
  return text
    .replace(/[“”"'‘’]/g, '')
    .replace(/\s*×\s*/g, ', ')
    .replace(/₹\s*([\d.,]+)/g, '$1 rupaye')
    .replace(/\s+[—–·→]\s+/g, ', ')
    .replace(/[^\p{L}\p{N}\s,.?!%-]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

let speakSeq = 0
/**
 * The Windows system voice is OFF: on this PC it is an English robot reading Marathi,
 * and the owner could not understand it. Every reply is a recorded natural-voice clip
 * (phrases.json -> voice-say/); a line with no clip says nothing rather than sound like
 * that. Kept switchable only for debugging.
 */
const ROBOT_VOICE = false

/** Say it aloud with the system voice. `hooks` let Voice ON pause the mic so it does not hear itself. */
export function speak(text: string, hooks?: SayHooks) {
  const said = ROBOT_VOICE ? forSpeech(text) : ''
  if (!said) {
    if (!ROBOT_VOICE && text) console.warn('[voice] no recorded clip for:', text)
    hooks?.done?.()
    return
  }
  const id = ++speakSeq
  let finished = false
  const finish = () => {
    if (finished) return
    finished = true
    // Only the newest line un-pauses: cancel() ends the old one while the new one talks.
    if (id === speakSeq) hooks?.done?.()
  }
  try {
    const u = new SpeechSynthesisUtterance(said)
    const v = speechSynthesis.getVoices().find((x) => /en-IN|hi-IN/i.test(x.lang))
    if (v) u.voice = v
    u.rate = 1.05
    u.onend = finish
    u.onerror = finish
    speechSynthesis.cancel()
    hooks?.start?.()
    speechSynthesis.speak(u)
    // Some engines never fire onend; the mic must not stay paused for good.
    window.setTimeout(finish, 2500 + said.length * 90)
  } catch {
    finish()
  }
}

function speakAsync(text: string): Promise<void> {
  return new Promise((resolve) => speak(text, { done: () => resolve() }))
}

// ── error / warning sounds ──

let audioCtx: AudioContext | null = null
function ctx(): AudioContext | null {
  try {
    if (!audioCtx || audioCtx.state === 'closed') {
      const AC = (window as any).AudioContext || (window as any).webkitAudioContext
      audioCtx = new AC() as AudioContext
    }
    if (audioCtx.state === 'suspended') void audioCtx.resume()
    return audioCtx
  } catch {
    return null
  }
}

/** One tone with a short attack and decay, so it does not click. */
function tone(ac: AudioContext, at: number, from: number, to: number, ms: number, type: OscillatorType, vol: number) {
  const osc = ac.createOscillator()
  const gain = ac.createGain()
  const end = at + ms / 1000
  osc.type = type
  osc.frequency.setValueAtTime(from, at)
  if (to !== from) osc.frequency.exponentialRampToValueAtTime(to, end)
  gain.gain.setValueAtTime(0.0001, at)
  gain.gain.exponentialRampToValueAtTime(vol, at + 0.012)
  gain.gain.exponentialRampToValueAtTime(0.0001, end)
  osc.connect(gain)
  gain.connect(ac.destination)
  osc.start(at)
  osc.stop(end + 0.02)
}

/** Not understood: a low two-tone going down (~250 ms). Resolves when it has finished. */
export function errorSound(): Promise<void> {
  const ac = ctx()
  if (!ac) return Promise.resolve()
  try {
    const t = ac.currentTime + 0.01
    tone(ac, t, 440, 420, 110, 'triangle', 0.22)
    tone(ac, t + 0.12, 300, 280, 140, 'triangle', 0.22)
  } catch {
    return Promise.resolve()
  }
  return new Promise((r) => window.setTimeout(r, 320))
}

/** Not available: a single mid "bonk" (~180 ms). Resolves when it has finished. */
export function warnSound(): Promise<void> {
  const ac = ctx()
  if (!ac) return Promise.resolve()
  try {
    tone(ac, ac.currentTime + 0.01, 620, 330, 180, 'sine', 0.3)
  } catch {
    return Promise.resolve()
  }
  return new Promise((r) => window.setTimeout(r, 240))
}

// ── the clips ──

type Job = { keys: string[]; fallback: string; cue?: Cue; silent: boolean; hooks?: SayHooks; skipMissing?: boolean }

/** Lines waiting behind the one playing; older waiting lines give way to newer ones. */
const MAX_WAITING = 2
let queue: Job[] = []
let running = false
let stopNow: (() => void) | null = null
/** Bumped by sayCancel(): a line that was cut off does not go on to its next part. */
let cancelSeq = 0
/** Clips that failed to load, per voice: go straight to the fallback next time. */
const missing = new Set<string>()

function clipUrl(gender: Gender, key: string): string {
  return `${import.meta.env.BASE_URL}voice-say/${gender}/${key}.mp3`
}

/** Play one clip. Resolves true when it played (or was stopped), false when it could not load. */
function playClip(key: string): Promise<boolean> {
  const gender = getVoiceGender()
  const id = `${gender}/${key}`
  if (missing.has(id)) return Promise.resolve(false)
  return new Promise((resolve) => {
    const a = new Audio(clipUrl(gender, key))
    let settled = false
    let timer = 0
    // `gone`: the file is not there (not merely blocked from playing): skip it from now on.
    const end = (ok: boolean, gone = false) => {
      if (settled) return
      settled = true
      window.clearTimeout(timer)
      a.onended = null
      a.onerror = null
      a.onloadedmetadata = null
      if (stopNow === stop) stopNow = null
      if (gone) missing.add(id)
      resolve(ok)
    }
    const stop = () => {
      try {
        a.pause()
      } catch {
        /* already gone */
      }
      settled = true
      window.clearTimeout(timer)
      if (stopNow === stop) stopNow = null
      resolve(true)
    }
    stopNow = stop
    // Safety: a clip that never loads or never ends must not keep the mic paused.
    timer = window.setTimeout(() => end(false), 6000)
    a.onloadedmetadata = () => {
      window.clearTimeout(timer)
      const secs = Number.isFinite(a.duration) && a.duration > 0 ? a.duration : 6
      timer = window.setTimeout(() => end(true), secs * 1000 + 1500)
    }
    a.onended = () => end(true)
    a.onerror = () => end(false, true)
    a.play().catch(() => end(false))
  })
}

async function run() {
  running = true
  // The hooks of the line that started the run pause the mic; the newest one's resume it.
  let hooks = queue[0]?.hooks
  hooks?.start?.()
  try {
    while (queue.length) {
      const job = queue.shift()!
      const seq = cancelSeq
      hooks = job.hooks || hooks
      if (job.cue === 'error') await errorSound()
      else if (job.cue === 'warn') await warnSound()
      if (job.silent) continue
      for (const key of job.keys) {
        if (seq !== cancelSeq) break
        const ok = await playClip(key)
        if (!ok) {
          if (seq !== cancelSeq) break
          // A spoken amount is many small clips: one not made yet is left out, the rest still play.
          if (job.skipMissing) continue
          // No clip: say the whole line with the system voice instead.
          if (job.fallback) await speakAsync(job.fallback)
          break
        }
      }
    }
  } finally {
    running = false
    hooks?.done?.()
  }
}

/**
 * Say the phrase(s) `key` (in order), after an error / warning sound when `cue` is given.
 * `silent` plays only the sound (spoken replies turned off). `fallback` is said
 * with the system voice when a clip cannot be loaded; with `skipMissing` that clip is
 * just left out and the next one plays.
 */
export function sayKey(
  key: string | string[] | null,
  hooks?: SayHooks,
  opts: { fallback?: string; cue?: Cue; silent?: boolean; skipMissing?: boolean } = {},
) {
  const keys = (Array.isArray(key) ? key : key ? [key] : []).filter(Boolean)
  const silent = !!opts.silent || !keys.length
  if (silent && !opts.cue) return
  queue.push({ keys, fallback: opts.fallback || '', cue: opts.cue, silent, hooks, skipMissing: opts.skipMissing })
  while (queue.length > MAX_WAITING) queue.shift()
  if (!running) void run()
}

/** Drop what is waiting and stop what is playing (a new reply is coming). */
export function sayCancel() {
  cancelSeq++
  queue = []
  stopNow?.()
  try {
    speechSynthesis.cancel()
  } catch {
    /* no speech engine */
  }
}
