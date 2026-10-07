/** Offline-first (engine: core/offline_first): the sync badge in the top bar, and the
 *  switch-over an Online PC makes by itself on its first start of this version. */
import { useEffect, useRef, useState } from 'react'
import { getApiBase } from '../api'

type OfStatus = {
  ok?: boolean
  active?: boolean
  registered?: boolean
  noted?: number
  waiting?: number
  stock_moves_waiting?: number
  flagged?: number
  device_no?: string | null
  server_last_seq?: string | null
  last_push_at?: string | null
  last_pull_at?: string | null
  last_error?: string | null
  last_error_at?: string | null
  numbers_left?: Record<string, number>
  worker_alive?: boolean
}

async function getJ<T>(path: string): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`)
  return (await res.json()) as T
}

async function postJ<T>(path: string, body: unknown = {}): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return (await res.json()) as T
}

function ago(iso?: string | null): string {
  if (!iso) return 'never'
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return '—'
  const s = Math.max(0, Math.round((Date.now() - t) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  return `${Math.round(s / 3600)} h ago`
}

export function OfflineFirstPill() {
  const [st, setSt] = useState<OfStatus | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let alive = true
    const load = () =>
      void getJ<OfStatus>('/api/offline-first/status')
        .then((s) => alive && setSt(s))
        .catch(() => undefined)
    load()
    const id = window.setInterval(load, 8000)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [])

  if (!st?.registered) {
    return <span className="mainnav-sync mainnav-sync-offline">Offline-first</span>
  }
  const waiting = (st.noted || 0) + (st.waiting || 0) + (st.stock_moves_waiting || 0)
  const errRecent =
    st.last_error && st.last_error_at && Date.now() - Date.parse(st.last_error_at) < 60_000
  const label = st.flagged
    ? `Sync: ${st.flagged} to check`
    : errRecent
      ? waiting
        ? `No internet · ${waiting} saved here`
        : 'No internet · all saved here'
      : waiting
        ? `Sending ${waiting}…`
        : 'Synced'
  const cls = st.flagged || errRecent ? 'mainnav-sync-offline' : 'mainnav-sync-online'
  return (
    <span style={{ position: 'relative' }}>
      <button
        type="button"
        className={`mainnav-sync ${cls}`}
        style={{ cursor: 'pointer' }}
        title="Offline-first sync: this PC works on its own copy and keeps it in step with the server"
        onClick={() => setOpen((v) => !v)}
      >
        {label}
      </button>
      {open ? (
        <div className="offline-first-pop" role="dialog" aria-label="Sync details">
          <div><b>This PC</b> · device {st.device_no ?? '—'}</div>
          <div>Last sent: {ago(st.last_push_at)} · last received: {ago(st.last_pull_at)}</div>
          <div>
            Waiting to send: {waiting} {waiting ? '(kept on this PC until the server has them)' : ''}
          </div>
          {st.flagged ? (
            <div style={{ color: 'var(--danger, #c0392b)' }}>
              {st.flagged} record(s) need a look (kept on the server, nothing lost).
            </div>
          ) : null}
          {st.numbers_left ? (
            <div className="muted">
              Bill numbers ready: {st.numbers_left.sales ?? 0} · purchase numbers: {st.numbers_left.purchases ?? 0}
            </div>
          ) : null}
          {errRecent ? <div className="muted">Last try: {st.last_error}</div> : null}
          <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => void postJ('/api/offline-first/sync-now')}
            >
              Sync now
            </button>
            <button type="button" className="btn btn-neutral btn-sm" onClick={() => setOpen(false)}>
              Close
            </button>
          </div>
        </div>
      ) : null}
    </span>
  )
}

type Progress = { running?: boolean; steps?: string[]; result?: unknown; error?: string | null }

/** First start of this version on an Online PC: copy the store and switch to offline-first.
 *  Blocks the screen while it runs (closing the app half way would only mean it starts
 *  again next time; the mode changes only at the end). */
export function OfflineFirstAutoSwitch({ enabled }: { enabled: boolean }) {
  const [state, setState] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [steps, setSteps] = useState<string[]>([])
  const [error, setError] = useState('')
  const started = useRef(false)

  useEffect(() => {
    if (!enabled || started.current) return
    try {
      if (sessionStorage.getItem('of_auto_skip') === '1') return
    } catch {
      /* ignore */
    }
    started.current = true
    setState('running')
    let alive = true
    void (async () => {
      try {
        await postJ('/api/offline-first/activate')
        for (;;) {
          await new Promise((r) => setTimeout(r, 1500))
          const p = await getJ<Progress>('/api/offline-first/activate/progress')
          if (!alive) return
          setSteps(p.steps || [])
          if (!p.running) {
            if (p.error) {
              setError(p.error)
              setState('error')
            } else {
              setState('done')
              window.setTimeout(() => window.location.reload(), 1200)
            }
            return
          }
        }
      } catch (e) {
        if (!alive) return
        setError(e instanceof Error ? e.message : String(e))
        setState('error')
      }
    })()
    return () => {
      alive = false
    }
  }, [enabled])

  if (state === 'idle') return null
  return (
    <div className="modal-backdrop" role="presentation" style={{ zIndex: 9999 }}>
      <div className="modal-card" role="dialog" aria-modal="true" aria-label="Setting up offline-first">
        <div className="modal-head">
          <h2>{state === 'error' ? 'Could not switch yet' : 'Setting up this PC'}</h2>
        </div>
        <div className="modal-body">
          {state === 'running' ? (
            <p>
              This PC now keeps its own copy of the store, so billing works even without
              internet and everything reaches the server by itself. Copying the store…
              <b> app band karu naka.</b>
            </p>
          ) : null}
          {state === 'done' ? <p>Done. Opening the store…</p> : null}
          {state === 'error' ? (
            <>
              <p>{error}</p>
              <p className="muted">Nothing was changed: the store stays Online. It will try again next time.</p>
            </>
          ) : null}
          <ul className="muted" style={{ maxHeight: 180, overflow: 'auto', fontSize: 12 }}>
            {steps.slice(-8).map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
        {state === 'error' ? (
          <div className="modal-foot">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                try {
                  sessionStorage.setItem('of_auto_skip', '1')
                } catch {
                  /* ignore */
                }
                setState('idle')
              }}
            >
              Continue Online
            </button>
          </div>
        ) : null}
      </div>
    </div>
  )
}
