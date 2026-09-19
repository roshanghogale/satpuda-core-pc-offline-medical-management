type Props = {
  open: boolean
  billNo?: string
  html?: string
  loading?: boolean
  error?: string
  saleId?: number
  printing?: boolean
  onClose: () => void
  onPrint?: (slot: 1 | 2, mode: 'slot' | 'silent') => void
}

export function BillPreviewDialog({
  open,
  billNo,
  html,
  loading,
  error,
  saleId,
  printing,
  onClose,
  onPrint,
}: Props) {
  if (!open) return null
  const canPrint = Boolean(saleId && onPrint && !loading && !error)
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide modal-card-tall"
        role="dialog"
        aria-modal="true"
        aria-label="Bill preview"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>Bill Preview {billNo ? `— ${billNo}` : ''}</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body bill-preview-body">
          {loading ? <p className="note">Loading preview…</p> : null}
          {error ? <p className="error">{error}</p> : null}
          {html ? (
            <iframe
              title={`Bill ${billNo || 'preview'}`}
              className="bill-preview-frame"
              srcDoc={html}
            />
          ) : null}
        </div>
        <div className="modal-foot">
          {canPrint ? (
            <>
              <button
                type="button"
                className="btn btn-neutral"
                disabled={printing}
                onClick={() => onPrint!(1, 'slot')}
              >
                Print Sales 1 (F7)
              </button>
              <button
                type="button"
                className="btn btn-neutral"
                disabled={printing}
                onClick={() => onPrint!(2, 'slot')}
              >
                Print Sales 2 (F8)
              </button>
              <button
                type="button"
                className="btn btn-neutral"
                disabled={printing}
                onClick={() => onPrint!(2, 'silent')}
              >
                Silent Reprint (F9)
              </button>
            </>
          ) : null}
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
