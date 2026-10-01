/** Regular medicines: each customer's standing list (BP, sugar, thyroid ...), kept on purpose
 *  -- not read from the sales history.
 *
 *  RegularOfferDialog   pops up when such a customer is picked: give all, give some, change a
 *                       quantity, or skip.
 *  RegularManagerDialog makes and edits the list (from Sales -> Tabs & Tools, or "Yaadi badla").
 *
 *  A bill made from the list is an ordinary sale: history, dues and stock see nothing special.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchAllRegulars, fetchMedicineNames, type RegularItem, type RegularList } from '../pagesApi'

const UNIT_LABEL: Record<string, string> = { strip: 'Patta (strip)', tablet: 'Goli / nag', unit: 'Bottle / nag', '': 'Bill pramane' }

function UnitSelect({ value, onChange }: { value: string; onChange: (v: RegularItem['unit']) => void }) {
  return (
    <select className="settings-input" value={value} onChange={(e) => onChange(e.target.value as RegularItem['unit'])}>
      {Object.entries(UNIT_LABEL).map(([k, l]) => (
        <option key={k} value={k}>
          {l}
        </option>
      ))}
    </select>
  )
}

type Row = RegularItem & { on: boolean }

/** "Ramesh che regular: Telmikind AM 1 patta, Glycomet 500 2 patte -- dyaych ka?" */
export function RegularOfferDialog({
  open,
  customer,
  items,
  busy,
  onGive,
  onSkip,
  onEdit,
}: {
  open: boolean
  customer: string
  items: RegularItem[]
  busy: boolean
  onGive: (items: RegularItem[]) => void
  onSkip: () => void
  onEdit: () => void
}) {
  const [rows, setRows] = useState<Row[]>([])
  const giveRef = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    if (open) {
      setRows(items.map((it) => ({ ...it, on: true })))
      window.setTimeout(() => giveRef.current?.focus(), 30)
    }
  }, [open, items])
  if (!open) return null
  const picked = rows.filter((r) => r.on && Number(r.qty) > 0)
  const give = () => onGive(picked.map(({ on: _on, ...r }) => ({ ...r, qty: Number(r.qty) })))
  const node = (
    <div className="modal-backdrop" role="presentation" onClick={onSkip}>
      <div
        className="modal-card modal-card-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Regular medicines"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onSkip()
          } else if (e.key === 'Enter' && !(e.target instanceof HTMLSelectElement)) {
            e.preventDefault()
            if (!busy && picked.length) give()
          }
        }}
      >
        <div className="modal-head">
          <h2>Regular aushadha — {customer}</h2>
        </div>
        <div className="modal-body">
          <p style={{ margin: '0 0 10px' }}>
            Nehmichi aushadha billa madhe ghalaychi ka? Nako aslele kadha (✓ kadha), quantity badla.
          </p>
          <div className="table-scroll">
            <table className="sat-table">
              <thead>
                <tr>
                  <th style={{ width: 40 }}>✓</th>
                  <th>Aushadh</th>
                  <th className="num" style={{ width: 110 }}>Qty</th>
                  <th style={{ width: 160 }}>Unit</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.name}>
                    <td>
                      <input
                        type="checkbox"
                        checked={r.on}
                        onChange={(e) => setRows((all) => all.map((x, j) => (j === i ? { ...x, on: e.target.checked } : x)))}
                      />
                    </td>
                    <td>{r.name}</td>
                    <td className="num">
                      <input
                        className="settings-input"
                        style={{ width: 90, textAlign: 'right' }}
                        type="number"
                        min={0}
                        step="any"
                        value={r.qty}
                        onChange={(e) =>
                          setRows((all) => all.map((x, j) => (j === i ? { ...x, qty: Number(e.target.value) } : x)))
                        }
                      />
                    </td>
                    <td>
                      <UnitSelect
                        value={r.unit}
                        onChange={(v) => setRows((all) => all.map((x, j) => (j === i ? { ...x, unit: v } : x)))}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-neutral" onClick={onEdit} disabled={busy}>
            Yaadi badla
          </button>
          <button type="button" className="btn btn-neutral" onClick={onSkip} disabled={busy}>
            Nako (Esc)
          </button>
          <button
            ref={giveRef}
            type="button"
            className="btn btn-primary"
            disabled={busy || !picked.length}
            onClick={give}
          >
            {busy ? 'Ghalat aahe…' : `Bill madhe ghala (${picked.length}) — Enter`}
          </button>
        </div>
      </div>
    </div>
  )
  return createPortal(node, document.body)
}

