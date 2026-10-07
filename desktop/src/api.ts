/** Local Python desktop API client (127.0.0.1). */

const API_BASE =
  (import.meta.env.VITE_DESKTOP_API as string | undefined)?.replace(/\/$/, '') ||
  'http://127.0.0.1:8765'

export type MetaResponse = {
  app_name: string
  company_name: string
  tagline: string
  version: string
  store_key: string
  store_name: string
  sync_mode: string
  sync_label: string
  /** Same key as Tk theme_config.txt (e.g. navy-dark, forest-light). */
  theme?: string
  /** classic | accessible | modern — desktop color pack. */
  theme_pack?: string
  /** Appearance font size 7–20 (Tk font_config). */
  font_size?: number
  api_port: number
  server_time: string
  server_only?: boolean
  needs_migrate?: boolean
  /** An Online PC this version moves onto offline-first by itself (core/offline_first). */
  offline_first_auto?: boolean
  /** A registry rebuild picked this store; no person has confirmed it. */
  store_auto_selected?: boolean
}

export type DashboardCard = { label: string; value: string }

export type HomeQuickAction = {
  key: string
  label: string
  style: string
}

export type DashboardResponse = {
  today: string
  fy_label: string
  cards: DashboardCard[]
  today_cards: DashboardCard[]
  period_cards: DashboardCard[]
  quick_actions?: HomeQuickAction[]
  home_banner_size?: number
  /** Share of the banner panel, 10-100; 0 = never chosen, home_banner_size
   *  (pixels) still decides. See homeBanner.ts. */
  home_banner_width_pct?: number
  dashboard_sections?: Record<string, boolean>
  /** Set when the figures above came from the empty local database because
   *  the Online read failed. Zeroes on this payload then mean "unknown",
   *  not "no sales today". */
  server_error?: string
}

export type DesktopUiPrefs = {
  show_nav_shortcut_keys: boolean
  theme_pack?: string
  /** Scroll long table cell text (marquee). Default off. */
  table_text_marquee?: boolean
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error((data as { error?: string }).error || `HTTP ${res.status}`)
  }
  return data as T
}

export function getApiBase() {
  return API_BASE
}

export function bannerUrl() {
  // An <img src> never goes through the fetch interceptor, so the demo build
  // has to point at its own copy of the picture directly.
  if (import.meta.env.VITE_DEMO === '1') return './demo-assets/banner.png'
  return `${API_BASE}/api/home/banner?t=${Date.now()}`
}

export type HealthResponse = {
  ok: boolean
  service: string
  port: number
  db: boolean
  revision?: number
  python?: string
  python_minor?: number
  db_path?: string
  routes?: string[]
}

/** Must match core.desktop_api.API_REVISION — rejects stale engines without settings. */
export const REQUIRED_API_REVISION = 54

export function brandIconUrl() {
  return `${API_BASE}/api/brand/icon?t=${Date.now()}`
}

export function brandLogoUrl() {
  return `${API_BASE}/api/brand/logo?t=${Date.now()}`
}

export function brandCardUrl() {
  return `${API_BASE}/api/brand/card?t=${Date.now()}`
}

export function fetchHealth() {
  return getJson<HealthResponse>('/api/health')
}

export type OnlineMigrateStatus = {
  ok?: boolean
  online: boolean
  needs_migrate: boolean
  has_data: boolean
  server_only?: boolean
  db_path?: string
  message?: string
  error?: string
}

export function fetchOnlineMigrateStatus() {
  return getJson<OnlineMigrateStatus>('/api/online/migrate')
}

export function healthSupportsSettings(h: HealthResponse): boolean {
  // Strict: revision only. Older engines can expose /api/settings/bundle but
  // still miss /api/inventory and history routes (empty pages).
  if (typeof h.revision !== 'number' || h.revision < REQUIRED_API_REVISION) {
    return false
  }
  if (typeof h.python_minor === 'number' && h.python_minor < 10) {
    return false
  }
  return true
}

export function fetchMeta() {
  return getJson<MetaResponse>('/api/meta')
}

export function fetchDashboard() {
  return getJson<DashboardResponse>('/api/home/dashboard')
}

export function fetchPrefs() {
  return getJson<DesktopUiPrefs>('/api/prefs')
}

