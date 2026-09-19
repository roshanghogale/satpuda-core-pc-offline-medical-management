type Props = {
  open: boolean
  status?: string
}

/** Full-screen loading overlay while the local engine and first paint finish. */
export function StartupSplashOverlay({
  open,
  status = 'Starting local engine…',
}: Props) {
  if (!open) return null

  return (
    <div className="startup-splash-overlay" role="dialog" aria-modal="true" aria-busy="true">
      <div className="startup-splash-card">
        <h2 className="startup-splash-title">Satpuda Core Private Limited</h2>
        <p className="startup-splash-subtitle">Medical Management Software</p>
        <p className="startup-splash-status">{status}</p>
        <div className="startup-splash-bar" aria-hidden="true">
          <div className="startup-splash-bar-fill is-busy" />
        </div>
        <p className="startup-splash-time">Please wait…</p>
      </div>
    </div>
  )
}