/** Make or change a customer's regular list; with no customer, pick one that has a list. */
export function RegularManagerDialog({
  open,
  customerId,
  customer,
  initial,
  billItems,
  billDate,
  busy,
  onSave,
  onPickCustomer,
  onClose,
}: {
  open: boolean
  customerId: number | null
  customer: string
  initial: RegularItem[]
  /** The open bill's lines, to take them as the list in one go. */
  billItems: { name: string; qty: number }[]
  billDate: string
  busy: boolean
  onSave: (items: RegularItem[]) => void
  onPickCustomer: (name: string) => void
  onClose: () => void
}) {
  const [rows, setRows] = useState<RegularItem[]>([])
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<string[]>([])
  const [lists, setLists] = useState<RegularList[] | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!open) return
    setRows(initial.map((r) => ({ ...r })))
    setQ('')
    setErr('')
    setLists(null)
    if (!customerId) {
      void fetchAllRegulars()
        .then((r) => setLists(r.lists || []))
        .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
    }
  }, [open, customerId, initial])

  useEffect(() => {
    if (!open || q.trim().length < 2) {
      setHits([])
      return
    }
    let live = true
    const t = window.setTimeout(() => {
      void fetchMedicineNames(q.trim(), { bill_date: billDate, limit: 12 })
        .then((r) => live && setHits((r.names || []).map((n) => n.name)))
        .catch(() => live && setHits([]))
    }, 180)
    return () => {
      live = false
      window.clearTimeout(t)
    }
  }, [q, open, billDate])

  const have = useMemo(() => new Set(rows.map((r) => r.name.toUpperCase())), [rows])
  if (!open) return null

  const add = (name: string, qty = 1, unit: RegularItem['unit'] = '') => {
    const n = name.trim().toUpperCase()
    if (!n || have.has(n)) return
    setRows((all) => [...all, { name: n, qty, unit }])
    setQ('')
    setHits([])
  }

  const node = (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Regular medicines list"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onClose()
          }
        }}
      >
        <div className="modal-head">
          <h2>{customerId ? `Regular aushadha yaadi — ${customer}` : 'Regular grahak'}</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          {err ? <p className="page-error">{err}</p> : null}
          {!customerId ? (
            <>
              <p style={{ margin: '0 0 10px' }}>
                Aadhi bill var grahak nivda — mag ithe tyachi yaadi banvta yeil. Khali regular yaadi aslele grahak:
              </p>
              {lists === null ? <p className="muted">Loading…</p> : null}
              {lists && !lists.length ? <p className="muted">Ajun konachich regular yaadi nahi.</p> : null}
              {lists && lists.length ? (
                <div className="table-scroll">
                  <table className="sat-table">
                    <thead>
                      <tr>
                        <th>Grahak</th>
                        <th>Phone</th>
                        <th>Aushadha</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {lists.map((l) => (
                        <tr key={l.customer_id}>
                          <td>{l.customer}</td>
                          <td>{l.phone}</td>
                          <td>{l.items.map((i) => `${i.name} ×${i.qty}`).join(', ')}</td>
                          <td>
                            <button type="button" className="btn btn-primary btn-sm" onClick={() => onPickCustomer(l.customer)}>
                              Ya grahakache bill
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </>
          ) : (
            <>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
                <input
                  className="settings-input"
                  style={{ flex: 1, minWidth: 220 }}
                  placeholder="Aushadh shodha (2 akshare)…"
                  value={q}
                  list="regular-med-hits"
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      add(hits.find((h) => h.toUpperCase() === q.trim().toUpperCase()) || hits[0] || q)
                    }
                  }}
                />
                <datalist id="regular-med-hits">
                  {hits.map((h) => (
                    <option key={h} value={h} />
                  ))}
                </datalist>
                <button type="button" className="btn btn-neutral" onClick={() => add(hits[0] || q)} disabled={!q.trim()}>
                  Jodha
                </button>
                <button
                  type="button"
                  className="btn btn-neutral"
                  disabled={!billItems.length}
                  title="Ya bill madhli aushadha yaadit ghya"
                  onClick={() => {
                    for (const it of billItems) add(it.name, Number(it.qty) || 1, 'tablet')
                  }}
                >
                  Ya bill madhli aushadha ghya
                </button>
              </div>
              <div className="table-scroll">
                <table className="sat-table">
                  <thead>
                    <tr>
                      <th>Aushadh</th>
                      <th className="num" style={{ width: 110 }}>Qty</th>
                      <th style={{ width: 160 }}>Unit</th>
                      <th style={{ width: 60 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={r.name}>
                        <td>{r.name}</td>
                        <td className="num">
                          <input
                            className="settings-input"
                            style={{ width: 90, textAlign: 'right' }}
                            type="number"
                            min={0}
                            step="any"
                            value={r.qty}
                            onChange={(e) =>
                              setRows((all) => all.map((x, j) => (j === i ? { ...x, qty: Number(e.target.value) } : x)))
                            }
                          />
                        </td>
                        <td>
                          <UnitSelect
                            value={r.unit}
                            onChange={(v) => setRows((all) => all.map((x, j) => (j === i ? { ...x, unit: v } : x)))}
                          />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="settings-link-btn danger-text"
                            onClick={() => setRows((all) => all.filter((_, j) => j !== i))}
                          >
                            Kadha
                          </button>
                        </td>
                      </tr>
                    ))}
                    {!rows.length ? (
                      <tr>
                        <td colSpan={4} className="muted">
                          Yaadi rikami — vara aushadh shodhun jodha.
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
        {customerId ? (
          <div className="modal-foot">
            <button type="button" className="btn btn-neutral" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => onSave(rows.filter((r) => Number(r.qty) > 0))}
            >
              {busy ? 'Save hot aahe…' : rows.length ? 'Yaadi save kara' : 'Yaadi kadhun taka'}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  )
  return createPortal(node, document.body)
}
