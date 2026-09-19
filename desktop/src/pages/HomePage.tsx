import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import {
  bannerUrl,
  fetchDashboard,
  type DashboardCard,
  type HomeQuickAction,
} from '../api'
import { ensureLocalEngine } from '../backend'
import { IS_DEMO } from '../demoMode'
import type { AppNavigate } from '../App'
import { HomeExportDialog } from '../components/HomeExportDialog'
import { usePageHotkeys } from '../hooks/usePageHotkeys'
import { QuickActionIcon } from '../quickActionIcons'
import { Panel } from './pageChrome'
import {
  BANNER_PREVIEW_EVENT,
  bannerCssWidth,
  rememberBannerFrameWidth,
} from '../homeBanner'

type Props = {
  onNavigate: AppNavigate
  syncRefreshNonce?: number
  /** Required on purpose. Every page stays mounted once visited, so a page
   *  that does not know whether it is on screen keeps answering the
   *  keyboard from behind another one. An optional prop defaulting to true
   *  let exactly that omission through the compiler. */
  active: boolean
}

type QaStyle =
  | 'success'
  | 'primary'
  | 'info'
  | 'secondary'
  | 'danger'
  | 'warning'
  | 'indigo'

const FALLBACK_QUICK_ACTIONS: HomeQuickAction[] = [
  { key: 'new_bill', label: 'New Bill', style: 'success' },
  { key: 'new_purchase', label: 'New Purchase', style: 'primary' },
  { key: 'search_medicine', label: 'Search Medicine', style: 'info' },
  { key: 'contacts', label: 'Contacts', style: 'secondary' },
  { key: 'payments', label: 'Payments', style: 'secondary' },
  { key: 'ledger', label: 'Ledger', style: 'danger' },
  { key: 'alerts', label: 'Alerts', style: 'warning' },
  { key: 'general_products', label: 'General Products', style: 'indigo' },
]

function qaStyle(style: string): QaStyle {
  const allowed: QaStyle[] = [
    'success',
    'primary',
    'info',
    'secondary',
    'danger',
    'warning',
    'indigo',
  ]
  return allowed.includes(style as QaStyle) ? (style as QaStyle) : 'primary'
}

function handleQuickAction(key: string, onNavigate: AppNavigate) {
  switch (key) {
    case 'new_bill':
      onNavigate('sales')
      break
    case 'new_purchase':
      onNavigate('purchase')
      break
    case 'general_products':
      onNavigate('general_products')
      break
    case 'search_medicine':
    case 'export_inventory':
      onNavigate('inventory')
      break
    case 'export_sales':
      onNavigate('sales_history')
      break
    case 'export_purchases':
      onNavigate('purchase_history')
      break
    case 'contacts':
      onNavigate('settings', { settingsTab: 'contacts', settingsSection: 'doctors' })
      break
    case 'payments':
      onNavigate('payment')
      break
    case 'ledger':
      onNavigate('settings', {
        settingsTab: 'ledger',
        settingsToggle: 'supplier',
      })
      break
    case 'alerts':
      onNavigate('settings', {
        settingsTab: 'alerts',
        settingsToggle: 'low',
      })
      break
    case 'export_all':
      onNavigate('settings', {
        settingsTab: 'data_system',
        settingsSection: 'export',
      })
      break
    default:
      break
  }
}

function StatCell({ card }: { card: DashboardCard }) {
  return (
    <div className="dash-cell">
      <div className="dash-value">{card.value}</div>
      <div className="dash-label">{card.label}</div>
    </div>
  )
}

