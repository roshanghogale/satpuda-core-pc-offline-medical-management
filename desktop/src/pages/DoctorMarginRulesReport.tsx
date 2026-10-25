/**
 * Doctor Margin Report by the margin rules (owner, 22 + 23 Oct 2026) — the default mode of the
 * Doctor Margin Report dialog (Sales History → Export).
 *
 * Per doctor, two parts: On profit — the owner's Excel, product / quantity / MRP / rate / total
 * value (total value = (MRP − rate) × quantity) and a TOTAL, only the products in a group (or
 * customer-wise product-batch-wise with sub-totals); On total sales — the other software's
 * "Customer-Wise Product-Batch Wise Sales": a heading per product, one row per bill line, a Sub
 * Total, the total sale value and the commission (X% of it). Then the doctor's margin = both.
 * The figures are the doctor report's saved lines (core/doctor_margin_store.py), written when
 * each bill was saved; Recalculate works a period out again with today's rules. The rules:
 * DoctorMarginRulesEditor.
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { getApiBase } from '../api'
import { todayLocal } from '../localDate'
import { saveFileAndShow, showSavedFile } from '../savedFiles'
import { TypePicker } from './DoctorMarginDialog'
import { DoctorMarginRulesEditor } from './DoctorMarginRulesEditor'
import '../styles/doctor-margin.css'
import '../styles/doctor-margin-rules.css'
import { SummaryCustomize } from '../components/SummaryCustomize'
import { usePageSummary } from '../pageSummaries'

type Flag = 'estimated' | 'no_rate' | 'rate_over_mrp'
type RateBasis = '' | 'purchase' | 'purchase_free'
type View = 'doctor' | 'detail'

export type MarginLine = {
  sale_id: number
  bill_no: string
  date: string
  patient: string
  doctor: string
  name: string
  type: string
  batch: string
  qty: number
  qty_returned: number
  mrp: number
  rate: number
  rate_basis: string
  kind: string
  margin_unit: number
  value: number
  rule: string
  flags: Flag[]
}

type SummaryLine = Pick<
  MarginLine,
  'doctor' | 'name' | 'type' | 'batch' | 'mrp' | 'rate' | 'rate_basis' | 'kind' | 'margin_unit' | 'qty' | 'value' | 'rule' | 'flags'
>

/** A total-sales line: the bill line's own rate, discount and value (net of returns). */
type SalesLine = {
  sale_id: number
  bill_no: string
  date: string
  patient: string
  doctor: string
  name: string
  pack: string
  type: string
  batch: string
  qty: number
  qty_returned: number
  sale_rate: number
  disc: number
  value: number
  kind_value: number
}

type SalesDoctor = {
  doctor: string
  qty: number
  value: number
  lines: number
  types: string[]
  by_pct: { pct: number; value: number; commission: number }[]
  commission: number
  products: { name: string; pack: string; type: string; qty: number; value: number; lines: SalesLine[] }[]
}

type RulesReport = {
  ok: boolean
  has_rules: boolean
  rate_basis_text: string
  rows: MarginLine[]
  summary: SummaryLine[]
  doctors: { doctor: string; qty: number; value: number; lines: number }[]
  products: { name: string; type: string; qty: number; value: number; lines: MarginLine[] }[]
  totals: { qty: number; value: number; lines: number }
  sales: {
    rows: SalesLine[]
    doctors: SalesDoctor[]
    totals: { qty: number; value: number; lines: number; commission: number }
  }
  grand: {
    doctors: { doctor: string; profit: number; sales_value: number; commission: number; total: number }[]
    totals: { profit: number; sales_value: number; commission: number; total: number }
  }
  flag_counts: Record<Flag, number>
  sync: { bills?: number; worked_out?: number; recalculated?: boolean; lines_written?: number }
}

function pctText(p: number) {
  return `${Number((Number(p) || 0).toFixed(4))}%`
}

