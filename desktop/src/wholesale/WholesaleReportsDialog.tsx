/** Wholesale reports (owner, 18 Oct 2026): sales register, party-wise sales, item-wise sales by
 *  party, outstanding with ageing, and the Rate A / B / C price list -- each with CSV, Excel, PDF
 *  and Print, as the other reports. Shown only when the store is in wholesale mode.
 *
 *  Every figure is the engine's (core/wholesale_reports.py); this dialog only shows them.
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { getApiBase } from '../api'
import { todayLocal } from '../localDate'
import { saveFileAndShow } from '../savedFiles'

export type WholesaleReport = 'register' | 'party' | 'items' | 'outstanding' | 'price_list'

type Section = { title: string; columns: string[]; rows: (string | number)[][]; total_row: (string | number)[] | null }
type Report = {
  ok?: boolean
  error?: string
  report: WholesaleReport
  title: string
  subtitle: string
  count: number
  without_rate?: number
  sections: Section[]
}
type Party = { id: number; name: string }

const TABS: { key: WholesaleReport; label: string }[] = [
  { key: 'register', label: 'Sales register' },
  { key: 'party', label: 'Party-wise sales' },
  { key: 'items', label: 'Item-wise by party' },
  { key: 'outstanding', label: 'Outstanding / ageing' },
  { key: 'price_list', label: 'Price list' },
]

// money columns (two decimals, right aligned); counts and days stay as they are
const MONEY = /^(Taxable|CGST|SGST|IGST|GST$|Total|Invoice Total|Paid|Balance|Amount|Bill Total|Not Due|Due \d|Limit$|Purchase Rate|Rate [ABC]|MRP)/
const NUMBER = /^(Bills|Qty|Free Qty|Stock|Days Late)/

const money = (v: unknown) =>
  typeof v === 'number' ? v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : String(v ?? '')

function monthStart(): string {
  return `${todayLocal().slice(0, 8)}01`
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, init)
  const d = (await res.json().catch(() => ({}))) as T & { ok?: boolean; error?: string }
  if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
  return d
}

function Table({ s }: { s: Section }) {
  const cls = (c: string) => (MONEY.test(c) || NUMBER.test(c) ? 'num' : '')
  const cell = (c: string, v: unknown) => (MONEY.test(c) ? money(v) : String(v ?? ''))
  const crossedAt = s.columns.indexOf('Limit Crossed')
  if (!s.rows.length) return <div className="vl-dim" style={{ marginTop: 8 }}>Nothing here for these filters.</div>
  return (
    <div className="table-scroll" style={{ maxHeight: '52vh' }}>
      <table className="sat-table">
        <thead>
          <tr>
            {s.columns.map((c) => (
              <th key={c} className={cls(c)}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {s.rows.map((r, i) => (
            <tr
              key={i}
              style={crossedAt >= 0 && r[crossedAt] === 'Yes' ? { color: 'var(--danger, #c0392b)', fontWeight: 600 } : undefined}
            >
              {r.map((v, j) => (
                <td key={j} className={cls(s.columns[j] || '')}>{cell(s.columns[j] || '', v)}</td>
              ))}
            </tr>
          ))}
        </tbody>
        {s.total_row ? (
          <tfoot>
            <tr>
              {s.total_row.map((v, j) => (
                <th key={j} className={cls(s.columns[j] || '')}>{cell(s.columns[j] || '', v)}</th>
              ))}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  )
}

type Props = {
  open: boolean
  onClose: () => void
  /** Back to the dialog this was opened from (Export). */
  onBack?: () => void
  initial?: WholesaleReport
  from?: string
  to?: string
}

