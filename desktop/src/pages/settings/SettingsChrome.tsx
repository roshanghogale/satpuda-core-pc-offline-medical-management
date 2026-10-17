/** Shared Settings chrome matching Tk LabelFrames / fields / save buttons. */

export function Field({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <label className="settings-field">
      <span>{label}</span>
      {children}
    </label>
  )
}

export function Check({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
}) {
  return (
    <label className="settings-field-check" style={disabled ? { opacity: 0.55 } : undefined}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  )
}

export function Frame({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <fieldset className="settings-labelframe">
      <legend>{title}</legend>
      {children}
    </fieldset>
  )
}

export function SaveBtn({
  label,
  saving,
  onClick,
  disabled,
  navOrder,
  navChain,
  navAction,
}: {
  label: string
  saving?: boolean
  onClick: () => void
  disabled?: boolean
  navOrder?: number | string
  navChain?: string
  /** When set, Enter on this button activates it (see keyboard.ts). */
  navAction?: string
}) {
  return (
    <button
      type="button"
      className="settings-action-btn"
      disabled={Boolean(saving || disabled)}
      onClick={onClick}
      data-nav-order={navOrder}
      data-nav-chain={navChain}
      data-nav-action={navAction}
    >
      {label}
    </button>
  )
}

export function Note({ children }: { children: React.ReactNode }) {
  return <p className="settings-note">{children}</p>
}

export function PanelTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="settings-panel-title">{children}</h3>
}

/** A long explanation folded under "How this works" (owner, 18 Oct 2026: walls of help text). */
export function Help({
  title = 'How this works',
  children,
}: {
  title?: string
  children: React.ReactNode
}) {
  return (
    <details className="settings-help">
      <summary>{title}</summary>
      <div>{children}</div>
    </details>
  )
}

/** The panel's main Save, kept in sight at the bottom, with what the last save said. */
export function SaveBar({
  label,
  saving,
  onClick,
  disabled,
  msg,
  err,
}: {
  label: string
  saving?: boolean
  onClick: () => void
  disabled?: boolean
  msg?: string
  err?: string
}) {
  return (
    <div className="settings-savebar">
      <SaveBtn label={saving ? 'Saving…' : label} saving={saving} onClick={onClick} disabled={disabled} />
      {err ? (
        <span className="settings-savebar-msg err">{err}</span>
      ) : msg ? (
        <span className="settings-savebar-msg ok">{msg}</span>
      ) : null}
    </div>
  )
}
