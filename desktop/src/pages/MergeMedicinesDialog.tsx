/**
 * Inventory → Merge medicines (owner, 30 Oct 2026): rows of one medicine kept under two or three
 * names on the same batch ("DOXY 1 LDR FORTE CAP 10'S" and "DOXY-L-DR-FORT CAP"). Bills took
 * stock off one row while the shelf showed on the other.
 *
 * Each group: pick the row to keep, tick the rows that go into it, and the stock it ends with
 * (the sum, or the count on the shelf). Every bill line moves to the kept row; the others are
 * hidden and deleted. The engine does the work (core/medicine_merge.py).
 */

import { Fragment, useEffect, useState } from 'react'
import { getApiBase } from '../api'

type Row = {
  id: number
  name: string
  batch_no: string
  mrp: number
  expiry_date: string
  stock_qty: number
  is_hidden: boolean
  bill_lines?: number
}
type Group = { batch: string; rows: Row[]; stock_total: number; split_stock: boolean }
type Answer = { ok: boolean; online: boolean; groups: Group[]; error?: string }
type Pick = { keep: number; merge: Set<number>; stock: string }

const SHOWN = 150

async function call<T>(path: string, body?: unknown): Promise<T> {
  // a write is a PUT: the engine's writes go through desktop_api _handle_settings_write
  const res = await fetch(`${getApiBase()}${path}`, body === undefined ? undefined : {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = (await res.json().catch(() => ({}))) as T & { error?: string }
  if (!res.ok || (data as { ok?: boolean }).ok === false) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

const keyOf = (g: Group) => `${g.batch}|${g.rows.map((r) => r.id).join(',')}`

function firstPick(g: Group): Pick {
  const keep = g.rows[0]?.id ?? 0
  return { keep, merge: new Set(g.rows.filter((r) => r.id !== keep).map((r) => r.id)), stock: '' }
}

function sumOf(g: Group, p: Pick): number {
  return g.rows
    .filter((r) => r.id === p.keep || p.merge.has(r.id))
    .reduce((t, r) => t + (Number(r.stock_qty) || 0), 0)
}

function expiryText(iso: string): string {
  const m = /^(\d{4})-(\d{2})/.exec(iso || '')
  return m ? `${m[2]}/${m[1].slice(2)}` : ''
}

export function MergeMedicinesDialog({ open, onClose, onMerged }: { open: boolean; onClose: () => void; onMerged?: () => void }) {
  const [data, setData] = useState<Answer | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [q, setQ] = useState('')
  const [splitOnly, setSplitOnly] = useState(true)
  const [busy, setBusy] = useState('')
  const [picks, setPicks] = useState<Record<string, Pick>>({})

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const p = new URLSearchParams({ q, split: splitOnly ? '1' : '' })
      const out = await call<Answer>(`/api/inventory/merge-candidates?${p}`)
      setData(out)
      const next: Record<string, Pick> = {}
      for (const g of out.groups) next[keyOf(g)] = firstPick(g)
      setPicks(next)
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
  }, [open, q, splitOnly])

  if (!open) return null

  const setPick = (k: string, p: Pick) => setPicks((all) => ({ ...all, [k]: p }))

  const doMerge = async (g: Group) => {
    const k = keyOf(g)
    const p = picks[k]
    if (!p || !p.merge.size) return
    const keep = g.rows.find((r) => r.id === p.keep)
    const going = g.rows.filter((r) => p.merge.has(r.id))
    const total = sumOf(g, p)
    const typed = p.stock.trim()
    const after = typed === '' ? total : Number(typed)
    if (!Number.isFinite(after) || after < 0) {
      setError('Stock after merge must be a number, 0 or more.')
      return
    }
    const msg =
      `Merge ${going.map((r) => r.name).join(', ')} into ${keep?.name} (batch ${g.batch})?\n\n` +
      `Their bills move to ${keep?.name}; they are hidden and deleted.\n` +
      `Stock of ${keep?.name}: ${after}${after !== total ? ` (the rows add up to ${total})` : ''}.`
    if (!window.confirm(msg)) return
    setBusy(k)
    setError('')
    setNote('')
    try {
      const out = await call<{ merged: number; stock: number }>('/api/inventory/merge', {
        keep_id: p.keep,
        merge_ids: [...p.merge],
        stock_after: typed === '' ? null : after,
      })
      setNote(`Merged ${out.merged} row(s) into ${keep?.name}. Stock now ${out.stock}.`)
      onMerged?.()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  const groups = (data?.groups || []).slice(0, SHOWN)

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose} onKeyDown={(e) => e.key === 'Escape' && onClose()}>
      <div className="modal-card dm-card" role="dialog" aria-modal="true" aria-label="Merge medicines" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Merge medicines</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body dm-body">
          <p className="muted" style={{ marginTop: 0 }}>
            One medicine kept under two or three names on the same batch: a bill takes stock off one row while
            the shelf shows on the other. Pick the row to <b>keep</b>, tick the rows to <b>merge</b> into it, and
            Merge. Their bills move to the kept row and they are hidden. Stock after merge is their sum; type
            the count on the shelf to set it.
          </p>
          <div className="dm-filters">
            <div className="dm-field">
              <span className="dm-label">Search</span>
              <input className="settings-input" placeholder="Medicine name or batch" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <label className="dm-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={splitOnly} onChange={(e) => setSplitOnly(e.target.checked)} />
              <span>Only where stock is on two rows</span>
            </label>
          </div>

          {error ? <p className="page-error">{error}</p> : null}
          {note ? <p className="dm-ok">{note}</p> : null}

          <div className="dm-preview" aria-busy={loading}>
            <div className="dm-preview-head">
              <h3>{loading ? 'Looking…' : `${(data?.groups || []).length} medicines on more than one row`}</h3>
              <span className="muted">Same batch, names that start alike. Check that the rows are really one medicine.</span>
            </div>
            {groups.length ? (
              <div className="dm-table-wrap">
                <table className="sat-table dm-table">
                  <thead>
                    <tr>
                      <th>Keep</th>
                      <th>Merge</th>
                      <th>Medicine</th>
                      <th>Batch</th>
                      <th>Exp</th>
                      <th>MRP</th>
                      <th>Stock</th>
                      {data?.online ? null : <th>Bill lines</th>}
                      <th>Stock after merge</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {groups.map((g) => {
                      const k = keyOf(g)
                      const p = picks[k] || firstPick(g)
                      const total = sumOf(g, p)
                      return (
                        <Fragment key={k}>
                          {g.rows.map((r, i) => (
                            <tr key={r.id} className={i === 0 ? 'dm-group-start' : undefined}>
                              <td>
                                <input
                                  type="radio"
                                  name={`keep-${k}`}
                                  aria-label={`Keep ${r.name}`}
                                  checked={p.keep === r.id}
                                  onChange={() => {
                                    const merge = new Set(p.merge)
                                    merge.delete(r.id)
                                    if (p.keep && p.keep !== r.id) merge.add(p.keep)
                                    setPick(k, { ...p, keep: r.id, merge })
                                  }}
                                />
                              </td>
                              <td>
                                <input
                                  type="checkbox"
                                  aria-label={`Merge ${r.name}`}
                                  disabled={p.keep === r.id}
                                  checked={p.merge.has(r.id)}
                                  onChange={() => {
                                    const merge = new Set(p.merge)
                                    if (merge.has(r.id)) merge.delete(r.id)
                                    else merge.add(r.id)
                                    setPick(k, { ...p, merge })
                                  }}
                                />
                              </td>
                              <td className="dm-wrap">
                                <b>{r.name}</b>
                                {r.is_hidden ? <span className="muted"> (hidden)</span> : null}
                              </td>
                              <td className="mono">{r.batch_no}</td>
                              <td className="mono">{expiryText(r.expiry_date)}</td>
                              <td className="mono">{Number(r.mrp || 0).toFixed(2)}</td>
                              <td className="mono">{r.stock_qty}</td>
                              {data?.online ? null : <td className="mono">{r.bill_lines ?? 0}</td>}
                              {i === 0 ? (
                                <>
                                  <td rowSpan={g.rows.length}>
                                    <input
                                      className="settings-input mono"
                                      style={{ width: 90 }}
                                      inputMode="numeric"
                                      aria-label={`Stock after merge, batch ${g.batch}`}
                                      placeholder={String(total)}
                                      value={p.stock}
                                      onChange={(e) => setPick(k, { ...p, stock: e.target.value.replace(/[^0-9]/g, '') })}
                                    />
                                  </td>
                                  <td rowSpan={g.rows.length}>
                                    <button
                                      type="button"
                                      className="btn btn-primary btn-sm"
                                      disabled={Boolean(busy) || !p.merge.size}
                                      onClick={() => void doMerge(g)}
                                    >
                                      {busy === k ? 'Merging…' : 'Merge'}
                                    </button>
                                  </td>
                                </>
                              ) : null}
                            </tr>
                          ))}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
                {(data?.groups || []).length > groups.length ? (
                  <p className="muted dm-more">Showing {groups.length} of {data?.groups.length}. Search a name to see others.</p>
                ) : null}
              </div>
            ) : !loading ? (
              <p className="dm-empty">{data ? 'No medicine is on more than one row of a batch.' : ''}</p>
            ) : null}
          </div>
        </div>
        <div className="modal-foot dm-foot">
          <button type="button" className="btn btn-neutral" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
