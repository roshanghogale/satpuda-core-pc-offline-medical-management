import type { ExistingSupplierBill, PurchaseImportPreview } from '../pagesApi'
import { DataTable } from './pageChrome'

type Props = {
  open: boolean
  preview: PurchaseImportPreview | null
  busy?: boolean
  /** The purchase this invoice number is already saved on, if any. Sent with the
   *  parse (startPurchaseImport's existing_bill). */
  existingBill?: ExistingSupplierBill | null
  onClose: () => void
  onApply: () => void
  /** Add only the medicines that purchase does not have yet to that purchase. */
  onMerge?: (purchaseId: number) => void
  /** Save this as a second purchase for the same bill number. */
  onSaveAsNew?: () => void
}

/** Say where the lines came from, never which AI model read them: the model
 *  is not the shop's business and changes without notice. Supplier-format
 *  parsers (Marg, Tuljai) keep their own names. */
function importSourceLabel(parser?: string, sourceType?: string): string {
  const p = String(parser || '').trim()
  if (/gemini|gpt|claude|openai|anthropic|llm|vision/i.test(p)) {
    const pages = p.match(/\((\d+)\s*pages?\)/i)
    return pages ? `Photo / scanned bill (${pages[1]} pages)` : 'Photo / scanned bill'
  }
  return p || String(sourceType || '') || '—'
}

export function PurchaseImportReviewDialog({
  open,
  preview,
  busy,
  existingBill,
  onClose,
  onApply,
  onMerge,
  onSaveAsNew,
}: Props) {
  if (!open || !preview) return null
  const rows = (preview.lines || []).map((ln) => [
    ln.name,
    ln.batch,
    ln.qty,
    ln.rate,
    ln.valid ? 'OK' : (ln.issues || []).join('; ') || 'Invalid',
  ])
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide modal-card-tall"
        role="dialog"
        aria-modal="true"
        aria-label="Review import"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>Review Purchase Import</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p className="note">
            {preview.supplier_name || 'Supplier'} · Bill{' '}
            {preview.bill_number || '—'} · {preview.purchase_date || ''} · Source:{' '}
            {importSourceLabel(preview.parser, preview.source_type)}
          </p>
          <p className="note">
            Valid {preview.valid_count ?? 0} · Invalid {preview.invalid_count ?? 0}
          </p>
          <p className="note">
            Check MRP, rate, quantity and schedule on every line before you save
            the purchase.
          </p>
          {/* One supplier bill is one purchase: store 127 held the same bill as
              purchases 35 and 36, one second apart. Say it in the shop's own
              words, and offer to add the missing medicines to the bill that is
              already there. */}
          {existingBill ? (
            <p className="note" role="alert">
              Ha bill {existingBill.bill_number || preview.bill_number || ''}{' '}
              already saved aahe (purchase {existingBill.purchase_no}
              {existingBill.purchase_date ? `, ${existingBill.purchase_date}` : ''}
              , {existingBill.item_count ?? 0} aushadhe). Tyat{' '}
              {existingBill.new_line_count ?? 0} navin aushadhe jodu ka?
            </p>
          ) : null}
          <DataTable
            columns={['Medicine', 'Batch', 'Qty', 'Rate', 'Status']}
            rows={rows}
            empty="No lines parsed"
          />
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-neutral" onClick={onClose}>
            {existingBill ? 'Radd' : 'Cancel'}
          </button>
          {existingBill ? (
            <>
              <button
                type="button"
                className="btn btn-neutral"
                disabled={busy || !(preview.valid_count ?? 0)}
                onClick={onSaveAsNew}
              >
                Naveen bill banav
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || !(preview.valid_count ?? 0)}
                onClick={() => onMerge?.(existingBill.purchase_id)}
              >
                {busy ? 'Applying…' : 'Merge'}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || !(preview.valid_count ?? 0)}
              onClick={onApply}
            >
              {busy ? 'Applying…' : 'Apply to Purchase'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
