import { useEffect, useState } from 'react'
import { fetchAppLoginStatus, verifyAppLogin } from '../api'

type Props = {
  onAuthenticated: () => void
}

export function AppLoginDialog({ onAuthenticated }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    void fetchAppLoginStatus().then((s) => {
      if (!cancelled && s.username_hint) setUsername(s.username_hint)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const submit = async () => {
    setError('')
    setBusy(true)
    try {
      await verifyAppLogin(username.trim(), password)
      sessionStorage.setItem('app_login_ok', '1')
      onAuthenticated()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop activation-backdrop" role="presentation">
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label="App login"
      >
        <div className="modal-head">
          <h2>App Login</h2>
        </div>
        <div className="modal-body">
          <p className="note">Enter the shop login configured in Settings → Pharmacy.</p>
          <label className="field-block">
            <span>Username</span>
            <input
              className="settings-input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
          </label>
          <label className="field-block">
            <span>Password</span>
            <input
              className="settings-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submit()
              }}
            />
          </label>
          {error ? <p className="error">{error}</p> : null}
        </div>
        <div className="modal-foot">
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => void submit()}
          >
            {busy ? 'Checking…' : 'Login'}
          </button>
        </div>
      </div>
    </div>
  )
}
