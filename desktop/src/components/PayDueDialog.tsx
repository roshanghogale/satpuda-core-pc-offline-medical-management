/** Take a payment for one bill, straight from Sales / Purchase History (engine:
 *  core/history_pay.py). Saved as an ordinary customer / supplier payment with the bill number
 *  as its reference, so it is in the ledger, the party's due and every bill's status. It pays
 *  THIS bill first (the amount starts at this bill's due); any more clears the oldest bills. */
import { useEffect, useRef, useState } from 'react'
import { getApiBase } from '../api'
import { dispatchPaymentsChanged } from '../syncRefresh'
import { todayLocal } from '../localDate'

type DueInfo = {
  ok?: boolean
  error?: string
  kind: 'sale' | 'purchase'
  party_kind: 'customer' | 'supplier'
  bill_id: number
  bill_no: string
  bill_date: string
  party: string
  party_due: number
  bill_due: number
  older_due: number
  to_clear: number
  oldest_first_note?: string
}

const rs = (n: number | undefined) => `₹${Number(n || 0).toFixed(2)}`

export function PayDueDialog({
  kind,
  billId,
  onClose,
  onPaid,
}: {
  kind: 'sale' | 'purchase'
  /** null = closed. */
  billId: number | null
  onClose: () => void
  onPaid?: (message: string) => void
}) {
  const [info, setInfo] = useState<DueInfo | null>(null)
  const [error, setError] = useState('')
  const [amount, setAmount] = useState('')
  const [mode, setMode] = useState<'Cash' | 'Online'>('Cash')
  const [date, setDate] = useState(() => todayLocal())
  const [receiptNo, setReceiptNo] = useState('')
  const [saving, setSaving] = useState(false)
  const amountRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (billId == null) return
    let alive = true
    setInfo(null)
    setError('')
    setSaving(false)
    setMode('Cash')
    setDate(todayLocal())
    setReceiptNo('')
    fetch(`${getApiBase()}/api/history/bill-due?kind=${kind}&id=${billId}`)
      .then((r) => r.json())
      .then((d: DueInfo) => {
        if (!alive) return
        if (d.ok === false || d.error) {
          setError(d.error || 'Could not read this bill.')
          return
        }
        setInfo(d)
        setAmount(d.to_clear > 0 ? String(d.to_clear) : '')
        window.setTimeout(() => amountRef.current?.select(), 30)
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
  }, [billId, kind])

  if (billId == null) return null
  const isSale = kind === 'sale'

  const save = async () => {
    if (!info) return
    const amt = Number(amount)
    if (!(amt > 0)) {
      setError('Enter the amount.')
      return
    }
    setSaving(true)
    setError('')
    try {
      const res = await fetch(`${getApiBase()}/api/history/pay-bill`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, id: info.bill_id, amount: amt, mode, date, receipt_no: receiptNo.trim() }),
      })
      const d = (await res.json()) as { ok?: boolean; error?: string; message?: string }
      if (!res.ok || d.ok === false) {
        setError(d.error || "Could not reach the app's data. Try again.")
        setSaving(false)
        return
      }
      // Ledger, dues and every list follow at once.
      dispatchPaymentsChanged(isSale ? 'customer' : 'supplier')
      onPaid?.(d.message || 'Payment saved.')
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label={isSale ? 'Receive payment for this bill' : 'Pay the supplier for this purchase'}
        onKeyDown={(e) => {
          if (e.key === 'Escape') onClose()
          if (e.key === 'Enter' && !saving) void save()
        }}
      >
        <div className="modal-head">
          <h2>{isSale ? 'Receive payment' : 'Pay supplier'}</h2>
        </div>
        <div className="modal-body">
          {!info && !error ? <p className="muted">Reading the bill…</p> : null}
          {info ? (
            <>
              <p>
                <b>{info.bill_no}</b> · {info.bill_date} · {info.party}
              </p>
              <table className="sat-table settings-table" style={{ marginBottom: 10 }}>
                <tbody>
                  <tr>
                    <td>Due on this bill</td>
                    <td className="num mono">{rs(info.bill_due)}</td>
                  </tr>
                  {info.older_due > 0.009 ? (
                    <tr>
                      <td>Due on older bills</td>
                      <td className="num mono">{rs(info.older_due)}</td>
                    </tr>
                  ) : null}
                  <tr>
                    <td>{isSale ? 'Customer' : 'Supplier'} total due</td>
                    <td className="num mono">{rs(info.party_due)}</td>
                  </tr>
                </tbody>
              </table>
              {info.oldest_first_note ? (
                <p className="muted" style={{ fontSize: 12 }}>
                  {info.oldest_first_note}
                </p>
              ) : null}
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  Amount
                  <input
                    ref={amountRef}
                    className="settings-input"
                    type="number"
                    min="0"
                    step="0.01"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    style={{ width: 130 }}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  Mode
                  <select
                    className="settings-input"
                    value={mode}
                    onChange={(e) => setMode(e.target.value === 'Online' ? 'Online' : 'Cash')}
                  >
                    <option value="Cash">Cash</option>
                    <option value="Online">Online</option>
                  </select>
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  Date
                  <input
                    className="settings-input"
                    type="date"
                    value={date}
                    onChange={(e) => setDate(e.target.value)}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  Receipt no.
                  <input
                    className="settings-input"
                    value={receiptNo}
                    maxLength={40}
                    placeholder="optional"
                    onChange={(e) => setReceiptNo(e.target.value)}
                    style={{ width: 120 }}
                  />
                </label>
              </div>
            </>
          ) : null}
          {error ? (
            <p style={{ color: 'var(--danger, #c0392b)', marginTop: 8 }}>{error}</p>
          ) : null}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-neutral" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!info || saving}
            onClick={() => void save()}
          >
            {saving ? 'Saving…' : isSale ? 'Save payment' : 'Save payment to supplier'}
          </button>
        </div>
      </div>
    </div>
  )
}
