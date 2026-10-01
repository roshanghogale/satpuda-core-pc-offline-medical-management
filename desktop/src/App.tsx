import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchMeta,
  fetchPrefs,
  fetchLicenseStatus,
  fetchAppLoginStatus,
  fetchOnlineMigrateStatus,
  type DesktopUiPrefs,
  type MetaResponse,
} from './api'
import { useSyncRefreshPoller } from './hooks/useSyncRefreshPoller'
import { systemAction } from './settingsApi'
import { ensureLocalEngine } from './backend'
import { IS_DEMO } from './demoMode'
import {
  fetchStartupAlerts,
  loadLastSale,
  printSalesBill,
  type BulkPurchasePrefill,
  type ReorderPrefill,
  type StartupAlertTab,
} from './pagesApi'
import type { SettingsTabId } from './pages/settings/settingsConfig'
import {
  PAGE_NAV,
  bindArrowFocusNav,
  bindEnterFocusChain,
  bindEscapeBlur,
  bindPageShortcuts,
  type PageId,
} from './keyboard'
import { HomePage } from './pages/HomePage'
import { InventoryPage } from './pages/InventoryPage'
import { PurchaseHistoryPage } from './pages/PurchaseHistoryPage'
import { PurchasePage } from './pages/PurchasePage'
import { GeneralProductsPage } from './pages/GeneralProductsPage'
import { ReturnsPage } from './pages/ReturnsPage'
import { SalesHistoryPage } from './pages/SalesHistoryPage'
import { SalesPage } from './pages/SalesPage'
import { SettingsPage } from './pages/SettingsPage'
import { StartupAlertsDialog } from './pages/StartupAlertsDialog'
import { StartupSplashOverlay } from './components/StartupSplashOverlay'
import {
  ActivationDialog,
  LicenseAccessBlockedDialog,
} from './pages/ActivationDialog'
import { AppLoginDialog } from './pages/AppLoginDialog'
import { OnlineMigrateDialog } from './pages/OnlineMigrateDialog'
import {
  applyFontSize,
  applyTheme,
  applyThemePack,
  flipThemeVariant,
  loadStoredFontSize,
  loadStoredTheme,
  loadStoredThemePack,
  normalizeTheme,
  normalizeThemePack,
  persistTheme,
  syncThemeFromDisk,
  themeVariantLabel,
  type ThemePack,
} from './theme'
import { restartDesktopApp } from './restartApp'
import { DesktopUiPrefsProvider } from './desktopUiPrefsContext'
import { VoiceBar } from './voice/VoiceBar'
import { useVoiceEnabled } from './voice/voiceEnabled'

const NAV_LABELS: Record<PageId, string> = {
  home: 'Home',
  sales: 'Sales',
  purchase: 'Purchase',
  inventory: 'Inventory',
  sales_history: 'Sales History',
  purchase_history: 'Purchase History',
  returns: 'Returns',
  payment: 'Payments',
  general_products: 'General Products',
  settings: 'Settings',
}

const DEFAULT_PREFS: DesktopUiPrefs = {
  show_nav_shortcut_keys: true,
  theme_pack: 'modern',
  table_text_marquee: false,
}

export type NavPayload = {
  saleId?: number
  purchaseId?: number
  reorderPrefill?: ReorderPrefill
  returnsTab?: 'sales' | 'purchase' | 'disposal' | 'bulk'
  returnsBulkPrefill?: BulkPurchasePrefill
  /** Sales -> Returns: open the sales-return tab with this saved bill loaded. */
  returnsSaleId?: number
  /** Open Returns -> Purchase with this saved return loaded for editing. */
  returnsEditReturnId?: number
  disposalPrefill?: {
    medicine_name: string
    batch_no: string
    expiry_date: string
    available_qty: number
  }
  settingsTab?: SettingsTabId
  settingsToggle?: string
  settingsSection?: string
  reorderMedicinePrefill?: {
    medicine_name: string
    pack_size: string
    quantity: number
    unit_price: number
  }
  bulkReorderLoad?: boolean
}

