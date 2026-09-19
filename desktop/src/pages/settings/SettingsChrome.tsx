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
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="settings-field-check">
      <input
        type="checkbox"
        checked={checked}
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
