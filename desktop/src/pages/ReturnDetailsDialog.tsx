/**
 * View details of a saved return, purchase or sales (owner, 18 Oct 2026: "the purchase
 * return View details looks bad"; sales returns get the same dialog).
 *
 * Header (return no, date, party, original bill), the medicines as a table, the totals,
 * and a Credit part saying how the credit stands: what is still open, the bills it was
 * settled in, or the money handed back for it. One bar of buttons: Save PDF, Print, Edit
 * (purchase returns), Delete (red outline), Close. The actions are the page's own
 * handlers; this file only lays them out (docs/SETTINGS_DESIGN.md).
 */
import { useEffect, useRef } from 'react'
import type { ReturnCreditStatus, ReturnDetailItem } from '../pagesApi'
import '../styles/returns.css'

export type ReturnDetailView = {
  kind: 'sales' | 'purchase'
  id: number
  returnNo: string
  returnDate: string
  /** Supplier (purchase) or customer (sales). */
  party: string
  /** The original purchase entry no / sales bill no. */
  billNo: string
  billDate: string
  /** Purchase: the supplier's own bill number, when it has one. */
  supplierBillNo: string
  reason: string
  items: ReturnDetailItem[]
  subtotal: number
  discount: number
  discountPct: number
  refund: number
  /** null: not known here (an Online shop keeps its returns on the server). */
  credit: ReturnCreditStatus | null
}