/** The on-profit detail layout of one doctor: per product, its lines and a sub-total. */
function profitProducts(rows: MarginLine[]) {
  const out = new Map<string, { name: string; qty: number; value: number; lines: MarginLine[] }>()
  for (const r of [...rows].sort((a, b) =>
    a.name === b.name ? (a.batch === b.batch ? (a.date === b.date ? a.bill_no.localeCompare(b.bill_no) : a.date.localeCompare(b.date)) : a.batch.localeCompare(b.batch)) : a.name.localeCompare(b.name),
  )) {
    let g = out.get(r.name)
    if (!g) {
      g = { name: r.name, qty: 0, value: 0, lines: [] }
      out.set(r.name, g)
    }
    g.lines.push(r)
    g.qty = Math.round((g.qty + r.qty) * 1000) / 1000
    g.value = Math.round((g.value + r.value) * 100) / 100
  }
  return [...out.values()]
}

const FLAG_TEXT: Record<Flag, string> = {
  estimated: 'Rate not from a purchase',
  no_rate: 'No purchase rate',
  rate_over_mrp: 'Rate above MRP',
}
const KIND_TEXT: Record<string, string> = {
  mrp_rate: 'MRP − rate',
  pct_mrp: '% of MRP',
  fixed: 'Fixed per unit',
}
const PREVIEW_ROWS = 400

export async function dmCall<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = (await res.json().catch(() => ({}))) as T & { error?: string }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

