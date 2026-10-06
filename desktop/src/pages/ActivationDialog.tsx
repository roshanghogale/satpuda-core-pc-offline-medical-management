import { useEffect, useMemo, useState } from 'react'
import {
  ActivationError,
  activateLicense,
  provisionTrial,
  pairWithStoreKey,
  recheckLicense,
  brandCardUrl,
  brandIconUrl,
  brandLogoUrl,
  fetchLicenseStatus,
  type LicenseStatusResponse,
} from '../api'
import { storeNameProblem } from '../storeName'

type Props = {
  onActivated: () => void
}

// 'storeKey' is "I already have a shop": the SC- key, and nothing else.
//
// That way in was missing entirely, and its absence is why reaching a store the
// owner made by hand in the admin panel needed the long three-factor form —
// whose Online step signed in as the vendor administrator with a password
// compiled into the build, and then matched stores by display NAME. Pairing
// with the key carries no credential and can only ever reach the one store the
// key belongs to.
type Overlay = 'admin' | 'deviceKey' | 'androidKey' | 'storeKey' | null

const ADMIN_USER = 'satpudacore'
const ADMIN_PASS = 'satpudacore'

const FEATURES = [
  { short: 'Billing', label: 'Easy Billing' },
  { short: 'Inventory', label: 'Smart Inventory' },
  { short: 'Medicines', label: 'Medicine Mgmt' },
  { short: 'Reports', label: 'Insightful Reports' },
]