export function HomePage({ onNavigate, syncRefreshNonce = 0, active }: Props) {
  const [todayCards, setTodayCards] = useState<DashboardCard[]>([])
  const [periodCards, setPeriodCards] = useState<DashboardCard[]>([])
  const [quickActions, setQuickActions] = useState<HomeQuickAction[]>(
    FALLBACK_QUICK_ACTIONS,
  )
  const [error, setError] = useState('')
  // Kept apart from `error`: that one is worded for a dead local engine and
  // tells you to restart the app. This one means the app is fine and the
  // server is not, and the numbers on screen are zeroes from an empty
  // in-memory database rather than a quiet day.
  const [serverError, setServerError] = useState('')
  const [bannerSrc, setBannerSrc] = useState('')
  const [bannerFailed, setBannerFailed] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  // Both of these arrive on the dashboard payload. Neither had a reader before:
  // the banner width only ever resized the old Tk home page, and the Dashboard
  // Sections checkboxes had no consumer at all outside Tk.
  const [bannerWidth, setBannerWidth] = useState(0)
  // Share of the panel (10-100) once the shop chose one; 0 = bannerWidth, an
  // older pixel save, still decides. previewPct is what Settings is typing now.
  const [bannerPct, setBannerPct] = useState(0)
  const [previewPct, setPreviewPct] = useState(0)
  const bannerFrameRef = useRef<HTMLElement>(null)
  const [sections, setSections] = useState<Record<string, boolean>>({})

  usePageHotkeys({
    // The hook has always taken this; no page passed it, so F5 and the
    // rest fired on every page that had ever been opened.
    enabled: active,
    onExport: () => setExportOpen(true),
    onLetter: (k) => {
      if (k === 'b') onNavigate('sales')
      else if (k === 'p') onNavigate('purchase')
      else if (k === 'i') onNavigate('inventory')
      else if (k === 'e') setExportOpen(true)
    },
  })

  const refreshBanner = () => {
    setBannerFailed(false)
    setBannerSrc(bannerUrl())
  }

  const loadDashboard = useCallback(async () => {
    try {
      const dash = await fetchDashboard()
      setTodayCards(dash.today_cards || dash.cards.slice(0, 6))
      setPeriodCards(dash.period_cards || dash.cards.slice(6))
      // Array.isArray, not `?.length`: turning every button OFF gives an EMPTY
      // array, which is falsy -- so the all-off case never reached here and the
      // hardcoded fallback set came back instead of nothing.
      if (Array.isArray(dash.quick_actions)) {
        setQuickActions(dash.quick_actions)
      }
      if (typeof dash.home_banner_size === 'number') {
        setBannerWidth(Math.max(300, Math.min(4000, dash.home_banner_size)))
      }
      if (typeof dash.home_banner_width_pct === 'number') {
        setBannerPct(dash.home_banner_width_pct)
      }
      setPreviewPct(0) // what is saved is what shows now
      if (dash.dashboard_sections) setSections(dash.dashboard_sections)
      setServerError(dash.server_error || '')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setError('')
      setServerError('')
      setBannerFailed(false)
      setBannerSrc('')
      const engine = await ensureLocalEngine()
      if (cancelled) return
      if (!engine.ok) {
        setError(engine.error)
        return
      }
      // Only request the image after the local engine is up (avoids a permanent
      // "Banner image not available" from the first failed fetch).
      setBannerSrc(bannerUrl())
      await loadDashboard()
    }
    load()
    const onAppearanceSaved = () => {
      if (cancelled) return
      refreshBanner()
      // Quick Access, the banner width and the Dashboard Sections all live on
      // this payload, and nothing refetched it -- so every one of them only
      // took effect after the whole app was restarted.
      void loadDashboard()
    }
    // Settings -> Banner width, as the operator types: redraw now, not only
    // after Save Appearance. Save then reloads the payload above.
    const onBannerPreview = (e: Event) => {
      setPreviewPct(Number((e as CustomEvent<{ pct?: number }>).detail?.pct) || 0)
    }
    window.addEventListener('satpuda:home-banner-changed', onAppearanceSaved)
    window.addEventListener(BANNER_PREVIEW_EVENT, onBannerPreview)
    return () => {
      cancelled = true
      window.removeEventListener('satpuda:home-banner-changed', onAppearanceSaved)
      window.removeEventListener(BANNER_PREVIEW_EVENT, onBannerPreview)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Settings says "drawn at N px of the M px beside Quick Actions"; M is this
  // panel, and only Home can measure it. Hidden (display:none) reads 0: skipped.
  useEffect(() => {
    const el = bannerFrameRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      if (el.clientWidth > 0) rememberBannerFrameWidth(el.clientWidth)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    if (!syncRefreshNonce) return
    void (async () => {
      try {
        const dash = await fetchDashboard()
        setTodayCards(dash.today_cards || dash.cards.slice(0, 6))
        setPeriodCards(dash.period_cards || dash.cards.slice(6))
      } catch {
        /* ignore background refresh */
      }
    })()
  }, [syncRefreshNonce])

  return (
    <div className="home-page">
      {/* Never in the demo: there is no local engine there, and this told a
          sales prospect to "Restart with npm run tauri:dev". */}
      {error && !IS_DEMO ? (
        <div className="error">
          Local data: {error}. Restart with <code>npm run tauri:dev</code> from{' '}
          <code>desktop</code>.
        </div>
      ) : null}

      {!error && serverError ? (
        <div className="error">
          {serverError} The figures below are not your shop's — they are
          zeroes from an empty database. Do not read them as today's business.
        </div>
      ) : null}

      <div className="home-body">
        <Panel title="Quick Actions" className="qa-frame">
          <div className="qa-col">
            {quickActions.length ? (
              quickActions.map((a, i) => (
                <button
                  key={a.key}
                  type="button"
                  className={`qa-btn qa-${qaStyle(a.style)}`}
                  data-nav-order={i + 1}
                  onClick={() => handleQuickAction(a.key, onNavigate)}
                >
                  <QuickActionIcon actionKey={a.key} size={16} />
                  <span className="qa-label">{a.label}</span>
                </button>
              ))
            ) : (
              <p className="muted qa-empty">
                No quick actions enabled. Turn them on in Settings → Appearance →
                Quick Access.
              </p>
            )}
          </div>
        </Panel>

        <section
          className="panel banner-frame"
          // Settings -> Appearance -> Banner width, handed to the stylesheet.
          // An inline width on the <img> was not enough: .home-banner-img
          // capped it with max-height, so every value from roughly the panel
          // width up to the 4000 maximum drew the same picture. The variable
          // lets the rule keep "fill the panel" as its no-setting default while
          // the saved number decides the size. The number is a percentage of
          // this panel now (homeBanner.ts): pixels above the panel's width all
          // drew the same banner. An old pixel save still arrives as px.
          ref={bannerFrameRef}
          style={
            bannerWidth || bannerPct || previewPct
              ? ({
                  '--home-banner-width': bannerCssWidth(
                    previewPct || bannerPct,
                    bannerWidth,
                  ),
                } as CSSProperties)
              : undefined
          }
        >
          {bannerSrc && !bannerFailed ? (
            <img
              className="home-banner-img"
              src={bannerSrc}
              alt="Home banner"
              onError={() => setBannerFailed(true)}
            />
          ) : (
            <div className="banner-fallback muted">
              {bannerSrc
                ? 'Banner image not available'
                : 'Loading banner…'}
              {bannerFailed ? (
                <button
                  type="button"
                  className="btn btn-neutral btn-sm"
                  style={{ marginTop: 8 }}
                  onClick={refreshBanner}
                >
                  Retry
                </button>
              ) : null}
            </div>
          )}
        </section>
      </div>

      {sections.home_dashboard !== false ? (
      <Panel title="Dashboard">
        <div className="dash-row">
          {(todayCards.length
            ? todayCards
            : Array.from({ length: 6 }, (_, i) => ({
                label: [
                  'Today Sales',
                  'Today Collected',
                  'Today Bills',
                  'Customer Due',
                  'Supplier Due',
                  'Stock Value',
                ][i],
                value: '…',
              }))
          ).map((c) => (
            <StatCell key={c.label} card={c} />
          ))}
        </div>
        <hr className="dash-sep" />
        <div className="dash-row dash-row-period">
          {(periodCards.length
            ? periodCards
            : Array.from({ length: 6 }, (_, i) => ({
                label: [
                  'Month Sales',
                  'Month Collected',
                  'Month Bills',
                  'Year Sales',
                  'Year Collected',
                  'Year Bills',
                ][i],
                value: '…',
              }))
          ).map((c) => (
            <StatCell key={c.label} card={c} />
          ))}
        </div>
      </Panel>
      ) : null}
      <HomeExportDialog open={exportOpen} onClose={() => setExportOpen(false)} />
    </div>
  )
}
