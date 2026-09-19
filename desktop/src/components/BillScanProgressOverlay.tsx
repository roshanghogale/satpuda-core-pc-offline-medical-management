type Props = {
  open: boolean
  status?: string
}

/**
 * The wait while a purchase bill is being read.
 *
 * Importing a photo makes two network calls to Gemini, each with a 180-second
 * timeout, and the only thing that moved on screen was one small button in the
 * payment row relabelling itself — which the counter is not even looking at
 * when the import was started from the tools dialog or with Shift+F2. So the
 * experience was: click, silence for a minute, popup.
 *
 * Classic shows a grabbed Toplevel with an indeterminate bar over the still
 * visible form, and Android shows the same thing; this is that. It also takes
 * the clicks and keystrokes, so a second import or a save cannot be started
 * half way through a parse.
 */
export function BillScanProgressOverlay({
  open,
  status = 'Reading your bill…',
}: Props) {
  if (!open) return null

  return (
    <div
      className="bill-scan-overlay"
      role="dialog"
      aria-modal="true"
      aria-busy="true"
    >
      <div className="bill-scan-card">
        <h2 className="bill-scan-title">Reading your bill — please wait</h2>
        <p className="bill-scan-status" aria-live="polite">{status}</p>
        <div className="bill-scan-bar" aria-hidden="true">
          <div className="bill-scan-bar-fill" />
        </div>
        <p className="bill-scan-hint">
          A bill of several pages can take a little time — keep the internet on.
        </p>
        <p className="bill-scan-hint">
          After the import, check MRP, rate, quantity and schedule on every line
          before you save the purchase.
        </p>
      </div>
    </div>
  )
}
