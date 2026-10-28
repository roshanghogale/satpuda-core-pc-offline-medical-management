/** Settings > Accounts (owner, 28 Oct 2026): "add the balance sheet and trial balance for proper
 *  account management".
 *
 *  Three views of one report, From – To (default: this financial year, 1 April to today): the
 *  Trial Balance (Dr = Cr), the Profit & Loss (net sales − cost of goods sold − expenses) and the
 *  Balance Sheet as on the To date. Every figure is worked out by the engine from the books the app
 *  already keeps (core/accounts_report.py: bills, returns, payments, purchases, Cash & Expenses,
 *  today's stock) -- there is no ledger to type into. What the app does not track (the owner's
 *  capital, opening balances, bank deposits, GST paid) sits in one line, "Capital & opening
 *  balances (balancing figure)". Excel / PDF / CSV / Print as Cash & Expenses does.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { getApiBase } from '../api'
import { saveFileAndShow } from '../savedFiles'
import '../styles/cash-register.css'
import '../styles/accounts.css'

type Part = 'trial_balance' | 'profit_loss' | 'balance_sheet'
type TbRow = { key: string; group: string; account: string; dr: number | null; cr: number | null; note: string }
/** A line of the P&L or a side of the Balance Sheet: a heading, a line, a sub-total or a total. */
type Line = { kind: 'head' | 'line' | 'sub' | 'total'; label: string; amount?: number; note?: string }
type Report = {
  ok?: boolean
  error?: string
  online_mode?: boolean
  from: string; to: string; from_dmy: string; to_dmy: string; fy: string
  gst_split: boolean
  stock_worked_back: boolean
  trial_balance: {
    rows: TbRow[]; total_dr: number; total_cr: number; difference: number
    memo: { account: string; amount: number; note: string }[]
  }
  profit_loss: { rows: Line[]; gross_profit: number; net_profit: number; notes: string[] }
  balance_sheet: {
    as_on_dmy: string; liabilities: Line[]; assets: Line[]
    total_liabilities: number; total_assets: number; difference: number
  }
  checks: string[]
}

const PARTS: { id: Part; label: string }[] = [
  { id: 'trial_balance', label: 'Trial Balance' },
  { id: 'profit_loss', label: 'Profit & Loss' },
  { id: 'balance_sheet', label: 'Balance Sheet' },
]

const pad = (n: number) => String(n).padStart(2, '0')
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const todayIso = () => iso(new Date())
/** 1 April of the financial year running today (Indian FY, as the engine's default). */
const fyStart = () => {
  const d = new Date()
  return `${d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1}-04-01`
}
const money = (v: number | null | undefined) =>
  typeof v === 'number' ? v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : ''

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

