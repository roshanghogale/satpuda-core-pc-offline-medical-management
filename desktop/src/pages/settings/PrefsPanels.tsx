/** Appearance, Layout & Lists, Sales & Billing — mirrors Tk settings tabs. */

import { useRef, useState } from 'react'
import type { DesktopUiPrefs } from '../../api'
import {
  imageFileToBase64,
  uploadHomeBanner,
  saveSettingsSection,
  type SettingsBundle,
} from '../../settingsApi'
import { applyFontSize } from '../../theme'
import { ThresholdsPanel } from './InteractivePanels'
import { BannerWidthField } from './BannerWidthField'
import {
  Check,
  Field,
  Frame,
  Note,
  PanelTitle,
  SaveBtn,
} from './SettingsChrome'

type Opts = SettingsBundle['options']

const ROW_FIELDS = [
  ['billing_rows', 'Sales / Billing rows', 4, 30],
  ['inventory_rows', 'Inventory rows', 5, 50],
  ['sales_history_rows', 'Sales History rows', 5, 50],
  ['purchase_history_rows', 'Purchase History rows', 5, 50],
  ['purchase_rows', 'Purchase rows', 2, 20],
  ['customers_rows', 'Customers rows', 5, 50],
  ['doctors_rows', 'Doctors rows', 2, 20],
  ['suppliers_rows', 'Suppliers rows', 2, 20],
] as const

const SALES_PARTIAL_KEYS: Record<string, string[]> = {
  billing_layout: [
    'history_scope',
    'payment_mode_enabled',
    'payment_mode_position',
    'item_discount_mode',
    'billing_show_item_discount',
    'billing_show_margin_column',
    'billing_show_total_margin',
    'billing_margin_loss_warning',
    'billing_margin_display_mode',
    'require_doctor_for_other_schedules',
  ],
  batch_picker: ['batch_sort_order', 'show_zero_stock'],
  sales_return: ['sales_return_lookup_days'],
  sales_bills: ['pdf_save_layout', 'sales_bill_save_dir'],
  upi_payment: ['upi_qr_enabled', 'upi_id', 'upi_qr_amount_mode'],
  autosave: ['autosave_enabled', 'autosave_interval_seconds'],
}

const SALES_SAVE_LABELS: Record<string, string> = {
  billing_layout: 'Save Billing Layout & FY',
  batch_picker: 'Save Batch Picker Settings',
  sales_return: 'Save Sales Return Settings',
  sales_bills: 'Save Sales Bill Settings',
  upi_payment: 'Save UPI Settings',
  autosave: 'Save Autosave Settings',
}

function pickKeys(
  src: Record<string, unknown>,
  keys: string[],
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const k of keys) {
    if (k in src) out[k] = src[k]
  }
  return out
}

