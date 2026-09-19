import { useEffect, useState } from 'react'
import { DataTable } from './pageChrome'

export type FileImportBill = {
  supplier?: {
    name?: string
    address?: string
    phone?: string
    gstin?: string
    dl_numbers?: string
  }
  purchase_date?: string
  bill_number?: string
  gst_calc_method?: string
  overall_discount?: number
  overall_discount_pct?: number
  cash_paid?: number
  online_paid?: number
  amount_paid?: number
  items?: {
    name?: string
    type?: string
    batch?: string
    expiry?: string
    qty?: number
    free_qty?: number
    rate?: number
    gst_pct?: number
    discount_pct?: number
    mrp?: number
  }[]
}

type Props = {
  open: boolean
  index: number
  count: number
  bill: FileImportBill | null
  busy?: boolean
  onClose: () => void
  onPrev: () => void
  onNext: () => void
  onSubmitAll: () => void
  onBillChange: (bill: FileImportBill) => void
}

function num(v: unknown, fallback = 0): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

export function FileImportReviewDialog({
  open,
  index,
  count,
  bill,
  busy,
  onClose,
  onPrev,
  onNext,
  onSubmitAll,
  onBillChange,
}: Props) {
  const [draft, setDraft] = useState<FileImportBill | null>(bill)

  useEffect(() => {
    setDraft(bill)
  }, [bill, index])

  if (!open || !draft) return null

  const sup = draft.supplier || {}
  const isLast = index >= count - 1

  function patch(partial: Partial<FileImportBill>) {
    const next = { ...draft, ...partial }
    setDraft(next)
    onBillChange(next)
  }

  function patchSupplier(field: string, value: string) {
    patch({ supplier: { ...sup, [field]: value } })
  }

  const rows = (draft.items || []).map((it) => [
    it.name || '',
    it.type || '',
    it.batch || '',
    it.expiry || '',
    String(it.qty ?? ''),
    String(it.rate ?? ''),
    String(it.gst_pct ?? ''),
  ])

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide modal-card-tall"
        role="dialog"
        aria-modal="true"
        aria-label="Review import bills"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>
            Import Purchases — Bill {index + 1} of {count}
          </h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body file-import-review">
          <div className="settings-inline-row">
            <label className="settings-field">
              <span>Supplier</span>
              <input
                className="settings-input"
                value={sup.name || ''}
                onChange={(e) => patchSupplier('name', e.target.value)}
              />
            </label>
            <label className="settings-field">
              <span>Bill no.</span>
              <input
                className="settings-input"
                value={draft.bill_number || ''}
                onChange={(e) => patch({ bill_number: e.target.value })}
              />
            </label>
            <label className="settings-field">
              <span>Date</span>
              <input
                className="settings-input"
                value={draft.purchase_date || ''}
                onChange={(e) => patch({ purchase_date: e.target.value })}
              />
            </label>
          </div>
          <div className="settings-inline-row">
            <label className="settings-field">
              <span>Phone</span>
              <input
                className="settings-input"
                value={sup.phone || ''}
                onChange={(e) => patchSupplier('phone', e.target.value)}
              />
            </label>
            <label className="settings-field">
              <span>GSTIN</span>
              <input
                className="settings-input"
                value={sup.gstin || ''}
                onChange={(e) => patchSupplier('gstin', e.target.value)}
              />
            </label>
            <label className="settings-field">
              <span>DL</span>
              <input
                className="settings-input"
                value={sup.dl_numbers || ''}
                onChange={(e) => patchSupplier('dl_numbers', e.target.value)}
              />
            </label>
          </div>
          <label className="settings-field">
            <span>Address</span>
            <input
              className="settings-input"
              value={sup.address || ''}
              onChange={(e) => patchSupplier('address', e.target.value)}
            />
          </label>
          <div className="settings-inline-row">
            <label className="settings-field">
              <span>Overall disc. ₹</span>
              <input
                className="settings-input"
                type="number"
                value={draft.overall_discount ?? 0}
                onChange={(e) =>
                  patch({ overall_discount: num(e.target.value) })
                }
              />
            </label>
            <label className="settings-field">
              <span>Cash paid</span>
              <input
                className="settings-input"
                type="number"
                value={draft.cash_paid ?? 0}
                onChange={(e) => patch({ cash_paid: num(e.target.value) })}
              />
            </label>
            <label className="settings-field">
              <span>Online paid</span>
              <input
                className="settings-input"
                type="number"
                value={draft.online_paid ?? 0}
                onChange={(e) => patch({ online_paid: num(e.target.value) })}
              />
            </label>
          </div>
          <DataTable
            columns={[
              'Medicine',
              'Type',
              'Batch',
              'Expiry',
              'Qty',
              'Rate',
              'GST%',
            ]}
            rows={rows}
            empty="No line items"
          />
        </div>
        <div className="modal-foot">
          <button
            type="button"
            className="btn btn-neutral"
            disabled={busy}
            onClick={onClose}
          >
            Cancel import
          </button>
          <button
            type="button"
            className="btn btn-neutral"
            disabled={busy || index <= 0}
            onClick={onPrev}
          >
            ◀ Previous
          </button>
          {isLast ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={onSubmitAll}
            >
              {busy ? 'Saving…' : `✔ Submit all ${count}`}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={onNext}
            >
              Next ▶
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
