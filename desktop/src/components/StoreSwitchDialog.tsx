/** The box shown while the app opens another store, and then restarts.
 *
 *  ONE component for both ways a store is switched: the Retail | Wholesale switch in the top
 *  bar (wholesale/WholesaleSwitch.tsx) and Settings > Stores > Switch
 *  (pages/settings/ImportDataPanels.tsx). Each calls `beginStoreSwitch(job)`; the host mounted
 *  once in App.tsx runs the job, follows the copy job's progress
 *  (GET /api/offline-first/activate/progress) when the engine answers `running`, and restarts
 *  the app when the other store is open. It lives at App level because a switch drops every
 *  kept-alive page (App.onStoreSwitched) -- a box inside Settings would vanish mid-switch.
 *
 *    beginStoreSwitch({
 *      target: 'Roshan Wholesale', kind: 'wholesale', current: 'Roshan Medical',
 *      confirm: 'Wholesale is a separate store …',   // optional: ask first, in the box
 *      start: async () => ({ restart: true, running: true, progress: '/api/…/progress' }),
 *    })
 *
 *  The box never changes size while it works (fixed width, every step drawn from the start,
 *  one fixed-height detail line), so nothing jumps; it follows the theme's colours, so it
 *  reads the same in dark mode.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { restartDesktopApp } from '../restartApp'
import { fetchSwitchProgress } from '../wholesale/mode'
import './store-switch.css'

export type StoreKind = 'retail' | 'wholesale'

export type StoreSwitchStart = {
  /** The app restarts after it (false: nothing changed, the box just closes). */
  restart: boolean
  /** The other store's own copy is being made in the background: follow `progress`. */
  running?: boolean
  progress?: string
  /** The name / kind the engine says the opened store has (shown when known). */
  storeName?: string
  kind?: StoreKind | null
}

export type StoreSwitchJob = {
  /** The store being opened. */
  target: string
  kind?: StoreKind | null
  /** The store this PC is on now ("Stay in …"). Falls back to the open store's name. */
  current?: string
  /** Ask first, inside the box. Without it the switch starts at once. */
  confirm?: string
  /** Do the switch. Throws with the message to show. */
  start: () => Promise<StoreSwitchStart>
  /** Called when the shop stays where it is (cancel, or Stay after an error). */
  onStay?: () => void
}

type Phase = 'confirm' | 'working' | 'error' | 'restarting'
type StepId = 'copy' | 'register' | 'open' | 'restart'

const POLL_MS = 800
/** "Restarting…" stays on screen this long before the app goes, so it is read. */
const RESTART_SHOW_MS = 450

/* ---------------------------------------------------------------- controller */

let current: StoreSwitchJob | null = null
let jobSeq = 0
const listeners = new Set<() => void>()

function emit() {
  for (const l of listeners) l()
}

/** Open the store switch box for this job (replaces nothing: a running switch wins). */
export function beginStoreSwitch(job: StoreSwitchJob): boolean {
  if (current) return false
  current = job
  jobSeq += 1
  emit()
  return true
}

/** A switch box is open (a second switch is refused while it is). */
export function storeSwitchBusy(): boolean {
  return current !== null
}

function closeJob() {
  current = null
  emit()
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

function snapshot() {
  return current ? jobSeq : 0
}

/** Re-renders when a switch box opens or closes. */
export function useStoreSwitchOpen(): boolean {
  return useSyncExternalStore(subscribe, snapshot, snapshot) !== 0
}

/* ---------------------------------------------------------------- progress model */

/** Which step the engine's progress lines have reached (they only move forward). */
export function phaseOfSteps(steps: string[]): Exclude<StepId, 'restart'> {
  let at: Exclude<StepId, 'restart'> = 'copy'
  const rank = { copy: 0, register: 1, open: 2 } as const
  for (const s of steps) {
    let p: Exclude<StepId, 'restart'> = 'copy'
    if (/^\s*opening\b|\bis open\b/i.test(s)) p = 'open'
    else if (/regist|reserving|^\s*done:/i.test(s)) p = 'register'
    if (rank[p] > rank[at]) at = p
  }
  return at
}

/** How much of the copy is done, from the newest "Downloading sales (5/12)…" line the
 *  server pull writes (core/server_live.sync_down_all): 4 of 12 lists done -> 0.333.
 *  null before the first such line. */
export function copyFraction(steps: string[]): number | null {
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const m = /^\s*downloading\b.*\(\s*([\d,]+)\s*\/\s*([\d,]+)\s*\)/i.exec(steps[i] || '')
    if (!m) continue
    const a = Number(m[1].replace(/,/g, ''))
    const b = Number(m[2].replace(/,/g, ''))
    if (!(b > 0) || !(a >= 1)) return null
    return Math.min(1, (a - 1) / b)
  }
  return null
}