export function AppearancePanel({
  sectionId,
  appearance,
  setAppearance,
  opts,
  saving,
  onSaveAppearance,
  prefs,
  currentTheme,
  currentThemePack,
  onPrefsChange,
  onRequestRestart,
}: {
  sectionId: string
  appearance: Record<string, unknown>
  setAppearance: React.Dispatch<React.SetStateAction<Record<string, unknown>>>
  opts: Opts
  saving: boolean
  onSaveAppearance: () => void
  prefs: DesktopUiPrefs
  currentTheme?: string
  currentThemePack?: string
  onPrefsChange: (prefs: DesktopUiPrefs) => void
  onRequestRestart?: (message?: string) => void
}) {
  const [bannerBusy, setBannerBusy] = useState(false)
  const bannerInputRef = useRef<HTMLInputElement>(null)
  const [bannerNote, setBannerNote] = useState('')
  const qa = (appearance.quick_access || {}) as Record<string, boolean>
  const dash = (appearance.dashboard_sections || {}) as Record<string, boolean>
  const packOpts =
    opts.theme_packs && opts.theme_packs.length
      ? opts.theme_packs
      : [
          { value: 'classic', label: 'Classic — current palettes' },
          {
            value: 'accessible',
            label: 'Accessible — Gemini contrast + clearer chrome',
          },
          {
            value: 'modern',
            label: 'Modern — ChatGPT deep surfaces & accents',
          },
        ]

  /** Same story as the bill logo: the engine cannot open a picker, so the
   *  browser opens it and sends the bytes. The old call reached a tkinter
   *  import that is not in the shipped engine, and the shop got the raw
   *  "No module named 'tkinter'" in place of a banner. */
  const uploadBanner = async (file: File) => {
    setBannerBusy(true)
    setBannerNote('')
    try {
      const data = await imageFileToBase64(file)
      const res = await uploadHomeBanner(file.name, data)
      if (!res.ok) {
        setBannerNote(res.error || 'Could not use that picture.')
        return
      }
      const path = String(res.home_banner_path || res.path || '')
      setAppearance((a) => ({
        ...a,
        home_banner_path: path,
        home_banner_use_default: false,
      }))
      setBannerNote(res.message || 'Banner selected. Save Appearance to keep other settings.')
      window.dispatchEvent(new Event('satpuda:home-banner-changed'))
    } catch (e) {
      setBannerNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBannerBusy(false)
    }
  }

  const saveBtn = (
    <>
      <SaveBtn
        label="Save Appearance"
        saving={saving}
        onClick={onSaveAppearance}
      />
      {onRequestRestart ? (
        <button
          type="button"
          className="settings-action-btn"
          style={{ marginLeft: 8 }}
          onClick={() =>
            onRequestRestart(
              'Restart the desktop app to fully reload all pages and the local data engine.',
            )
          }
        >
          Restart App…
        </button>
      ) : null}
      <Note>
        Theme and pack preview update instantly when you change the lists — no
        restart. Click Save Appearance to keep them after you close the app.
        Font size and the banner width also apply live. Quick Access and the
        Dashboard Sections apply as soon as you press Save Appearance.
      </Note>
    </>
  )

  if (sectionId === 'theme') {
    return (
      <>
        <PanelTitle>Theme</PanelTitle>
        <Frame title="Theme">
          <Field label="Theme pack">
            <select
              className="settings-input"
              value={String(
                appearance.theme_pack ||
                  currentThemePack ||
                  prefs.theme_pack ||
                  'classic',
              )}
              onChange={(e) => {
                const theme_pack = e.target.value
                setAppearance((a) => ({ ...a, theme_pack }))
                onPrefsChange({ ...prefs, theme_pack })
              }}
            >
              {packOpts.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Theme">
            <select
              className="settings-input"
              value={String(
                appearance.theme || currentTheme || 'navy-light',
              )}
              onChange={(e) => {
                const theme = e.target.value
                setAppearance((a) => ({ ...a, theme }))
              }}
            >
              <optgroup label="Dark">
                {opts.themes
                  .filter((o) => String(o.value).endsWith('-dark'))
                  .map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
              </optgroup>
              <optgroup label="Light">
                {opts.themes
                  .filter((o) => String(o.value).endsWith('-light'))
                  .map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
              </optgroup>
            </select>
          </Field>
          <Check
            label="Show shortcut numbers on top navigation buttons"
            checked={Boolean(
              appearance.show_nav_shortcut_keys ?? prefs.show_nav_shortcut_keys,
            )}
            onChange={(v) => {
              setAppearance((a) => ({ ...a, show_nav_shortcut_keys: v }))
              onPrefsChange({ ...prefs, show_nav_shortcut_keys: v })
            }}
          />
          <Check
            label="Scroll long text in tables (marquee)"
            checked={Boolean(
              appearance.table_text_marquee ?? prefs.table_text_marquee,
            )}
            onChange={(v) => {
              setAppearance((a) => ({ ...a, table_text_marquee: v }))
              onPrefsChange({ ...prefs, table_text_marquee: v })
            }}
          />
          <Note>
            When off (default), long medicine or customer names show ellipsis
            only. Turn on to animate scrolling in inventory and history lists.
          </Note>
        </Frame>
        <Note>
          Packs rematch the same 20 themes: Classic (original), Accessible
          (clearer chrome + high-contrast status), Modern (deeper dark / soft
          light panels + vivid accents). Use Dark / Use Light still flips the
          family variant only.
        </Note>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'font') {
    return (
      <>
        <PanelTitle>Font Size</PanelTitle>
        <Frame title="Font Size">
          <Field label="Font size (7–20)">
            <input
              className="settings-input"
              type="number"
              min={7}
              max={20}
              value={Number(appearance.font_size ?? 10)}
              onChange={(e) => {
                const font_size = Number(e.target.value)
                setAppearance((a) => ({
                  ...a,
                  font_size,
                }))
                applyFontSize(font_size)
              }}
            />
          </Field>
          <Note>
            Size {Number(appearance.font_size ?? 10)} — UI scales as you change
            the value; Save Appearance writes it permanently.
          </Note>
        </Frame>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'banner') {
    const useDefault = Boolean(appearance.home_banner_use_default)
    return (
      <>
        <PanelTitle>Home Banner</PanelTitle>
        <Frame title="Home Banner">
          <BannerWidthField
            appearance={appearance}
            setAppearance={setAppearance}
          />
          <Check
            label="Use default banner (assets/home_banner.png)"
            checked={useDefault}
            onChange={(v) =>
              setAppearance((a) => ({
                ...a,
                home_banner_use_default: v,
                ...(v ? { home_banner_path: '' } : {}),
              }))
            }
          />
          <Field label="Custom banner path">
            <div className="settings-inline-row">
              <input
                className="settings-input"
                value={String(appearance.home_banner_path ?? '')}
                readOnly
                disabled={useDefault}
                placeholder="Choose an image with Browse…"
              />
              <button
                type="button"
                className="settings-action-btn"
                disabled={useDefault || bannerBusy}
                // Straight from the click: an await first would spend the
                // browser's user activation and the picker would not open.
                onClick={() => bannerInputRef.current?.click()}
              >
                Browse…
              </button>
              <input
                ref={bannerInputRef}
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp,image/bmp"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  e.target.value = ''
                  if (f) void uploadBanner(f)
                }}
              />
              <button
                type="button"
                className="settings-action-btn"
                disabled={useDefault || bannerBusy}
                onClick={() => {
                  void (async () => {
                    setBannerBusy(true)
                    setBannerNote('')
                    try {
                      const next = {
                        ...appearance,
                        home_banner_path: '',
                        home_banner_use_default: true,
                      }
                      setAppearance(next)
                      await saveSettingsSection('appearance', {
                        home_banner_use_default: true,
                        home_banner_path: '',
                        home_banner_size: Number(
                          appearance.home_banner_size ?? 1500,
                        ),
                      })
                      setBannerNote('Using default banner (assets/home_banner.png).')
                      window.dispatchEvent(
                        new Event('satpuda:home-banner-changed'),
                      )
                    } catch (e) {
                      setBannerNote(
                        e instanceof Error ? e.message : String(e),
                      )
                    } finally {
                      setBannerBusy(false)
                    }
                  })()
                }}
              >
                Clear
              </button>
            </div>
          </Field>
          {bannerNote ? <Note>{bannerNote}</Note> : null}
          <Note>
            Browse copies the image into the app config folder (same as the Python
            app). After Browse or Clear, open Home to see the banner (or click
            Retry if it was blank).
          </Note>
        </Frame>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'quick_access') {
    return (
      <>
        <PanelTitle>Quick Access</PanelTitle>
        <Frame title="Quick Access buttons">
          {opts.quick_access.map((q) => (
            <Check
              key={q.key}
              label={q.label}
              checked={qa[q.key] !== false}
              onChange={(v) =>
                setAppearance((a) => ({
                  ...a,
                  quick_access: { ...qa, [q.key]: v },
                }))
              }
            />
          ))}
        </Frame>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'dashboard') {
    return (
      <>
        <PanelTitle>Dashboard Sections</PanelTitle>
        <Frame title="Home dashboard sections">
          {opts.dashboard_sections.map((d) => (
            <Check
              key={d.key}
              label={d.label}
              checked={dash[d.key] !== false}
              onChange={(v) =>
                setAppearance((a) => ({
                  ...a,
                  dashboard_sections: { ...dash, [d.key]: v },
                }))
              }
            />
          ))}
        </Frame>
        {saveBtn}
      </>
    )
  }

  return <p className="muted">Unknown appearance section.</p>
}

export function LayoutListsPanel({
  sectionId,
  layoutLists,
  setLayoutLists,
  opts,
  bundle,
  saving,
  onSaveLayout,
  onThresholdsChange,
}: {
  sectionId: string
  layoutLists: Record<string, unknown>
  setLayoutLists: React.Dispatch<React.SetStateAction<Record<string, unknown>>>
  opts: Opts
  bundle: SettingsBundle
  saving: boolean
  onSaveLayout: () => void
  onThresholdsChange: (
    t: NonNullable<SettingsBundle['thresholds']>,
  ) => void
}) {
  const pages = opts.column_pages || []
  const [exportPage, setExportPage] = useState(pages[0]?.key || '')

  const exportPageKey = exportPage || pages[0]?.key || ''
  // The export report's OWN columns. Using the screen list here meant an
  // export-only column such as "Returns" or "Month" never had a tick at all.
  const exportCols =
    opts.export_columns?.[exportPageKey] || opts.table_columns?.[exportPageKey] || []
  const exportVisRoot = (layoutLists.export_column_visibility || {}) as Record<
    string,
    Record<string, unknown>
  >
  // The store keeps export ticks per REPORT ({report: {col: bool}}); this panel
  // offers one set per page. It used to read and write the flat shape, so as
  // soon as any nested value existed -- the defaults, or the old Tk screen --
  // the engine kept only the nested entries and threw every tick made here
  // away. Read through the nested shape, and write into every report below.
  const exportPageVis: Record<string, boolean> = {}
  for (const [k, v] of Object.entries(exportVisRoot[exportPageKey] || {})) {
    if (typeof v === 'boolean') exportPageVis[k] = v
    else if (v && typeof v === 'object') {
      for (const [ck, cv] of Object.entries(v as Record<string, unknown>)) {
        if (typeof cv === 'boolean' && !(ck in exportPageVis)) exportPageVis[ck] = cv
      }
    }
  }

  if (sectionId === 'thresholds') {
    if (!bundle.thresholds) {
      return <p className="muted">Thresholds unavailable (DB offline).</p>
    }
    return (
      <ThresholdsPanel
        thresholds={bundle.thresholds}
        onChange={onThresholdsChange}
      />
    )
  }

  const saveBtn = (
    <SaveBtn
      label="Save Layout & Lists"
      saving={saving}
      onClick={onSaveLayout}
    />
  )

  if (sectionId === 'columns') {
    return (
      <>
        <PanelTitle>Column Visibility</PanelTitle>
        <Frame title="On-screen table columns">
          <Note>
            Check columns to show on each screen (same as Tk Column Visibility).
          </Note>
          {pages.map((page) => {
            const vis = (layoutLists.column_visibility || {}) as Record<
              string,
              Record<string, boolean>
            >
            const pageVis = vis[page.key] || {}
            const cols = opts.table_columns?.[page.key] || []
            return (
              <div key={page.key} className="settings-column-page">
                <h4>{page.label}</h4>
                <div className="settings-check-grid">
                  {cols.map((col) => (
                    <Check
                      key={col.key}
                      label={col.label}
                      // An unsaved key is not automatically "shown": a few
                      // columns ship hidden, and rendering those ticked told
                      // the shop the opposite of what the screen was doing.
                      checked={
                        pageVis[col.key] ??
                        opts.column_defaults?.[page.key]?.[col.key] ??
                        true
                      }
                      onChange={(v) =>
                        setLayoutLists((l) => ({
                          ...l,
                          column_visibility: {
                            ...((l.column_visibility as object) || {}),
                            [page.key]: {
                              ...(((l.column_visibility as Record<
                                string,
                                Record<string, boolean>
                              >) || {})[page.key] || {}),
                              [col.key]: v,
                            },
                          },
                        }))
                      }
                    />
                  ))}
                </div>
              </div>
            )
          })}
        </Frame>
        <Frame title="Export report columns">
          <Field label="Page">
            <select
              className="settings-input"
              value={exportPageKey}
              onChange={(e) => setExportPage(e.target.value)}
            >
              {pages.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
          </Field>
          <div className="settings-check-grid">
            {exportCols.map((col) => (
              <Check
                key={col.key}
                label={col.label}
                checked={exportPageVis[col.key] !== false}
                onChange={(v) =>
                  setLayoutLists((l) => {
                    const root = {
                      ...((l.export_column_visibility as Record<
                        string,
                        Record<string, unknown>
                      >) || {}),
                    }
                    const reports = opts.export_reports?.[exportPageKey] || []
                    const cur: Record<string, unknown> = {
                      ...(root[exportPageKey] || {}),
                    }
                    // Same tick into every report for this page, because that
                    // is the shape the engine reads and this panel has no
                    // per-report control.
                    for (const rk of reports) {
                      const existing = cur[rk]
                      cur[rk] = {
                        ...(existing && typeof existing === 'object'
                          ? (existing as Record<string, boolean>)
                          : exportPageVis),
                        [col.key]: v,
                      }
                    }
                    // Drop any stray flat booleans left by the old shape.
                    for (const k of Object.keys(cur)) {
                      if (typeof cur[k] === 'boolean') delete cur[k]
                    }
                    root[exportPageKey] = cur
                    return { ...l, export_column_visibility: root }
                  })
                }
              />
            ))}
          </div>
          {!exportCols.length ? (
            <Note>No columns for this page.</Note>
          ) : null}
        </Frame>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'rows') {
    return (
      <>
        <PanelTitle>Table Row Counts</PanelTitle>
        <Frame title="Visible rows">
          <Note>
            How many rows are visible in each page list (table height). Extra
            rows stay in the list — scroll inside the table. Save, then open
            the page to see the new height.
          </Note>
          {ROW_FIELDS.map(([k, lab, mn, mx]) => (
            <Field key={k} label={lab}>
              <input
                className="settings-input"
                type="number"
                min={mn}
                max={mx}
                value={Number(layoutLists[k] ?? 0)}
                onChange={(e) =>
                  setLayoutLists((l) => ({
                    ...l,
                    [k]: Number(e.target.value),
                  }))
                }
              />
            </Field>
          ))}
        </Frame>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'med_types') {
    return (
      <>
        <PanelTitle>Medicine Types</PanelTitle>
        <Frame title="Medicine types">
          <Field label="Medicine types (one per line)">
            <textarea
              className="settings-input settings-textarea"
              rows={10}
              value={
                (layoutLists.med_types as string[] | undefined)?.join('\n') ||
                ''
              }
              onChange={(e) =>
                setLayoutLists((l) => ({
                  ...l,
                  med_types: e.target.value
                    .split('\n')
                    .map((x) => x.trim())
                    .filter(Boolean),
                }))
              }
            />
          </Field>
        </Frame>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'units') {
    return (
      <>
        <PanelTitle>Medicine Units</PanelTitle>
        <Frame title="Units & default qty">
          <div className="settings-grid-3">
            {((layoutLists.med_types as string[]) || []).map((mt) => {
              const units = (layoutLists.units || {}) as Record<
                string,
                { unit?: string; default_qty?: number }
              >
              const cfg = units[mt] || { unit: '', default_qty: 0 }
              return (
                <div key={mt} className="settings-unit-card">
                  <strong>{mt}</strong>
                  <Field label="Unit">
                    <input
                      className="settings-input"
                      value={cfg.unit || ''}
                      onChange={(e) =>
                        setLayoutLists((l) => ({
                          ...l,
                          units: {
                            ...((l.units as object) || {}),
                            [mt]: { ...cfg, unit: e.target.value },
                          },
                        }))
                      }
                    />
                  </Field>
                  <Field label="Default qty">
                    <input
                      className="settings-input"
                      type="number"
                      value={cfg.default_qty ?? 0}
                      onChange={(e) =>
                        setLayoutLists((l) => ({
                          ...l,
                          units: {
                            ...((l.units as object) || {}),
                            [mt]: {
                              ...cfg,
                              default_qty: Number(e.target.value),
                            },
                          },
                        }))
                      }
                    />
                  </Field>
                </div>
              )
            })}
          </div>
        </Frame>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'schedules') {
    return (
      <>
        <PanelTitle>Schedules</PanelTitle>
        <Frame title="Schedules">
          <Field label="Schedules (one per line; blank line = empty schedule)">
            <textarea
              className="settings-input settings-textarea"
              rows={8}
              value={
                (layoutLists.schedules as string[] | undefined)?.join('\n') ||
                ''
              }
              onChange={(e) =>
                setLayoutLists((l) => ({
                  ...l,
                  schedules: e.target.value.split('\n'),
                }))
              }
            />
          </Field>
        </Frame>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'sales_margin') {
    return (
      <>
        <PanelTitle>Sales Margin Display</PanelTitle>
        <Frame title="Margin on sales">
          <Check
            label="Show margin column"
            checked={Boolean(layoutLists.billing_show_margin_column)}
            onChange={(v) =>
              setLayoutLists((l) => ({
                ...l,
                billing_show_margin_column: v,
              }))
            }
          />
          <Field label="Margin display">
            <select
              className="settings-input"
              value={String(layoutLists.billing_margin_display_mode || 'rupees')}
              onChange={(e) =>
                setLayoutLists((l) => ({
                  ...l,
                  billing_margin_display_mode: e.target.value,
                }))
              }
            >
              {(opts.margin_display_modes || opts.item_discount_modes || []).map(
                (o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ),
              )}
            </select>
          </Field>
          <Check
            label="Show total margin"
            checked={Boolean(layoutLists.billing_show_total_margin)}
            onChange={(v) =>
              setLayoutLists((l) => ({
                ...l,
                billing_show_total_margin: v,
              }))
            }
          />
          <Check
            label="Warn on loss"
            checked={Boolean(layoutLists.billing_margin_loss_warning)}
            onChange={(v) =>
              setLayoutLists((l) => ({
                ...l,
                billing_margin_loss_warning: v,
              }))
            }
          />
        </Frame>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'record_indicators') {
    return (
      <>
        <PanelTitle>Record Indicators</PanelTitle>
        <Frame title="Record indicators">
          <Field label="Display style">
            <select
              className="settings-input"
              value={String(
                (
                  (layoutLists.record_indicators || {}) as Record<
                    string,
                    unknown
                  >
                ).display_style || 'auto',
              )}
              onChange={(e) =>
                setLayoutLists((l) => ({
                  ...l,
                  record_indicators: {
                    ...((l.record_indicators as object) || {}),
                    display_style: e.target.value,
                  },
                }))
              }
            >
              {(opts.display_styles || []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
        </Frame>
        {saveBtn}
      </>
    )
  }

  if (sectionId === 'app_mode') {
    return (
      <>
        <PanelTitle>App Mode</PanelTitle>
        <Frame title="App mode">
          <Field label="App mode">
            <select
              className="settings-input"
              value={String(layoutLists.app_mode || 'medical')}
              onChange={(e) =>
                setLayoutLists((l) => ({ ...l, app_mode: e.target.value }))
              }
            >
              {opts.app_modes.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
        </Frame>
        {saveBtn}
      </>
    )
  }

  return <p className="muted">Unknown layout section.</p>
}

export function SalesBillingPanel({
  sectionId,
  salesBilling,
  setSalesBilling,
  opts,
  saving,
  onSavePartial,
}: {
  sectionId: string
  salesBilling: Record<string, unknown>
  setSalesBilling: React.Dispatch<React.SetStateAction<Record<string, unknown>>>
  opts: Opts
  saving: boolean
  onSavePartial: (partialBody: Record<string, unknown>) => void
}) {
  const keys = SALES_PARTIAL_KEYS[sectionId]
  const saveLabel = SALES_SAVE_LABELS[sectionId]
  if (!keys || !saveLabel) {
    return <p className="muted">Unknown sales &amp; billing section.</p>
  }

  const save = () => onSavePartial(pickKeys(salesBilling, keys))

  if (sectionId === 'billing_layout' || sectionId === 'sales_screen') {
    return (
      <>
        <PanelTitle>Billing Layout &amp; FY</PanelTitle>
        <Frame title="Financial Year (Sales / Purchase History)">
          <Note>
            Ongoing financial year: {String(salesBilling.current_fy_label || '—')}.
            History filters reset to this when you return to history screens.
          </Note>
          <Field label="Default history scope">
            <select
              className="settings-input"
              value={String(salesBilling.history_scope || 'current_fy')}
              onChange={(e) =>
                setSalesBilling((s) => ({ ...s, history_scope: e.target.value }))
              }
            >
              {(opts.history_scopes || []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
        </Frame>
        <Frame title="Cash / Due (Payment Mode)">
          <Check
            label="Show Payment Mode field (Cash / Due)"
            checked={Boolean(salesBilling.payment_mode_enabled)}
            onChange={(v) =>
              setSalesBilling((s) => ({ ...s, payment_mode_enabled: v }))
            }
          />
          <Field label="Payment Mode position">
            <select
              className="settings-input"
              value={String(salesBilling.payment_mode_position || 'first')}
              onChange={(e) =>
                setSalesBilling((s) => ({
                  ...s,
                  payment_mode_position: e.target.value,
                }))
              }
            >
              {opts.payment_mode_positions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
        </Frame>
        <Frame title="Discount per medicine">
          <Field label="Line discount input">
            <select
              className="settings-input"
              value={String(salesBilling.item_discount_mode || 'rupees')}
              disabled={salesBilling.billing_show_item_discount === false}
              onChange={(e) =>
                setSalesBilling((s) => ({
                  ...s,
                  item_discount_mode: e.target.value,
                }))
              }
            >
              {(opts.item_discount_modes || []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Check
            label="Show item discount column on Sales"
            checked={Boolean(
              salesBilling.billing_show_item_discount !== false,
            )}
            onChange={(v) =>
              setSalesBilling((s) => ({
                ...s,
                billing_show_item_discount: v,
              }))
            }
          />
          <Note>
            Stored bills always keep the discount in ₹. Percentage is converted
            when you add the line.
          </Note>
        </Frame>
        <Frame title="Margin column (Sales screen only)">
          <Check
            label="Show margin column per medicine"
            checked={Boolean(salesBilling.billing_show_margin_column)}
            onChange={(v) =>
              setSalesBilling((s) => ({
                ...s,
                billing_show_margin_column: v,
              }))
            }
          />
          <Field label="Margin display">
            <select
              className="settings-input"
              value={String(salesBilling.billing_margin_display_mode || 'rupees')}
              onChange={(e) =>
                setSalesBilling((s) => ({
                  ...s,
                  billing_margin_display_mode: e.target.value,
                }))
              }
            >
              {(opts.margin_display_modes || opts.item_discount_modes || []).map(
                (o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ),
              )}
            </select>
          </Field>
          <Note>
            Percentage shows margin as % of MRP line value (same idea as Disc %).
            Rupees shows the absolute margin amount.
          </Note>
          <Check
            label="Show total margin in billing summary"
            checked={Boolean(salesBilling.billing_show_total_margin)}
            onChange={(v) =>
              setSalesBilling((s) => ({
                ...s,
                billing_show_total_margin: v,
              }))
            }
          />
          <Check
            label="Warn when discount is larger than margin"
            checked={Boolean(salesBilling.billing_margin_loss_warning)}
            onChange={(v) =>
              setSalesBilling((s) => ({
                ...s,
                billing_margin_loss_warning: v,
              }))
            }
          />
        </Frame>
        <Frame title="Doctor name on Sales">
          {/* The note sits above the box, as it does in the old screen this
              shop learned the setting on. */}
          <Note>
            H1 and X always require a doctor name. Uncheck to allow saving other
            schedules (H, G, C, …) without a doctor.
          </Note>
          <Check
            label="Require doctor name for schedules other than H1 and X"
            /* Stored default is ON, so an absent value must read as ticked --
               Boolean(undefined) would show "off" while the sale still refused
               to save. */
            checked={salesBilling.require_doctor_for_other_schedules !== false}
            onChange={(v) =>
              setSalesBilling((s) => ({
                ...s,
                require_doctor_for_other_schedules: v,
              }))
            }
          />
        </Frame>
        <SaveBtn label={saveLabel} saving={saving} onClick={save} />
      </>
    )
  }

  if (sectionId === 'batch_picker') {
    return (
      <>
        <PanelTitle>Batch Picker</PanelTitle>
        <Frame title="Batch Picker">
          <Field label="Batch sort order">
            <select
              className="settings-input"
              value={String(salesBilling.batch_sort_order || 'oldest_first')}
              onChange={(e) =>
                setSalesBilling((s) => ({
                  ...s,
                  batch_sort_order: e.target.value,
                }))
              }
            >
              {opts.batch_sort_orders.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Check
            label="Show zero-stock batches in dropdown"
            checked={Boolean(salesBilling.show_zero_stock)}
            onChange={(v) =>
              setSalesBilling((s) => ({ ...s, show_zero_stock: v }))
            }
          />
        </Frame>
        <SaveBtn label={saveLabel} saving={saving} onClick={save} />
      </>
    )
  }

  if (sectionId === 'sales_return') {
    return (
      <>
        <PanelTitle>Sales Return</PanelTitle>
        <Frame title="Sales Return">
          <Field label="Lookup window (days)">
            <input
              className="settings-input"
              type="number"
              min={1}
              max={365}
              value={Number(salesBilling.sales_return_lookup_days ?? 90)}
              onChange={(e) =>
                setSalesBilling((s) => ({
                  ...s,
                  sales_return_lookup_days: Number(e.target.value),
                }))
              }
            />
          </Field>
        </Frame>
        <SaveBtn label={saveLabel} saving={saving} onClick={save} />
      </>
    )
  }

  if (sectionId === 'sales_bills') {
    return (
      <>
        <PanelTitle>Sales Bill Save</PanelTitle>
        <Frame title="Sales Bill Save">
          {/* Honest label. The file written beside each sale is laid out by the
              Print Sales 1 slot's copies, not by this control -- that is what
              apply_print_bill_layout does with print_slot_copies. Renaming it
              is better than a dropdown that changes nothing on the saved PDF. */}
          <Field label="PDF save layout (preview window)">
            <select
              className="settings-input"
              value={String(salesBilling.pdf_save_layout || 'two_copies')}
              onChange={(e) =>
                setSalesBilling((s) => ({
                  ...s,
                  pdf_save_layout: e.target.value,
                }))
              }
            >
              {opts.pdf_save_layouts.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Save folder">
            <input
              className="settings-input"
              value={String(salesBilling.sales_bill_save_dir ?? '')}
              onChange={(e) =>
                setSalesBilling((s) => ({
                  ...s,
                  sales_bill_save_dir: e.target.value,
                }))
              }
            />
          </Field>
        </Frame>
        <SaveBtn label={saveLabel} saving={saving} onClick={save} />
      </>
    )
  }

  if (sectionId === 'upi_payment') {
    return (
      <>
        <PanelTitle>UPI Payment QR</PanelTitle>
        <Frame title="UPI Payment QR">
          <Check
            label="Enable UPI QR on sales"
            checked={Boolean(salesBilling.upi_qr_enabled)}
            onChange={(v) =>
              setSalesBilling((s) => ({ ...s, upi_qr_enabled: v }))
            }
          />
          <Field label="UPI ID">
            <input
              className="settings-input"
              value={String(salesBilling.upi_id ?? '')}
              onChange={(e) =>
                setSalesBilling((s) => ({ ...s, upi_id: e.target.value }))
              }
            />
          </Field>
          <Field label="Amount mode">
            <select
              className="settings-input"
              value={String(salesBilling.upi_qr_amount_mode || 'total')}
              onChange={(e) =>
                setSalesBilling((s) => ({
                  ...s,
                  upi_qr_amount_mode: e.target.value,
                }))
              }
            >
              {opts.upi_amount_modes.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
        </Frame>
        <SaveBtn label={saveLabel} saving={saving} onClick={save} />
      </>
    )
  }

  // autosave
  return (
    <>
      <PanelTitle>Autosave</PanelTitle>
      <Frame title="Autosave">
        <Check
          label="Enable autosave"
          checked={Boolean(salesBilling.autosave_enabled)}
          onChange={(v) =>
            setSalesBilling((s) => ({ ...s, autosave_enabled: v }))
          }
        />
        <Field label="Interval (seconds)">
          <input
            className="settings-input"
            type="number"
            min={30}
            max={3600}
            value={Number(salesBilling.autosave_interval_seconds ?? 120)}
            onChange={(e) =>
              setSalesBilling((s) => ({
                ...s,
                autosave_interval_seconds: Number(e.target.value),
              }))
            }
          />
        </Field>
      </Frame>
      <SaveBtn label={saveLabel} saving={saving} onClick={save} />
    </>
  )
}
