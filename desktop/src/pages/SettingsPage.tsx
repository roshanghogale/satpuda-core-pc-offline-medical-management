import { IS_DEMO } from '../demoMode'
import { PageActiveContext } from '../hooks/usePageHotkeys'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DesktopUiPrefs } from '../api'
import {
  fetchSettingsBundle,
  fetchContacts,
  fetchAlerts,
  fetchPayments,
  fetchShelf,
  fetchReorder,
  saveSettingsSection,
  type SettingsBundle,
} from '../settingsApi'
import { ensureLocalEngine } from '../backend'
import type { AppNavigate } from '../App'
import type { ReorderPrefill } from '../pagesApi'
import { restartDesktopApp } from '../restartApp'
import { applyFontSize } from '../theme'
import { SETTINGS_TABS, type SettingsTabId } from './settings/settingsConfig'
import { isTypingTarget } from '../keyboard'
import {
  AlertsPanel,
  ContactsPanel,
  LedgerPanel,
  PaymentPanel,
  ReorderPanel,
  ShelfPanel,
  ShortcutsPanel,
} from './settings/InteractivePanels'
import { PharmacyPanel } from './settings/PharmacyPanels'
import {
  AppearancePanel,
  LayoutListsPanel,
  SalesBillingPanel,
} from './settings/PrefsPanels'
import { DataSystemPanel, ImportPanel } from './settings/ImportDataPanels'
import { AlertPrefsPanel } from './settings/AlertPrefsPanel'
import { VoiceCommandsPanel } from '../voice/VoiceCommandsPanel'
import { useVoiceEnabled } from '../voice/voiceEnabled'
import { registerVoicePage, sendToPage, setVoicePlace, type VoiceHandler } from '../voice/voiceBus'
import {
  RestartAppDialog,
  type RestartPrompt,
} from './RestartAppDialog'

type Props = {
  prefs: DesktopUiPrefs
  onPrefsChange: (prefs: DesktopUiPrefs) => void
  /** Live applied theme (window chrome) — keeps the Theme select in sync. */
  currentTheme: string
  /** Live applied pack — keeps the Theme pack select in sync. */
  currentThemePack: string
  /** Live preview in the window (does not write theme_config.txt). */
  onThemeChange?: (theme: string) => void
  /** Persist theme to disk (Save Appearance / F10). */
  onThemeSave?: (theme: string) => void | Promise<void>
  /** Live preview theme pack (classic | accessible | modern). */
  onThemePackChange?: (pack: string) => void
  /** After store switch — refresh meta / leave stale pages. */
  onStoreSwitched?: () => void | Promise<void>
  initialTab?: SettingsTabId
  initialToggle?: string
  initialSection?: string
  reorderMedicinePrefill?: {
    medicine_name: string
    pack_size: string
    quantity: number
    unit_price: number
  }
  bulkReorderLoad?: boolean
  onNavigate?: AppNavigate
  /** False while another top-level page is showing (this page stays mounted). */
  active?: boolean
  /** Bumped after payments so Payment/Ledger dues reload immediately. */
  syncRefreshNonce?: number
}