/** Creeps from `from` toward `to` (never reaching it) as the step goes on. */
function creep(from: number, to: number, seconds: number, halfLife: number) {
  return from + (to - from) * (1 - Math.pow(0.5, Math.max(0, seconds) / halfLife))
}

function mmss(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function kindLabel(kind?: StoreKind | null) {
  return kind === 'wholesale' ? 'Wholesale' : kind === 'retail' ? 'Retail' : ''
}

/* ---------------------------------------------------------------- host */

export function StoreSwitchHost({ currentStoreName = '' }: { currentStoreName?: string }) {
  const open = useStoreSwitchOpen()
  const job = current
  if (!open || !job) return null
  return <StoreSwitchRunner key={jobSeq} job={job} fallbackCurrent={currentStoreName} />
}

function StoreSwitchRunner({ job, fallbackCurrent }: { job: StoreSwitchJob; fallbackCurrent: string }) {
  const [phase, setPhase] = useState<Phase>(job.confirm ? 'confirm' : 'working')
  const [copying, setCopying] = useState(false)
  const [steps, setSteps] = useState<string[]>([])
  const [error, setError] = useState('')
  const [target, setTarget] = useState(job.target)
  const [kind, setKind] = useState<StoreKind | null>(job.kind ?? null)
  const [startedAt, setStartedAt] = useState(0)
  const [stepStartedAt, setStepStartedAt] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const [pct, setPct] = useState(0)
  const alive = useRef(true)
  const run = useRef(0)
  const primaryRef = useRef<HTMLButtonElement>(null)
  const stayName = (job.current || fallbackCurrent || '').trim() || 'this store'

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const stage: StepId =
    phase === 'restarting' ? 'restart' : copying ? phaseOfSteps(steps) : 'open'

  // One clock for the timer and the bar.
  useEffect(() => {
    if (phase !== 'working' && phase !== 'restarting') return
    const t = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(t)
  }, [phase])

  // A new step restarts that step's creep.
  useEffect(() => {
    setStepStartedAt(Date.now())
  }, [stage])

  // The bar: each step owns a band; inside it, a real fraction from the engine's line when
  // there is one ("1,250/4,237"), else a slow creep. It never goes backwards.
  useEffect(() => {
    if (phase === 'confirm') return
    if (phase === 'restarting') {
      setPct(100)
      return
    }
    if (phase !== 'working') return
    const secs = (now - stepStartedAt) / 1000
    let next: number
    if (!copying) {
      next = creep(6, 92, secs, 2.5)
    } else if (stage === 'copy') {
      const f = copyFraction(steps)
      next = Math.max(creep(4, 68, secs, 45), f == null ? 0 : 4 + 64 * f)
    } else if (stage === 'register') {
      next = creep(70, 88, secs, 6)
    } else {
      next = creep(90, 97, secs, 2)
    }
    setPct((p) => Math.max(p, next))
  }, [now, phase, copying, stage, steps, stepStartedAt])

  const go = useCallback(async () => {
    const mine = ++run.current
    const t0 = Date.now()
    setPhase('working')
    setError('')
    setSteps([])
    setCopying(false)
    setPct(0)
    setStartedAt(t0)
    setStepStartedAt(t0)
    setNow(t0)
    const stillMine = () => alive.current && run.current === mine
    try {
      const res = await job.start()
      if (!stillMine()) return
      if (res.storeName) setTarget(res.storeName)
      if (res.kind) setKind(res.kind)
      if (res.running) {
        setCopying(true)
        for (;;) {
          await new Promise((r) => window.setTimeout(r, POLL_MS))
          if (!stillMine()) return
          let p
          try {
            p = await fetchSwitchProgress(res.progress)
          } catch {
            continue // the engine is busy reopening the store; ask again
          }
          if (!stillMine()) return
          setSteps(p.steps)
          if (!p.running) {
            if (p.error) throw new Error(p.error)
            break
          }
        }
      }
      if (!res.restart && !res.running) {
        closeJob()
        return
      }
      setPhase('restarting')
      await new Promise((r) => window.setTimeout(r, RESTART_SHOW_MS))
      if (!stillMine()) return
      await restartDesktopApp()
    } catch (e) {
      if (!stillMine()) return
      setError(e instanceof Error ? e.message : String(e))
      setPhase('error')
    }
  }, [job])

  // No question to ask: start at once.
  useEffect(() => {
    if (!job.confirm) void go()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (phase === 'confirm' || phase === 'error') primaryRef.current?.focus()
  }, [phase])

  function stay() {
    run.current += 1
    job.onStay?.()
    closeJob()
  }

  // Esc = stay, but only while nothing is running.
  useEffect(() => {
    if (phase !== 'confirm' && phase !== 'error') return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        stay()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase])

  const stepList: { id: StepId; label: string }[] = copying
    ? [
        { id: 'copy', label: 'Copying the store to this PC' },
        { id: 'register', label: 'Registering this PC' },
        { id: 'open', label: `Opening ${target}` },
        { id: 'restart', label: 'Restarting the app' },
      ]
    : [
        { id: 'open', label: `Opening ${target}` },
        { id: 'restart', label: 'Restarting the app' },
      ]
  const order: StepId[] = stepList.map((s) => s.id)
  const atIdx = order.indexOf(stage)
  const lastLine = steps.length ? steps[steps.length - 1] : ''
  const kl = kindLabel(kind)
  const elapsed = startedAt ? now - startedAt : 0

  const title =
    phase === 'confirm'
      ? `Open ${target}?`
      : phase === 'restarting'
        ? 'Restarting…'
        : phase === 'error'
          ? `Could not open ${target}`
          : `Opening ${target}`

  return (
    <div className="ssw-backdrop" role="presentation">
      <div
        className={`ssw-card ssw-${phase}`}
        role={phase === 'confirm' || phase === 'error' ? 'alertdialog' : 'dialog'}
        aria-modal="true"
        aria-labelledby="ssw-title"
        aria-describedby="ssw-desc"
        aria-busy={phase === 'working' || phase === 'restarting'}
      >
        <div className="ssw-head">
          <span className={`ssw-icon${kind === 'wholesale' ? ' ws' : ''}`} aria-hidden="true">
            <StoreIcon />
          </span>
          <div className="ssw-titles">
            <h2 id="ssw-title" title={title}>
              {title}
            </h2>
            <div className="ssw-sub">
              {kl ? <span className={`ssw-kind ssw-kind-${kind}`}>{kl.toUpperCase()}</span> : null}
              <span className="ssw-target" title={phase === 'restarting' ? target : `Now in ${stayName}`}>
                {phase === 'restarting' ? `${target} is ready` : `from ${stayName}`}
              </span>
            </div>
          </div>
          {phase !== 'confirm' ? (
            <span className="ssw-timer mono" title="Time since the switch started" aria-label={`Elapsed ${mmss(elapsed)}`}>
              {mmss(elapsed)}
            </span>
          ) : null}
        </div>

        <div className="ssw-body" id="ssw-desc">
          {phase === 'confirm' ? (
            <p className="ssw-confirm-text">{job.confirm}</p>
          ) : (
            <>
              <ol className="ssw-steps">
                {stepList.map((s, i) => {
                  const state =
                    phase === 'error'
                      ? i < atIdx
                        ? 'done'
                        : i === atIdx
                          ? 'failed'
                          : 'todo'
                      : i < atIdx
                        ? 'done'
                        : i === atIdx
                          ? 'now'
                          : 'todo'
                  return (
                    <li key={s.id} className={`ssw-step ${state}`} aria-current={state === 'now' ? 'step' : undefined}>
                      <span className="ssw-dot" aria-hidden="true">
                        {state === 'done' ? '✓' : state === 'failed' ? '!' : i + 1}
                      </span>
                      <span className="ssw-step-label">{s.label}</span>
                    </li>
                  )
                })}
              </ol>
              <div
                className="ssw-bar"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(pct)}
                aria-label="Progress"
              >
                <span style={{ width: `${Math.max(2, Math.min(100, pct))}%` }} />
              </div>
              <div className="ssw-detail" aria-live="polite" title={phase === 'error' ? error : lastLine}>
                {phase === 'error'
                  ? error || 'The store could not be opened.'
                  : phase === 'restarting'
                    ? `${target} is ready. The app opens it in a moment.`
                    : lastLine || (copying ? 'Starting…' : 'Opening the store…')}
              </div>
            </>
          )}
        </div>

        <div className="ssw-foot">
          {phase === 'confirm' || phase === 'error' ? (
            <>
              <button
                type="button"
                className="btn btn-neutral"
                onClick={stay}
                title={
                  phase === 'error'
                    ? `Nothing changed: this PC is still on ${stayName}.`
                    : `Keep working in ${stayName}`
                }
              >
                <span className="ssw-btn-text">Stay in {stayName}</span>
              </button>
              <button ref={primaryRef} type="button" className="btn btn-primary" onClick={() => void go()}>
                <span className="ssw-btn-text">{phase === 'error' ? 'Retry' : `Open ${target}`}</span>
              </button>
            </>
          ) : (
            <span className="ssw-note ssw-warn">
              <span aria-hidden="true">⚠</span> Don&apos;t close the app
              {copying ? ' — a big store can take a few minutes.' : '.'}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

function StoreIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 9l1.5-5h15L21 9" />
      <path d="M3 9h18v1.5a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0z" />
      <path d="M5 12.5V20h14v-7.5" />
      <path d="M10 20v-4h4v4" />
    </svg>
  )
}