function money(n: number) {
  return `₹${(Number(n) || 0).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

/** 2026-10-18 -> 18-10-2026 (as the bill prints it). */
function showDate(raw: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(raw || ''))
  return m ? `${m[3]}-${m[2]}-${m[1]}` : String(raw || '')
}

/** 2028-12-01 -> 12/28 (as the bill prints it); anything else as stored. */
function showExpiry(raw?: string) {
  const s = String(raw || '').trim()
  const m = /^(\d{4})-(\d{2})/.exec(s)
  return m ? `${m[2]}/${m[1].slice(2)}` : s
}

function showQty(q: number) {
  const n = Number(q) || 0
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

const HOW: Record<string, string> = {
  chosen: 'Settled in the bill',
  oldest: 'Paid an older due',
}

function modeWord(mode: string) {
  const m = String(mode || '').toLowerCase()
  if (m === 'online') return 'online'
  if (m === 'cash/online') return 'cash and online'
  return 'cash'
}

/** One sentence on top of the Credit part. */
function creditSentence(view: ReturnDetailView, c: ReturnCreditStatus): { text: string; tone: string } {
  const purchase = view.kind === 'purchase'
  const nextBill = purchase ? 'the next purchase from this supplier' : 'this customer’s next bill'
  const mode = String(c.settle_mode || 'ledger').toLowerCase()
  if (mode === 'cash' || mode === 'online') {
    if (c.paid_out > 0.009) {
      return { text: `Refunded in ${modeWord(c.paid_out_mode || mode)}: ${money(c.paid_out)} handed back.`, tone: 'done' }
    }
    return { text: `Settled in ${modeWord(mode)}; nothing was left to hand back.`, tone: 'done' }
  }
  if (!c.party_id) {
    return { text: purchase ? 'No supplier on this return, so it is not kept as credit.' : 'Counter sale: not kept as a customer’s credit.', tone: 'muted' }
  }
  if (c.left > 0.009 && c.left + 0.009 >= c.refund) {
    return { text: `${money(c.left)} credit open — offered in ${nextBill}.`, tone: 'open' }
  }
  if (c.left > 0.009) {
    return { text: `${money(c.left)} of ${money(c.refund)} still open — offered in ${nextBill}.`, tone: 'open' }
  }
  return { text: 'Fully settled — no credit left on this return.', tone: 'done' }
}

export function ReturnDetailsDialog({
  view,
  busy,
  onClose,
  onPdf,
  onPrint,
  onEdit,
  onDelete,
}: {
  view: ReturnDetailView
  busy?: boolean
  onClose: () => void
  onPdf: () => void
  onPrint: () => void
  /** Purchase returns: open it on the form for editing. */
  onEdit?: () => void
  onDelete: () => void
}) {
  const backdropRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const purchase = view.kind === 'purchase'
  const partyLabel = purchase ? 'Supplier' : 'Customer'
  const billLabel = purchase ? 'Original purchase' : 'Original bill'
  const totalLabel = purchase ? 'Credit to supplier' : 'Refund / credit'
  const c = view.credit
  const sentence = c ? creditSentence(view, c) : null

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // A box opened on top of this one (File saved, an alert) closes first.
      const all = document.querySelectorAll('.modal-backdrop')
      if (all.length && all[all.length - 1] !== backdropRef.current) return
      e.preventDefault()
      e.stopPropagation()
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  return (
    <div className="modal-backdrop" role="presentation" ref={backdropRef}>
      <div
        className="modal-card rd-card"
        role="dialog"
        aria-modal="true"
        aria-label={`${purchase ? 'Purchase' : 'Sales'} return ${view.returnNo}`}
      >
        <div className="modal-head">
          <h2>
            {purchase ? 'Purchase Return' : 'Sales Return'} {view.returnNo}
          </h2>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-body rd-body">
          <dl className="rd-head">
            <div className="rd-head-cell">
              <dt>Return no</dt>
              <dd>{view.returnNo || '—'}</dd>
            </div>
            <div className="rd-head-cell">
              <dt>Date</dt>
              <dd className="rd-num">{showDate(view.returnDate) || '—'}</dd>
            </div>
            <div className="rd-head-cell rd-head-wide">
              <dt>{partyLabel}</dt>
              <dd>{view.party || '—'}</dd>
            </div>
            <div className="rd-head-cell">
              <dt>{billLabel}</dt>
              <dd>
                {view.billNo || '—'}
                {view.billDate || view.supplierBillNo ? (
                  <span className="rd-sub">
                    {view.supplierBillNo ? `Supplier bill ${view.supplierBillNo}` : ''}
                    {view.supplierBillNo && view.billDate ? ' · ' : ''}
                    {view.billDate ? showDate(view.billDate) : ''}
                  </span>
                ) : null}
              </dd>
            </div>
            {view.reason ? (
              <div className="rd-head-cell rd-head-full">
                <dt>Reason</dt>
                <dd className="rd-reason">{view.reason}</dd>
              </div>
            ) : null}
          </dl>

          <section className="rd-section">
            <h3 className="rd-title">Medicines returned</h3>
            <div className="rd-table-wrap">
              <table className="sat-table settings-table rd-table">
                <thead>
                  <tr>
                    <th className="rd-col-sr">#</th>
                    <th>Medicine</th>
                    <th>Batch</th>
                    <th>Expiry</th>
                    <th className="num">Qty</th>
                    <th className="num">Rate</th>
                    <th className="num">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {view.items.map((it, i) => (
                    <tr key={`${it.medicine_id}-${i}`}>
                      <td className="rd-col-sr">{i + 1}</td>
                      <td className="rd-med">{it.name || '—'}</td>
                      <td>{it.batch || '—'}</td>
                      <td>{showExpiry(it.expiry) || '—'}</td>
                      <td className="num">{showQty(it.qty)}</td>
                      <td className="num">{(Number(it.rate) || 0).toFixed(2)}</td>
                      <td className="num">{(Number(it.amount) || 0).toFixed(2)}</td>
                    </tr>
                  ))}
                  {!view.items.length ? (
                    <tr>
                      <td colSpan={7} className="dt-empty">
                        No medicines on this return.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>

          <div className="rd-bottom">
            <section className="rd-section rd-credit">
              <h3 className="rd-title">Credit</h3>
              {!c ? (
                <p className="rd-line rd-tone-muted">
                  How this credit was used is not known on this PC (an Online shop keeps its returns on the server).
                </p>
              ) : (
                <>
                  {sentence ? <p className={`rd-line rd-tone-${sentence.tone}`}>{sentence.text}</p> : null}
                  <div className="rd-figures">
                    <div className="rd-fig">
                      <span>Return</span>
                      <strong>{money(c.refund)}</strong>
                    </div>
                    <div className="rd-fig">
                      <span>Settled in bills</span>
                      <strong>{money(c.settled_total)}</strong>
                    </div>
                    {c.paid_out > 0.009 ? (
                      <div className="rd-fig">
                        <span>{purchase ? 'Paid back to us' : 'Handed back'}</span>
                        <strong>{money(c.paid_out)}</strong>
                      </div>
                    ) : null}
                    <div className={`rd-fig${c.left > 0.009 ? ' rd-fig-open' : ''}`}>
                      <span>Open credit left</span>
                      <strong>{money(c.left)}</strong>
                    </div>
                  </div>
                  {c.settled.length ? (
                    <table className="sat-table settings-table rd-table rd-settled">
                      <thead>
                        <tr>
                          <th>{purchase ? 'Purchase no' : 'Bill no'}</th>
                          <th>Date</th>
                          <th>How</th>
                          <th className="num">Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {c.settled.map((s) => (
                          <tr key={s.bill_id}>
                            <td>
                              {s.bill_no || '—'}
                              {s.supplier_bill_no ? <span className="rd-sub">Supplier bill {s.supplier_bill_no}</span> : null}
                            </td>
                            <td>{showDate(s.date) || '—'}</td>
                            <td>{HOW[s.how] || s.how}</td>
                            <td className="num">{(Number(s.amount) || 0).toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p className="rd-line rd-tone-muted">
                      {purchase ? 'Not used in any purchase yet.' : 'Not used in any bill yet.'}
                    </p>
                  )}
                </>
              )}
            </section>

            <section className="rd-section rd-totals" aria-label="Totals">
              <h3 className="rd-title">Totals</h3>
              <div className="rd-total-row">
                <span>Subtotal</span>
                <span className="rd-num">{money(view.subtotal)}</span>
              </div>
              <div className="rd-total-row">
                <span>
                  Discount
                  {view.discountPct > 0 ? ` (${view.discountPct}%)` : ''}
                </span>
                <span className="rd-num">− {money(view.discount)}</span>
              </div>
              <div className="rd-total-row rd-grand">
                <span>{totalLabel}</span>
                <span className="rd-num">{money(view.refund)}</span>
              </div>
            </section>
          </div>
        </div>

        <div className="modal-foot rd-foot">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={onPdf}>
            Save PDF
          </button>
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={onPrint}>
            Print
          </button>
          {onEdit ? (
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={onEdit}>
              Edit Return
            </button>
          ) : null}
          <button type="button" className="btn rd-danger" disabled={busy} onClick={onDelete}>
            Delete Return
          </button>
          <button
            ref={closeRef}
            type="button"
            className="btn btn-neutral rd-close"
            onClick={onClose}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
