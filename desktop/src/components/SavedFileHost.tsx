import { useEffect, useState } from 'react'
import {
  FILE_SAVED_EVENT,
  openSavedFile,
  showSavedFileInFolder,
  type SavedFile,
} from '../savedFiles'

/** "Saved: <file>" with Open and Show in folder, for every report, export and PDF. */
export function SavedFileHost() {
  const [file, setFile] = useState<SavedFile | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    const onSaved = (e: Event) => {
      const detail = (e as CustomEvent<SavedFile>).detail
      if (detail?.path) {
        setErr('')
        setFile(detail)
      }
    }
    window.addEventListener(FILE_SAVED_EVENT, onSaved)
    return () => window.removeEventListener(FILE_SAVED_EVENT, onSaved)
  }, [])

  useEffect(() => {
    if (!file) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === 'Enter') {
        e.preventDefault()
        e.stopPropagation()
        setFile(null)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [file])

  if (!file) return null
  const name = file.file_name || file.path.replace(/^.*[\\/]/, '')
  const folder = file.folder || file.path.replace(/[\\/][^\\/]*$/, '')

  const act = async (fn: (p: string) => Promise<{ ok: boolean; error?: string }>) => {
    setErr('')
    try {
      const res = await fn(file.path)
      if (res.ok === false) setErr(res.error || 'Could not do that.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={() => setFile(null)}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label="File saved"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 560 }}
      >
        <div className="modal-head">
          <h2>File saved</h2>
          <button type="button" className="icon-btn" onClick={() => setFile(null)}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          {file.message && !file.message.startsWith('Saved to:') ? (
            <p style={{ marginTop: 0 }}>{file.message}</p>
          ) : null}
          <p style={{ margin: '0 0 4px', fontWeight: 700, wordBreak: 'break-all' }}>{name}</p>
          <p className="muted" style={{ margin: 0, fontSize: 12, wordBreak: 'break-all' }}>
            Folder: {folder}
          </p>
          {err ? (
            <p className="muted printall-warn" style={{ marginBottom: 0 }}>
              {err}
            </p>
          ) : null}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-neutral" onClick={() => setFile(null)}>
            Close
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => void act(showSavedFileInFolder)}
          >
            Show in folder
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void act(openSavedFile)}>
            Open
          </button>
        </div>
      </div>
    </div>
  )
}