export function money(v: number) {
  return (Number(v) || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function qtyText(v: number) {
  return (Number(v) || 0).toLocaleString('en-IN', { maximumFractionDigits: 3 })
}

function dmy(iso: string, sep = '/') {
  const s = String(iso || '').slice(0, 10)
  return s.length === 10 ? `${s.slice(8, 10)}${sep}${s.slice(5, 7)}${sep}${s.slice(0, 4)}` : s
}

function Flags({ flags }: { flags: Flag[] }) {
  if (!flags?.length) return null
  return (
    <>
      {flags.map((f) => (
        <span key={f} className={`dm-flag dmr-flag-${f}`} title={FLAG_TEXT[f]}>
          {FLAG_TEXT[f] || f}
        </span>
      ))}
    </>
  )
}

type Props = {
  modeSwitch: ReactNode
  doctors: string[]
  online: boolean
  from: string
  to: string
  onDates: (from: string, to: string) => void
  onClose: () => void
  onBack?: () => void
}

export function DoctorMarginRulesReport({ modeSwitch, doctors, online, from, to, onDates, onClose, onBack }: Props) {
  const dmrSummary = usePageSummary('doctor_margin_rules')
  const [picked, setPicked] = useState<string[]>(doctors)
  const [rateBasis, setRateBasis] = useState<RateBasis>('')
  const [view, setView] = useState<View>('doctor')
  const [report, setReport] = useState<RulesReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [printTo, setPrintTo] = useState<'' | 'dot_matrix' | 'printer'>('')
  const [rulesOpen, setRulesOpen] = useState(false)
  const [askRecalc, setAskRecalc] = useState(false)
  const [reload, setReload] = useState(0)
  const seq = useRef(0)

  // The doctors list arrives after the dialog opens: tick them all once it does.
  const known = useRef(0)
  useEffect(() => {
    if (doctors.length && known.current !== doctors.length) {
      known.current = doctors.length
      setPicked(doctors)
    }
  }, [doctors])

  const problem = useMemo(() => {
    if (!from || !to) return 'Choose the From and To dates.'
    if (from > to) return 'From date is after To date.'
    if (doctors.length && !picked.length) return 'Tick at least one doctor.'
    return ''
  }, [from, to, doctors.length, picked.length])

  const filters = useMemo(
    () => ({
      mode: 'rules',
      from,
      to,
      doctors: picked.length === doctors.length ? [] : picked,
      rate_basis: rateBasis,
      view,
    }),
    [from, to, picked, doctors.length, rateBasis, view],
  )
  const dataKey = JSON.stringify({ ...filters, view: undefined, reload })

  useEffect(() => {
    if (online) return
    if (problem) {
      seq.current += 1
      setReport(null)
      setLoading(false)
      return
    }
    const mine = ++seq.current
    setLoading(true)
    const t = window.setTimeout(() => {
      void dmCall<RulesReport>('/api/reports/doctor-margin', filters)
        .then((r) => {
          if (mine !== seq.current) return
          setReport(r)
          setError('')
        })
        .catch((e) => {
          if (mine === seq.current) setError(e instanceof Error ? e.message : String(e))
        })
        .finally(() => {
          if (mine === seq.current) setLoading(false)
        })
    }, 250)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [online, problem, dataKey])

  const quick = (which: 'today' | 'week' | 'month' | 'last10') => {
    const t = todayLocal()
    const d = new Date(`${t}T00:00:00`)
    if (which === 'week') d.setDate(d.getDate() - ((d.getDay() + 6) % 7))
    if (which === 'last10') d.setDate(d.getDate() - 9)
    onDates(which === 'today' ? t : which === 'month' ? `${t.slice(0, 8)}01` : todayLocal(d), t)
  }

  const recalc = async () => {
    setAskRecalc(false)
    setBusy('recalc')
    setError('')
    setNote('')
    try {
      const r = await dmCall<RulesReport>('/api/reports/doctor-margin', { ...filters, recalculate: true })
      seq.current += 1
      setReport(r)
      setNote(`Recalculated ${r.sync?.bills ?? 0} doctor bill(s) of ${dmy(from)} – ${dmy(to)} with today's rules.`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  const exportAs = async (format: 'xlsx' | 'pdf' | 'csv' | 'print') => {
    setBusy(format)
    setError('')
    setNote('')
    try {
      const out = await dmCall<{
        ok: boolean
        filename?: string
        mime?: string
        content_base64?: string
        path?: string
        message?: string
      }>('/api/reports/doctor-margin/export', { ...filters, format, print_to: printTo })
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
  const counts = report?.flag_counts
  const anyLines = Boolean(report && (report.rows.length || report.sales?.rows.length))
  const cannot = Boolean(busy) || Boolean(problem) || !report || !anyLines || online
  let shownLeft = PREVIEW_ROWS

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card dm-card"
        role="dialog"
        aria-modal="true"
        aria-label="Doctor Margin Report"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && !rulesOpen) onClose()
        }}
      >
        <div className="modal-head">
          <h2>Doctor Margin Report</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body dm-body">
          {online ? (
            <p className="page-error">
              This report reads this PC's own books. An Online store's bills are on the server: use Sales History →
              Export on the web.
            </p>
          ) : null}
          <div className="dm-filters">
            {modeSwitch}
            <div className="dm-field">
              <span className="dm-label">From</span>
              <input type="date" className="settings-input" value={from} onChange={(e) => onDates(e.target.value, to)} />
            </div>
            <div className="dm-field">
              <span className="dm-label">To</span>
              <input type="date" className="settings-input" value={to} onChange={(e) => onDates(from, e.target.value)} />
            </div>
            <div className="dm-field dm-field-wide">
              <span className="dm-label">Quick pick</span>
              <div className="dm-quick">
                <button type="button" className="btn btn-neutral btn-sm" onClick={() => quick('today')}>
                  Today
                </button>
                <button type="button" className="btn btn-neutral btn-sm" onClick={() => quick('last10')}>
                  Last 10 days
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
              <span className="dm-label">Doctors</span>
              {doctors.length ? (
                <TypePicker types={doctors} picked={picked} onChange={setPicked} noun="doctor" />
              ) : (
                <span className="muted dm-inline-note">No doctor on any bill yet.</span>
              )}
            </div>
            <div className="dm-field dm-field-wide">
              <span className="dm-label">Rate</span>
              <select className="settings-input" value={rateBasis} onChange={(e) => setRateBasis(e.target.value as RateBasis)}>
                <option value="">As saved with each bill (rules)</option>
                <option value="purchase">Purchase rate</option>
                <option value="purchase_free">Purchase rate with free included</option>
              </select>
            </div>
            <div className="dm-field dm-field-wide">
              <span className="dm-label">On-profit layout</span>
              <div className="dm-seg" role="radiogroup" aria-label="On-profit layout">
                <button type="button" role="radio" aria-checked={view === 'doctor'} className={view === 'doctor' ? 'on' : ''} onClick={() => setView('doctor')}>
                  Doctor-wise (Excel)
                </button>
                <button type="button" role="radio" aria-checked={view === 'detail'} className={view === 'detail' ? 'on' : ''} onClick={() => setView('detail')}>
                  Customer-wise product-batch
                </button>
              </div>
            </div>
            <div className="dm-field dm-field-wide dmr-actions">
              <span className="dm-label">Rules</span>
              <div className="dm-quick">
                <button type="button" className="btn btn-primary btn-sm" onClick={() => setRulesOpen(true)} disabled={online}>
                  Margin rules…
                </button>
                <button
                  type="button"
                  className="btn btn-neutral btn-sm"
                  disabled={Boolean(busy) || Boolean(problem) || online}
                  onClick={() => setAskRecalc(true)}
                  title="Work this period out again with today's rules"
                >
                  {busy === 'recalc' ? 'Recalculating…' : 'Recalculate period'}
                </button>
              </div>
            </div>
          </div>

          {askRecalc ? (
            <div className="dmr-confirm" role="alertdialog" aria-label="Recalculate">
              <span>
                Every doctor bill of {dmy(from)} – {dmy(to)} will be worked out again with today's rules and today's
                purchase rates. The figures saved with those bills are replaced.
              </span>
              <button type="button" className="btn btn-primary btn-sm" onClick={() => void recalc()}>
                Recalculate
              </button>
              <button type="button" className="btn btn-neutral btn-sm" onClick={() => setAskRecalc(false)}>
                Cancel
              </button>
            </div>
          ) : null}
          {problem ? <p className="page-error">{problem}</p> : null}
          {error && !problem ? <p className="page-error">{error}</p> : null}
          {note ? <p className="dm-ok">{note}</p> : null}

          <div className="dm-preview" aria-busy={loading}>
            <div className="dm-preview-head">
              <h3>
                {dmy(from, '-')} to {dmy(to, '-')}
              </h3>
              <span className="muted">
                {loading ? 'Updating…' : report ? `On profit: ${report.rate_basis_text} · on total sales: % of the sale value` : ''}
              </span>
            </div>
            {report && t ? (
              <>
                {!report.has_rules ? (
                  <p className="dmr-hint">
                    No margin rules yet. Press <b>Margin rules…</b> and choose, for each medicine type, which products give
                    the doctor a margin.
                  </p>
                ) : null}
                {/* The shop's choice of figures (Settings → Layout & Lists → Page Summaries);
                    on screen only, the saved file keeps every figure. */}
                <div className={`dm-cards${dmrSummary.show ? '' : ' dm-cards-off'}`}>
                  {dmrSummary.show && dmrSummary.field('doctors') ? (
                  <div className="dm-stat">
                    <span>Doctors</span>
                    <b>{report.grand.doctors.length}</b>
                  </div>
                  ) : null}
                  {dmrSummary.show && dmrSummary.field('on_profit') ? (
                  <div className="dm-stat">
                    <span>On profit</span>
                    <b>₹{money(report.grand.totals.profit)}</b>
                  </div>
                  ) : null}
                  {dmrSummary.show && dmrSummary.field('total_sales') ? (
                  <div className="dm-stat">
                    <span>Total sales</span>
                    <b>₹{money(report.grand.totals.sales_value)}</b>
                  </div>
                  ) : null}
                  {dmrSummary.show && dmrSummary.field('commission') ? (
                  <div className="dm-stat">
                    <span>Commission on sales</span>
                    <b>₹{money(report.grand.totals.commission)}</b>
                  </div>
                  ) : null}
                  {dmrSummary.show && dmrSummary.field('doctors_margin') ? (
                  <div className="dm-stat dm-stat-main">
                    <span>Doctors' margin</span>
                    <b>₹{money(report.grand.totals.total)}</b>
                  </div>
                  ) : null}
                  <SummaryCustomize page="doctor_margin_rules" />
                </div>
                {counts && (counts.estimated || counts.no_rate || counts.rate_over_mrp) ? (
                  <ul className="dm-flag-notes">
                    {counts.estimated ? (
                      <li>
                        <span className="dm-flag dmr-flag-estimated">{FLAG_TEXT.estimated}</span> {counts.estimated} line(s):
                        no purchase bill of that batch; the batch's own purchase rate (or the cost on the bill) is used.
                      </li>
                    ) : null}
                    {counts.no_rate ? (
                      <li>
                        <span className="dm-flag dmr-flag-no_rate">{FLAG_TEXT.no_rate}</span> {counts.no_rate} line(s): no
                        rate known, so MRP − rate gives 0.
                      </li>
                    ) : null}
                    {counts.rate_over_mrp ? (
                      <li>
                        <span className="dm-flag dmr-flag-rate_over_mrp">{FLAG_TEXT.rate_over_mrp}</span>{' '}
                        {counts.rate_over_mrp} line(s): the rate is above MRP, counted as 0.
                      </li>
                    ) : null}
                  </ul>
                ) : null}

                {!anyLines ? (
                  <p className="dm-empty">No doctor margin in this period{picked.length < doctors.length ? ' for these doctors' : ''}.</p>
                ) : (
                  <div className="dmr-doctors">
                    {report.grand.doctors.map((g) => {
                      const pd = report.doctors.find((d) => d.doctor === g.doctor)
                      const sd = report.sales.doctors.find((d) => d.doctor === g.doctor)
                      const mineSummary = report.summary.filter((r) => r.doctor === g.doctor)
                      const mineRows = report.rows.filter((r) => r.doctor === g.doctor)
                      const sumShow = view === 'doctor' ? mineSummary.slice(0, Math.max(0, shownLeft)) : []
                      shownLeft -= sumShow.length
                      return (
                        <section key={g.doctor} className="dmr-doctor">
                          <div className="dmr-doctor-head">
                            <b>{g.doctor}</b>
                            <span className="muted">
                              {pd ? `on profit ₹${money(g.profit)}` : ''}
                              {pd && sd ? ' + ' : ''}
                              {sd ? `commission ₹${money(g.commission)}` : ''}
                            </span>
                            <span className="dmr-doctor-total">₹{money(g.total)}</span>
                          </div>

                          {pd ? (
                            <div className="dmr-part">
                              <div className="dmr-part-head">
                                <b>On profit</b>
                                <span className="muted">
                                  products in the groups · {pd.lines} bill line(s) · qty {qtyText(pd.qty)}
                                </span>
                              </div>
                              {view === 'doctor' ? (
                                <table className="sat-table dm-table dmr-table">
                                  <thead>
                                    <tr>
                                      <th>Product name</th>
                                      <th>Batch</th>
                                      <th className="num">Quantity</th>
                                      <th className="num">MRP</th>
                                      <th className="num">Rate</th>
                                      <th className="num">Margin / unit</th>
                                      <th className="num">Total value</th>
                                      <th>Rule</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {sumShow.map((r, i) => (
                                      <tr key={i}>
                                        <td className="dm-wrap">
                                          {r.name} <Flags flags={r.flags} />
                                        </td>
                                        <td>{r.batch}</td>
                                        <td className="num">{qtyText(r.qty)}</td>
                                        <td className="num">{money(r.mrp)}</td>
                                        <td className="num">{money(r.rate)}</td>
                                        <td className="num">{money(r.margin_unit)}</td>
                                        <td className="num">{money(r.value)}</td>
                                        <td className="dmr-rule" title={KIND_TEXT[r.kind] || r.kind}>
                                          {r.rule}
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                  <tfoot>
                                    <tr className="dm-total">
                                      <td colSpan={2}>TOTAL value</td>
                                      <td className="num">{qtyText(pd.qty)}</td>
                                      <td colSpan={3} />
                                      <td className="num">{money(pd.value)}</td>
                                      <td />
                                    </tr>
                                  </tfoot>
                                </table>
                              ) : (
                                <table className="sat-table dm-table dmr-table">
                                  <thead>
                                    <tr>
                                      <th>Customer</th>
                                      <th>Bill no</th>
                                      <th>Date</th>
                                      <th>Batch</th>
                                      <th className="num">Qty</th>
                                      <th className="num">MRP</th>
                                      <th className="num">Rate</th>
                                      <th className="num">Margin / unit</th>
                                      <th className="num">Value</th>
                                      <th>Type</th>
                                    </tr>
                                  </thead>
                                  {profitProducts(mineRows).map((p) => {
                                    const show = p.lines.slice(0, Math.max(0, shownLeft))
                                    shownLeft -= show.length
                                    if (!show.length) return null
                                    return (
                                      <tbody key={p.name}>
                                        <tr className="dmr-product">
                                          <td colSpan={10}>{p.name}</td>
                                        </tr>
                                        {show.map((r, i) => (
                                          <tr key={i}>
                                            <td className="dm-wrap">{r.patient}</td>
                                            <td>{r.bill_no}</td>
                                            <td>{dmy(r.date)}</td>
                                            <td>{r.batch}</td>
                                            <td className="num">
                                              {qtyText(r.qty)}
                                              {r.qty_returned ? <span className="muted"> (−{qtyText(r.qty_returned)} ret.)</span> : null}
                                            </td>
                                            <td className="num">{money(r.mrp)}</td>
                                            <td className="num">{money(r.rate)}</td>
                                            <td className="num">{money(r.margin_unit)}</td>
                                            <td className="num">{money(r.value)}</td>
                                            <td>
                                              {r.type} <Flags flags={r.flags} />
                                            </td>
                                          </tr>
                                        ))}
                                        <tr className="dmr-sub">
                                          <td colSpan={4}>Sub total</td>
                                          <td className="num">{qtyText(p.qty)}</td>
                                          <td colSpan={3} />
                                          <td className="num">{money(p.value)}</td>
                                          <td />
                                        </tr>
                                      </tbody>
                                    )
                                  })}
                                  <tfoot>
                                    <tr className="dm-total">
                                      <td colSpan={4}>TOTAL value</td>
                                      <td className="num">{qtyText(pd.qty)}</td>
                                      <td colSpan={3} />
                                      <td className="num">{money(pd.value)}</td>
                                      <td />
                                    </tr>
                                  </tfoot>
                                </table>
                              )}
                            </div>
                          ) : null}

                          {sd ? (
                            <div className="dmr-part">
                              <div className="dmr-part-head">
                                <b>On total sales</b>
                                <span className="muted">
                                  Customer-wise product-batch-wise sales {dmy(from)} – {dmy(to)} · {sd.types.join(', ')}
                                </span>
                              </div>
                              <table className="sat-table dm-table dmr-table dmr-sales">
                                <thead>
                                  <tr>
                                    <th>Customer name</th>
                                    <th>Bill no</th>
                                    <th>Date</th>
                                    <th>Batch no</th>
                                    <th className="num">Qty</th>
                                    <th className="num">Rate</th>
                                    <th className="num">Disc.</th>
                                    <th className="num">Value</th>
                                    <th>Doctor name</th>
                                    <th>Type</th>
                                  </tr>
                                </thead>
                                {sd.products.map((p) => {
                                  const show = p.lines.slice(0, Math.max(0, shownLeft))
                                  shownLeft -= show.length
                                  if (!show.length) return null
                                  return (
                                    <tbody key={`${p.name}|${p.pack}`}>
                                      <tr className="dmr-product">
                                        <td colSpan={10}>
                                          {p.name}
                                          {p.pack ? <span className="dmr-pack"> {p.pack}</span> : null}
                                        </td>
                                      </tr>
                                      {show.map((r, i) => (
                                        <tr key={i}>
                                          <td className="dm-wrap">{r.patient}</td>
                                          <td>{r.bill_no}</td>
                                          <td>{dmy(r.date)}</td>
                                          <td>{r.batch}</td>
                                          <td className="num">
                                            {qtyText(r.qty)}
                                            {r.qty_returned ? <span className="muted"> (−{qtyText(r.qty_returned)} ret.)</span> : null}
                                          </td>
                                          <td className="num">{money(r.sale_rate)}</td>
                                          <td className="num">{r.disc ? money(r.disc) : '-'}</td>
                                          <td className="num">{money(r.value)}</td>
                                          <td className="dm-wrap">{r.doctor}</td>
                                          <td>{String(r.type).toUpperCase()}</td>
                                        </tr>
                                      ))}
                                      <tr className="dmr-sub">
                                        <td colSpan={3} />
                                        <td>Sub Total:</td>
                                        <td className="num">{qtyText(p.qty)}</td>
                                        <td colSpan={2} />
                                        <td className="num">{money(p.value)}</td>
                                        <td colSpan={2} />
                                      </tr>
                                    </tbody>
                                  )
                                })}
                                <tfoot>
                                  <tr className="dm-total">
                                    <td colSpan={4}>Total sales value</td>
                                    <td className="num">{qtyText(sd.qty)}</td>
                                    <td colSpan={2} />
                                    <td className="num">{money(sd.value)}</td>
                                    <td colSpan={2} />
                                  </tr>
                                  {sd.by_pct.map((b) => (
                                    <tr key={b.pct} className="dm-total dmr-commission">
                                      <td colSpan={7}>
                                        Commission {pctText(b.pct)} of ₹{money(b.value)}
                                      </td>
                                      <td className="num">{money(b.commission)}</td>
                                      <td colSpan={2} />
                                    </tr>
                                  ))}
                                </tfoot>
                              </table>
                            </div>
                          ) : null}

                          <div className="dmr-doctor-margin">
                            <span>
                              {g.doctor} — doctor's margin
                              {pd && sd ? ` (on profit ₹${money(g.profit)} + commission ₹${money(g.commission)})` : ''}
                            </span>
                            <b>₹{money(g.total)}</b>
                          </div>
                        </section>
                      )
                    })}
                  </div>
                )}
                {report.rows.length + report.sales.rows.length > PREVIEW_ROWS ? (
                  <p className="muted dm-more">The preview shows the first {PREVIEW_ROWS} rows; Excel / PDF / Print carry all of them.</p>
                ) : null}
                {report.grand.doctors.length > 1 ? (
                  <div className="dmr-grand">
                    <span>All doctors — doctors' margin</span>
                    <b>₹{money(report.grand.totals.total)}</b>
                  </div>
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
          <button type="button" className="btn btn-primary" disabled={cannot} onClick={() => void exportAs('xlsx')}>
            {busy === 'xlsx' ? 'Saving…' : 'Excel'}
          </button>
          <button type="button" className="btn btn-neutral" disabled={cannot} onClick={() => void exportAs('pdf')}>
            {busy === 'pdf' ? 'Saving…' : 'PDF'}
          </button>
          <button type="button" className="btn btn-neutral" disabled={cannot} onClick={() => void exportAs('csv')}>
            {busy === 'csv' ? 'Saving…' : 'CSV'}
          </button>
          <button type="button" className="btn btn-neutral" disabled={cannot} onClick={() => void exportAs('print')}>
            {busy === 'print' ? 'Printing…' : 'Print'}
          </button>
        </div>
        {rulesOpen ? (
          <DoctorMarginRulesEditor
            onClose={() => setRulesOpen(false)}
            onSaved={() => {
              setNote(
                "Rules saved. New bills use them; bills already saved keep their figures — press Recalculate period to apply the new rules to this period.",
              )
              setReload((n) => n + 1)
            }}
          />
        ) : null}
      </div>
    </div>
  )
}
