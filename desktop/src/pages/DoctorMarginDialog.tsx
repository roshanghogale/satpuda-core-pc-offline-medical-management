/**
 * Doctor Margin Report (owner, 17 Oct 2026): Sales History -> Export -> Doctor Margin Report.
 *
 * The filters, a preview (table + totals + per-doctor totals + flags) and Excel / CSV / PDF /
 * Print. The figures are the engine's (core/doctor_margin_report.py, the rule in
 * core/doctor_margin.py that the server and the phone share); this dialog only shows them.
 * Every export is asked for with the filters on screen, so a file never carries stale ones.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { getApiBase, savePrefs } from '../api'
import { todayLocal } from '../localDate'
import { saveFileAndShow, showSavedFile } from '../savedFiles'
import '../styles/doctor-margin.css'
import { DoctorMarginRulesReport } from './DoctorMarginRulesReport'

type Mode = 'rules' | 'regular' | 'injection'
type Style = 'detailed' | 'summary'
type Flag = 'estimated' | 'unavailable' | 'loss'

type Line = {
  date: string
  patient: string
  doctor: string
  bill_no: string
  medicine_id: number
  name: string
  type: string
  batch: string
  qty: number
  sale_rate: number
  purchase_rate: number
  basis: string
  amount: number
  cost: number
  profit: number
  pct: number
  commission: number
  remaining: number
  flags: Flag[]
}

type SummaryLine = Omit<Line, 'date' | 'patient' | 'bill_no' | 'sale_rate'> & {
  avg_sale_rate: number
}

type Totals = {
  qty: number
  amount: number
  cost: number
  profit: number
  commission: number
  remaining: number
}

type Report = {
  ok: boolean
  mode: Mode
  pct: number
  rows: Line[]
  summary: SummaryLine[]
  doctors: (Totals & { doctor: string })[]
  totals: Totals
  flag_counts: Record<Flag, number>
  bill_count: number
  method: string
  method_note: string
}

type Options = {
  doctors: string[]
  types: string[]
  pct: number
  style: Style
  method: string
  method_note: string
  online: boolean
}

type Props = {
  open: boolean
  onClose: () => void
  /** Back to the export list. */
  onBack?: () => void
  from?: string
  to?: string
}

const NO_DOCTOR = '(No doctor)'
const PREVIEW_ROWS = 300
const ALLOCATION_RULE = '100% of positive profit'
const FLAG_TEXT: Record<Flag, string> = {
  estimated: 'Estimated cost',
  unavailable: 'No cost',
  loss: 'Loss',
}

/** Same rule as the engine: an injection is a type with "inj" in it. */
function isInjection(type: string) {
  return type.toLowerCase().includes('inj')
}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = (await res.json().catch(() => ({}))) as T & { error?: string }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

