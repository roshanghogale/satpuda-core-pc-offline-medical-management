/** The bar shown while a store is copied from the server for Offline use: the engine's real
 *  figures (records copied of the server's total, time left -- core/copy_progress.py). */

/** The store copy's figures from the engine (`copy` in heavy_job_status). */
export type CopyFigures = {
  pct: number
  label: string
  collection?: string
  done?: number
  total?: number
  /** Seconds left, once there is enough to estimate it. */
  eta_s?: number | null
  elapsed_s?: number
}

export function copyFigures(raw: unknown): CopyFigures | null {
  if (!raw || typeof raw !== 'object') return null
  const d = raw as Record<string, unknown>
  const pct = Number(d.pct)
  if (!Number.isFinite(pct)) return null
  const eta = d.eta_s == null ? null : Number(d.eta_s)
  return {
    pct: Math.max(0, Math.min(100, pct)),
    label: String(d.label || ''),
    collection: d.collection ? String(d.collection) : undefined,
    done: Number(d.done) || 0,
    total: Number(d.total) || 0,
    eta_s: eta != null && Number.isFinite(eta) ? eta : null,
    elapsed_s: Number(d.elapsed_s) || 0,
  }
}

/** "about 3 min left" / "less than a minute left" ('' when not known yet). */
export function timeLeftText(etaS: number | null | undefined): string {
  if (etaS == null || !Number.isFinite(etaS)) return ''
  if (etaS < 45) return 'less than a minute left'
  const m = Math.round(etaS / 60)
  return m <= 1 ? 'about 1 min left' : `about ${m} min left`
}

export function CopyProgressBar({ pct, label, etaS, secs }: { pct: number; label: string; etaS?: number | null; secs?: number }) {
  const left = timeLeftText(etaS)
  return (
    <div className="copy-progress" aria-label="Progress">
      <div className="copy-progress-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="copy-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="copy-progress-text">
        <b>{pct}%</b>
        <span>{label || 'Starting…'}</span>
        <span className="muted">
          {left ? `${left} · ` : ''}
          {secs != null ? `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}` : ''}
        </span>
      </div>
    </div>
  )
}
