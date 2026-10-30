/**
 * Inventory → Check with master (owner, 30 Oct 2026): this shop's medicines whose schedule, HSN,
 * GST %, content or manufacturer is not what the master list says, and Fix.
 *
 * "Sure" = the master list check confirmed that field against the official rules and the web; the
 * master wins there, and "Fix all sure" changes only those. A medicine's own Fix takes the fields
 * ticked on its row (sure ones ticked to begin with). Every batch of the name is fixed together.
 * The engine does the work (core/master_check.py).
 */

import { useEffect, useMemo, useState } from 'react'
import { getApiBase } from '../api'

type Diff = { field: string; label: string; shop: string; master: string; sure: boolean }
type Row = { name: string; batches: number; diffs: Diff[]; sure: boolean }
type Answer = {
  ok: boolean
  rows: Row[]
  counts: Record<string, number>
  medicines: number
  compared: number
  online: boolean
  error?: string
}

const FIELDS: [string, string][] = [
  ['', 'All fields'],
  ['schedule', 'Schedule'],
  ['hsn', 'HSN'],
  ['gst', 'GST %'],
  ['content', 'Content'],
  ['manufacturer', 'Manufacturer'],
]
const SHOWN = 300

async function call<T>(path: string, body?: unknown): Promise<T> {
  // a write is a PUT: the engine's writes for settings and fixes (desktop_api _handle_settings_write)
  const res = await fetch(`${getApiBase()}${path}`, body === undefined ? undefined : {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = (await res.json().catch(() => ({}))) as T & { error?: string }
  if (!res.ok && !(data as { fixed?: number }).fixed) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

export function MasterCheckDialog({ open, onClose, onFixed }: { open: boolean; onClose: () => void; onFixed?: () => void }) {
  const [data, setData] = useState<Answer | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [sureOnly, setSureOnly] = useState(false)
  const [field, setField] = useState('')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState('')
  // per medicine, the fields its Fix takes (sure ones ticked to begin with)
  const [ticks, setTicks] = useState<Record<string, Set<string>>>({})

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const p = new URLSearchParams({ sure: sureOnly ? '1' : '', field, q })
      const out = await call<Answer>(`/api/inventory/master-check?${p}`)
      setData(out)
      const t: Record<string, Set<string>> = {}
      for (const r of out.rows) t[r.name] = new Set(r.diffs.filter((d) => d.sure).map((d) => d.field))
      setTicks(t)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => void load(), q ? 350 : 0)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sureOnly, field, q])

  const sureCount = useMemo(() => (data?.rows || []).filter((r) => r.sure).length, [data])

  if (!open) return null

  const fix = async (body: unknown, label: string) => {
    setBusy(label)
    setError('')
    setNote('')
    try {
      const out = await call<{ ok: boolean; fixed: number; rows: number; failed: { name: string; error: string }[] }>(
        '/api/inventory/master-check/fix',
        body,
      )
      setNote(`${out.fixed} medicine(s) fixed (${out.rows} batch rows).${out.failed?.length ? ` ${out.failed.length} could not be fixed.` : ''}`)
      onFixed?.()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  const toggle = (name: string, f: string) =>
    setTicks((t) => {
      const s = new Set(t[name] || [])
      if (s.has(f)) s.delete(f)
      else s.add(f)
      return { ...t, [name]: s }
    })

  const rows = (data?.rows || []).slice(0, SHOWN)

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose} onKeyDown={(e) => e.key === 'Escape' && onClose()}>
      <div className="modal-card dm-card" role="dialog" aria-modal="true" aria-label="Check with master" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Check with master</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body dm-body">
          <p className="muted" style={{ marginTop: 0 }}>
            Your medicines whose schedule, HSN, GST, content or company is not what the master list says.{' '}
            <b>Sure</b> = the master list check confirmed it from the official rules and the web: the master is right there.
            Fix puts the master's value into every batch of the medicine.
          </p>
          <div className="dm-filters">
            <div className="dm-field">
              <span className="dm-label">Search</span>
              <input className="settings-input" placeholder="Medicine name" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <div className="dm-field">
              <span className="dm-label">Field</span>
              <select className="settings-input" value={field} onChange={(e) => setField(e.target.value)}>
                {FIELDS.map(([v, t]) => (
                  <option key={v} value={v}>
                    {t}
                    {v && data?.counts?.[v] != null ? ` (${data.counts[v]})` : ''}
                  </option>
                ))}
              </select>
            </div>
            <label className="dm-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={sureOnly} onChange={(e) => setSureOnly(e.target.checked)} />
              <span>Only where the master is sure</span>
            </label>
          </div>

          {error ? <p className="page-error">{error}</p> : null}
          {note ? <p className="dm-ok">{note}</p> : null}

          <div className="dm-preview" aria-busy={loading}>
            <div className="dm-preview-head">
              <h3>{loading ? 'Checking…' : `${(data?.rows || []).length} medicines differ`}</h3>
              <span className="muted">
                {data ? `${data.compared} of your ${data.medicines} medicine names are in the master list · ${sureCount} with a sure difference` : ''}
              </span>
            </div>
            {rows.length ? (
              <div className="dm-table-wrap">
                <table className="sat-table dm-table">
                  <thead>
                    <tr>
                      <th>Medicine</th>
                      <th>Field</th>
                      <th>Your inventory</th>
                      <th>Master</th>
                      <th>Fix it</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) =>
                      r.diffs.map((d, i) => (
                        <tr key={`${r.name}|${d.field}`}>
                          {i === 0 ? (
                            <td rowSpan={r.diffs.length} className="dm-wrap">
                              <b>{r.name}</b>
                              {r.batches > 1 ? <div className="muted">{r.batches} batches</div> : null}
                            </td>
                          ) : null}
                          <td>{d.label}</td>
                          <td className="dm-wrap" style={{ color: 'var(--danger, #c0392b)' }}>{d.shop || <span className="muted">empty</span>}</td>
                          <td className="dm-wrap">
                            {d.master || <span className="muted">none (no schedule)</span>}{' '}
                            {d.sure ? <span className="dm-chip">sure</span> : <span className="muted">(not confirmed)</span>}
                          </td>
                          <td>
                            <input
                              type="checkbox"
                              aria-label={`Fix ${d.label} of ${r.name}`}
                              checked={Boolean(ticks[r.name]?.has(d.field))}
                              onChange={() => toggle(r.name, d.field)}
                            />
                          </td>
                          {i === 0 ? (
                            <td rowSpan={r.diffs.length}>
                              <button
                                type="button"
                                className="btn btn-primary btn-sm"
                                disabled={Boolean(busy) || !ticks[r.name]?.size}
                                onClick={() => void fix({ items: [{ name: r.name, fields: [...(ticks[r.name] || [])] }] }, r.name)}
                              >
                                {busy === r.name ? 'Fixing…' : 'Fix'}
                              </button>
                            </td>
                          ) : null}
                        </tr>
                      )),
                    )}
                  </tbody>
                </table>
                {(data?.rows || []).length > rows.length ? (
                  <p className="muted dm-more">Showing {rows.length} of {data?.rows.length}. Search a name to see others.</p>
                ) : null}
              </div>
            ) : !loading ? (
              <p className="dm-empty">{data ? 'Every medicine agrees with the master list.' : ''}</p>
            ) : null}
          </div>
        </div>
        <div className="modal-foot dm-foot">
          <button type="button" className="btn btn-neutral" onClick={onClose}>
            Close
          </button>
          <span className="dm-foot-gap" />
          <button
            type="button"
            className="btn btn-primary"
            disabled={Boolean(busy) || loading || !sureCount}
            onClick={() => {
              if (window.confirm(`Put the master's sure values into ${sureCount} medicine(s)? Only the sure fields change.`)) {
                void fix({ all: true }, 'all')
              }
            }}
          >
            {busy === 'all' ? 'Fixing…' : `Fix all sure (${sureCount})`}
          </button>
        </div>
      </div>
    </div>
  )
}
