/** Returns as party credit (owner, 17 Oct 2026): a return is kept as the customer's (or the
 *  supplier's) credit, and settled inside their next bill.
 *
 *  ReturnsPickDialog  pops up when a party with return credit not used yet is picked on a
 *                     NEW sale / purchase: tick the returns to settle in this bill.
 *  ReturnTableRows    the bill's item table, after the items: a "Return SR1-7 17-10-2026" row
 *                     (minus what this bill uses of it), then the medicines that came back.
 *                     Shown, never edited -- they are the return's, not lines of this bill.
 *
 *  The engine settles them on save (core/return_credit.py), the same way the web does.
 */
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import type { BillReturn, OpenReturn } from '../pagesApi'

function money(n: number) {
  return `₹${(Number(n) || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function round2(n: number) {
  return Math.round((Number(n) || 0) * 100) / 100
}

/** 2026-10-17 -> 17-10-2026, as the bill prints it. */
export function fmtReturnDate(raw: string) {
  const s = String(raw || '').slice(0, 10)
  const p = s.split('-')
  return p.length === 3 && p[0].length === 4 ? `${p[2]}-${p[1]}-${p[0]}` : s
}

/** The centred row that opens a return in a bill: "Return SR1-7 17-10-2026", and on a
 *  purchase the supplier's name on the same row (owner, 18 Oct 2026). Never the customer. */
export function returnRowLabel(r: { return_no: string; return_date: string }, party = '') {
  const head = `Return ${r.return_no} ${fmtReturnDate(r.return_date)}`
  return party.trim() ? `${head} · ${party.trim().toUpperCase()}` : head
}

/** The row closing a return under its medicines, with "(₹5 of ₹10 used in this bill)" when
 *  part of it went to other bills. */
export function returnTotalLabel(r: { amount: number; refund: number }) {
  return r.refund > r.amount + 0.009
    ? `Return total (${money(r.amount)} of ${money(r.refund)} used in this bill)`
    : 'Return total'
}

/** "2028-12-01" or "12/28" -> "12/28", as the bill's Exp column. */
function fmtExp(raw?: string) {
  const s = String(raw || '').trim()
  const m = /^(\d{4})-(\d{2})/.exec(s)
  return m ? `${m[2]}/${m[1].slice(2)}` : s
}

/** The ticked open returns as they will sit in this bill: each gives what it has left, never
 *  more than `room` (what the bill leaves unpaid), in order -- as the engine settles them. */
export function returnsForScreen(open: OpenReturn[], picked: number[], room: number): BillReturn[] {
  let left = round2(Math.max(0, room))
  const out: BillReturn[] = []
  for (const r of open) {
    if (!picked.includes(r.return_id) || left <= 0.009) continue
    const amount = round2(Math.min(r.left, left))
    if (amount <= 0.009) continue
    left = round2(left - amount)
    out.push({
      return_id: r.return_id,
      return_no: r.return_no,
      return_date: r.return_date,
      bill_no: r.bill_no,
      refund: r.amount,
      amount,
      items: r.items,
    })
  }
  return out
}

/** `cols` = the table's column keys in order. Each return is a block (owner, 18 Oct 2026):
 *  a centred "Return SR1-7 17-10-2026" row across the table (the supplier's name too on a
 *  purchase), the medicines that came back one per row like the items (batch, expiry, qty,
 *  MRP, minus amount), then the return's total. */
export function ReturnTableRows({
  returns,
  cols,
  party = '',
}: {
  returns: BillReturn[]
  cols: string[]
  /** a purchase's supplier, shown on the Return row */
  party?: string
}) {
  if (!returns.length) return null
  const amountAt = cols.indexOf('amount')
  const span = amountAt >= 0 ? amountAt : cols.length
  const tail = amountAt >= 0 ? cols.length - amountAt - 1 : 0
  const cell = (c: string, it: BillReturn['items'][number], j: number) => {
    if (c === 'name') return `${j + 1}. ${it.name}`
    if (c === 'batch') return it.batch_no || ''
    if (c === 'expiry') return fmtExp(it.expiry)
    if (c === 'qty') return it.qty
    if (c === 'mrp') return Number(it.mrp) ? Number(it.mrp).toFixed(2) : ''
    if (c === 'amount') return `− ${Number(it.amount).toFixed(2)}`
    return ''
  }
  return (
    <>
      {returns.map((r) => [
        <tr key={`ret-${r.return_id}`} className="ret-head" data-return-row="1">
          <td colSpan={cols.length}>{returnRowLabel(r, party)}</td>
        </tr>,
        ...(r.items || []).map((it, j) => (
          <tr key={`ret-${r.return_id}-${j}`} className="ret-line">
            {cols.map((c, k) => (
              <td
                key={k}
                className={c === 'qty' ? 'numsm mono' : c === 'amount' || c === 'mrp' ? 'num mono' : undefined}
              >
                {cell(c, it, j)}
              </td>
            ))}
          </tr>
        )),
        <tr key={`ret-${r.return_id}-t`} className="ret-total">
          <td colSpan={Math.max(1, span)}>{returnTotalLabel(r)}</td>
          {amountAt >= 0 ? <td className="num mono">− {Number(r.amount).toFixed(2)}</td> : null}
          {tail > 0 ? <td colSpan={tail} /> : null}
        </tr>,
      ])}
    </>
  )
}

/** "<party> has return credit": tick the returns to settle in this bill. */
export function ReturnsPickDialog({
  open,
  party,
  word,
  returns,
  picked,
  onChange,
  onUse,
  onSkip,
}: {
  open: boolean
  party: string
  /** 'bill' or 'purchase' */
  word: string
  returns: OpenReturn[]
  picked: number[]
  onChange: (picked: number[]) => void
  onUse: () => void
  onSkip: () => void
}) {
  const useRef_ = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    if (open) window.setTimeout(() => useRef_.current?.focus(), 30)
  }, [open])
  if (!open) return null
  const total = round2(returns.filter((r) => picked.includes(r.return_id)).reduce((s, r) => s + r.left, 0))
  const node = (
    <div className="modal-backdrop" role="presentation" onClick={onUse}>
      <div
        className="modal-card modal-card-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Return credit"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onUse()
          }
        }}
      >
        <div className="modal-head">
          <h2>{party} has return credit</h2>
        </div>
        <div className="modal-body">
          <p style={{ margin: '0 0 10px' }}>
            Tick the returns to settle in this {word}. They show on the {word} as Return rows and come off
            its total.
          </p>
          <div className="table-scroll">
            <table className="ret-pick-list">
              <tbody>
                {returns.map((r) => (
                  <tr key={r.return_id}>
                    <td style={{ width: 28 }}>
                      <input
                        type="checkbox"
                        aria-label={`Use ${r.return_no}`}
                        checked={picked.includes(r.return_id)}
                        onChange={(e) =>
                          onChange(
                            e.target.checked
                              ? returns.filter((x) => x.return_id === r.return_id || picked.includes(x.return_id)).map((x) => x.return_id)
                              : picked.filter((x) => x !== r.return_id),
                          )
                        }
                      />
                    </td>
                    <td>
                      <b>{r.return_no}</b>
                      <div className="sub">
                        {fmtReturnDate(r.return_date)}
                        {r.bill_no ? ` · ${word === 'purchase' ? 'purchase' : 'bill'} ${r.bill_no}` : ''}
                        {r.left < r.amount - 0.009
                          ? ` · ${money(r.amount - r.left)} of ${money(r.amount)} already went to older dues`
                          : ''}
                      </div>
                      <div className="sub">{(r.items || []).map((it) => `${it.name} × ${it.qty}`).join(', ')}</div>
                    </td>
                    <td className="num">{money(r.left)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="note" style={{ margin: '8px 0 0' }}>
            Ticked: {money(total)} (never more than the {word} leaves unpaid).
          </p>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onSkip}>
            Not in this {word}
          </button>
          <button type="button" className="btn btn-primary" ref={useRef_} onClick={onUse}>
            Use ticked
          </button>
        </div>
      </div>
    </div>
  )
  return createPortal(node, document.body)
}