function money(v: number) {
  return (Number(v) || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function qtyText(v: number) {
  return (Number(v) || 0).toLocaleString('en-IN', { maximumFractionDigits: 3 })
}

function dmy(iso: string) {
  const s = String(iso || '').slice(0, 10)
  return s.length === 10 ? `${s.slice(8, 10)}/${s.slice(5, 7)}/${s.slice(0, 4)}` : s
}

function weekStart(today: string) {
  const d = new Date(`${today}T00:00:00`)
  const back = (d.getDay() + 6) % 7 // Monday
  d.setDate(d.getDate() - back)
  return todayLocal(d)
}

function FlagChips({ flags }: { flags: Flag[] }) {
  if (!flags?.length) return null
  return (
    <span className="dm-flags">
      {flags.map((f) => (
        <span key={f} className={`dm-flag dm-flag-${f}`}>
          {FLAG_TEXT[f] || f}
        </span>
      ))}
    </span>
  )
}

/** Searchable multi-select with tick boxes, Select all / Clear all, chips. */
export function TypePicker({
  types,
  picked,
  onChange,
  noun = 'type',
}: {
  types: string[]
  picked: string[]
  onChange: (next: string[]) => void
  /** What one entry is called in the summary ("type", "doctor"). */
  noun?: string
}) {
  const [open, setOpen] = useState(false)
  const [find, setFind] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const set = useMemo(() => new Set(picked), [picked])
  const shown = types.filter((t) => t.toLowerCase().includes(find.trim().toLowerCase()))

  useEffect(() => {
    if (!open) return
    const off = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', off)
    return () => document.removeEventListener('mousedown', off)
  }, [open])

  const all = types.length > 0 && picked.length === types.length
  const summary = !picked.length
    ? `No ${noun} ticked`
    : all
      ? `All ${types.length} ${noun}s`
      : picked.length > 3
        ? `${picked.length} ${noun}s`
        : ''

  return (
    <div className="dm-types" ref={box}>
      <button
        type="button"
        className="settings-input dm-types-btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && open) {
            e.stopPropagation()
            setOpen(false)
          }
        }}
      >
        {summary ? (
          <span className="dm-types-summary">{summary}</span>
        ) : (
          picked.map((t) => (
            <span key={t} className="dm-chip">
              {t}
            </span>
          ))
        )}
        <span className="dm-caret" aria-hidden>
          ▾
        </span>
      </button>
      {open ? (
        <div
          className="dm-types-pop"
          role="listbox"
          aria-multiselectable="true"
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.stopPropagation()
              setOpen(false)
            }
          }}
        >
          <input
            className="settings-input"
            placeholder={`Find a ${noun}…`}
            value={find}
            autoFocus
            onChange={(e) => setFind(e.target.value)}
          />
          <div className="dm-types-actions">
            <button type="button" className="btn btn-neutral btn-sm" onClick={() => onChange([...types])}>
              Select all
            </button>
            <button type="button" className="btn btn-neutral btn-sm" onClick={() => onChange([])}>
              Clear all
            </button>
          </div>
          <div className="dm-types-list">
            {shown.length ? (
              shown.map((t) => (
                <label key={t} className="dm-types-item">
                  <input
                    type="checkbox"
                    checked={set.has(t)}
                    onChange={(e) =>
                      onChange(e.target.checked ? types.filter((x) => set.has(x) || x === t) : picked.filter((x) => x !== t))
                    }
                  />
                  <span>{t}</span>
                </label>
              ))
            ) : (
              <p className="muted dm-empty-small">No {noun} matches.</p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export function DoctorMarginDialog({ open, onClose, onBack, from = '', to = '' }: Props) {
  const today = todayLocal()
  const [dFrom, setDFrom] = useState(from || today)
  const [dTo, setDTo] = useState(to || today)
  const [doctor, setDoctor] = useState('')
  // By margin rules (owner, 22 Oct 2026) first; the two older reports stay one click away.
  const [mode, setMode] = useState<Mode>('rules')
  const [pctText, setPctText] = useState('10')
  const [style, setStyle] = useState<Style>('detailed')
  const [opts, setOpts] = useState<Options | null>(null)
  // Each mode keeps its own ticks, so switching back and forth loses nothing.
  const [regPicked, setRegPicked] = useState<string[]>([])
  const [injPicked, setInjPicked] = useState<string[]>([])
  const [printTo, setPrintTo] = useState<'' | 'dot_matrix' | 'printer'>('')
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const seq = useRef(0)
  const savedPct = useRef<number | null>(null)
  const savedStyle = useRef<Style | null>(null)

  useEffect(() => {
    if (!open) return
    setDFrom(from || todayLocal())
    setDTo(to || todayLocal())
    setError('')
    setNote('')
    setReport(null)
    void call<Options>('/api/reports/doctor-margin/options')
      .then((o) => {
        setOpts(o)
        const reg = o.types.filter((t) => !isInjection(t))
        const inj = o.types.filter((t) => isInjection(t))
        setRegPicked(reg)
        setInjPicked(inj)
        const p = Number.isFinite(Number(o.pct)) ? Number(o.pct) : 10
        savedPct.current = p
        setPctText(String(p))
        savedStyle.current = o.style === 'summary' ? 'summary' : 'detailed'
        setStyle(savedStyle.current)
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [open, from, to])

  const regTypes = useMemo(() => (opts?.types || []).filter((t) => !isInjection(t)), [opts])
  const injTypes = useMemo(() => (opts?.types || []).filter((t) => isInjection(t)), [opts])
  const modeTypes = mode === 'injection' ? injTypes : regTypes
  const picked = mode === 'injection' ? injPicked : regPicked
  const setPicked = mode === 'injection' ? setInjPicked : setRegPicked

  const pct = Number(pctText)
  const problem = useMemo(() => {
    if (!dFrom || !dTo) return 'Choose the From and To dates.'
    if (dFrom > dTo) return 'From date is after To date.'
    if (mode === 'rules') return ''
    if (mode === 'regular' && (pctText.trim() === '' || !Number.isFinite(pct) || pct < 0 || pct > 100)) {
      return 'Doctor Commission (%) must be 0 to 100.'
    }
    if (modeTypes.length && !picked.length) return 'Tick at least one medicine type.'
    return ''
  }, [dFrom, dTo, mode, pctText, pct, modeTypes.length, picked.length])

  // All ticked = no type filter (a line without a type counts too), as the reference does.
  const filters = useMemo(
    () => ({
      from: dFrom,
      to: dTo,
      doctor,
      mode,
      pct: mode === 'regular' ? pct : 10,
      types: picked.length === modeTypes.length ? [] : picked,
      style,
    }),
    [dFrom, dTo, doctor, mode, pct, picked, modeTypes.length, style],
  )
  const dataKey = JSON.stringify({ ...filters, style: undefined })

  useEffect(() => {
    if (!open || !opts || opts.online || mode === 'rules') return
    if (problem) {
      seq.current += 1 // an answer still on its way is for filters no longer on screen
      setReport(null)
      setLoading(false)
      return
    }
    const mine = ++seq.current
    setLoading(true)
    const t = window.setTimeout(() => {
      void call<Report>('/api/reports/doctor-margin', filters)
        .then((r) => {
          if (mine !== seq.current) return
          setReport(r)
          setError('')
          if (mode === 'regular' && savedPct.current !== pct) {
            savedPct.current = pct
            void savePrefs({ doctor_margin_pct: pct }).catch(() => undefined)
          }
        })
        .catch((e) => {
          if (mine === seq.current) setError(e instanceof Error ? e.message : String(e))
        })
        .finally(() => {
          if (mine === seq.current) setLoading(false)
        })
    }, 300)
    return () => window.clearTimeout(t)
    // filters minus the style: both styles come back in one answer
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, opts, problem, dataKey])

  useEffect(() => {
    if (!open || !opts || savedStyle.current === style) return
    savedStyle.current = style
    void savePrefs({ doctor_margin_style: style }).catch(() => undefined)
  }, [open, opts, style])

  if (!open) return null

  const quick = (which: 'today' | 'week' | 'month') => {
    const t = todayLocal()
    setDTo(t)
    setDFrom(which === 'today' ? t : which === 'week' ? weekStart(t) : `${t.slice(0, 8)}01`)
  }

  const exportAs = async (format: 'xlsx' | 'csv' | 'pdf' | 'print') => {
    if (problem) {
      setError(problem)
      return
    }
    setBusy(format)
    setError('')
    setNote('')
    try {
      const out = await call<{
        ok: boolean
        filename?: string
        mime?: string
        content_base64?: string
        printed?: boolean
        path?: string
        message?: string
        error?: string
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

  const inj = mode === 'injection'
  const modeSwitch = (
    <div className="dm-field dm-field-wide">
      <span className="dm-label">Report mode</span>
      <div className="dm-seg" role="radiogroup" aria-label="Report mode">
        <button type="button" role="radio" aria-checked={mode === 'rules'} className={mode === 'rules' ? 'on' : ''} onClick={() => setMode('rules')}>
          By margin rules
        </button>
        <button type="button" role="radio" aria-checked={mode === 'regular'} className={mode === 'regular' ? 'on' : ''} onClick={() => setMode('regular')}>
          Regular medicine %
        </button>
        <button type="button" role="radio" aria-checked={inj} className={inj ? 'on' : ''} onClick={() => setMode('injection')}>
          Injection profit
        </button>
      </div>
    </div>
  )
  if (mode === 'rules') {
    return (
      <DoctorMarginRulesReport
        modeSwitch={modeSwitch}
        doctors={(opts?.doctors || []).filter((d) => d !== NO_DOCTOR)}
        online={Boolean(opts?.online)}
        from={dFrom}
        to={dTo}
        onDates={(f, t) => {
          setDFrom(f)
          setDTo(t)
        }}
        onClose={onClose}
        onBack={onBack}
      />
    )
  }
  const marginWord = inj ? 'Doctor margin' : 'Commission'
  const lines = report ? (style === 'detailed' ? report.rows : report.summary) : []
  const shown = lines.slice(0, PREVIEW_ROWS)
  const counts = report?.flag_counts
  const t = report?.totals

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose()
      }}
    >
      <div
        className="modal-card dm-card"
        role="dialog"
        aria-modal="true"
        aria-label="Doctor Margin Report"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>Doctor Margin Report</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body dm-body">
          {opts?.online ? (
            <p className="page-error">
              This report reads this PC's own books. An Online store's bills are on the server: use
              Sales History → Export on the web.
            </p>
          ) : null}

          <div className="dm-filters">
            <div className="dm-field">
              <span className="dm-label">From</span>
              <input type="date" className="settings-input" value={dFrom} onChange={(e) => setDFrom(e.target.value)} />
            </div>
            <div className="dm-field">
              <span className="dm-label">To</span>
              <input type="date" className="settings-input" value={dTo} onChange={(e) => setDTo(e.target.value)} />
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
            <div className="dm-field">
              <span className="dm-label">Doctor</span>
              <select className="settings-input" value={doctor} onChange={(e) => setDoctor(e.target.value)}>
                <option value="">All doctors</option>
                {(opts?.doctors || []).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </div>

            {modeSwitch}
            <div className="dm-field dm-field-wide">
              <span className="dm-label">Medicine types</span>
              {modeTypes.length ? (
                <TypePicker types={modeTypes} picked={picked} onChange={setPicked} />
              ) : (
                <span className="muted dm-inline-note">
                  {inj ? 'No injection type in the medicine list.' : 'No medicine types set up.'}
                </span>
              )}
            </div>
            <div className="dm-field">
              <span className="dm-label">Doctor Commission (%)</span>
              {inj ? (
                <span className="muted dm-inline-note">Not used: {ALLOCATION_RULE}</span>
              ) : (
                <input
                  type="number"
                  className="settings-input dm-num"
                  min={0}
                  max={100}
                  step={0.5}
                  value={pctText}
                  onChange={(e) => setPctText(e.target.value)}
                />
              )}
            </div>
            <div className="dm-field">
              <span className="dm-label">Report display style</span>
              <div className="dm-seg" role="radiogroup" aria-label="Report display style">
                <button
                  type="button"
                  role="radio"
                  aria-checked={style === 'detailed'}
                  className={style === 'detailed' ? 'on' : ''}
                  onClick={() => setStyle('detailed')}
                >
                  Patient-wise detailed
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={style === 'summary'}
                  className={style === 'summary' ? 'on' : ''}
                  onClick={() => setStyle('summary')}
                >
                  Medicine-wise summary
                </button>
              </div>
            </div>
            <div className="dm-field dm-field-wide">
              <span className="dm-label">Purchase rate</span>
              <span className="dm-method" title={opts?.method_note || ''}>
                {opts?.method || 'Scheme-adjusted effective purchase rate'}
                <span className="muted"> — purchase amount after discount, with GST ÷ units received (free included)</span>
              </span>
            </div>
          </div>

          {problem ? <p className="page-error">{problem}</p> : null}
          {error && !problem ? <p className="page-error">{error}</p> : null}
          {note ? <p className="dm-ok">{note}</p> : null}

          <div className="dm-preview" aria-busy={loading}>
            <div className="dm-preview-head">
              <h3>Preview</h3>
              <span className="muted">
                {loading
                  ? 'Updating…'
                  : report
                    ? `${dmy(dFrom)} to ${dmy(dTo)} · ${doctor || 'All doctors'} · ${report.bill_count} bills · ${
                        style === 'detailed' ? `${report.rows.length} lines` : `${report.summary.length} rows`
                      }`
                    : ''}
              </span>
            </div>

            {report && t ? (
              <>
                <div className="dm-cards">
                  <div className="dm-stat">
                    <span>Sale</span>
                    <b>₹{money(t.amount)}</b>
                  </div>
                  <div className="dm-stat">
                    <span>Purchase cost</span>
                    <b>₹{money(t.cost)}</b>
                  </div>
                  <div className="dm-stat">
                    <span>{inj ? 'Injection profit' : 'Profit'}</span>
                    <b>₹{money(t.profit)}</b>
                  </div>
                  <div className="dm-stat dm-stat-main">
                    <span>
                      {inj ? 'Doctor margin' : `Commission ${report.pct}%`}
                    </span>
                    <b>₹{money(t.commission)}</b>
                  </div>
                  <div className="dm-stat">
                    <span>Store remaining</span>
                    <b>₹{money(t.remaining)}</b>
                  </div>
                </div>
                {counts && (counts.estimated || counts.unavailable || counts.loss) ? (
                  <ul className="dm-flag-notes">
                    {counts.estimated ? (
                      <li>
                        <span className="dm-flag dm-flag-estimated">Estimated cost</span> {counts.estimated} line(s):
                        no purchase of that batch; the cost recorded on the bill is used.
                      </li>
                    ) : null}
                    {counts.unavailable ? (
                      <li>
                        <span className="dm-flag dm-flag-unavailable">No cost</span> {counts.unavailable} line(s): no
                        cost known, taken as 0 — the profit is too high.
                      </li>
                    ) : null}
                    {counts.loss ? (
                      <li>
                        <span className="dm-flag dm-flag-loss">Loss</span> {counts.loss} line(s) sold below cost; no
                        negative {inj ? 'margin' : 'commission'} — check them.
                      </li>
                    ) : null}
                  </ul>
                ) : null}

                {lines.length ? (
                  <div className="dm-table-wrap">
                    <table className="sat-table dm-table">
                      <thead>
                        {style === 'detailed' ? (
                          <tr>
                            <th>Sale date</th>
                            <th>Customer / Patient</th>
                            <th>Doctor</th>
                            <th>Bill no</th>
                            <th>Medicine</th>
                            <th>Type</th>
                            <th>Batch</th>
                            <th className="num">Sold qty</th>
                            <th className="num">Selling rate</th>
                            <th className="num">Purchase rate</th>
                            <th className="num">Sale amount</th>
                            <th className="num">Purchase cost</th>
                            <th className="num">{inj ? 'Injection profit' : 'Profit'}</th>
                            {inj ? null : <th className="num">Comm. %</th>}
                            <th className="num">{inj ? 'Doctor margin ₹' : 'Commission ₹'}</th>
                            {inj ? <th>Allocation rule</th> : null}
                            <th className="num">Store remaining</th>
                            <th>Flags</th>
                          </tr>
                        ) : (
                          <tr>
                            <th>Doctor</th>
                            <th>Medicine</th>
                            <th>Type</th>
                            <th>Batch</th>
                            <th className="num">Total qty</th>
                            <th className="num">Avg selling rate</th>
                            <th className="num">Purchase rate</th>
                            <th className="num">Total sale</th>
                            <th className="num">Total cost</th>
                            <th className="num">{inj ? 'Total injection profit' : 'Total profit'}</th>
                            {inj ? null : <th className="num">Comm. %</th>}
                            <th className="num">{inj ? 'Total doctor margin ₹' : 'Total commission ₹'}</th>
                            {inj ? <th>Allocation rule</th> : null}
                            <th className="num">Store remaining</th>
                            <th>Flags</th>
                          </tr>
                        )}
                      </thead>
                      <tbody>
                        {style === 'detailed'
                          ? (shown as Line[]).map((r, i) => (
                              <tr key={i} className={r.flags.includes('loss') ? 'dm-row-loss' : ''}>
                                <td>{dmy(r.date)}</td>
                                <td className="dm-wrap">{r.patient}</td>
                                <td className="dm-wrap">{r.doctor}</td>
                                <td>{r.bill_no}</td>
                                <td className="dm-wrap">{r.name}</td>
                                <td>{r.type}</td>
                                <td>{r.batch}</td>
                                <td className="num">{qtyText(r.qty)}</td>
                                <td className="num">{money(r.sale_rate)}</td>
                                <td className="num">{money(r.purchase_rate)}</td>
                                <td className="num">{money(r.amount)}</td>
                                <td className="num">{money(r.cost)}</td>
                                <td className="num">{money(r.profit)}</td>
                                {inj ? null : <td className="num">{r.pct}</td>}
                                <td className="num">{money(r.commission)}</td>
                                {inj ? <td className="dm-wrap">{ALLOCATION_RULE}</td> : null}
                                <td className="num">{money(r.remaining)}</td>
                                <td>
                                  <FlagChips flags={r.flags} />
                                </td>
                              </tr>
                            ))
                          : (shown as SummaryLine[]).map((r, i) => (
                              <tr key={i} className={r.flags.includes('loss') ? 'dm-row-loss' : ''}>
                                <td className="dm-wrap">{r.doctor}</td>
                                <td className="dm-wrap">{r.name}</td>
                                <td>{r.type}</td>
                                <td>{r.batch}</td>
                                <td className="num">{qtyText(r.qty)}</td>
                                <td className="num">{money(r.avg_sale_rate)}</td>
                                <td className="num">{money(r.purchase_rate)}</td>
                                <td className="num">{money(r.amount)}</td>
                                <td className="num">{money(r.cost)}</td>
                                <td className="num">{money(r.profit)}</td>
                                {inj ? null : <td className="num">{r.pct}</td>}
                                <td className="num">{money(r.commission)}</td>
                                {inj ? <td className="dm-wrap">{ALLOCATION_RULE}</td> : null}
                                <td className="num">{money(r.remaining)}</td>
                                <td>
                                  <FlagChips flags={r.flags} />
                                </td>
                              </tr>
                            ))}
                      </tbody>
                      <tfoot>
                        <tr className="dm-total">
                          <td colSpan={style === 'detailed' ? 7 : 4}>Total</td>
                          <td className="num">{qtyText(t.qty)}</td>
                          <td colSpan={2} />
                          <td className="num">{money(t.amount)}</td>
                          <td className="num">{money(t.cost)}</td>
                          <td className="num">{money(t.profit)}</td>
                          {inj ? null : <td className="num">{report.pct}</td>}
                          <td className="num">{money(t.commission)}</td>
                          {inj ? <td /> : null}
                          <td className="num">{money(t.remaining)}</td>
                          <td />
                        </tr>
                      </tfoot>
                    </table>
                    {lines.length > shown.length ? (
                      <p className="muted dm-more">
                        Showing {shown.length} of {lines.length}. Excel / CSV / PDF / Print carry all of them.
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className="dm-empty">No sales match these filters.</p>
                )}

                {report.doctors.length ? (
                  <>
                    <h3 className="dm-sub">Totals by doctor</h3>
                    <table className="sat-table dm-table dm-doctors">
                      <thead>
                        <tr>
                          <th>Doctor</th>
                          <th className="num">Qty</th>
                          <th className="num">Sale amount</th>
                          <th className="num">Purchase cost</th>
                          <th className="num">Profit</th>
                          <th className="num">{marginWord} ₹</th>
                          <th className="num">Store remaining</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.doctors.map((d) => (
                          <tr key={d.doctor}>
                            <td className={d.doctor === NO_DOCTOR ? 'muted' : ''}>{d.doctor}</td>
                            <td className="num">{qtyText(d.qty)}</td>
                            <td className="num">{money(d.amount)}</td>
                            <td className="num">{money(d.cost)}</td>
                            <td className="num">{money(d.profit)}</td>
                            <td className="num">{money(d.commission)}</td>
                            <td className="num">{money(d.remaining)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                ) : null}
              </>
            ) : !loading && !problem && !opts?.online ? (
              <p className="dm-empty">{opts ? 'Preparing the preview…' : 'Loading…'}</p>
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
            <select
              className="settings-input"
              value={printTo}
              onChange={(e) => setPrintTo(e.target.value as '' | 'dot_matrix' | 'printer')}
            >
              <option value="">As set in Settings</option>
              <option value="printer">Normal printer</option>
              <option value="dot_matrix">Dot matrix</option>
            </select>
          </label>
          <button
            type="button"
            className="btn btn-primary"
            disabled={Boolean(busy) || Boolean(problem) || !report || opts?.online}
            onClick={() => void exportAs('xlsx')}
          >
            {busy === 'xlsx' ? 'Saving…' : 'Excel'}
          </button>
          <button
            type="button"
            className="btn btn-neutral"
            disabled={Boolean(busy) || Boolean(problem) || !report || opts?.online}
            onClick={() => void exportAs('csv')}
          >
            {busy === 'csv' ? 'Saving…' : 'CSV'}
          </button>
          <button
            type="button"
            className="btn btn-neutral"
            disabled={Boolean(busy) || Boolean(problem) || !report || opts?.online}
            onClick={() => void exportAs('pdf')}
          >
            {busy === 'pdf' ? 'Saving…' : 'PDF'}
          </button>
          <button
            type="button"
            className="btn btn-neutral"
            disabled={Boolean(busy) || Boolean(problem) || !report || opts?.online}
            onClick={() => void exportAs('print')}
          >
            {busy === 'print' ? 'Printing…' : 'Print'}
          </button>
        </div>
      </div>
    </div>
  )
}
