/** "Fakt majha awaj": teach the voice service the owner's voice.
 *
 * Five short sentences, one at a time. Hold the big button (or Space) and say
 * the sentence; let go and the service keeps that sample (it must be at least a
 * second of speech). A sentence can be said again. While this is open,
 * hands-free listening is paused (and held paused: a spoken reply finishing
 * does not open the mic again); it resumes when the wizard closes.
 *
 * A new enrollment starts clean: the first sample recorded here first clears
 * the old ones (POST /speaker/reset). When all five are in, "Fakt majha awaj"
 * is switched on.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  voiceEnrollStart,
  voiceEnrollStop,
  voiceListenPause,
  voiceSpeakerReset,
} from './voiceClient'
import { holdListening } from './voiceMode'
import { sayCancel } from './sayClip'
import { reloadVoiceLevel, saveVoiceLevel, sayLevelKey } from './voiceLevel'

export const ENROLL_SENTENCES: readonly string[] = [
  'Satpuda, Dolo 650 don patte',
  'Grahak Ramesh Patil',
  'Bill save kar',
  'Inventory ughad',
  'Out of stock dakhav',
]

const DONE_TEXT = 'Tumcha awaj olakhla — ata dusryancha awaj ignore hoil'
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function SpeakerEnrollWizard({
  wasEnrolled,
  onClose,
}: {
  /** Samples are there already: say they will be replaced. */
  wasEnrolled: boolean
  /** `enrolled`: all five sentences went in. */
  onClose: (enrolled: boolean) => void
}) {
  const total = ENROLL_SENTENCES.length
  const [idx, setIdx] = useState(0)
  const [done, setDone] = useState<boolean[]>(() => ENROLL_SENTENCES.map(() => false))
  const [recording, setRecording] = useState(false)
  const [busy, setBusy] = useState(false)
  const [finished, setFinished] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const recordingRef = useRef(false)
  const startingRef = useRef<Promise<boolean> | null>(null)
  const resetRef = useRef(false)
  const doneRef = useRef(done)
  doneRef.current = done
  const idxRef = useRef(idx)
  idxRef.current = idx
  const finishedRef = useRef(false)
  const aliveRef = useRef(true)

  // Pause hands-free listening for as long as the wizard is open.
  useEffect(() => {
    aliveRef.current = true
    const release = holdListening()
    sayCancel()
    void voiceListenPause(true).catch(() => {})
    sayLevelKey('speaker_enroll_start', 'Tumcha awaj olakhayla paach chhoti vakya bola', { pauseMic: false })
    return () => {
      aliveRef.current = false
      if (recordingRef.current) {
        recordingRef.current = false
        void voiceEnrollStop().catch(() => {})
      }
      sayCancel()
      release()
      void voiceListenPause(false).catch(() => {})
    }
  }, [])

  const finish = useCallback(async () => {
    finishedRef.current = true
    setFinished(true)
    setMsg(null)
    sayLevelKey('speaker_enrolled', DONE_TEXT, { pauseMic: false })
    // The final message promises other voices are ignored: switch the check on.
    try {
      await saveVoiceLevel({ speaker_check: true })
    } catch {
      void reloadVoiceLevel()
    }
  }, [])

  const startRec = useCallback(() => {
    if (recordingRef.current || startingRef.current || finishedRef.current) return
    sayCancel()
    setMsg(null)
    const p = (async () => {
      try {
        // A new enrollment replaces the old samples, once, before its first sample.
        if (!resetRef.current) {
          const r = await voiceSpeakerReset()
          if (!r?.ok) throw new Error(r?.error || 'juna awaj pusla nahi')
          resetRef.current = true
        }
        const r = await voiceEnrollStart()
        if (!r?.ok) throw new Error(r?.error || 'mic ughadla nahi')
        if (!aliveRef.current) {
          void voiceEnrollStop().catch(() => {})
          return false
        }
        recordingRef.current = true
        setRecording(true)
        return true
      } catch (e) {
        if (aliveRef.current) setMsg({ ok: false, text: `Record suru zala nahi: ${errText(e)}` })
        return false
      }
    })()
    startingRef.current = p
    void p.finally(() => {
      if (startingRef.current === p) startingRef.current = null
    })
  }, [])

  const stopRec = useCallback(async () => {
    // Let go before the mic had opened: stop once it has.
    if (startingRef.current) await startingRef.current
    if (!recordingRef.current) return
    recordingRef.current = false
    setRecording(false)
    setBusy(true)
    try {
      const r = await voiceEnrollStop()
      if (!aliveRef.current) return
      if (!r?.ok) {
        setMsg({ ok: false, text: r?.error ? `Khup chhota — ${r.error}` : 'Khup chhota — 1 second peksha jast, purna vakya bola' })
        sayLevelKey('speaker_sample_short', 'Khup chhota, button dharun purna vakya bola', { cue: 'warn', pauseMic: false })
        return
      }
      const at = idxRef.current
      const next = doneRef.current.map((d, i) => d || i === at)
      setDone(next)
      const left = next.findIndex((d) => !d)
      if (left < 0) {
        await finish()
        return
      }
      // The next sentence not said yet, after this one first.
      const after = next.findIndex((d, i) => !d && i > at)
      setIdx(after >= 0 ? after : left)
      setMsg({ ok: true, text: `Zala (${next.filter(Boolean).length}/${total})${r.samples ? ` · ${r.samples} namune` : ''}` })
      sayLevelKey('speaker_sample_ok', 'Chhan, pudhcha vakya', { pauseMic: false })
    } catch (e) {
      if (aliveRef.current) setMsg({ ok: false, text: `Save zala nahi: ${errText(e)}` })
    } finally {
      if (aliveRef.current) setBusy(false)
    }
  }, [finish, total])

  // Space held = the big button held. Esc closes (not while recording).
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === 'Space' || e.key === ' ') {
        if (finishedRef.current) return
        e.preventDefault()
        e.stopPropagation()
        if (!e.repeat) startRec()
      } else if (e.key === 'Escape' && !recordingRef.current) {
        e.preventDefault()
        e.stopPropagation()
        onClose(finishedRef.current)
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.code !== 'Space' && e.key !== ' ') return
      if (finishedRef.current) return
      e.preventDefault()
      e.stopPropagation()
      void stopRec()
    }
    window.addEventListener('keydown', down, true)
    window.addEventListener('keyup', up, true)
    return () => {
      window.removeEventListener('keydown', down, true)
      window.removeEventListener('keyup', up, true)
    }
  }, [onClose, startRec, stopRec])

  const count = done.filter(Boolean).length

  return createPortal(
    <div className="vb-enroll-backdrop">
      <div className="vb-enroll" role="dialog" aria-modal="true" aria-labelledby="vb-enroll-title">
        <div className="vb-enroll-head">
          <b id="vb-enroll-title">Fakt majha awaj — awaj shikva</b>
          <span className="vb-enroll-count">
            {finished ? `${total}/${total}` : `${idx + 1}/${total}`}
            {!finished && count ? ` · ${count} zale` : ''}
          </span>
        </div>
        <div className="vb-enroll-dots" aria-hidden="true">
          {done.map((d, i) => (
            <span key={i} className={d ? 'on' : i === idx && !finished ? 'now' : ''} />
          ))}
        </div>

        {finished ? (
          <div className="vb-enroll-finish">
            <div className="vb-enroll-ok">✓ {DONE_TEXT}</div>
            <button type="button" className="vb-yes" onClick={() => onClose(true)} autoFocus>
              Zale
            </button>
          </div>
        ) : (
          <>
            <p className="vb-enroll-note">
              Button (kinva Space) dabun dharun khalcha vakya bola, mag soda.
              {wasEnrolled ? ' Aadhicha awaj pusun navin shikavla jail.' : ''}
            </p>
            <div className="vb-enroll-sentence">“{ENROLL_SENTENCES[idx]}”</div>
            <button
              type="button"
              className={`vb-enroll-btn${recording ? ' on' : ''}`}
              disabled={busy}
              onPointerDown={(e) => {
                e.preventDefault()
                startRec()
              }}
              onPointerUp={() => void stopRec()}
              onPointerLeave={() => {
                if (recordingRef.current || startingRef.current) void stopRec()
              }}
              onPointerCancel={() => void stopRec()}
            >
              {busy ? 'Thamba…' : recording ? '🎤 Bolto aahe… soda' : '🎤 Dabun dharun bola'}
            </button>
            {msg ? <div className={msg.ok ? 'vb-enroll-msg' : 'vb-enroll-msg vb-warn'}>{msg.text}</div> : null}
          </>
        )}

        <ol className="vb-enroll-list">
          {ENROLL_SENTENCES.map((s, i) => (
            <li key={s} className={i === idx && !finished ? 'now' : ''}>
              <span>{done[i] ? '✓' : '·'}</span> {s}
              {done[i] && !finished ? (
                <button
                  type="button"
                  className="vb-small"
                  disabled={recording || busy}
                  onClick={() => {
                    setIdx(i)
                    setMsg(null)
                  }}
                >
                  Parat
                </button>
              ) : null}
            </li>
          ))}
        </ol>

        {!finished ? (
          <div className="vb-enroll-foot">
            <button type="button" className="vb-no" disabled={recording} onClick={() => onClose(false)}>
              Band (Esc)
            </button>
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  )
}
