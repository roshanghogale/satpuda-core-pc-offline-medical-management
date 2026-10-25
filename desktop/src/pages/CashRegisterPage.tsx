/** Settings > Cash & Expenses (owner, 25 Oct 2026): the day's cash register.
 *
 *  Open the shop: type the cash in the drawer (asked once, on the first open of the day;
 *  editable). Through the day the bills' cash, dues received, returns refunded, purchases paid
 *  at entry and supplier payments appear by themselves from the books (core/cash_register.py
 *  reads them; nothing is stored twice), and the shop types its expenses -- tea, medicines bought
 *  from another store -- or cash it added (Enter adds a line). At close: Expected cash, the
 *  counted cash, the difference in green / red, "Close day" keeps the count. A month view lists
 *  every day's difference and the expenses by category. Excel / PDF / Print for both.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { getApiBase } from '../api'
import { saveFileAndShow } from '../savedFiles'
import '../styles/cash-register.css'

type Line = { id: number; time: string; ref: string; party: string; detail: string; amount: number }
type Section = { key: string; title: string; flow: 'in' | 'out'; lines: Line[]; total: number }
type Manual = { id: number; time: string; kind: 'in' | 'out'; category: string; description: string; amount: number }
type DayReport = {
  ok?: boolean
  error?: string
  date: string
  date_dmy: string
  opening: { value: number; set: boolean; row_id: number | null }
  sections: Section[]
  expenses: Manual[]
  cash_added: Manual[]
  totals: { cash_in: number; cash_out: number; expenses: number; cash_added: number; expected: number }
  counted: number | null
  closed_at: string | null
  note: string
  difference: number | null
  categories: string[]
  saved_id?: number
}
type DayLine = {
  date: string; date_dmy: string; opening: number; cash_in: number; cash_out: number; expenses: number
  expected: number; counted: number | null; difference: number | null; closed: boolean
}
type RangeReport = {
  ok?: boolean
  error?: string
  from: string; to: string; from_dmy: string; to_dmy: string
  days: DayLine[]
  categories: { category: string; amount: number }[]
  expenses: (Manual & { date: string; date_dmy: string })[]
  totals: { opening: number; cash_in: number; cash_out: number; expenses: number; difference: number; days_closed: number }
}

const pad = (n: number) => String(n).padStart(2, '0')
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const todayIso = () => iso(new Date())
const thisMonth = () => todayIso().slice(0, 7)
const nowHm = () => {
  const d = new Date()
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}
const money = (v: number | null | undefined) =>
  typeof v === 'number' ? v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : ''
const shiftDay = (day: string, n: number) => {
  const d = new Date(`${day}T12:00:00`)
  d.setDate(d.getDate() + n)
  return iso(d)
}
function monthBounds(ym: string) {
  const [y, m] = ym.split('-').map(Number)
  return { from: iso(new Date(y, m - 1, 1)), to: iso(new Date(y, m, 0)) }
}
const dmy = (s: string) => (s && s.length >= 10 ? `${s.slice(8, 10)}-${s.slice(5, 7)}-${s.slice(0, 4)}` : s)
const hm = (ts: string | null) => {
  if (!ts) return ''
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? ts : `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const d = (await res.json().catch(() => ({}))) as T & { ok?: boolean; error?: string }
  if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
  return d
}

/** A difference: green when the drawer holds what it should, red otherwise. */
function Diff({ value }: { value: number | null }) {
  if (value === null || value === undefined) return <span className="vl-dim">—</span>
  const ok = Math.abs(value) < 0.005
  return (
    <span className={`cr-diff ${ok ? 'cr-ok' : value > 0 ? 'cr-more' : 'cr-less'}`}>
      {ok ? 'Tallies' : `${value > 0 ? '+' : '−'}${money(Math.abs(value))} ${value > 0 ? 'extra' : 'short'}`}
    </span>
  )
}