export function ActivationDialog({ onActivated }: Props) {
  const [status, setStatus] = useState<LicenseStatusResponse | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [deviceKey, setDeviceKey] = useState('')
  const [storeName, setStoreName] = useState('')
  const [syncMode, setSyncMode] = useState<'online' | 'offline'>('offline')
  // The second of the two questions. Kept apart from `syncMode` above, which
  // belongs to the long form and is seeded from what this PC is already set to:
  // a fresh install has no mode worth inheriting (the stored default is Offline
  // before anyone has chosen anything), and the answer a new shop should be
  // offered first is the one that puts its books on the server.
  const [setupMode, setSetupMode] = useState<'online' | 'offline'>('online')
  const [mode, setMode] = useState<'activate' | 'add_store'>('activate')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [shake, setShake] = useState(false)
  const [overlay, setOverlay] = useState<Overlay>(null)
  const [adminUser, setAdminUser] = useState('')
  const [adminPass, setAdminPass] = useState('')
  const [adminError, setAdminError] = useState('')
  const [androidKey, setAndroidKey] = useState('')
  const [androidStore, setAndroidStore] = useState('')
  const [serverNote, setServerNote] = useState('')
  const [licenseActivation, setLicenseActivation] = useState('')
  const [licenseExpiry, setLicenseExpiry] = useState('')
  // TWO QUESTIONS. A computer that has never been activated answers the shop
  // name and Online or Offline, and presses one button. Everything else the
  // machine knows about itself, or the vendor already knows.
  //
  // The long form is still one click away and unchanged, because three things
  // still need it: an existing shop reconnecting a replaced PC, a satellite
  // device restoring a store from Drive, and a shop the vendor is re-activating
  // after an expiry. None of those is a fresh install, and the screen sends
  // them there by itself (see the status read below).
  const [simple, setSimple] = useState(true)
  // The SC- key path. `pairKeyStore` is the shop name the server read back off
  // the key; nothing local has moved until the person confirms it.
  const [pairKey, setPairKey] = useState('')
  const [pairKeyStore, setPairKeyStore] = useState('')
  const [trialDays, setTrialDays] = useState(3)
  // The shop name is already on the server: almost always this very shop,
  // reinstalled. Ask for its SC- key; a new shop only when he says so.
  const [nameTaken, setNameTaken] = useState(false)
  const [logoSrc, setLogoSrc] = useState(brandLogoUrl())
  const [cardSrc, setCardSrc] = useState(brandCardUrl())
  const [showCard, setShowCard] = useState(true)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const s = await fetchLicenseStatus()
        if (cancelled) return
        setStatus(s)
        if (s.store_name) setStoreName(s.store_name)
        if (s.sync_mode === 'online' || s.sync_mode === 'offline') {
          setSyncMode(s.sync_mode)
        }
        // A PC that already has stores, or one being asked to re-activate after
        // expiry, is not a fresh install -- give it the full form straight away
        // rather than offering it a new trial it must not take.
        if (s.has_registry || s.expiry_reactivation) setSimple(false)
        if (s.provision_error) {
          setError(s.provision_error)
          // A refused sign-up must not become a form to fill in again. Both
          // answers the installer collected come back with the refusal, and this
          // is a fresh PC -- nothing was created -- so the two-question form is
          // still right, with both questions already answered.
          setSimple(true)
          if (s.provision_code === 'name_exists') setNameTaken(true)
          if (s.provision_store_name) setStoreName(s.provision_store_name)
          if (s.provision_sync_mode === 'online' || s.provision_sync_mode === 'offline') {
            setSetupMode(s.provision_sync_mode)
          }
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
        }
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  const hint = useMemo(() => {
    if (mode === 'add_store') {
      return 'Add store: enter the exact store name from the admin device. The latest Drive backup will be restored.'
    }
    if (status?.has_registry) {
      return "Same store name as before → loads that store's data. New name → creates an empty store. Leave blank to keep current."
    }
    return 'First activation: enter your initial store name (becomes the backup folder name).'
  }, [mode, status?.has_registry])

  const triggerShake = () => {
    setShake(true)
    window.setTimeout(() => setShake(false), 420)
  }

  const finish = () => {
    onActivated()
  }

  const submit = async () => {
    setError('')
    setBusy(true)
    try {
      const result = await activateLicense({
        username: username.trim(),
        password: password.trim(),
        device_key: deviceKey.trim(),
        store_name: storeName.trim() || undefined,
        sync_mode: syncMode,
        mode,
      })
      const note = String(result.server_note || '').trim()
      setLicenseActivation(String(result.activation_date || ''))
      setLicenseExpiry(String(result.expiry_date || ''))
      if (result.show_key && result.android_key) {
        setAndroidKey(result.android_key)
        setAndroidStore(result.store_name || storeName.trim())
        setServerNote(note)
        setOverlay('androidKey')
        return
      }
      if (syncMode === 'online' && note) {
        setServerNote(note)
        setAndroidKey('')
        setAndroidStore(result.store_name || storeName.trim())
        setOverlay('androidKey')
        return
      }
      finish()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      triggerShake()
    } finally {
      setBusy(false)
    }
  }

  const submitTrial = async (confirmNew = false) => {
    const name = storeName.trim()
    // Checked here in the server's own words, so a name it would refuse is
    // refused now rather than after a round trip the shopkeeper watches.
    const problem = storeNameProblem(name)
    if (problem) {
      setError(problem)
      triggerShake()
      return
    }
    setError('')
    setNameTaken(false)
    setBusy(true)
    try {
      const result = await provisionTrial(name, setupMode, confirmNew)
      setTrialDays(Number(result.trial_days) || trialDays)
      setLicenseActivation(String(result.activation_date || ''))
      setLicenseExpiry(String(result.expiry_date || ''))
      // The SC- key is what an Android phone needs to join an ONLINE store, so
      // it is only worth stopping for when Online was chosen. An Offline shop
      // that could not reach the server still gets its sentence -- it activated,
      // but the owner's Trials page does not know about it yet.
      if (result.show_key && result.android_key) {
        setAndroidKey(result.android_key)
        setAndroidStore(result.store_name || name)
        setServerNote('')
        setOverlay('androidKey')
        return
      }
      const note = String(result.server_note || '').trim()
      if (note) {
        setAndroidKey('')
        setAndroidStore(result.store_name || name)
        setServerNote(note)
        setOverlay('androidKey')
        return
      }
      finish()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      if (e instanceof ActivationError && e.code === 'name_exists') {
        setNameTaken(true)
      } else {
        triggerShake()
      }
    } finally {
      setBusy(false)
    }
  }

  // THE SC- KEY PATH, in two presses.
  //
  // The first reads the key back and shows whose shop it is; nothing on this
  // computer moves. The second does the work. A key is not a name: a typo
  // cannot land on somebody else's shop, it is simply refused.
  const lookUpKey = async () => {
    const key = pairKey.trim()
    if (!key) {
      setError("Enter the shop's SC- key.")
      triggerShake()
      return
    }
    setError('')
    setBusy(true)
    try {
      const result = await pairWithStoreKey(key, false)
      if (result.ok === false) {
        setError(String(result.error || 'That key did not match a shop.'))
        triggerShake()
        return
      }
      setPairKeyStore(String(result.store_name || ''))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      triggerShake()
    } finally {
      setBusy(false)
    }
  }

  const confirmKey = async () => {
    setError('')
    setBusy(true)
    try {
      const result = await pairWithStoreKey(pairKey.trim(), true)
      if (result.ok === false) {
        setError(String(result.error || 'Could not connect to that shop.'))
        triggerShake()
        return
      }
      setLicenseActivation(String(result.activation_date || ''))
      setLicenseExpiry(String(result.expiry_date || ''))
      const note = String(result.server_note || '').trim()
      if (note) {
        setAndroidKey('')
        setAndroidStore(String(result.store_name || ''))
        setServerNote(note)
        setOverlay('androidKey')
        return
      }
      finish()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      triggerShake()
    } finally {
      setBusy(false)
    }
  }

  const openAdminKey = () => {
    setAdminError('')
    if (adminUser.trim() !== ADMIN_USER || adminPass !== ADMIN_PASS) {
      setAdminError('Invalid administrator credentials.')
      return
    }
    if (!status?.device_key) {
      setAdminError('Device key could not be loaded.')
      return
    }
    setOverlay('deviceKey')
  }

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="activation-screen" role="presentation">
      <div
        className={`activation-shell${shake ? ' is-shake' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label="Software activation"
      >
        <aside className="activation-left">
          <img
            className="activation-logo"
            src={logoSrc}
            alt="Satpuda Core Private Limited"
            onError={() => setLogoSrc(brandIconUrl())}
          />
          <p className="activation-tagline">
            Medical Management Software · Satpuda Core Private Limited
          </p>
          <div className="activation-pill">
            Medical management for modern pharmacies
          </div>
          <div className="activation-features">
            {FEATURES.map((f) => (
              <div key={f.short} className="activation-feature">
                <span className="activation-feature-bar" />
                <strong>{f.short}</strong>
                <span>{f.label}</span>
              </div>
            ))}
          </div>
          {showCard ? (
            <img
              className="activation-card-img"
              src={cardSrc}
              alt="Satpuda Core visiting card"
              onError={() => {
                setShowCard(false)
                setCardSrc('')
              }}
            />
          ) : (
            <div className="activation-card-fallback">
              Satpuda Core Private Limited
            </div>
          )}
          <div className="activation-dots" aria-hidden="true">
            {Array.from({ length: 36 }, (_, i) => (
              <span key={i} />
            ))}
          </div>
        </aside>

        <div className="activation-right">
          <div className="activation-shield" aria-hidden="true">
            <svg viewBox="0 0 56 56" width="56" height="56">
              <circle cx="28" cy="28" r="25" fill="#dbeafe" stroke="#2563eb" strokeWidth="2" />
              <path
                d="M20 26h16v12H20z"
                fill="none"
                stroke="#1e293b"
                strokeWidth="2"
              />
              <path
                d="M24 26v-5a4 4 0 0 1 8 0v5"
                fill="none"
                stroke="#1e293b"
                strokeWidth="2"
              />
              <circle cx="28" cy="34" r="2" fill="#1e293b" />
            </svg>
          </div>
          <h1 className="activation-title">
            {simple ? <>Set <span>Up</span></> : <>Software <span>Activation</span></>}
          </h1>
          <p className="activation-lead">
            {simple ? (
              <>
                Two questions, then press Start.
                <br />
                Nothing else is needed.
              </>
            ) : (
              <>
                This device is not activated.
                <br />
                Enter your credentials to continue.
              </>
            )}
          </p>
          {status?.expiry_reactivation ? (
            <p className="activation-expiry">License expired — re-activation required.</p>
          ) : null}

          {simple ? (
            <>
              <label className="activation-field">
                <span>Shop Name</span>
                <div className="activation-input-wrap">
                  <span className="activation-ico" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="16" height="16">
                      <path
                        d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-5v-6H10v6H5a1 1 0 0 1-1-1z"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.6"
                      />
                    </svg>
                  </span>
                  <input
                    value={storeName}
                    onChange={(e) => setStoreName(e.target.value)}
                    placeholder="e.g. Roshan Medical"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void submitTrial()
                    }}
                  />
                </div>
              </label>

              <fieldset className="activation-sync">
                <legend>Where should your data be kept?</legend>
                <div className="activation-sync-row">
                  <label>
                    <input
                      type="radio"
                      name="setup_mode"
                      checked={setupMode === 'online'}
                      onChange={() => setSetupMode('online')}
                    />
                    Online (Server)
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="setup_mode"
                      checked={setupMode === 'offline'}
                      onChange={() => setSetupMode('offline')}
                    />
                    Offline (This PC)
                  </label>
                </div>
              </fieldset>

              <p className="activation-hint">
                {setupMode === 'online' ? (
                  <>
                    Sets up your shop on the Satpuda server, open for{' '}
                    {trialDays} days to start; Satpuda then activates it. Your
                    bills and stock are kept on the server from the first bill,
                    and the Android app can share the same shop.
                  </>
                ) : (
                  <>
                    Your bills and stock stay on this computer, with Google Drive
                    backup. Works without internet. You can switch to Online later
                    from Settings.
                  </>
                )}
              </p>
              {error ? <p className="activation-error">{error}</p> : null}

              {nameTaken ? (
                <div className="activation-actions" style={{ flexDirection: 'column', gap: 8 }}>
                  <button
                    type="button"
                    className="activation-submit"
                    disabled={busy}
                    onClick={() => {
                      setError('')
                      setNameTaken(false)
                      setOverlay('storeKey')
                    }}
                  >
                    It is my shop: connect with its SC- key  →
                  </button>
                  <button
                    type="button"
                    className="activation-ghost"
                    disabled={busy}
                    onClick={() => void submitTrial(true)}
                  >
                    {busy ? 'Setting up…' : 'No, this is a new shop: set it up'}
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="activation-submit"
                  disabled={busy}
                  onClick={() => void submitTrial()}
                >
                  {busy ? 'Setting up…' : 'Start  →'}
                </button>
              )}

              <div className="activation-actions">
                <button
                  type="button"
                  className="activation-ghost activation-ghost-blue"
                  disabled={busy}
                  onClick={() => {
                    setError('')
                    setPairKey('')
                    setPairKeyStore('')
                    setOverlay('storeKey')
                  }}
                >
                  I already have a shop
                </button>
                <button
                  type="button"
                  className="activation-ghost"
                  disabled={busy}
                  onClick={() => {
                    setError('')
                    setSimple(false)
                  }}
                >
                  I already have an account
                </button>
              </div>
            </>
          ) : (
          <>
          <label className="activation-field">
            <span>Username</span>
            <div className="activation-input-wrap">
              <span className="activation-ico" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="16" height="16">
                  <circle cx="12" cy="8" r="3.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
                  <path
                    d="M5 19c1.5-3.5 4-5 7-5s5.5 1.5 7 5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                  />
                </svg>
              </span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter your username"
                autoComplete="username"
                autoFocus
              />
            </div>
          </label>

          <label className="activation-field">
            <span>Password</span>
            <div className="activation-input-wrap">
              <span className="activation-ico" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="16" height="16">
                  <rect x="6" y="11" width="12" height="9" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
                  <path d="M9 11V8a3 3 0 0 1 6 0v3" fill="none" stroke="currentColor" strokeWidth="1.6" />
                </svg>
              </span>
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                autoComplete="current-password"
              />
              <button
                type="button"
                className="activation-eye"
                onClick={() => setShowPassword((v) => !v)}
              >
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
          </label>

          <label className="activation-field">
            <span>Device Key</span>
            <div className="activation-input-wrap">
              <span className="activation-ico activation-ico-key" aria-hidden="true">
                KEY
              </span>
              <input
                value={deviceKey}
                onChange={(e) => setDeviceKey(e.target.value)}
                placeholder="Enter your device key"
                spellCheck={false}
                autoComplete="off"
              />
            </div>
          </label>

          <label className="activation-field">
            <span>Store Name</span>
            <div className="activation-input-wrap">
              <span className="activation-ico" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="16" height="16">
                  <path
                    d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-5v-6H10v6H5a1 1 0 0 1-1-1z"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                  />
                </svg>
              </span>
              <input
                value={storeName}
                onChange={(e) => setStoreName(e.target.value)}
                placeholder="Enter initial store name"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void submit()
                }}
              />
            </div>
          </label>

          <fieldset className="activation-sync">
            <legend>Sync Mode</legend>
            <div className="activation-sync-row">
              <label>
                <input
                  type="radio"
                  name="sync_mode"
                  checked={syncMode === 'offline'}
                  onChange={() => setSyncMode('offline')}
                />
                Offline (Drive)
              </label>
              <label>
                <input
                  type="radio"
                  name="sync_mode"
                  checked={syncMode === 'online'}
                  onChange={() => setSyncMode('online')}
                />
                Online (Server)
              </label>
            </div>
            <p>
              Online: data syncs via Satpuda Core Server. Copy the SC- Android
              key from Settings after activation.
            </p>
          </fieldset>

          <p className="activation-hint">{hint}</p>
          {error ? <p className="activation-error">{error}</p> : null}

          <button
            type="button"
            className="activation-submit"
            disabled={busy}
            onClick={() => void submit()}
          >
            {busy
              ? syncMode === 'online'
                ? 'Activating — Online setup…'
                : 'Activating…'
              : mode === 'add_store'
                ? 'Connect & Restore Store  →'
                : 'Activate Software  →'}
          </button>

          <div className="activation-actions">
            {/* The shop this path exists for lands HERE, not on the two-question
                screen: a lapsed starter window and an expired licence both send
                the status read to the full form. Without this button the one
                way out -- the shop's own SC- key, which the engine accepts --
                could not be typed anywhere. */}
            <button
              type="button"
              className="activation-ghost activation-ghost-blue"
              disabled={busy}
              onClick={() => {
                setError('')
                setPairKey('')
                setPairKeyStore('')
                setOverlay('storeKey')
              }}
            >
              I already have a shop
            </button>
            <button
              type="button"
              className="activation-ghost activation-ghost-blue"
              disabled={busy}
              onClick={() => setMode('add_store')}
            >
              Add Store (Drive)
            </button>
            <button
              type="button"
              className="activation-ghost"
              disabled={busy}
              onClick={() => {
                setAdminUser('')
                setAdminPass('')
                setAdminError('')
                setOverlay('admin')
              }}
            >
              Administrator
            </button>
          </div>
          {/* A PC that has a store, or one being re-activated after an expiry,
              belongs on this form and is sent here by the status read -- there
              is nothing behind the two questions for it, so no way back is
              offered. Anyone else arrived by clicking, and can leave the same
              way. */}
          {!status?.has_registry && !status?.expiry_reactivation ? (
            <div className="activation-actions activation-actions-one">
              <button
                type="button"
                className="activation-ghost"
                disabled={busy}
                onClick={() => {
                  setError('')
                  setSimple(true)
                }}
              >
                ←  Back to setup
              </button>
            </div>
          ) : null}
          </>
          )}
          <p className="activation-secure">Your data is secure and encrypted</p>
        </div>
      </div>

      {overlay === 'admin' ? (
        <div className="activation-overlay" role="dialog" aria-modal="true" aria-label="Administrator login">
          <div className="activation-mini">
            <h2>Administrator Login</h2>
            <label className="activation-field">
              <span>Username</span>
              <input
                className="activation-plain"
                value={adminUser}
                onChange={(e) => setAdminUser(e.target.value)}
                autoFocus
              />
            </label>
            <label className="activation-field">
              <span>Password</span>
              <input
                className="activation-plain"
                type="password"
                value={adminPass}
                onChange={(e) => setAdminPass(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') openAdminKey()
                }}
              />
            </label>
            {adminError ? <p className="activation-error">{adminError}</p> : null}
            <div className="activation-mini-actions">
              <button type="button" className="activation-submit" onClick={openAdminKey}>
                Login
              </button>
              <button type="button" className="activation-ghost" onClick={() => setOverlay(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {overlay === 'storeKey' ? (
        <div
          className="activation-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Connect to an existing shop"
        >
          <div className="activation-mini activation-mini-wide">
            <h2>I already have a shop</h2>
            <p className="activation-hint">
              Type the shop's SC- key. It is on the Satpuda admin panel next to
              the shop, and on any device already connected to it under
              Settings → Database → Administrator → Android Store Connection Key.
            </p>
            <input
              className="activation-plain"
              value={pairKey}
              autoFocus
              spellCheck={false}
              autoComplete="off"
              placeholder="e.g. SC-A1B2C3D4"
              onChange={(e) => {
                setPairKey(e.target.value)
                setPairKeyStore('')
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void (pairKeyStore ? confirmKey() : lookUpKey())
              }}
            />
            {pairKeyStore ? (
              <p className="activation-hint">
                This key belongs to <strong>{pairKeyStore}</strong>.
                <br />
                Connect this computer to that shop? Its bills and stock will be
                kept on the Satpuda server (Online mode).
              </p>
            ) : null}
            {error ? <p className="activation-error">{error}</p> : null}
            <div className="activation-mini-actions">
              {pairKeyStore ? (
                <button
                  type="button"
                  className="activation-submit"
                  disabled={busy}
                  onClick={() => void confirmKey()}
                >
                  {busy ? 'Connecting…' : `Yes, connect to ${pairKeyStore}`}
                </button>
              ) : (
                <button
                  type="button"
                  className="activation-submit"
                  disabled={busy || !pairKey.trim()}
                  onClick={() => void lookUpKey()}
                >
                  {busy ? 'Checking…' : 'Check this key  →'}
                </button>
              )}
              <button
                type="button"
                className="activation-ghost"
                disabled={busy}
                onClick={() => {
                  setError('')
                  setOverlay(null)
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {overlay === 'deviceKey' ? (
        <div className="activation-overlay" role="dialog" aria-modal="true" aria-label="Device key">
          <div className="activation-mini activation-mini-wide">
            <h2>Device Key</h2>
            <p className="activation-hint">Use Copy to paste directly into the activation field.</p>
            <textarea
              className="activation-keybox"
              readOnly
              value={status?.device_key || ''}
              rows={4}
            />
            <div className="activation-mini-actions">
              <button
                type="button"
                className="activation-submit"
                onClick={() => {
                  const key = status?.device_key || ''
                  void copyText(key)
                  setDeviceKey(key)
                }}
              >
                Copy Device Key
              </button>
              <button type="button" className="activation-ghost" onClick={() => setOverlay(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {overlay === 'androidKey' ? (
        <div className="activation-overlay" role="dialog" aria-modal="true" aria-label="Android store connection key">
          <div className="activation-mini activation-mini-wide">
            <h2>Android Store Connection Key</h2>
            <p className="activation-hint">
              Store: {androidStore || '—'}
              <br />
              On Android Satpuda Core activation enter the SAME store name and
              paste this key.
              <br />
              Also available later: Settings → Database → Administrator → Android
              Store Connection Key.
            </p>
            {androidKey ? (
              <input className="activation-plain activation-key-display" readOnly value={androidKey} />
            ) : (
              <p className="activation-error">
                {serverNote ||
                  'Store was activated locally, but the server store could not be created yet.'}
              </p>
            )}
            {licenseActivation || licenseExpiry ? (
              <p className="activation-hint">
                Server licence: activated {licenseActivation || '—'}
                {licenseExpiry
                  ? ` · expires ${licenseExpiry} (10 days from first activation)`
                  : ''}
                .
              </p>
            ) : null}
            {androidKey && serverNote ? (
              <p className="activation-hint">{serverNote}</p>
            ) : null}
            <div className="activation-mini-actions">
              {androidKey ? (
                <button
                  type="button"
                  className="activation-submit"
                  onClick={() => void copyText(androidKey)}
                >
                  Copy Key
                </button>
              ) : null}
              <button type="button" className="activation-ghost" onClick={finish}>
                Continue
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

/**
 * Two different problems, and they must not wear the same sentence.
 *
 *   * the licence has EXPIRED, or the store was switched off -- the shop waits
 *     for the administrator to extend the date and reopens;
 *   * the licence FILE is missing or does not verify -- the shop is not out of
 *     time at all, and one internet connection fetches the same licence back.
 *
 * Telling the second shopkeeper that his licence expired sends him to the phone
 * for something he can fix by plugging in a cable.
 */
export function LicenseAccessBlockedDialog({
  needsInternet = false,
  message = '',
  onRecovered,
}: {
  needsInternet?: boolean
  message?: string
  /** Called when a re-check finds the shop is no longer blocked. */
  onRecovered?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [showKeyForm, setShowKeyForm] = useState(false)
  const [key, setKey] = useState('')
  const [keyStore, setKeyStore] = useState('')

  // KEEP LOOKING WHILE BLOCKED.
  //
  // The self-healing re-check in App.tsx was wired to the activation screen
  // only, so this one — the screen a shop with no licence actually lands on —
  // never recovered by itself. A shopkeeper who plugged the network in saw
  // nothing happen, and the desktop shell has no reload binding, so the only
  // way out was quitting the app. Every few seconds is cheap: the engine's
  // licence fetch rate-limits itself to once a minute.
  useEffect(() => {
    let cancelled = false
    const id = window.setInterval(async () => {
      try {
        const lic = await fetchLicenseStatus()
        if (cancelled) return
        if (!lic.access_blocked) {
          onRecovered ? onRecovered() : window.location.reload()
        }
      } catch {
        /* engine not up yet — keep waiting */
      }
    }, 5000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [onRecovered])

  const tryAgain = async () => {
    setBusy(true)
    setNote('')
    try {
      const lic = await recheckLicense()
      if (!lic.access_blocked) {
        onRecovered ? onRecovered() : window.location.reload()
        return
      }
      setNote(
        String(lic.seal_message || '') ||
          'Still no licence. Check that this computer is on the internet, then try again.',
      )
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const lookUp = async () => {
    setBusy(true)
    setNote('')
    try {
      const res = await pairWithStoreKey(key.trim(), false)
      if (res.ok === false) {
        setNote(String(res.error || 'That key did not match a shop.'))
        return
      }
      setKeyStore(String(res.store_name || ''))
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const connect = async () => {
    setBusy(true)
    setNote('')
    try {
      const res = await pairWithStoreKey(key.trim(), true)
      if (res.ok === false) {
        setNote(String(res.error || 'Could not connect to that shop.'))
        return
      }
      onRecovered ? onRecovered() : window.location.reload()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="activation-screen" role="presentation">
      <div
        className="activation-mini activation-mini-wide"
        role="alertdialog"
        aria-modal="true"
        aria-label={needsInternet ? 'Licence not found' : 'Access blocked'}
      >
        <h2>{needsInternet ? 'Licence not found' : 'Access blocked'}</h2>
        <p className="activation-hint" style={{ textAlign: 'left', fontSize: 13, color: '#1e293b' }}>
          {needsInternet ? (
            <>
              {message ||
                'This computer needs to be connected to the internet once so Satpuda can restore its licence.'}
              <br />
              <br />
              Nothing has been lost — the shop's records are untouched. Connect
              the internet and press Try again now; this screen also checks by
              itself every few seconds.
            </>
          ) : (
            <>
              {message || 'Store license expired or access disabled.'}
              <br />
              <br />
              Contact the administrator to extend the expiry date.
              <br />
              You do not need to re-activate — this screen reopens the shop by
              itself once the licence is extended.
            </>
          )}
        </p>
        {note ? <p className="activation-error">{note}</p> : null}
        {showKeyForm ? (
          <>
            <p className="activation-hint" style={{ textAlign: 'left' }}>
              If this computer belongs to a shop that is already on the Satpuda
              server, type that shop's SC- key and it will fetch the licence
              straight away.
            </p>
            <input
              className="activation-plain"
              value={key}
              autoFocus
              spellCheck={false}
              autoComplete="off"
              placeholder="e.g. SC-A1B2C3D4"
              onChange={(e) => {
                setKey(e.target.value)
                setKeyStore('')
              }}
            />
            {keyStore ? (
              <p className="activation-hint">
                This key belongs to <strong>{keyStore}</strong>. Connect this
                computer to that shop?
              </p>
            ) : null}
          </>
        ) : null}
        <div className="activation-mini-actions">
          <button
            type="button"
            className="activation-submit"
            disabled={busy}
            onClick={() => void tryAgain()}
          >
            {busy ? 'Checking…' : 'Try again now'}
          </button>
          {showKeyForm ? (
            <button
              type="button"
              className="activation-ghost"
              disabled={busy || !key.trim()}
              onClick={() => void (keyStore ? connect() : lookUp())}
            >
              {keyStore ? `Connect to ${keyStore}` : 'Check this key'}
            </button>
          ) : (
            <button
              type="button"
              className="activation-ghost"
              disabled={busy}
              onClick={() => {
                setNote('')
                setShowKeyForm(true)
              }}
            >
              Enter my shop's SC- key
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