function TrialBalance({ rep }: { rep: Report }) {
  const tb = rep.trial_balance
  return (
    <section className="cr-card">
      <div className="cr-card-head">
        <h3>Trial Balance</h3>
        <span className="vl-dim">{rep.from_dmy} – {rep.to_dmy} · balances as on {rep.to_dmy}</span>
      </div>
      <table className="sat-table cr-table cb-table">
        <thead>
          <tr>
            <th>Account</th>
            <th>Group</th>
            <th className="num">Dr</th>
            <th className="num">Cr</th>
            <th>Worked out from</th>
          </tr>
        </thead>
        <tbody>
          {tb.rows.map((a) => (
            <tr key={a.key} className={a.key === 'capital' ? 'cb-opening' : ''}>
              <td>{a.account}</td>
              <td className="vl-dim">{a.group}</td>
              <td className="num">{money(a.dr)}</td>
              <td className="num">{money(a.cr)}</td>
              <td className="cr-detail vl-dim">{a.note}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th colSpan={2}>Total</th>
            <th className="num">{money(tb.total_dr)}</th>
            <th className="num">{money(tb.total_cr)}</th>
            <th>{Math.abs(tb.difference) < 0.005 ? <span className="cr-diff cr-ok">Tallies</span> : null}</th>
          </tr>
          {tb.memo.map((m) => (
            <tr key={m.account}>
              <th colSpan={2} className="ac-memo">{m.account}</th>
              <th className="num ac-memo">{money(m.amount)}</th>
              <th />
              <th className="ac-memo">{m.note}</th>
            </tr>
          ))}
        </tfoot>
      </table>
    </section>
  )
}

/** Particulars | Rs, with headings, indented lines and bold sub-totals / totals. */
function Statement({ rows, total }: { rows: Line[]; total?: { label: string; amount: number } }) {
  return (
    <table className="sat-table cr-table cb-table">
      <tbody>
        {rows.map((r, i) =>
          r.kind === 'head' ? (
            <tr key={i} className="ac-head">
              <td colSpan={2}>{r.label}</td>
            </tr>
          ) : (
            <tr key={i} className={r.kind === 'line' ? '' : 'cb-total'} title={r.note || undefined}>
              <td className={r.kind === 'line' ? 'ac-indent' : ''}>
                {r.label}
                {r.note && r.kind !== 'line' ? <span className="vl-dim ac-note"> — {r.note}</span> : null}
              </td>
              <td className={`num${r.kind === 'total' && (r.amount ?? 0) < 0 ? ' cr-out' : ''}`}>{money(r.amount)}</td>
            </tr>
          ),
        )}
      </tbody>
      {total ? (
        <tfoot>
          <tr>
            <th>{total.label}</th>
            <th className="num">{money(total.amount)}</th>
          </tr>
        </tfoot>
      ) : null}
    </table>
  )
}

function ProfitLoss({ rep }: { rep: Report }) {
  const pl = rep.profit_loss
  return (
    <section className="cr-card ac-narrow">
      <div className="cr-card-head">
        <h3>Profit &amp; Loss</h3>
        <span className="vl-dim">{rep.from_dmy} – {rep.to_dmy}</span>
      </div>
      <Statement rows={pl.rows} />
      {pl.notes.map((n) => (
        <div key={n} className="settings-hint">{n}</div>
      ))}
    </section>
  )
}

function BalanceSheet({ rep }: { rep: Report }) {
  const bs = rep.balance_sheet
  return (
    <>
      <div className="cr-main ac-sides">
        <section className="cr-card cr-left">
          <div className="cr-card-head">
            <h3>Liabilities</h3>
            <span className="vl-dim">as on {bs.as_on_dmy}</span>
          </div>
          <Statement rows={bs.liabilities} total={{ label: 'Total', amount: bs.total_liabilities }} />
        </section>
        <section className="cr-card cr-left">
          <div className="cr-card-head">
            <h3>Assets</h3>
            <span className="vl-dim">as on {bs.as_on_dmy}</span>
          </div>
          <Statement rows={bs.assets} total={{ label: 'Total', amount: bs.total_assets }} />
        </section>
      </div>
      {Math.abs(bs.difference) >= 0.005 ? (
        <div className="vb-warn cr-msg">The two sides differ by {money(bs.difference)}.</div>
      ) : null}
    </>
  )
}

export function AccountsPage({ active, refreshNonce = 0 }: { active: boolean; refreshNonce?: number }) {
  const [part, setPart] = useState<Part>('trial_balance')
  const [from, setFrom] = useState(fyStart())
  const [to, setTo] = useState(todayIso())
  const [rep, setRep] = useState<Report | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [nonce, setNonce] = useState(0)

  const bounds = useMemo(() => (from <= to ? { from, to } : { from: to, to: from }), [from, to])

  const load = useCallback(async () => {
    setBusy(true)
    setErr('')
    try {
      const res = await fetch(`${getApiBase()}/api/accounts/report?from=${bounds.from}&to=${bounds.to}`)
      const d = (await res.json().catch(() => ({}))) as Report
      if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
      setRep(d)
    } catch (e) {
      setRep(null)
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [bounds.from, bounds.to])

  useEffect(() => {
    if (!active) return
    void load()
  }, [active, load, nonce, refreshNonce])

  async function exportAs(format: 'xlsx' | 'pdf' | 'csv' | 'print') {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      // Excel holds all three (a sheet each); PDF / CSV / paper the view on screen.
      const body: Record<string, unknown> = { from: bounds.from, to: bounds.to, part: format === 'xlsx' ? 'all' : part }
      if (format === 'print') body.print_to = 'printer'
      else body.format = format
      if (part === 'trial_balance') body.page_layout = 'landscape'
      const d = await post<{ message?: string; filename?: string; mime?: string; content_base64?: string }>('/api/accounts/export', body)
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

  const net = rep?.profit_loss.net_profit ?? 0

  return (
    <div className="cr-page">
      <div className="cr-top">
        <div className="cr-view">
          {PARTS.map((p) => (
            <button key={p.id} type="button" className={`cr-tab${part === p.id ? ' active' : ''}`} onClick={() => setPart(p.id)}>
              {p.label}
            </button>
          ))}
        </div>
        <span className="vl-dim">From</span>
        <input className="settings-input" type="date" value={from} aria-label="From" onChange={(e) => e.target.value && setFrom(e.target.value)} />
        <span className="vl-dim">To</span>
        <input className="settings-input" type="date" value={to} aria-label="To" onChange={(e) => e.target.value && setTo(e.target.value)} />
        <button type="button" className="cr-link" onClick={() => { setFrom(fyStart()); setTo(todayIso()) }}>This year</button>
        {rep ? <span className="vl-dim">FY {rep.fy}</span> : null}
        <button type="button" className="btn btn-neutral btn-sm" disabled={busy} onClick={() => setNonce((n) => n + 1)}>
          {busy ? 'Loading…' : 'Refresh'}
        </button>
        <div className="cr-actions">
          <button type="button" className="btn btn-primary" disabled={busy || !rep} onClick={() => void exportAs('xlsx')}>Excel</button>
          <button type="button" className="btn btn-neutral" disabled={busy || !rep} onClick={() => void exportAs('pdf')}>PDF</button>
          <button type="button" className="btn btn-neutral" disabled={busy || !rep} onClick={() => void exportAs('csv')}>CSV</button>
          <button type="button" className="btn btn-neutral" disabled={busy || !rep} onClick={() => void exportAs('print')}>Print</button>
        </div>
      </div>
      {err ? <div className="vb-warn cr-msg">{err}</div> : null}
      {msg ? <div className="settings-hint cr-msg">{msg}</div> : null}

      {rep ? (
        <div className="cr-left ac-body">
          <div className="ac-summary">
            <span>Net {net >= 0 ? 'profit' : 'loss'} <b className={`num ${net >= 0 ? 'cr-in' : 'cr-out'}`}>{money(Math.abs(net))}</b></span>
            <span>Gross profit <b className="num">{money(rep.profit_loss.gross_profit)}</b></span>
            <span>Trial balance {Math.abs(rep.trial_balance.difference) < 0.005 ? 'tallies' : 'does not tally'}</span>
            <span className="vl-dim">{rep.gst_split ? 'GST taken out of sales and purchases' : 'GST inside the figures (bills do not print GST)'}</span>
          </div>
          {part === 'trial_balance' ? <TrialBalance rep={rep} /> : null}
          {part === 'profit_loss' ? <ProfitLoss rep={rep} /> : null}
          {part === 'balance_sheet' ? <BalanceSheet rep={rep} /> : null}

          {rep.checks.length ? (
            <section className="cr-card">
              <div className="cr-card-head"><h3>Look at</h3></div>
              <ul className="ac-checks">
                {rep.checks.map((c) => <li key={c}>{c}</li>)}
              </ul>
            </section>
          ) : null}
          <div className="settings-hint">
            Worked out from the books the app keeps — bills, returns, payments, purchases, Cash &amp; Expenses and
            today&apos;s stock. Nothing is typed here. &ldquo;Capital &amp; opening balances (balancing figure)&rdquo; holds what
            the app is not told: the owner&apos;s capital, opening cash / stock / dues, money put into or taken out of the
            bank, GST paid.
          </div>
        </div>
      ) : busy ? (
        <div className="cr-empty">Loading…</div>
      ) : null}
    </div>
  )
}