function BookSection({ s }: { s: Section }) {
  const [open, setOpen] = useState(true)
  return (
    <div className={`cr-section cr-flow-${s.flow}`}>
      <button type="button" className="cr-section-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="cr-caret">{open ? '▾' : '▸'}</span>
        <span className="cr-section-title">{s.title}</span>
        <span className="cr-section-count">{s.lines.length ? `${s.lines.length} ${s.lines.length === 1 ? 'line' : 'lines'}` : 'none today'}</span>
        <span className={`cr-section-total num ${s.flow === 'in' ? 'cr-in' : 'cr-out'}`}>
          {s.lines.length ? `${s.flow === 'in' ? '+' : '−'} ${money(s.total)}` : ''}
        </span>
      </button>
      {open && s.lines.length ? (
        <table className="sat-table cr-table">
          <thead>
            <tr>
              <th className="cr-w-time">Time</th>
              <th className="cr-w-ref">Bill / Ref</th>
              <th>Party</th>
              <th>Detail</th>
              <th className="num">Amount</th>
            </tr>
          </thead>
          <tbody>
            {s.lines.map((l) => (
              <tr key={`${s.key}-${l.id}`}>
                <td>{l.time}</td>
                <td>{l.ref}</td>
                <td>{l.party}</td>
                <td className="cr-detail">{l.detail}</td>
                <td className="num">{money(l.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  )
}

export function CashRegisterPage({ active, refreshNonce = 0 }: { active: boolean; refreshNonce?: number }) {
  const [view, setView] = useState<'day' | 'month'>('day')
  const [day, setDay] = useState(todayIso())
  const [month, setMonth] = useState(thisMonth())
  const [rep, setRep] = useState<DayReport | null>(null)
  const [range, setRange] = useState<RangeReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [nonce, setNonce] = useState(0)

  // Opening cash box
  const [openingText, setOpeningText] = useState('')
  const [editOpening, setEditOpening] = useState(false)
  const openingRef = useRef<HTMLInputElement>(null)

  // The expense form (also edits a line)
  const [editId, setEditId] = useState<number | null>(null)
  const [kind, setKind] = useState<'out' | 'in'>('out')
  const [at, setAt] = useState(nowHm())
  const [desc, setDesc] = useState('')
  const [cat, setCat] = useState('')
  const [amt, setAmt] = useState('')
  const descRef = useRef<HTMLInputElement>(null)

  // Close day
  const [countedText, setCountedText] = useState('')
  const [note, setNote] = useState('')

  const bounds = useMemo(() => monthBounds(month), [month])

  const load = useCallback(async () => {
    setBusy(true)
    setErr('')
    try {
      if (view === 'day') {
        const res = await fetch(`${getApiBase()}/api/cash/day?date=${day}`)
        const d = (await res.json().catch(() => ({}))) as DayReport
        if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
        setRep(d)
      } else {
        const res = await fetch(`${getApiBase()}/api/cash/range?from=${bounds.from}&to=${bounds.to}`)
        const d = (await res.json().catch(() => ({}))) as RangeReport
        if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
        setRange(d)
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [view, day, bounds.from, bounds.to])

  useEffect(() => {
    if (!active) return
    void load()
  }, [active, load, nonce, refreshNonce])

  // The counted box follows the day loaded; the opening box opens by itself when nobody typed it.
  useEffect(() => {
    if (!rep) return
    setCountedText(rep.counted !== null ? String(rep.counted) : '')
    setNote(rep.note || '')
    setEditOpening(!rep.opening.set)
    setOpeningText(rep.opening.set ? String(rep.opening.value) : '')
  }, [rep])

  useEffect(() => {
    if (editOpening && active && view === 'day') openingRef.current?.focus()
  }, [editOpening, active, view])

  function apply(d: DayReport) {
    setRep(d)
    setMsg('')
  }

  async function saveOpening() {
    const v = Number(openingText)
    if (!Number.isFinite(v) || v < 0) {
      setErr('Type the cash in the drawer (0 is fine)')
      return
    }
    setBusy(true)
    setErr('')
    try {
      apply(await post<DayReport>('/api/cash/opening', { date: day, opening_cash: v }))
      setEditOpening(false)
      descRef.current?.focus()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function clearForm() {
    setEditId(null)
    setKind('out')
    setAt(nowHm())
    setDesc('')
    setCat('')
    setAmt('')
  }

  async function saveLine() {
    const v = Number(amt)
    if (!Number.isFinite(v) || v <= 0) {
      setErr('Enter the amount')
      return
    }
    if (!desc.trim() && !cat.trim()) {
      setErr('Say what the cash was for (e.g. Tea)')
      descRef.current?.focus()
      return
    }
    setBusy(true)
    setErr('')
    try {
      apply(await post<DayReport>('/api/cash/expense', {
        date: day, id: editId || 0, kind, at, description: desc, category: cat, amount: v,
      }))
      clearForm()
      descRef.current?.focus()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function startEdit(m: Manual) {
    setEditId(m.id)
    setKind(m.kind)
    setAt(m.time || nowHm())
    setDesc(m.description)
    setCat(m.category)
    setAmt(String(m.amount))
    descRef.current?.focus()
  }

  async function removeLine(m: Manual) {
    if (!window.confirm(`Remove "${m.description}" ${money(m.amount)}?`)) return
    setBusy(true)
    try {
      apply(await post<DayReport>('/api/cash/expense/delete', { id: m.id, date: day }))
      if (editId === m.id) clearForm()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function closeDay(reopen = false) {
    const v = Number(countedText)
    if (!reopen && (!Number.isFinite(v) || countedText.trim() === '' || v < 0)) {
      setErr('Count the cash in the drawer and type it')
      return
    }
    setBusy(true)
    setErr('')
    try {
      apply(await post<DayReport>('/api/cash/close', reopen ? { date: day, reopen: true } : { date: day, counted_cash: v, note }))
      setMsg(reopen ? 'Day opened again.' : 'Day closed. The count is kept.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function exportAs(format: 'xlsx' | 'pdf' | 'print') {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const body: Record<string, unknown> = view === 'day' ? { date: day } : { from: bounds.from, to: bounds.to }
      if (format === 'print') body.print_to = 'printer'
      else body.format = format
      if (view === 'month') body.page_layout = 'landscape'
      const d = await post<{ message?: string; filename?: string; mime?: string; content_base64?: string }>('/api/cash/export', body)
      if (d.content_base64 && d.filename) {
        const saved = await saveFileAndShow(d.filename, d.mime || 'application/octet-stream', d.content_base64)
        setMsg(saved ? `Saved ${saved.file_name || d.filename} in ${saved.folder || ''}` : `Saved ${d.filename}`)
      } else {
        setMsg(d.message || 'Sent to the printer')
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const onEnter = (fn: () => void) => (e: KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      fn()
    } else if (e.key === 'Escape' && editId !== null) {
      clearForm()
    }
  }

  const closed = Boolean(rep?.closed_at)
  const liveDiff = useMemo(() => {
    if (!rep) return null
    const v = Number(countedText)
    if (countedText.trim() === '' || !Number.isFinite(v)) return null
    return Math.round((v - rep.totals.expected) * 100) / 100
  }, [rep, countedText])
  const manual = useMemo(
    () => (rep ? [...rep.expenses, ...rep.cash_added].sort((a, b) => (a.time + a.id).localeCompare(b.time + b.id)) : []),
    [rep],
  )

  return (
    <div className="cr-page">
      <div className="cr-top">
        <div className="cr-view">
          <button type="button" className={`cr-tab${view === 'day' ? ' active' : ''}`} onClick={() => setView('day')}>Day</button>
          <button type="button" className={`cr-tab${view === 'month' ? ' active' : ''}`} onClick={() => setView('month')}>Month</button>
        </div>
        {view === 'day' ? (
          <>
            <button type="button" className="btn btn-neutral btn-sm" title="Previous day" onClick={() => setDay((d) => shiftDay(d, -1))}>◂</button>
            <input className="settings-input" type="date" value={day} aria-label="Date" onChange={(e) => e.target.value && setDay(e.target.value)} />
            <button type="button" className="btn btn-neutral btn-sm" title="Next day" disabled={day >= todayIso()} onClick={() => setDay((d) => shiftDay(d, 1))}>▸</button>
            {day !== todayIso() ? (
              <button type="button" className="cr-link" onClick={() => setDay(todayIso())}>Today</button>
            ) : null}
            {rep ? (
              <span className="vl-dim">
                {rep.date_dmy}
                {closed ? ` · closed ${hm(rep.closed_at)}` : ' · open'}
              </span>
            ) : null}
          </>
        ) : (
          <>
            <input className="settings-input" type="month" value={month} aria-label="Month" onChange={(e) => e.target.value && setMonth(e.target.value)} />
            <span className="vl-dim">{dmy(bounds.from)} – {dmy(bounds.to)}</span>
          </>
        )}
        <button type="button" className="btn btn-neutral btn-sm" disabled={busy} onClick={() => setNonce((n) => n + 1)}>
          {busy ? 'Loading…' : 'Refresh'}
        </button>
        <div className="cr-actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void exportAs('xlsx')}>Excel</button>
          <button type="button" className="btn btn-neutral" disabled={busy} onClick={() => void exportAs('pdf')}>PDF</button>
          <button type="button" className="btn btn-neutral" disabled={busy} onClick={() => void exportAs('print')}>Print</button>
        </div>
      </div>
      {err ? <div className="vb-warn cr-msg">{err}</div> : null}
      {msg ? <div className="settings-hint cr-msg">{msg}</div> : null}

      {view === 'day' && rep ? (
        <div className="cr-main">
          <div className="cr-left">
            <section className="cr-card">
              <div className="cr-card-head">
                <h3>Expenses &amp; cash added</h3>
                <span className="vl-dim">Type a line and press Enter. Esc cancels an edit.</span>
              </div>
              <div className="cr-form" onKeyDown={onEnter(() => void saveLine())}>
                <select className="settings-input cr-kind" value={kind} aria-label="Kind" onChange={(e) => setKind(e.target.value as 'out' | 'in')}>
                  <option value="out">Expense (cash out)</option>
                  <option value="in">Cash added (cash in)</option>
                </select>
                <input className="settings-input cr-time" type="time" value={at} aria-label="Time" onChange={(e) => setAt(e.target.value)} />
                <input ref={descRef} className="settings-input cr-desc" placeholder={kind === 'out' ? 'What for? e.g. Tea, Dolo from Shree Medical' : 'e.g. Owner added cash'}
                  value={desc} aria-label="Description" onChange={(e) => setDesc(e.target.value)} />
                <input className="settings-input cr-cat" list="cr-categories" placeholder="Category" value={cat} aria-label="Category"
                  onChange={(e) => setCat(e.target.value)} />
                <datalist id="cr-categories">
                  {(rep.categories || []).map((c) => <option key={c} value={c} />)}
                </datalist>
                <input className="settings-input cr-amt" type="number" min="0" step="0.01" placeholder="Amount" value={amt} aria-label="Amount"
                  onChange={(e) => setAmt(e.target.value)} />
                <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void saveLine()}>
                  {editId !== null ? 'Save' : 'Add'}
                </button>
                {editId !== null ? (
                  <button type="button" className="btn btn-neutral" onClick={clearForm}>Cancel</button>
                ) : null}
              </div>
              {manual.length ? (
                <table className="sat-table cr-table">
                  <thead>
                    <tr>
                      <th className="cr-w-time">Time</th>
                      <th>Description</th>
                      <th>Category</th>
                      <th className="num">Cash in</th>
                      <th className="num">Cash out</th>
                      <th className="cr-w-act" />
                    </tr>
                  </thead>
                  <tbody>
                    {manual.map((m) => (
                      <tr key={m.id} className={editId === m.id ? 'cr-editing' : ''}>
                        <td>{m.time}</td>
                        <td>{m.description}</td>
                        <td>{m.category}</td>
                        <td className="num cr-in">{m.kind === 'in' ? money(m.amount) : ''}</td>
                        <td className="num cr-out">{m.kind === 'out' ? money(m.amount) : ''}</td>
                        <td className="cr-act">
                          <button type="button" className="cr-link" onClick={() => startEdit(m)}>Edit</button>
                          <button type="button" className="cr-link cr-link-danger" onClick={() => void removeLine(m)}>Delete</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th colSpan={3}>Total</th>
                      <th className="num cr-in">{rep.totals.cash_added ? money(rep.totals.cash_added) : ''}</th>
                      <th className="num cr-out">{rep.totals.expenses ? money(rep.totals.expenses) : ''}</th>
                      <th />
                    </tr>
                  </tfoot>
                </table>
              ) : (
                <div className="cr-empty">No expenses typed for this day yet.</div>
              )}
            </section>

            <section className="cr-card">
              <div className="cr-card-head">
                <h3>From the books</h3>
                <span className="vl-dim">The same figures as the bills; a deleted or edited bill changes them here too.</span>
              </div>
              {rep.sections.map((s) => <BookSection key={s.key} s={s} />)}
            </section>
          </div>

          <aside className="cr-right">
            <section className="cr-card">
              <div className="cr-card-head"><h3>Opening cash</h3></div>
              {editOpening ? (
                <div className="cr-open-form" onKeyDown={onEnter(() => void saveOpening())}>
                  <p className="cr-ask">{rep.opening.set ? 'Change the opening cash:' : 'Shop opened — how much cash is in the drawer?'}</p>
                  <div className="cr-row">
                    <input ref={openingRef} className="settings-input cr-amt" type="number" min="0" step="0.01" placeholder="0.00"
                      value={openingText} aria-label="Opening cash" onChange={(e) => setOpeningText(e.target.value)} />
                    <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void saveOpening()}>Save</button>
                    {rep.opening.set ? (
                      <button type="button" className="btn btn-neutral" onClick={() => setEditOpening(false)}>Cancel</button>
                    ) : null}
                  </div>
                </div>
              ) : (
                <div className="cr-row cr-open-shown">
                  <span className="cr-big num">{money(rep.opening.value)}</span>
                  <button type="button" className="cr-link" onClick={() => setEditOpening(true)}>Edit</button>
                </div>
              )}
            </section>

            <section className="cr-card cr-summary">
              <div className="cr-card-head"><h3>Cash in the drawer</h3></div>
              <table className="cr-sum">
                <tbody>
                  <tr><td>Opening cash</td><td className="num">{money(rep.opening.value)}</td></tr>
                  <tr><td>+ Cash in</td><td className="num cr-in">{money(rep.totals.cash_in)}</td></tr>
                  <tr><td>− Cash out</td><td className="num cr-out">{money(rep.totals.cash_out)}</td></tr>
                  <tr><td>− Expenses</td><td className="num cr-out">{money(rep.totals.expenses)}</td></tr>
                  <tr className="cr-expected"><td>Expected in drawer</td><td className="num">{money(rep.totals.expected)}</td></tr>
                </tbody>
              </table>
              <div className="cr-count" onKeyDown={onEnter(() => { if (!closed) void closeDay() })}>
                <label className="cr-label" htmlFor="cr-counted">Counted cash</label>
                <input id="cr-counted" className="settings-input cr-amt" type="number" min="0" step="0.01" placeholder="Count the drawer"
                  value={countedText} disabled={closed} onChange={(e) => setCountedText(e.target.value)} />
                <div className="cr-diff-row">
                  <span>Difference</span>
                  <Diff value={closed ? rep.difference : liveDiff} />
                </div>
                <input className="settings-input cr-note" placeholder="Note (optional) e.g. 50 given as change to neighbour" value={note}
                  disabled={closed} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
                {closed ? (
                  <div className="cr-row">
                    <span className="vl-dim">Closed at {hm(rep.closed_at)}</span>
                    <button type="button" className="cr-link" disabled={busy} onClick={() => void closeDay(true)}>Open again</button>
                  </div>
                ) : (
                  <button type="button" className="btn btn-primary cr-close-btn" disabled={busy} onClick={() => void closeDay()}>
                    Close day
                  </button>
                )}
              </div>
            </section>
          </aside>
        </div>
      ) : null}

      {view === 'month' && range ? (
        <div className="cr-main cr-month">
          <div className="cr-left">
            <section className="cr-card">
              <div className="cr-card-head">
                <h3>Day by day</h3>
                <span className="vl-dim">{range.days.length} days with cash movement · {range.totals.days_closed} closed</span>
              </div>
              {range.days.length ? (
                <table className="sat-table cr-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th className="num">Opening</th>
                      <th className="num">Cash in</th>
                      <th className="num">Cash out</th>
                      <th className="num">Expenses</th>
                      <th className="num">Expected</th>
                      <th className="num">Counted</th>
                      <th>Difference</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {range.days.map((d) => (
                      <tr key={d.date}>
                        <td>
                          <button type="button" className="cr-link" onClick={() => { setDay(d.date); setView('day') }}>{d.date_dmy}</button>
                        </td>
                        <td className="num">{money(d.opening)}</td>
                        <td className="num cr-in">{money(d.cash_in)}</td>
                        <td className="num cr-out">{money(d.cash_out)}</td>
                        <td className="num cr-out">{money(d.expenses)}</td>
                        <td className="num">{money(d.expected)}</td>
                        <td className="num">{d.counted !== null ? money(d.counted) : <span className="vl-dim">not counted</span>}</td>
                        <td><Diff value={d.difference} /></td>
                        <td className="vl-dim">{d.closed ? 'Closed' : 'Open'}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th>Total</th>
                      <th className="num">{money(range.totals.opening)}</th>
                      <th className="num cr-in">{money(range.totals.cash_in)}</th>
                      <th className="num cr-out">{money(range.totals.cash_out)}</th>
                      <th className="num cr-out">{money(range.totals.expenses)}</th>
                      <th />
                      <th />
                      <th><Diff value={range.totals.difference} /></th>
                      <th />
                    </tr>
                  </tfoot>
                </table>
              ) : (
                <div className="cr-empty">No cash movement in this month.</div>
              )}
            </section>
          </div>
          <aside className="cr-right">
            <section className="cr-card">
              <div className="cr-card-head"><h3>Expenses by category</h3></div>
              {range.categories.length ? (
                <table className="sat-table cr-table">
                  <tbody>
                    {range.categories.map((c) => (
                      <tr key={c.category}>
                        <td>{c.category}</td>
                        <td className="num cr-out">{money(c.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th>Total</th>
                      <th className="num cr-out">{money(range.totals.expenses)}</th>
                    </tr>
                  </tfoot>
                </table>
              ) : (
                <div className="cr-empty">No expenses this month.</div>
              )}
            </section>
          </aside>
        </div>
      ) : null}

      {!rep && !range && busy ? <div className="cr-empty">Loading…</div> : null}
    </div>
  )
}