export type AppNavigate = (id: PageId, payload?: NavPayload) => void

/** Pages that scroll inside their own table instead of scrolling the shell.
 *  Driven by the ACTIVE page, never by what happens to be mounted. */
const LIST_PAGES = new Set<PageId>(['inventory', 'sales_history', 'purchase_history'])

export default function App() {
  const [page, setPage] = useState<PageId>('home')
  const voiceSwitch = useVoiceEnabled()
  const [visited, setVisited] = useState<Set<PageId>>(() => new Set(['home']))
  /** Bumped on store switch so keep-alive pages remount even if restart is delayed. */
  const [storeEpoch, setStoreEpoch] = useState(0)
  const [navPayload, setNavPayload] = useState<NavPayload>({})
  // Default product look: light navy (disk overrides after boot).
  const [theme, setTheme] = useState(() => loadStoredTheme() || 'navy-light')
  const [themePack, setThemePack] = useState<ThemePack>(loadStoredThemePack)
  const [meta, setMeta] = useState<MetaResponse | null>(null)

  // Repaint the Online/Offline chip and the store name whenever the mode is
  // saved, without waiting for a restart.
  useEffect(() => {
    const onModeChanged = () => {
      void (async () => {
        try {
          setMeta(await fetchMeta())
        } catch {
          /* the chip keeps what it has until the next successful read */
        }
      })()
    }
    window.addEventListener('satpuda:sync-mode-changed', onModeChanged)
    return () =>
      window.removeEventListener('satpuda:sync-mode-changed', onModeChanged)
  }, [])
  const [apiOk, setApiOk] = useState<boolean | null>(null)
  const [prefs, setPrefs] = useState<DesktopUiPrefs>(DEFAULT_PREFS)
  const [startupAlerts, setStartupAlerts] = useState<StartupAlertTab[] | null>(
    null,
  )
  const [startupAlertsTitle, setStartupAlertsTitle] = useState('Startup Alerts')
  // A failed alert read (Online: the store) used to be swallowed, and "no
  // popup" is exactly what a shop with a clean shelf sees. Now it says so.
  const [startupAlertsError, setStartupAlertsError] = useState('')
  const [alertRecheckMinutes, setAlertRecheckMinutes] = useState(0)
  const alertsOpenRef = useRef(false)
  useEffect(() => {
    alertsOpenRef.current =
      Boolean(startupAlerts?.length) || Boolean(startupAlertsError)
  }, [startupAlerts, startupAlertsError])
  // While the app runs: look again every N minutes (Settings -> Alert &
  // Monitoring -> Popup & Thresholds) and pop up ONLY rows not already shown
  // today. The engine enforces on/off, Skip Today and the interval itself.
  useEffect(() => {
    if (!alertRecheckMinutes || alertRecheckMinutes <= 0) return
    const id = window.setInterval(() => {
      if (alertsOpenRef.current) return
      void fetchStartupAlerts({ recheck: true })
        .then((a) => {
          if (a.ok && a.show && a.tabs?.length && !alertsOpenRef.current) {
            setStartupAlertsTitle('New Alerts')
            setStartupAlerts(a.tabs)
          }
        })
        .catch(() => {
          /* a background re-check stays quiet; the next one retries */
        })
    }, alertRecheckMinutes * 60_000)
    return () => window.clearInterval(id)
  }, [alertRecheckMinutes])
  useEffect(() => {
    const onShow = (e: Event) => {
      const d = (e as CustomEvent).detail || {}
      if (Array.isArray(d.tabs) && d.tabs.length) {
        setStartupAlertsError('')
        setStartupAlertsTitle(String(d.title || 'Startup Alerts'))
        setStartupAlerts(d.tabs as StartupAlertTab[])
      }
    }
    const onPrefs = (e: Event) => {
      const m = Number((e as CustomEvent).detail?.recheck_minutes)
      if (Number.isFinite(m)) setAlertRecheckMinutes(Math.max(0, m))
    }
    window.addEventListener('satpuda:show-startup-alerts', onShow)
    window.addEventListener('satpuda:alert-prefs-changed', onPrefs)
    return () => {
      window.removeEventListener('satpuda:show-startup-alerts', onShow)
      window.removeEventListener('satpuda:alert-prefs-changed', onPrefs)
    }
  }, [])
  const [splashOpen, setSplashOpen] = useState(true)
  const [splashStatus, setSplashStatus] = useState('Starting local engine…')
  const [licenseBlocked, setLicenseBlocked] = useState(false)
  const [accessBlocked, setAccessBlocked] = useState(false)
  // A lost licence file is not an expiry; the blocked screen says which.
  const [needsInternet, setNeedsInternet] = useState(false)
  const [sealMessage, setSealMessage] = useState('')
  const [loginBlocked, setLoginBlocked] = useState(false)
  const [migrateBlocked, setMigrateBlocked] = useState(false)
  const themeBootstrapped = useRef(false)

  const [salesFocusNonce, setSalesFocusNonce] = useState(0)

  const navigate = useCallback<AppNavigate>((id, payload) => {
    setPage(id)
    if (id === 'sales') setSalesFocusNonce((n) => n + 1)
    setVisited((prev) => {
      if (prev.has(id) && !(id === 'payment' || id === 'settings')) {
        return prev
      }
      const next = new Set(prev)
      next.add(id)
      if (id === 'payment' || id === 'settings') {
        next.add('payment')
        next.add('settings')
      }
      return next
    })
    setNavPayload(payload || {})
  }, [])

  const clearNavPayload = useCallback(() => {
    setNavPayload({})
  }, [])

  const setAppTheme = useCallback((next: string) => {
    setTheme(applyTheme(next))
  }, [])

  const setAppThemePack = useCallback((next: string) => {
    const pack = applyThemePack(next)
    setThemePack(pack)
    setPrefs((p) => ({ ...p, theme_pack: pack }))
  }, [])

  const toggleThemeVariant = useCallback(async () => {
    const next = flipThemeVariant(theme)
    setTheme(next)
    applyTheme(next)
    await persistTheme(next)
  }, [theme])

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  useEffect(() => {
    applyThemePack(themePack)
  }, [themePack])

  useEffect(() => bindPageShortcuts((id) => navigate(id)), [navigate])
  useEffect(() => bindEnterFocusChain(document), [])
  useEffect(() => bindArrowFocusNav(document), [])
  useEffect(() => bindEscapeBlur(document), [])

  useEffect(() => {
    let cancelled = false
    const boot = async () => {
      setSplashOpen(true)
      setSplashStatus('Starting local engine…')
      const engine = await ensureLocalEngine()
      if (cancelled) return
      if (!engine.ok) {
        setApiOk(false)
        setMeta(null)
        setSplashOpen(false)
        return
      }
      try {
        setSplashStatus('Checking license…')
        const lic = await fetchLicenseStatus()
        if (cancelled) return
        if (lic.needs_activation || lic.expiry_reactivation) {
          setLicenseBlocked(true)
          setApiOk(true)
          setSplashOpen(false)
          return
        }
        if (lic.access_blocked) {
          setNeedsInternet(Boolean(lic.needs_internet))
          setSealMessage(String(lic.seal_message || ''))
          setAccessBlocked(true)
          setApiOk(true)
          setSplashOpen(false)
          return
        }
        setSplashStatus('Checking login…')
        const login = await fetchAppLoginStatus()
        if (cancelled) return
        if (login.enabled && sessionStorage.getItem('app_login_ok') !== '1') {
          setLoginBlocked(true)
          setApiOk(true)
          setSplashOpen(false)
          return
        }
        setSplashStatus('Loading store…')
        const [m, p] = await Promise.all([fetchMeta(), fetchPrefs()])
        if (cancelled) return
        if (m.sync_mode === 'online') {
          try {
            const gate = await fetchOnlineMigrateStatus()
            if (!cancelled && gate.needs_migrate && gate.has_data) {
              setMigrateBlocked(true)
              setApiOk(true)
              setSplashOpen(false)
              return
            }
          } catch {
            /* engine is up; migrate prompt is optional */
          }
        }
        setApiOk(true)
        setMeta(m)
        setPrefs({
          show_nav_shortcut_keys: Boolean(p.show_nav_shortcut_keys),
          theme_pack: normalizeThemePack(
            p.theme_pack || m.theme_pack || 'classic',
          ),
          table_text_marquee: Boolean(p.table_text_marquee),
        })
        applyFontSize(
          m.font_size != null ? m.font_size : loadStoredFontSize(),
        )
        if (!themeBootstrapped.current) {
          themeBootstrapped.current = true
          // Disk (theme_config.txt) wins over any stale localStorage (e.g. crimson).
          const fromDisk = m.theme ? normalizeTheme(m.theme) : 'navy-light'
          setTheme(syncThemeFromDisk(fromDisk))
          const pack = normalizeThemePack(
            m.theme_pack || p.theme_pack || loadStoredThemePack(),
          )
          setThemePack(applyThemePack(pack))
        }
        setSplashStatus('Opening home…')
        setSplashOpen(false)
        // Alert scans can take seconds (inventory/dues) — never block first paint.
        window.setTimeout(() => {
          if (cancelled) return
          void fetchStartupAlerts()
            .then((alerts) => {
              if (cancelled) return
              setAlertRecheckMinutes(Number(alerts.recheck_minutes) || 0)
              if (!alerts.ok && alerts.error) {
                setStartupAlertsError(alerts.error)
              } else if (alerts.show && alerts.tabs?.length) {
                setStartupAlertsTitle('Startup Alerts')
                setStartupAlerts(alerts.tabs)
              }
            })
            .catch((e) => {
              if (!cancelled) {
                setStartupAlertsError(e instanceof Error ? e.message : String(e))
              }
            })
        }, 500)
      } catch {
        if (!cancelled) {
          setApiOk(false)
          setMeta(null)
          setSplashOpen(false)
        }
      }
    }
    void boot()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const onMigrate = () => setMigrateBlocked(true)
    window.addEventListener('satpuda:online-migrate', onMigrate)
    return () => window.removeEventListener('satpuda:online-migrate', onMigrate)
  }, [])

  // The dropdowns answer a failed server read with an empty list, because
  // raising there would take out bill save and printing. That leaves the
  // counter looking at a medicine box that is not in the search results with
  // nothing on screen explaining why. One bar in the shell covers every list.
  const [catalogError, setCatalogError] = useState('')
  // Dismissed for this run only. The flag itself is cleared server-side when
  // the operator confirms or switches store.
  const [storePickConfirmed, setStorePickConfirmed] = useState(false)
  // The engine knows when this PC's store stopped resolving on the server --
  // every caller of ensure_active_store_on_server that runs unattended catches
  // the exception, so the sentence it raised with used to reach nobody.
  const [storeLinkError, setStoreLinkError] = useState('')

  const syncRefreshNonce = useSyncRefreshPoller({
    enabled:
      apiOk === true &&
      !licenseBlocked &&
      !accessBlocked &&
      !loginBlocked &&
      !migrateBlocked,
    page,
    online: meta?.sync_mode === 'online',
    onCatalogError: setCatalogError,
    onStoreLinkError: setStoreLinkError,
  })

  useEffect(() => {
    if (!apiOk || licenseBlocked || accessBlocked || loginBlocked || migrateBlocked) return
    const onKey = (e: KeyboardEvent) => {
      // Sales and Sales History print their own bill on Ctrl+P. Answering here
      // too printed the last saved sale as well as the bill the shop picked.
      if (page === 'sales' || page === 'sales_history') return
      if (!e.ctrlKey || e.altKey || e.key.toLowerCase() !== 'p') return
      e.preventDefault()
      void (async () => {
        try {
          const last = await loadLastSale()
          const sid = last?.sale_id
          if (sid) {
            await printSalesBill({ sale_id: sid, slot: 2, mode: 'silent' })
          }
        } catch {
          /* ignore */
        }
      })()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [apiOk, licenseBlocked, accessBlocked, loginBlocked, migrateBlocked, page])

  // Self-heal the activation gate.
  //
  // licenseBlocked was decided ONCE at mount and only cleared by the activation
  // form's own success callback. If the device became activated by any other
  // route — the Administrator dialog, a second window, another instance, or an
  // operator activating for the user — this screen sat on "not activated"
  // forever. Reload does not rescue it either, because the desktop shell has no
  // reload binding, so the only way out was quitting the app. Re-checking while
  // blocked means the screen recovers on its own within a few seconds.
  //
  // accessBlocked is watched too, and that omission is what made "Licence not
  // found" a dead end: it is the screen a shop with no licence actually lands
  // on, it had no controls, and nothing re-checked it — so connecting the
  // internet changed nothing on screen and the app had to be killed. Both gates
  // recover by themselves now.
  useEffect(() => {
    if (!licenseBlocked && !accessBlocked) return
    let cancelled = false
    const id = window.setInterval(async () => {
      try {
        const lic = await fetchLicenseStatus()
        if (cancelled) return
        if (accessBlocked) {
          if (!lic.access_blocked) window.location.reload()
          return
        }
        if (!lic.needs_activation && !lic.expiry_reactivation) {
          window.location.reload()
        }
      } catch {
        /* backend not up yet — keep waiting */
      }
    }, 3000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [licenseBlocked, accessBlocked])

  if (accessBlocked) {
    return (
      <LicenseAccessBlockedDialog
        needsInternet={needsInternet}
        message={sealMessage}
        onRecovered={() => {
          setAccessBlocked(false)
          window.location.reload()
        }}
      />
    )
  }

  if (licenseBlocked) {
    return (
      <ActivationDialog
        onActivated={() => {
          window.location.reload()
        }}
      />
    )
  }

  if (loginBlocked) {
    return (
      <AppLoginDialog
        onAuthenticated={() => {
          setLoginBlocked(false)
          window.location.reload()
        }}
      />
    )
  }

  if (migrateBlocked) {
    return (
      <OnlineMigrateDialog
        onResolved={() => {
          setMigrateBlocked(false)
          window.location.reload()
        }}
      />
    )
  }

  return (
    <DesktopUiPrefsProvider prefs={prefs}>
    <div className="window">
      <nav className="mainnav" aria-label="Main">
        {PAGE_NAV.map((item, idx) => {
          const active = page === item.id
          return (
            <button
              key={item.id}
              type="button"
              className={`nav-item${active ? ' active' : ''}`}
              data-nav-order={100 + idx}
              data-nav-chain="app"
              onClick={() => navigate(item.id)}
              title={`${NAV_LABELS[item.id]} (${item.key})`}
            >
              {NAV_LABELS[item.id]}
              {prefs.show_nav_shortcut_keys ? (
                <span className="nav-key">{item.key}</span>
              ) : null}
            </button>
          )
        })}
        <div className="mainnav-right">
          {meta?.sync_mode ? (
            <span
              className={`mainnav-sync mainnav-sync-${
                meta.sync_mode === 'online' ? 'online' : 'offline'
              }`}
              title={meta.sync_label || meta.sync_mode}
            >
              {meta.sync_mode === 'online' ? 'Online' : 'Offline'}
            </span>
          ) : null}
          {meta?.store_name ? (
            <span className="mainnav-store" title={meta.store_name}>
              {meta.store_name}
            </span>
          ) : null}
          {IS_DEMO ? (
            <span
              className="mainnav-demo"
              title="Demonstration copy — no shop is connected and nothing is saved"
            >
              DEMO
            </span>
          ) : null}
          <button
            type="button"
            className="btn btn-neutral btn-sm"
            data-nav-order={200}
            onClick={() => void toggleThemeVariant()}
            title={`Switch to ${themeVariantLabel(theme).toLowerCase()} variant (${theme})`}
          >
            {theme.endsWith('-light') ? 'Use Dark' : 'Use Light'}
          </button>
        </div>
      </nav>

      <main className={`content${LIST_PAGES.has(page) ? ' content-list' : ''}`} key={storeEpoch}>
        {apiOk === false && !IS_DEMO ? (
          <div className="error" style={{ margin: '12px 16px 0' }}>
            Local data engine is not running. Close the browser tab and start from{' '}
            <code>desktop</code> with <code>npm run tauri:dev</code> (not{' '}
            <code>npm run dev</code>). That launches the same store engine as the
            Python app.
          </div>
        ) : null}
        {meta?.store_auto_selected && !storePickConfirmed && !IS_DEMO ? (
          <div className="error" style={{ margin: '12px 16px 0' }}>
            This PC picked the store <strong>{meta.store_name || meta.store_key}</strong>{' '}
            by itself after a settings reset — nobody chose it. Check it is the
            right shop before billing.{' '}
            <button
              type="button"
              className="btn btn-neutral btn-sm"
              onClick={() => {
                setStorePickConfirmed(true)
                void systemAction({ action: 'confirm_active_store' }).catch(
                  () => undefined,
                )
              }}
            >
              Yes, this is my shop
            </button>{' '}
            <button
              type="button"
              className="btn btn-neutral btn-sm"
              onClick={() =>
                navigate('settings', {
                  settingsTab: 'data_system',
                  settingsSection: 'stores',
                })
              }
            >
              Change store
            </button>
          </div>
        ) : null}
        {storeLinkError && !IS_DEMO ? (
          <div className="error" style={{ margin: '12px 16px 0' }}>
            {storeLinkError}{' '}
            <button
              type="button"
              className="btn btn-neutral btn-sm"
              onClick={() =>
                navigate('settings', {
                  settingsTab: 'data_system',
                  settingsSection: 'stores',
                })
              }
            >
              Connect this PC to its store
            </button>
          </div>
        ) : null}
        {catalogError && !IS_DEMO ? (
          <div className="error" style={{ margin: '12px 16px 0' }}>
            {catalogError} Medicine, customer and supplier lists are being shown
            from the last copy this PC has, or are empty — do not treat a name
            missing from a dropdown as a name that does not exist.
          </div>
        ) : null}
        {(visited.has('home') || page === 'home') && (
          <div
            style={{
              display: page === 'home' ? 'flex' : 'none',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
            }}
          >
            <HomePage
              onNavigate={navigate}
              syncRefreshNonce={syncRefreshNonce}
              active={page === 'home'}
            />
          </div>
        )}
        {(visited.has('sales') || page === 'sales') && (
          <div
            style={{
              display: page === 'sales' ? 'flex' : 'none',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
            }}
          >
            <SalesPage
              active={page === 'sales'}
              focusNonce={salesFocusNonce}
              editSaleId={page === 'sales' ? navPayload.saleId ?? null : null}
              onNavigate={navigate}
              onEditConsumed={clearNavPayload}
              syncRefreshNonce={syncRefreshNonce}
            />
          </div>
        )}
        {(visited.has('purchase') || page === 'purchase') && (
          <div
            style={{
              display: page === 'purchase' ? 'flex' : 'none',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
            }}
          >
            <PurchasePage
              editPurchaseId={
                page === 'purchase' ? navPayload.purchaseId ?? null : null
              }
              reorderPrefill={
                page === 'purchase' ? navPayload.reorderPrefill ?? null : null
              }
              onEditConsumed={clearNavPayload}
              onReorderPrefillConsumed={clearNavPayload}
              syncRefreshNonce={syncRefreshNonce}
              active={page === 'purchase'}
            />
          </div>
        )}
        {(visited.has('inventory') || page === 'inventory') && (
          <div
            style={{
              display: page === 'inventory' ? 'flex' : 'none',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
            }}
          >
            <InventoryPage
              onNavigate={navigate}
              syncRefreshNonce={syncRefreshNonce}
              active={page === 'inventory'}
            />
          </div>
        )}
        {(visited.has('general_products') || page === 'general_products') && (
          <div
            style={{
              display: page === 'general_products' ? 'flex' : 'none',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
            }}
          >
            <GeneralProductsPage />
          </div>
        )}
        {(visited.has('sales_history') || page === 'sales_history') && (
          <div
            style={{
              display: page === 'sales_history' ? 'flex' : 'none',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
            }}
          >
            <SalesHistoryPage
              onNavigate={navigate}
              syncRefreshNonce={syncRefreshNonce}
              active={page === 'sales_history'}
            />
          </div>
        )}
        {(visited.has('purchase_history') || page === 'purchase_history') && (
          <div
            style={{
              display: page === 'purchase_history' ? 'flex' : 'none',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
            }}
          >
            <PurchaseHistoryPage
              onNavigate={navigate}
              syncRefreshNonce={syncRefreshNonce}
              active={page === 'purchase_history'}
            />
          </div>
        )}
        {(visited.has('returns') || page === 'returns') && (
          <div
            style={{
              display: page === 'returns' ? 'flex' : 'none',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
            }}
          >
            <ReturnsPage
              initialTab={page === 'returns' ? navPayload.returnsTab : undefined}
              bulkPrefill={
                page === 'returns' ? navPayload.returnsBulkPrefill : undefined
              }
              disposalPrefill={
                page === 'returns' ? navPayload.disposalPrefill : undefined
              }
              salesPrefillSaleId={
                page === 'returns' ? navPayload.returnsSaleId : undefined
              }
              editPurchaseReturnId={
                page === 'returns' ? navPayload.returnsEditReturnId : undefined
              }
              syncRefreshNonce={syncRefreshNonce}
              active={page === 'returns'}
            />
          </div>
        )}
        {(visited.has('settings') ||
          visited.has('payment') ||
          page === 'payment' ||
          page === 'settings') && (
          <div
            style={{
              display:
                page === 'payment' || page === 'settings' ? 'flex' : 'none',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
            }}
          >
            <SettingsPage
              prefs={prefs}
              currentTheme={theme}
              currentThemePack={themePack}
              active={page === 'payment' || page === 'settings'}
              initialTab={
                page === 'payment' ? 'payment' : navPayload.settingsTab
              }
              initialToggle={navPayload.settingsToggle}
              initialSection={navPayload.settingsSection}
              reorderMedicinePrefill={navPayload.reorderMedicinePrefill}
              bulkReorderLoad={navPayload.bulkReorderLoad}
              onNavigate={navigate}
              syncRefreshNonce={syncRefreshNonce}
              onPrefsChange={setPrefs}
              onThemeChange={async (t) => {
                setAppTheme(t)
              }}
              onThemeSave={async (t) => {
                setAppTheme(t)
                await persistTheme(t)
              }}
              onThemePackChange={(pack) => {
                setAppThemePack(pack)
              }}
              onStoreSwitched={async () => {
                // Drop keep-alive pages so UI cannot show the previous store.
                setVisited(new Set(['home']))
                setPage('home')
                setNavPayload({})
                setStoreEpoch((n) => n + 1)
                try {
                  const m = await fetchMeta()
                  setMeta(m)
                  if (m.theme) setTheme(syncThemeFromDisk(m.theme))
                  if (m.theme_pack) {
                    setThemePack(applyThemePack(m.theme_pack))
                  }
                } catch {
                  /* meta refresh best-effort */
                }
                // Match Classic: full process restart so every page/cache reloads clean.
                await restartDesktopApp()
              }}
            />
          </div>
        )}
      </main>

      <StartupSplashOverlay open={splashOpen} status={splashStatus} />

      <StartupAlertsDialog
        open={Boolean(startupAlerts?.length) || Boolean(startupAlertsError)}
        tabs={startupAlerts || []}
        title={startupAlertsTitle}
        error={startupAlertsError}
        onClose={() => {
          setStartupAlerts(null)
          setStartupAlertsError('')
        }}
        onNavigate={navigate}
      />
      {/* Voice test build: hold F1 to speak, Ctrl+K to type a command. Only when the
          store's voice switch (admin panel) is on: off, there is no bar and F1 does nothing. */}
      {voiceSwitch.enabled ? <VoiceBar page={page} navigate={navigate} /> : null}
    </div>
    </DesktopUiPrefsProvider>
  )
}