export function WholesaleReportsDialog({ open, onClose, onBack, initial = 'register', from = '', to = '' }: Props) {
  const [tab, setTab] = useState<WholesaleReport>(initial)
  const [dFrom, setDFrom] = useState(from || monthStart())
  const [dTo, setDTo] = useState(to || todayLocal())
  const [asOf, setAsOf] = useState(todayLocal())
  const [partyId, setPartyId] = useState(0)
  const [tier, setTier] = useState('')
  const [inStock, setInStock] = useState(true)
  const [search, setSearch] = useState('')
  const [parties, setParties] = useState<Party[]>([])
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [printTo, setPrintTo] = useState<'printer' | 'dot_matrix'>('printer')
  const [layout, setLayout] = useState<'landscape' | 'portrait'>('landscape')

  useEffect(() => {
    if (!open) return
    setTab(initial)
    setDFrom(from || monthStart())
    setDTo(to || todayLocal())
    setErr('')
    setMsg('')
    void call<{ parties: Party[] }>('/api/reports/wholesale/parties')
      .then((d) => setParties(d.parties || []))
      .catch(() => setParties([]))
  }, [open, initial, from, to])

  const params = (): Record<string, string> => {
    const p: Record<string, string> = { report: tab }
    if (tab === 'register' || tab === 'party' || tab === 'items') {
      p.from = dFrom
      p.to = dTo
    }
    if ((tab === 'register' || tab === 'items' || tab === 'outstanding') && partyId) p.party_id = String(partyId)
    if (tab === 'outstanding') p.as_of = asOf
    if (tab === 'price_list') {
      if (tier) p.tier = tier
      p.in_stock = inStock ? '1' : '0'
      if (search.trim()) p.search = search.trim()
    }
    return p
  }

  useEffect(() => {
    if (!open) return
    let alive = true
    setLoading(true)
    setErr('')
    const t = window.setTimeout(() => {
      call<Report>(`/api/reports/wholesale?${new URLSearchParams(params())}`)
        .then((d) => {
          if (alive) setReport(d)
        })
        .catch((e) => {
          if (!alive) return
          setReport(null)
          setErr(e instanceof Error ? e.message : String(e))
        })
        .finally(() => {
          if (alive) setLoading(false)
        })
    }, 150)
    return () => {
      alive = false
      window.clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, tab, dFrom, dTo, asOf, partyId, tier, inStock, search])

  async function exportAs(format: 'csv' | 'xlsx' | 'pdf' | 'print') {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const body: Record<string, unknown> = { ...params(), page_layout: layout }
      if (format === 'print') body.print_to = printTo
      else body.format = format
      const d = await call<{ message?: string; filename?: string; mime?: string; content_base64?: string }>(
        '/api/reports/wholesale/export',
        { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
      )
      if (d.content_base64 && d.filename) {
        const saved = await saveFileAndShow(d.filename, d.mime || 'application/octet-stream', d.content_base64)
        setMsg(saved ? `${d.filename} saved: ${saved.folder || ''}` : `${d.filename} saved (Downloads)`)
      } else {
        setMsg(d.message || 'Sent to the printer.')
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null
  const dated = tab === 'register' || tab === 'party' || tab === 'items'
  const byParty = tab === 'register' || tab === 'items' || tab === 'outstanding'
  const empty = !report || !report.sections.some((s) => s.rows.length)

  const node = (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Wholesale reports"
        style={{ width: 'min(1180px, 96vw)', maxHeight: '94vh', display: 'flex', flexDirection: 'column' }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onClose()
          }
        }}
      >
        <div className="modal-head">
          <h2>Wholesale reports</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body" style={{ overflow: 'auto' }}>
          <div className="settings-inline-row" style={{ gap: 4, flexWrap: 'wrap' }}>
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                className={`btn ${t.key === tab ? 'btn-primary' : 'btn-neutral'}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="settings-inline-row" style={{ flexWrap: 'wrap', gap: 8, alignItems: 'center', marginTop: 10 }}>
            {dated ? (
              <>
                <label className="vl-dim">From</label>
                <input className="settings-input" type="date" value={dFrom} onChange={(e) => setDFrom(e.target.value)} />
                <label className="vl-dim">To</label>
                <input className="settings-input" type="date" value={dTo} onChange={(e) => setDTo(e.target.value)} />
              </>
            ) : null}
            {tab === 'outstanding' ? (
              <>
                <label className="vl-dim">As on</label>
                <input className="settings-input" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
              </>
            ) : null}
            {byParty ? (
              <select className="settings-input" value={partyId} onChange={(e) => setPartyId(Number(e.target.value))}>
                <option value={0}>All parties</option>
                {parties.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            ) : null}
            {tab === 'price_list' ? (
              <>
                <select className="settings-input" value={tier} onChange={(e) => setTier(e.target.value)}>
                  <option value="">All rates (with purchase rate)</option>
                  <option value="A">Rate A list</option>
                  <option value="B">Rate B list</option>
                  <option value="C">Rate C list</option>
                </select>
                <label className="vl-dim" style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  <input type="checkbox" checked={inStock} onChange={(e) => setInStock(e.target.checked)} />
                  In stock only
                </label>
                <input
                  className="settings-input"
                  placeholder="Medicine name"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </>
            ) : null}
            <span className="vl-dim">
              {loading ? 'Loading…' : report ? `${report.subtitle} · ${report.count} ${tab === 'price_list' ? 'batches' : tab === 'items' ? 'lines' : tab === 'register' ? 'invoices' : 'parties'}` : ''}
            </span>
          </div>

          {err ? <div className="vb-warn" style={{ marginTop: 8 }}>{err}</div> : null}
          {msg ? <div className="settings-hint" style={{ marginTop: 8 }}>{msg}</div> : null}
          {tab === 'price_list' && report?.without_rate ? (
            <div className="settings-hint" style={{ marginTop: 8 }}>
              {report.without_rate} batch(es) have no {tier ? `Rate ${tier}` : 'Rate A'} yet — it is set on the purchase.
            </div>
          ) : null}
          {tab === 'outstanding' ? (
            <div className="settings-hint" style={{ marginTop: 8 }}>
              Days are counted from each bill's due date (its bill date when it has none). What a bill still owes
              follows the payments and returns as the dues do.
            </div>
          ) : null}

          {(report?.sections || []).map((s) => (
            <div key={s.title} style={{ marginTop: 10 }}>
              {(report?.sections.length || 0) > 1 ? <h3 style={{ margin: '6px 0' }}>{s.title}</h3> : null}
              <Table s={s} />
            </div>
          ))}
        </div>
        <div className="modal-foot" style={{ flexWrap: 'wrap', gap: 6 }}>
          {onBack ? (
            <button type="button" className="btn btn-neutral" onClick={onBack}>
              Back
            </button>
          ) : null}
          <button type="button" className="btn btn-neutral" disabled={busy || empty} onClick={() => void exportAs('csv')}>
            CSV
          </button>
          <button type="button" className="btn btn-neutral" disabled={busy || empty} onClick={() => void exportAs('xlsx')}>
            Excel
          </button>
          <button type="button" className="btn btn-neutral" disabled={busy || empty} onClick={() => void exportAs('pdf')}>
            Save PDF
          </button>
          <select className="settings-input" value={printTo} onChange={(e) => setPrintTo(e.target.value as typeof printTo)}>
            <option value="printer">Printer</option>
            <option value="dot_matrix">Dot matrix</option>
          </select>
          <select className="settings-input" value={layout} onChange={(e) => setLayout(e.target.value as typeof layout)}>
            <option value="landscape">A4 landscape</option>
            <option value="portrait">A4 portrait</option>
          </select>
          <button type="button" className="btn btn-primary" disabled={busy || empty} onClick={() => void exportAs('print')}>
            Print
          </button>
          <button type="button" className="btn btn-neutral" onClick={onClose}>
            Close (Esc)
          </button>
        </div>
      </div>
    </div>
  )
  return createPortal(node, document.body)
}
