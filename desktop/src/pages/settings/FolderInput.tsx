/** A folder path box with a folder button that opens the native folder picker
 *  (owner, 19 Oct 2026). Without the desktop shell the button is not shown and
 *  the path is typed as before. */
import { useState } from 'react'
import { canPickFolder, pickFolder } from '../../folderPicker'

export function FolderInput({
  value,
  onChange,
  title = 'Choose a folder',
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  title?: string
  placeholder?: string
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const canPick = canPickFolder()

  const browse = async () => {
    setErr('')
    setBusy(true)
    try {
      const chosen = await pickFolder({ startAt: value, title })
      if (chosen) onChange(chosen)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="prefs-folder">
      <span className="prefs-folder-row">
        <input
          className="settings-input"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
        {canPick ? (
          <button
            type="button"
            className="prefs-folder-btn"
            title={title}
            aria-label={title}
            disabled={busy}
            onClick={(e) => {
              e.preventDefault()
              void browse()
            }}
          >
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path
                d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.38a1.5 1.5 0 0 1 1.06.44L11.5 7h8A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"
                fill="currentColor"
                opacity="0.85"
              />
            </svg>
          </button>
        ) : null}
      </span>
      {err ? <span className="prefs-folder-err">{err}</span> : null}
    </span>
  )
}