export async function savePrefs(updates: Partial<DesktopUiPrefs>) {
  const res = await fetch(`${API_BASE}/api/prefs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error((data as { error?: string }).error || `HTTP ${res.status}`)
  }
  return data as DesktopUiPrefs
}

export type SyncStatusResponse = {
  ok: boolean
  status_line: string
  /** Why the medicine/customer/supplier dropdowns are coming back empty.
   *  "" when the catalog is healthy or the store is offline. */
  catalog_error?: string
  /** Set when this PC's active store no longer resolves to a store on the
   *  server. The recovery is Settings → Data & System → Stores. */
  store_link_error?: string
  refresh_seq?: number
  refresh_collections?: string[]
  snapshot?: {
    last_sync_at?: string | null
    pending_count?: number
    skip_count?: number
    errors?: string[]
  }
}

export function fetchSyncStatus() {
  return getJson<SyncStatusResponse>('/api/sync/status')
}

export type LicenseStatusResponse = {
  ok: boolean
  needs_activation: boolean
  access_blocked?: boolean
  activated: boolean
  expiry_reactivation?: boolean
  /**
   * The licence file is gone or does not verify. NOT an expiry: the shop is not
   * out of time, it is out of a licence file, and one internet connection puts
   * the same one back. It must never open the three-factor activation form.
   */
  needs_internet?: boolean
  /** 'expired' | 'access_disabled' | 'needs_internet' | '' */
  block_reason?: string
  /** 'signed' | 'legacy' | 'missing' | 'tampered' */
  seal_state?: string
  /** The sentence to show for that state, written for a shopkeeper. */
  seal_message?: string
  device_key?: string
  device_key_path?: string
  has_registry?: boolean
  store_name?: string
  sync_mode?: 'online' | 'offline'
  /** Set only when the installer left a shop name and the sign-up was refused. */
  provision_error?: string
  /** The name it tried, so the first question comes back already answered. */
  provision_store_name?: string
  /** And the mode picked in the installer, so the second one does too. */
  provision_sync_mode?: string
  /** "name_exists": the installer's shop name is already on the server. */
  provision_code?: string
  error?: string
}

export function fetchLicenseStatus() {
  return getJson<LicenseStatusResponse>('/api/license/status')
}

export type ActivateLicenseResult = {
  ok: boolean
  activated?: boolean
  sync_mode?: 'online' | 'offline'
  store_name?: string
  android_key?: string
  show_key?: boolean
  server_note?: string
  activation_date?: string
  expiry_date?: string
  access_allowed?: boolean | null
  trial_days?: number
  error?: string
}

export async function activateLicense(body: {
  username: string
  password: string
  device_key: string
  store_name?: string
  sync_mode?: 'online' | 'offline'
  mode?: 'activate' | 'add_store'
}) {
  const res = await fetch(`${API_BASE}/api/license/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error((data as { error?: string }).error || `HTTP ${res.status}`)
  }
  return data as ActivateLicenseResult
}

/**
 * The whole of activation: the shop name, and Online or Offline.
 *
 * The machine supplies its own id and version; the server decides the trial
 * window and hands back a store that did not exist a moment ago. Nothing here
 * carries a credential, because a credential inside a downloadable installer is
 * not a credential.
 */
/** A refusal that says why, so the screen can offer the right next step. */
export class ActivationError extends Error {
  code: string
  constructor(message: string, code = '') {
    super(message)
    this.code = code
  }
}

export async function provisionTrial(
  storeName: string,
  syncMode: 'online' | 'offline',
  confirmNew = false,
) {
  const res = await fetch(`${API_BASE}/api/license/provision-trial`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      store_name: storeName,
      sync_mode: syncMode,
      // Only after the shopkeeper said "this is a NEW shop" to a name already on
      // the server (code "name_exists").
      ...(confirmNew ? { confirm_new: true } : {}),
    }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const d = data as { error?: string; code?: string }
    throw new ActivationError(d.error || `HTTP ${res.status}`, d.code || '')
  }
  return data as ActivateLicenseResult
}

export type PairStoreKeyResult = ActivateLicenseResult & {
  /** The first call only reads the key back: this is the shop it belongs to. */
  confirm_required?: boolean
  store_id?: string
}

/**
 * "I already have a shop" — connect this PC with the shop's SC- key.
 *
 * Two calls. The first (confirm: false) resolves the key and answers with the
 * shop's name so a person can look at it; nothing on this computer changes.
 * The second (confirm: true) does the work. Pairing is by KEY and never by
 * name, so a typo is a refusal rather than a different shop, and it carries no
 * administrator credential — the key can only reach the one store that owns it.
 */
export async function pairWithStoreKey(androidKey: string, confirm: boolean) {
  const res = await fetch(`${API_BASE}/api/license/pair-key`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ android_key: androidKey, confirm }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error((data as { error?: string }).error || `HTTP ${res.status}`)
  }
  return data as PairStoreKeyResult
}

/** "Try again now" from the blocked screen: one forced look for a licence. */
export async function recheckLicense() {
  const res = await fetch(`${API_BASE}/api/license/recheck`, { method: 'POST' })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error((data as { error?: string }).error || `HTTP ${res.status}`)
  }
  return data as LicenseStatusResponse
}

export type AppLoginStatus = {
  ok: boolean
  enabled: boolean
  username_hint?: string
}

export function fetchAppLoginStatus() {
  return getJson<AppLoginStatus>('/api/login/status')
}

export async function verifyAppLogin(username: string, password: string) {
  const res = await fetch(`${API_BASE}/api/login/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error((data as { error?: string }).error || `HTTP ${res.status}`)
  }
  return data as { ok: boolean; authenticated?: boolean }
}
