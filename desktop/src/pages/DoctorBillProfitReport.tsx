/**
 * Doctor bill-wise profit (owner, 30 Oct 2026) — the Doctor Margin Report dialog's "Bill-wise
 * profit" mode (Sales History → Export), like the other software's "Selected doctors: bill-wise
 * profit report": one row per bill of the doctors ticked (one, several, or all), for today, this
 * week, this month or any dates — Date, Bill No, Customer, Short address, Sale / −SR amt, Cost
 * amt, Profit amt, Profit %, the totals, and the totals per doctor.
 *
 * The figures are the engine's (core/doctor_bill_profit.py); this only shows them and asks for
 * the files with the filters on screen.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { getApiBase } from '../api'
import { todayLocal } from '../localDate'
import { saveFileAndShow, showSavedFile } from '../savedFiles'
import { TypePicker } from './DoctorMarginDialog'
import '../styles/doctor-margin.css'

type BillRow = {
  date: string
  bill_no: string
  patient: string
  doctor: string
  address: string
  sale: number
  cost: number
  profit: number
  pct: number
  flags: string[]
}

type DoctorRow = { doctor: string; bills: number; sale: number; cost: number; profit: number; pct: number }

type BillReport = {
  ok: boolean
  rows: BillRow[]
  doctors: DoctorRow[]
  totals: { bills: number; sale: number; cost: number; profit: number; pct: number }
  flag_counts: Record<string, number>
}

const PREVIEW_ROWS = 400

async function call<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = (await res.json().catch(() => ({}))) as T & { error?: string }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

function money(v: number) {
  return (Number(v) || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function dmy(iso: string) {
  const s = String(iso || '').slice(0, 10)
  return s.length === 10 ? `${s.slice(8, 10)}-${s.slice(5, 7)}-${s.slice(2, 4)}` : s
}

function weekStart(today: string) {
  const d = new Date(`${today}T00:00:00`)
  const back = (d.getDay() + 6) % 7 // Monday
  d.setDate(d.getDate() - back)
  return todayLocal(d)
}

export function DoctorBillProfitReport({
  modeSwitch,
  doctors,
  online,
  from,
  to,
  onDates,
  onClose,
  onBack,
}: {
  modeSwitch: ReactNode
  doctors: string[]
  online: boolean
  from: string
  to: string
  onDates: (from: string, to: string) => void
  onClose: () => void
  onBack?: () => void
}) {
  const [picked, setPicked] = useState<string[]>([])
  const [report, setReport] = useState<BillReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [printTo, setPrintTo] = useState<'' | 'dot_matrix' | 'printer'>('')
  const seq = useRef(0)

  // Starts with every doctor ticked ("All N doctors"); Clear all, then tick the ones wanted.
  const doctorKey = doctors.join('|')
  useEffect(() => {
    setPicked(doctors)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doctorKey])

  // None ticked or all ticked = every doctor (a bill without a doctor too).
  const chosen = picked.length === doctors.length ? [] : picked
  const problem = !from || !to ? 'Choose the From and To dates.' : from > to ? 'From date is after To date.' : ''
  const filters = { mode: 'bills', from, to, doctors: chosen }
  const key = JSON.stringify(filters)

  useEffect(() => {
    if (online) return
    if (problem) {
      seq.current += 1
      setReport(null)
      return
    }
    const mine = ++seq.current
    setLoading(true)
    const t = window.setTimeout(() => {
      void call<BillReport>('/api/reports/doctor-margin', filters)
        .then((r) => {
          if (mine === seq.current) {
            setReport(r)
            setError('')
          }
        })
        .catch((e) => mine === seq.current && setError(e instanceof Error ? e.message : String(e)))
        .finally(() => mine === seq.current && setLoading(false))
    }, 300)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, online, problem])

  const quick = (which: 'today' | 'week' | 'month') => {
    const t = todayLocal()
    onDates(which === 'today' ? t : which === 'week' ? weekStart(t) : `${t.slice(0, 8)}01`, t)
  }

  const exportAs = async (format: 'xlsx' | 'csv' | 'pdf' | 'print') => {
    if (problem) return setError(problem)
    setBusy(format)
    setError('')
    setNote('')
    try {
      const out = await call<{ ok: boolean; filename?: string; mime?: string; content_base64?: string; path?: string; message?: string }>(
        '/api/reports/doctor-margin/export',
        { ...filters, format, print_to: printTo },
      )
      if (format === 'print') {
        setNote(out.message || 'Sent to the printer.')
        if (out.path) showSavedFile({ path: out.path, message: 'A copy of the printed report:' })
        return
      }
      if (out.content_base64 && out.filename) {
        await saveFileAndShow(out.filename, out.mime || 'application/octet-stream', out.content_base64)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  const t = report?.totals
  const shown = (report?.rows || []).slice(0, PREVIEW_ROWS)
  const flags = report?.flag_counts || {}
  const off = Boolean(busy) || Boolean(problem) || !report || !report.rows.length || online

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose} onKeyDown={(e) => e.key === 'Escape' && onClose()}>
      <div className="modal-card dm-card" role="dialog" aria-modal="true" aria-label="Doctor Margin Report" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Doctor Margin Report</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body dm-body">
          {online ? (
            <p className="page-error">
              This report reads this PC's own books. An Online store's bills are on the server: use Sales History → Export on the web.
            </p>
          ) : null}
          <div className="dm-filters">
            <div className="dm-field">
              <span className="dm-label">From</span>
              <input type="date" className="settings-input" value={from} onChange={(e) => onDates(e.target.value, to)} />
            </div>
            <div className="dm-field">
              <span className="dm-label">To</span>
              <input type="date" className="settings-input" value={to} onChange={(e) => onDates(from, e.target.value)} />
            </div>
            <div className="dm-field">
              <span className="dm-label">Quick pick</span>
              <div className="dm-quick">
                <button type="button" className="btn btn-neutral btn-sm" onClick={() => quick('today')}>
                  Today
                </button>
                <button type="button" className="btn btn-neutral btn-sm" onClick={() => quick('week')}>
                  This week
                </button>
                <button type="button" className="btn btn-neutral btn-sm" onClick={() => quick('month')}>
                  This month
                </button>
              </div>
            </div>
            <div className="dm-field dm-field-wide">
              <span className="dm-label">Doctors (one or more)</span>
              <TypePicker types={doctors} picked={picked} onChange={setPicked} noun="doctor" />
            </div>
            {modeSwitch}
          </div>

          {problem ? <p className="page-error">{problem}</p> : null}
          {error && !problem ? <p className="page-error">{error}</p> : null}
          {note ? <p className="dm-ok">{note}</p> : null}

          <div className="dm-preview" aria-busy={loading}>
            <div className="dm-preview-head">
              <h3>Selected doctors: bill-wise profit report</h3>
              <span className="muted">
                {loading
                  ? 'Updating…'
                  : report
                    ? `${dmy(from)} to ${dmy(to)} · ${chosen.length ? chosen.join(', ') : 'All doctors'} · ${report.rows.length} bills`
                    : ''}
              </span>
            </div>
            {report && t ? (
              <>
                <div className="dm-cards">
                  <div className="dm-stat">
                    <span>Sale / −SR</span>
                    <b>₹{money(t.sale)}</b>
                  </div>
                  <div className="dm-stat">
                    <span>Cost</span>
                    <b>₹{money(t.cost)}</b>
                  </div>
                  <div className="dm-stat dm-stat-main">
                    <span>Profit</span>
                    <b>₹{money(t.profit)}</b>
                  </div>
                  <div className="dm-stat">
                    <span>Profit %</span>
                    <b>{money(t.pct)}%</b>
                  </div>
                </div>
                {flags.estimated || flags.unavailable ? (
                  <p className="muted">
                    {flags.estimated ? `${flags.estimated} line(s) had no purchase of that batch: the cost recorded on the bill is used. ` : ''}
                    {flags.unavailable ? `${flags.unavailable} line(s) have no cost at all (taken as 0, so their profit is too high).` : ''}
                  </p>
                ) : null}
                {report.rows.length ? (
                  <div className="dm-table-wrap">
                    <table className="sat-table dm-table">
                      <thead>
                        <tr>
                          <th>Date</th>
                          <th>Bill no</th>
                          <th>Customer</th>
                          <th>Short address</th>
                          {chosen.length === 1 ? null : <th>Doctor</th>}
                          <th className="num">Sale / −SR amt</th>
                          <th className="num">Cost amt</th>
                          <th className="num">Profit amt</th>
                          <th className="num">Profit %</th>
                        </tr>
                      </thead>
                      <tbody>
                        {shown.map((r) => (
                          <tr key={`${r.bill_no}|${r.date}`} className={r.profit < 0 ? 'dm-row-loss' : ''}>
                            <td>{dmy(r.date)}</td>
                            <td>{r.bill_no}</td>
                            <td className="dm-wrap">{r.patient}</td>
                            <td className="dm-wrap">{r.address}</td>
                            {chosen.length === 1 ? null : <td className="dm-wrap">{r.doctor}</td>}
                            <td className="num">{money(r.sale)}</td>
                            <td className="num">{money(r.cost)}</td>
                            <td className="num">{money(r.profit)}</td>
                            <td className="num">{money(r.pct)}</td>
                          </tr>
                        ))}
                      </tbody>
                      <tfoot>
                        <tr className="dm-total">
                          <td colSpan={chosen.length === 1 ? 4 : 5}>Totals</td>
                          <td className="num">{money(t.sale)}</td>
                          <td className="num">{money(t.cost)}</td>
                          <td className="num">{money(t.profit)}</td>
                          <td className="num">{money(t.pct)}</td>
                        </tr>
                      </tfoot>
                    </table>
                    {report.rows.length > shown.length ? (
                      <p className="muted dm-more">
                        Showing {shown.length} of {report.rows.length}. Excel / CSV / PDF / Print carry all of them.
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className="dm-empty">No bills of these doctors in these dates.</p>
                )}
                {report.doctors.length > 1 ? (
                  <>
                    <h3 className="dm-sub">Totals by doctor</h3>
                    <table className="sat-table dm-table dm-doctors">
                      <thead>
                        <tr>
                          <th>Doctor</th>
                          <th className="num">Bills</th>
                          <th className="num">Sale / −SR amt</th>
                          <th className="num">Cost amt</th>
                          <th className="num">Profit amt</th>
                          <th className="num">Profit %</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.doctors.map((d) => (
                          <tr key={d.doctor}>
                            <td>{d.doctor}</td>
                            <td className="num">{d.bills}</td>
                            <td className="num">{money(d.sale)}</td>
                            <td className="num">{money(d.cost)}</td>
                            <td className="num">{money(d.profit)}</td>
                            <td className="num">{money(d.pct)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                ) : null}
              </>
            ) : !loading && !problem && !online ? (
              <p className="dm-empty">Loading…</p>
            ) : null}
          </div>
        </div>
        <div className="modal-foot dm-foot">
          {onBack ? (
            <button type="button" className="btn btn-neutral" onClick={onBack}>
              Back
            </button>
          ) : null}
          <button type="button" className="btn btn-neutral" onClick={onClose}>
            Cancel
          </button>
          <span className="dm-foot-gap" />
          <label className="dm-print-to">
            <span className="dm-label">Printer</span>
            <select className="settings-input" value={printTo} onChange={(e) => setPrintTo(e.target.value as '' | 'dot_matrix' | 'printer')}>
              <option value="">As set in Settings</option>
              <option value="printer">Normal printer</option>
              <option value="dot_matrix">Dot matrix</option>
            </select>
          </label>
          <button type="button" className="btn btn-primary" disabled={off} onClick={() => void exportAs('xlsx')}>
            {busy === 'xlsx' ? 'Saving…' : 'Excel'}
          </button>
          <button type="button" className="btn btn-neutral" disabled={off} onClick={() => void exportAs('csv')}>
            {busy === 'csv' ? 'Saving…' : 'CSV'}
          </button>
          <button type="button" className="btn btn-neutral" disabled={off} onClick={() => void exportAs('pdf')}>
            {busy === 'pdf' ? 'Saving…' : 'PDF'}
          </button>
          <button type="button" className="btn btn-neutral" disabled={off} onClick={() => void exportAs('print')}>
            {busy === 'print' ? 'Printing…' : 'Print'}
          </button>
        </div>
      </div>
    </div>
  )
}
