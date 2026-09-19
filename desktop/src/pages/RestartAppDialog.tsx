/** Confirm dialog for Restart App after settings that need a process reload. */

export type RestartPrompt = {
  title?: string
  message: string
  onConfirm?: () => void
  onCancel?: () => void
} | null

export function RestartAppDialog({
  prompt,
  onClose,
  onRestart,
}: {
  prompt: RestartPrompt
  onClose: () => void
  onRestart: () => void | Promise<void>
}) {
  if (!prompt) return null
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={() => {
        prompt.onCancel?.()
        onClose()
      }}
    >
      <div
        className="modal-card"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="restart-app-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2 id="restart-app-title">{prompt.title || 'Restart App'}</h2>
          <button
            type="button"
            className="icon-btn"
            onClick={() => {
              prompt.onCancel?.()
              onClose()
            }}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{prompt.message}</p>
        </div>
        <div className="modal-foot">
          <button
            type="button"
            className="btn-neutral"
            onClick={() => {
              prompt.onCancel?.()
              onClose()
            }}
          >
            Later
          </button>
          <button
            type="button"
            className="btn-primary"
            autoFocus
            onClick={() => {
              prompt.onConfirm?.()
              onClose()
              void onRestart()
            }}
          >
            Restart App now
          </button>
        </div>
      </div>
    </div>
  )
}