export function SettingsPage({
  prefs,
  onPrefsChange,
  currentTheme,
  currentThemePack,
  onThemeChange,
  onThemeSave,
  onThemePackChange,
  onStoreSwitched,
  initialTab,
  initialToggle,
  initialSection,
  reorderMedicinePrefill,
  bulkReorderLoad,
  onNavigate,
  active = true,
  syncRefreshNonce = 0,
}: Props) {
  const [tabId, setTabId] = useState<SettingsTabId>(initialTab || 'pharmacy')
  const [sectionId, setSectionId] = useState(initialSection || 'profile')
  const [toggleId, setToggleId] = useState(initialToggle || 'supplier')
  const [nestedId, setNestedId] = useState('low')
  const [bundle, setBundle] = useState<SettingsBundle | null>(null)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)
  const [onlineSwitchMsg, setOnlineSwitchMsg] = useState('')
  const [onlineSwitchBusy, setOnlineSwitchBusy] = useState(false)
  const [restartPrompt, setRestartPrompt] = useState<RestartPrompt>(null)
  const sectionNavRef = useRef<HTMLDivElement | null>(null)

  const [appearance, setAppearance] = useState<Record<string, unknown>>({})
  const appearanceRef = useRef(appearance)
  appearanceRef.current = appearance
  const currentThemeRef = useRef(currentTheme)
  currentThemeRef.current = currentTheme
  const currentThemePackRef = useRef(currentThemePack)
  currentThemePackRef.current = currentThemePack
  const [layoutLists, setLayoutLists] = useState<Record<string, unknown>>({})
  const [salesBilling, setSalesBilling] = useState<Record<string, unknown>>({})
  const [system, setSystem] = useState<Record<string, unknown>>({})
  const [importPrefs, setImportPrefs] = useState<Record<string, unknown>>({})
  const [profile, setProfile] = useState<Record<string, unknown>>({})
  const [bill, setBill] = useState<Record<string, unknown>>({})
  const [login, setLogin] = useState<Record<string, unknown>>({})
  const [printer, setPrinter] = useState<Record<string, unknown>>({})

  // Voice switched off for this store (admin panel): no Voice Commands section.
  const voiceOn = useVoiceEnabled().enabled
  const tab = useMemo(() => {
    const t = SETTINGS_TABS.find((x) => x.id === tabId) || SETTINGS_TABS[0]
    if (voiceOn || !t.sections?.some((s) => s.id === 'voice')) return t
    return { ...t, sections: t.sections.filter((s) => s.id !== 'voice') }
  }, [tabId, voiceOn])

  useEffect(() => {
    if (!voiceOn && sectionId === 'voice') setSectionId(tab.sections?.[0]?.id || 'cheatsheet')
  }, [voiceOn, sectionId, tab])

  useEffect(() => {
    if (initialTab) setTabId(initialTab)
  }, [initialTab])

  useEffect(() => {
    if (initialToggle) setToggleId(initialToggle)
  }, [initialToggle])

  useEffect(() => {
    if (initialSection) setSectionId(initialSection)
  }, [initialSection])

  /** What a voice command asked to land on when it also changed the tab: the
   *  tab-change reset below would otherwise put the tab's FIRST section back. */
  const forcedRef = useRef<{ section?: string; toggle?: string; nested?: string } | null>(null)
  const tabIdRef = useRef(tabId)
  tabIdRef.current = tabId

  useEffect(() => {
    const forced = forcedRef.current
    forcedRef.current = null
    const firstSection = tab.sections?.[0]?.id || ''
    const firstToggle = tab.toggles?.[0]?.id || 'supplier'
    if (forced?.section) setSectionId(forced.section)
    else if (!initialSection) setSectionId(firstSection || 'profile')
    if (forced?.toggle) setToggleId(forced.toggle)
    else if (!initialToggle) setToggleId(firstToggle)
    setNestedId(forced?.nested || tab.nestedTabs?.[0]?.id || 'low')
  }, [tabId, tab, initialSection, initialToggle])

  // ── Voice (test build): "printer settings ughad", "customer ledger", "expired alerts" ──
  // Sets the same tab / section / sub-tab state the buttons above set.
  const voiceHandlerRef = useRef<VoiceHandler>(async () => null)
  voiceHandlerRef.current = async (cmd) => {
    // "Rajesh shodh" while a ledger is showing: that panel owns the search.
    if (cmd.intent === 'search' && tabIdRef.current === 'ledger') return sendToPage('ledger', cmd)
    if (cmd.intent !== 'open_settings') return null
    const a = cmd.args || {}
    const wantTab = String(a.tab || '').trim().toLowerCase()
    const wantSec = String(a.section || '').trim().toLowerCase()
    const has = (t: (typeof SETTINGS_TABS)[number], id: string) =>
      Boolean(
        t.sections?.some((x) => x.id === id) ||
          t.toggles?.some((x) => x.id === id) ||
          t.nestedTabs?.some((x) => x.id === id),
      )
    // A section said without its tab ("printer setup") is found by its id.
    const t =
      SETTINGS_TABS.find((x) => x.id === wantTab) ||
      (!wantTab && wantSec ? SETTINGS_TABS.find((x) => has(x, wantSec)) : undefined)
    if (!t) return { ok: false, say: `Settings madhe "${a.tab || a.section}" nahi` }
    const forced: { section?: string; toggle?: string; nested?: string } = {}
    let label = t.label.replace(/^[^A-Za-z]+/, '')
    if (wantSec) {
      const sec = t.sections?.find((x) => x.id === wantSec)
      const tog = t.toggles?.find((x) => x.id === wantSec)
      const nest = t.nestedTabs?.find((x) => x.id === wantSec)
      if (sec) forced.section = sec.id
      else if (tog) forced.toggle = tog.id
      else if (nest) forced.nested = nest.id
      else return { ok: false, say: `${label}: "${a.section}" vibhag nahi` }
      label = `${label} → ${(sec || tog || nest)!.label}`
    }
    if (tabIdRef.current !== t.id) {
      forcedRef.current = forced
      setTabId(t.id)
    }
    if (forced.section) setSectionId(forced.section)
    if (forced.toggle) setToggleId(forced.toggle)
    if (forced.nested) setNestedId(forced.nested)
    return { ok: true, say: `Settings: ${label} ughadla` }
  }
  useEffect(() => registerVoicePage('settings', () => voiceHandlerRef.current), [])
  // The voice bar tells the service which panel is showing: "payment/customer", "reorder/by_supplier".
  useEffect(() => setVoicePlace('settings', `${tabId}/${toggleId}`), [tabId, toggleId])

  const reload = useCallback(async () => {
    setError('')
    try {
      const engine = await ensureLocalEngine()
      if (!engine.ok) {
        setError(engine.error)
        return
      }
      const b = await fetchSettingsBundle()
      setBundle(b)
      const liveTheme = String(
        currentThemeRef.current || b.appearance.theme || '',
      ).trim()
      const livePack = String(
        currentThemePackRef.current || b.appearance.theme_pack || 'classic',
      ).trim()
      setAppearance({
        ...b.appearance,
        theme: liveTheme || b.appearance.theme,
        theme_pack: livePack,
      })
      setLayoutLists({ ...b.layout_lists })
      setSalesBilling({ ...b.sales_billing })
      setSystem({ ...b.system })
      setImportPrefs({ ...b.import })
      if (b.pharmacy) {
        setProfile({ ...b.pharmacy.profile })
        setBill({ ...b.pharmacy.bill })
        setLogin({ ...b.pharmacy.login })
        setPrinter({ ...b.pharmacy.printer })
      }
      onPrefsChange({
        show_nav_shortcut_keys: Boolean(b.appearance.show_nav_shortcut_keys),
        theme_pack: livePack,
        table_text_marquee: Boolean(b.appearance.table_text_marquee),
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [onPrefsChange])

  useEffect(() => {
    reload()
  }, [reload])

  useEffect(() => {
    if (!syncRefreshNonce) return
    let cancelled = false
    void (async () => {
      try {
        const kind = toggleId === 'customer' ? 'customer' : 'supplier'
        if (tabId === 'payment') {
          const p = await fetchPayments(kind)
          if (!cancelled) {
            setBundle((b) =>
              b
                ? {
                    ...b,
                    ...(kind === 'customer'
                      ? { payments_customer: p }
                      : { payments_supplier: p }),
                  }
                : b,
            )
          }
        }
      } catch {
        /* payment refresh best-effort */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [syncRefreshNonce, tabId, toggleId])

  // Lazy-load heavy Settings tabs (contacts / payments / alerts / shelf / reorder).
  useEffect(() => {
    if (!bundle) return
    let cancelled = false
    void (async () => {
      try {
        if (tabId === 'contacts' && !bundle.contacts) {
          const c = await fetchContacts()
          if (!cancelled) {
            setBundle((b) => (b ? { ...b, contacts: c } : b))
          }
        }
        if (tabId === 'alerts' && !bundle.alerts) {
          const a = await fetchAlerts()
          if (!cancelled) {
            setBundle((b) => (b ? { ...b, alerts: a } : b))
          }
        }
        if (tabId === 'payment') {
          const kind = toggleId === 'customer' ? 'customer' : 'supplier'
          const has =
            kind === 'customer'
              ? bundle.payments_customer
              : bundle.payments_supplier
          if (!has) {
            const p = await fetchPayments(kind)
            if (!cancelled) {
              setBundle((b) =>
                b
                  ? {
                      ...b,
                      ...(kind === 'customer'
                        ? { payments_customer: p }
                        : { payments_supplier: p }),
                    }
                  : b,
              )
            }
          }
        }
        if (tabId === 'shelf' && !bundle.shelf) {
          const s = await fetchShelf()
          if (!cancelled) {
            setBundle((b) => (b ? { ...b, shelf: s } : b))
          }
        }
        if (tabId === 'reorder' && !bundle.reorder) {
          const r = await fetchReorder()
          if (!cancelled) {
            setBundle((b) => (b ? { ...b, reorder: r } : b))
          }
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [tabId, toggleId, bundle])

  // Keep Appearance selects aligned with Use Dark / Use Light and live previews.
  useEffect(() => {
    const t = String(currentTheme || '').trim()
    if (!t) return
    setAppearance((a) => (a.theme === t ? a : { ...a, theme: t }))
  }, [currentTheme])

  useEffect(() => {
    const p = String(currentThemePack || '').trim()
    if (!p) return
    setAppearance((a) => (a.theme_pack === p ? a : { ...a, theme_pack: p }))
  }, [currentThemePack])

  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'F4' && !e.ctrlKey && !e.altKey) {
        e.preventDefault()
        const activeBtn =
          sectionNavRef.current?.querySelector<HTMLButtonElement>(
            '.settings-section-btn.active',
          ) ||
          sectionNavRef.current?.querySelector<HTMLButtonElement>(
            '.settings-section-btn',
          )
        activeBtn?.focus()
        return
      }
      if (e.altKey && !e.ctrlKey && !e.metaKey) {
        const digit = Number(e.key)
        if (digit >= 1 && digit <= 9 && tab.sections?.length) {
          const sec = tab.sections[digit - 1]
          if (sec) {
            e.preventDefault()
            setSectionId(sec.id)
          }
        }
      }
      if (
        (tabId === 'payment' || tabId === 'ledger') &&
        !e.ctrlKey &&
        !e.altKey &&
        !e.metaKey &&
        !isTypingTarget(e.target)
      ) {
        const k = e.key.toLowerCase()
        if (k === 's') {
          e.preventDefault()
          setToggleId('supplier')
        } else if (k === 'c') {
          e.preventDefault()
          setToggleId('customer')
        }
      }
      if (!e.ctrlKey || e.altKey || e.metaKey) return
      if (e.key === 'Tab') {
        e.preventDefault()
        const idx = SETTINGS_TABS.findIndex((t) => t.id === tabId)
        const next = e.shiftKey
          ? (idx - 1 + SETTINGS_TABS.length) % SETTINGS_TABS.length
          : (idx + 1) % SETTINGS_TABS.length
        setTabId(SETTINGS_TABS[next].id)
        return
      }
      const digit = e.key >= '0' && e.key <= '9' ? Number(e.key) : -1
      if (digit < 0) return
      const target = SETTINGS_TABS.find((t) => t.ctrlDigit === digit)
      if (!target) return
      e.preventDefault()
      setTabId(target.id)
    }
    // The tab-switching keys are the shell's, and this page stays mounted --
    // Ctrl+1..9 from the Sales screen used to move Settings underneath it.
    if (!active) return
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tabId, tab.sections, active])

  async function save(
    section:
      | 'appearance'
      | 'layout_lists'
      | 'sales_billing'
      | 'pharmacy'
      | 'system'
      | 'import',
    body: Record<string, unknown>,
  ) {
    setSaving(true)
    setMsg('')
    try {
      const next = await saveSettingsSection(section, body)
      // Going Online can be REFUSED: a different store on the server already
      // answers to this store's name, and pairing to it would hand this shop
      // that shop's books. The engine has always said so with a 409 carrying
      // the other store's name and id; the browser threw it away as a bare
      // Error, so the mode looked like it had changed when it had not.
      if (
        section === 'system' &&
        (next as { ok?: boolean }).ok === false &&
        (next as { code?: string }).code === 'store_name_taken_on_server'
      ) {
        const remoteName = String((next as { remote_store_name?: string }).remote_store_name || '')
        setMsg(
          `${String((next as { error?: string }).error || 'This store was not switched to Online.')} ` +
            'This PC has NOT been switched to Online. If ' +
            (remoteName ? `"${remoteName}" ` : 'that store ') +
            'is this shop, open Data & System → Stores and use ' +
            '"Connect this PC to a store on the server".',
        )
        return
      }
      if (section === 'appearance') {
        // Prefer the theme we just wrote (from request body), not a stale UI copy.
        const requestedTheme =
          typeof body.theme === 'string' ? String(body.theme).trim() : ''
        const returnedTheme =
          typeof next.theme === 'string' ? String(next.theme).trim() : ''
        const savedTheme = requestedTheme || returnedTheme
        const requestedPack =
          typeof body.theme_pack === 'string'
            ? String(body.theme_pack).trim()
            : ''
        const returnedPack =
          typeof next.theme_pack === 'string'
            ? String(next.theme_pack).trim()
            : ''
        const savedPack = requestedPack || returnedPack || 'classic'
        setAppearance({
          ...next,
          ...(savedTheme ? { theme: savedTheme } : {}),
          theme_pack: savedPack,
        })
        onPrefsChange({
          show_nav_shortcut_keys: Boolean(next.show_nav_shortcut_keys),
          theme_pack: savedPack,
          table_text_marquee: Boolean(next.table_text_marquee),
        })
        if (savedTheme) {
          // Apply locally — disk already updated by saveSettingsSection.
          if (onThemeChange) onThemeChange(savedTheme)
          // Ensure disk matches requested theme (guards stale overwrites).
          if (onThemeSave && requestedTheme && returnedTheme !== requestedTheme) {
            await onThemeSave(requestedTheme)
          } else if (onThemeSave && requestedTheme) {
            // Still sync localStorage / data-theme attribute.
            await onThemeSave(requestedTheme)
          }
        }
        if (onThemePackChange) onThemePackChange(savedPack)
        const fontSize = Number(body.font_size ?? next.font_size ?? 10)
        applyFontSize(fontSize)
        setMsg(
          savedTheme
            ? `Appearance saved. Theme: ${savedTheme} · Pack: ${savedPack}`
            : 'Appearance saved.',
        )
        window.dispatchEvent(new Event('satpuda:home-banner-changed'))
        await reload()
        // After reload, force the theme we saved (don't let disk lag flip UI).
        if (savedTheme) {
          setAppearance((prev) => ({
            ...prev,
            theme: savedTheme,
            theme_pack: savedPack,
            font_size: fontSize,
          }))
          if (onThemeChange) onThemeChange(savedTheme)
        }
        if (onThemePackChange) onThemePackChange(savedPack)
        applyFontSize(fontSize)
        return
      }
      if (section === 'layout_lists') {
        setLayoutLists(next)
        window.dispatchEvent(
          new CustomEvent('satpuda:layout-config-changed', { detail: next }),
        )
      }
      if (section === 'sales_billing') {
        setSalesBilling(next)
        // The Sales screen listens for this; without it the new billing layout
        // only appeared after a save or a restart.
        window.dispatchEvent(new Event('satpuda:layout-config-changed'))
      }
      if (section === 'system') {
        setSystem(next)
        // Tell the app the mode may have changed. The Online/Offline chip in the
        // top bar was painted once at startup from /api/meta and nothing ever
        // asked again, so switching mode left it showing the old one until the
        // app was restarted -- and the shop had no way to tell which mode it
        // was actually in.
        window.dispatchEvent(new CustomEvent('satpuda:sync-mode-changed'))
        if (next.online_bootstrap_pending) {
          setOnlineSwitchBusy(true)
          setOnlineSwitchMsg(
            String(next.message || 'Connecting to server in the background…'),
          )
          setMsg(
            String(
              next.message ||
                'Online mode saved — connecting in background…',
            ),
          )
          // Poll progress so UI shows clear step status (server-first switch is quick)
          const { systemAction } = await import('../settingsApi')
          const started = Date.now()
          while (Date.now() - started < 3 * 60 * 1000) {
            await new Promise((r) => setTimeout(r, 400))
            try {
              const st = await systemAction({ action: 'online_switch_status' })
              const step = Number(st.step || 0)
              const total = Number(st.steps_total || 4)
              const raw = String(st.message || 'Working…')
              const message =
                step > 0 && !raw.startsWith(`${step}/`)
                  ? `${step}/${total} ${raw}`
                  : raw
              setOnlineSwitchMsg(message)
              if (st.done) {
                setOnlineSwitchBusy(false)
                setMsg(
                  st.ok === false
                    ? `Online switch issue: ${st.error || message}`
                    : st.needs_migrate
                      ? 'Local data found — choose Push to Server or Delete Local.'
                      : st.server_sync_started
                        ? 'Online ready — lists load from server; live hints started.'
                        : message || 'Online mode saved.',
                )
                if (st.needs_migrate) {
                  window.dispatchEvent(new CustomEvent('satpuda:online-migrate'))
                  return
                }
                await reload()
                return
              }
            } catch {
              /* keep polling */
            }
          }
          setOnlineSwitchBusy(false)
          setMsg(
            'Online connect is still running in the background (check server/internet).',
          )
          return
        }
      }
      if (section === 'import') setImportPrefs(next)
      if (section === 'pharmacy') {
        const ph = next as {
          profile?: Record<string, unknown>
          bill?: Record<string, unknown>
          login?: Record<string, unknown>
          printer?: Record<string, unknown>
        }
        if (ph.profile) setProfile(ph.profile)
        if (ph.bill) setBill(ph.bill)
        if (ph.login) setLogin({ ...ph.login, password: '' })
        if (ph.printer) setPrinter(ph.printer)
        setMsg('Saved — same files as the Python app.')
        await reload()
        return
      }
      setMsg('Saved.')
      await reload()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const opts = bundle?.options
  const schedules = useMemo(() => {
    const raw = layoutLists.schedules
    if (Array.isArray(raw)) return raw.map(String)
    return []
  }, [layoutLists.schedules])

  function renderSectionContent() {
    if (!bundle || !opts) {
      return (
        <p className="muted">
          {error
            ? 'Local data unavailable — see message above.'
            : 'Starting local data… settings will appear in a moment.'}
        </p>
      )
    }

    if (tabId === 'pharmacy') {
      return (
        <PharmacyPanel
          sectionId={sectionId}
          opts={opts}
          profile={profile}
          setProfile={setProfile}
          bill={bill}
          setBill={setBill}
          login={login}
          setLogin={setLogin}
          printer={printer}
          setPrinter={setPrinter}
          installedPrinters={bundle.pharmacy?.installed_printers || []}
          onInstalledPrintersChange={(printers) =>
            setBundle((b) =>
              b?.pharmacy
                ? {
                    ...b,
                    pharmacy: { ...b.pharmacy, installed_printers: printers },
                  }
                : b,
            )
          }
          printLogPath={bundle.pharmacy?.print_log_path}
          spoolerRunning={bundle.pharmacy?.spooler_running}
          saving={saving}
          onSave={(body) => save('pharmacy', body)}
        />
      )
    }

    if (tabId === 'appearance') {
      return (
        <AppearancePanel
          sectionId={sectionId}
          appearance={appearance}
          setAppearance={(updater) => {
            setAppearance((prev) => {
              const next =
                typeof updater === 'function' ? updater(prev) : updater
              // Keep ref in sync immediately so Save never sends a stale theme.
              appearanceRef.current = next
              if (
                onThemeChange &&
                typeof next.theme === 'string' &&
                next.theme !== prev.theme
              ) {
                const theme = String(next.theme)
                queueMicrotask(() => onThemeChange(theme))
              }
              if (
                onThemePackChange &&
                typeof next.theme_pack === 'string' &&
                next.theme_pack !== prev.theme_pack
              ) {
                const pack = String(next.theme_pack)
                queueMicrotask(() => onThemePackChange(pack))
              }
              return next
            })
          }}
          opts={opts}
          saving={saving}
          onSaveAppearance={() => {
            // Always read latest appearance (avoids stale closure sending old theme).
            const latest = appearanceRef.current
            const theme = String(latest.theme || '').trim()
            const theme_pack = String(latest.theme_pack || prefs.theme_pack || 'classic').trim()
            void save('appearance', { ...latest, theme, theme_pack })
          }}
          prefs={prefs}
          currentTheme={currentTheme}
          currentThemePack={currentThemePack}
          onPrefsChange={onPrefsChange}
          onRequestRestart={(message) =>
            setRestartPrompt({
              title: 'Restart App',
              message:
                message ||
                'Restart the desktop app to fully apply this change.',
            })
          }
        />
      )
    }

    if (tabId === 'layout_lists') {
      return (
        <LayoutListsPanel
          sectionId={sectionId}
          layoutLists={layoutLists}
          setLayoutLists={setLayoutLists}
          opts={opts}
          bundle={bundle}
          saving={saving}
          onSaveLayout={() => save('layout_lists', layoutLists)}
          onThresholdsChange={(t) =>
            // Drop the cached alert lists too: they were computed with the OLD
            // thresholds, so the shop saved a new number, went to look, and saw
            // a byte-identical list -- which reads as "the setting did nothing".
            setBundle((b) => (b ? { ...b, thresholds: t, alerts: undefined } : b))
          }
        />
      )
    }

    if (tabId === 'sales_billing') {
      return (
        <SalesBillingPanel
          sectionId={sectionId}
          salesBilling={salesBilling}
          setSalesBilling={setSalesBilling}
          opts={opts}
          saving={saving}
          onSavePartial={(partial) => save('sales_billing', partial)}
        />
      )
    }

    if (tabId === 'import') {
      return (
        <ImportPanel
          sectionId={sectionId}
          importPrefs={importPrefs}
          setImportPrefs={setImportPrefs}
          opts={opts}
          schedules={schedules}
          saving={saving}
          onSaveImport={() => save('import', importPrefs)}
        />
      )
    }

    if (tabId === 'data_system') {
      return (
        <DataSystemPanel
          sectionId={sectionId}
          system={system}
          setSystem={setSystem}
          opts={opts}
          saving={saving}
          onSaveSystem={() => save('system', system)}
          onStoreSwitched={async () => {
            await reload()
            await onStoreSwitched?.()
            // Classic always restarts after store switch; do the same here.
            // Soft remount + restart is handled in App.onStoreSwitched.
          }}
          onRequestRestart={(message) =>
            setRestartPrompt({
              title: 'Restart App',
              message:
                message ||
                'Restart the desktop app to fully apply this change.',
            })
          }
        />
      )
    }

    if (tabId === 'contacts') {
      if (!bundle.contacts) {
        return <p className="muted">Loading contacts…</p>
      }
      return (
        <ContactsPanel
          sectionId={sectionId}
          contacts={bundle.contacts}
          onChange={(c) => setBundle((b) => (b ? { ...b, contacts: c } : b))}
        />
      )
    }

    if (tabId === 'shortcuts') {
      return sectionId === 'voice' && voiceOn ? <VoiceCommandsPanel /> : <ShortcutsPanel />
    }

    if (tabId === 'alerts') {
      if (nestedId === 'setup') {
        return (
          <AlertPrefsPanel
            counts={bundle.alerts?.counts}
            onThresholdsChange={(t) =>
              // New thresholds make the cached lists stale; drop them so the
              // lists recount instead of showing yesterday's answer.
              setBundle((b) => (b ? { ...b, thresholds: t, alerts: undefined } : b))
            }
          />
        )
      }
      if (!bundle.alerts) {
        return <p className="muted">Loading alerts…</p>
      }
      return (
        <AlertsPanel
          nestedId={nestedId}
          alerts={bundle.alerts}
          onRefresh={() => void reload()}
          onAlertsChange={(alerts) =>
            setBundle((b) => (b ? { ...b, alerts } : b))
          }
          onNavigate={onNavigate}
        />
      )
    }

    if (tabId === 'payment') {
      const kind = toggleId === 'customer' ? 'customer' : 'supplier'
      const initial =
        kind === 'customer'
          ? bundle.payments_customer
          : bundle.payments_supplier
      if (!initial) {
        return <p className="muted">Loading payments…</p>
      }
      return (
        <PaymentPanel
          key={kind}
          kind={kind}
          initial={initial}
          paymentModes={opts.payment_modes || ['Cash', 'Online', 'UPI']}
          onChange={(p) =>
            setBundle((b) =>
              b
                ? {
                    ...b,
                    ...(kind === 'customer'
                      ? { payments_customer: p }
                      : { payments_supplier: p }),
                  }
                : b,
            )
          }
        />
      )
    }

    if (tabId === 'ledger') {
      const kind = toggleId === 'customer' ? 'customer' : 'supplier'
      return <LedgerPanel key={kind} kind={kind} refreshNonce={syncRefreshNonce} />
    }

    if (tabId === 'reorder' && bundle.reorder) {
      return (
        <ReorderPanel
          reorder={bundle.reorder}
          mode={toggleId}
          medicinePrefill={reorderMedicinePrefill}
          bulkReorderLoad={bulkReorderLoad}
          onChange={(r) => setBundle((b) => (b ? { ...b, reorder: r } : b))}
          onOpenPurchase={(prefill: ReorderPrefill) => {
            onNavigate?.('purchase', { reorderPrefill: prefill })
          }}
        />
      )
    }

    if (tabId === 'shelf' && bundle.shelf) {
      return (
        <ShelfPanel
          shelf={bundle.shelf}
          sectionId={sectionId}
          onChange={(s) => setBundle((b) => (b ? { ...b, shelf: s } : b))}
        />
      )
    }

    return (
      <>
        <h3 className="settings-panel-title">{tab.label}</h3>
        <p className="settings-note">Loading…</p>
      </>
    )
  }

  return (
    // Everything under Settings stays mounted with the rest of the app. Without
    // this, the panels' own F5/F2/Enter handlers kept firing from behind the
    // Sales screen -- saving a bill also ran the hidden Contacts save.
    <PageActiveContext.Provider value={active}>
    <div className="settings-root">
      {/* Developer wording, so never on the demonstration site. */}
      {error && !IS_DEMO ? (
        <div className="error">
          Could not load settings from the same store as the Python app: {error}.
          Close this window and run <code>npm run tauri:dev</code> again (it
          replaces a stale local engine automatically).
        </div>
      ) : null}
      <div className="settings-notebook" role="tablist">
        {SETTINGS_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={t.id === tabId}
            className={`settings-tab${t.id === tabId ? ' active' : ''}`}
            onClick={() => setTabId(t.id)}
            title={
              t.ctrlDigit != null ? `${t.label} (Ctrl+${t.ctrlDigit})` : t.label
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="settings-body">
        {onlineSwitchBusy ? (
          <div
            className="settings-panel"
            style={{
              marginBottom: 10,
              padding: '12px 14px',
              border: '1px solid var(--border, #ccc)',
              background: 'var(--panel-bg, #f7f7f7)',
            }}
            role="status"
            aria-live="polite"
          >
            <strong>Switching to Online…</strong>
            <p className="settings-note" style={{ margin: '6px 0 0' }}>
              {onlineSwitchMsg || 'Connecting to Satpuda Core Server…'}
            </p>
            <p className="muted" style={{ margin: '4px 0 0' }}>
              Steps: check server → pair store → adopt server revision → start
              live hints. No full database download into SQLite.
            </p>
          </div>
        ) : null}
        {tab.layout === 'toggle' && tab.toggles ? (
          <div className="settings-toggle-bar">
            {tab.toggles.map((tg) => (
              <button
                key={tg.id}
                type="button"
                className={`settings-toggle-btn${toggleId === tg.id ? ' active' : ''}`}
                onClick={() => setToggleId(tg.id)}
              >
                {tg.label}
              </button>
            ))}
          </div>
        ) : null}

        {tab.layout === 'nested' && tab.nestedTabs ? (
          <div className="settings-nested-tabs">
            {tab.nestedTabs.map((nt) => (
              <button
                key={nt.id}
                type="button"
                className={`settings-nested-tab${nestedId === nt.id ? ' active' : ''}`}
                onClick={() => setNestedId(nt.id)}
              >
                {nt.label}
              </button>
            ))}
          </div>
        ) : null}

        <div
          className={
            tab.layout === 'sections' || tab.layout === 'split'
              ? 'settings-sections-shell'
              : 'settings-section-content settings-section-content-full'
          }
        >
          {(tab.layout === 'sections' || tab.layout === 'split') &&
          tab.sections ? (
            <fieldset className="labelframe settings-sections-nav">
              <legend>Sections</legend>
              <div className="settings-section-btns" ref={sectionNavRef}>
                {tab.sections.map((s, idx) => (
                  <button
                    key={s.id}
                    type="button"
                    className={`settings-section-btn${sectionId === s.id ? ' active' : ''}`}
                    onClick={() => setSectionId(s.id)}
                    onKeyDown={(e) => {
                      const btns = Array.from(
                        sectionNavRef.current?.querySelectorAll<HTMLButtonElement>(
                          '.settings-section-btn',
                        ) || [],
                      )
                      if (!btns.length) return
                      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                        e.preventDefault()
                        e.stopPropagation()
                        const next =
                          e.key === 'ArrowDown'
                            ? (idx + 1) % btns.length
                            : (idx - 1 + btns.length) % btns.length
                        btns[next]?.focus()
                        return
                      }
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        e.stopPropagation()
                        setSectionId(s.id)
                        // Hand form focus to the first field after paint.
                        window.setTimeout(() => {
                          const first =
                            document.querySelector<HTMLElement>(
                              '.settings-panel [data-nav-order="2"]',
                            ) ||
                            document.querySelector<HTMLElement>(
                              '.settings-panel [data-nav-order="1"]',
                            ) ||
                            document.querySelector<HTMLElement>(
                              '.settings-panel input:not([disabled])',
                            )
                          first?.focus()
                        }, 30)
                      }
                    }}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </fieldset>
          ) : null}
          <div
            className={
              tab.layout === 'sections' || tab.layout === 'split'
                ? 'settings-section-content'
                : undefined
            }
          >
            <div className="settings-panel">{renderSectionContent()}</div>
            {msg ? (
              <p className="muted" style={{ marginTop: 8 }}>
                {msg}
              </p>
            ) : null}
          </div>
        </div>
      </div>
      <RestartAppDialog
        prompt={restartPrompt}
        onClose={() => setRestartPrompt(null)}
        onRestart={() => restartDesktopApp()}
      />
    </div>
    </PageActiveContext.Provider>
  )
}
