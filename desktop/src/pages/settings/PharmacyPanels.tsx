/** Pharmacy Profile — mirrors ui/settings/settings_tabs/pharmacy_tab.py */

import { useMemo, useRef, useState } from 'react'
import {
  imageFileToBase64,
  pharmacyAction,
  printAlignmentTest,
  testPrinter,
  type SettingsBundle,
} from '../../settingsApi'
import { getApiBase } from '../../api'
import { IS_DEMO } from '../../demoMode'
import {
  Check,
  Field,
  Frame,
  Note,
  PanelTitle,
  SaveBtn,
} from './SettingsChrome'

type Opt = { value: string; label: string }

function asSizeModes(raw: Opt[] | string[] | undefined): Opt[] {
  if (!raw?.length) {
    return [
      { value: 'normal', label: 'Full size' },
      { value: 'dot_matrix', label: 'Dot matrix (smaller bill)' },
    ]
  }
  if (typeof raw[0] === 'string') {
    return (raw as string[]).map((label) => ({
      value: label.toLowerCase().includes('dot') ? 'dot_matrix' : 'normal',
      label,
    }))
  }
  return raw as Opt[]
}

function maxCopiesForPaper(paper: string): number {
  const p = (paper || 'A5').toUpperCase()
  if (p === 'A6') return 1
  if (p === 'A5') return 2
  return 4
}

type Props = {
  sectionId: string
  opts: SettingsBundle['options']
  profile: Record<string, unknown>
  setProfile: React.Dispatch<React.SetStateAction<Record<string, unknown>>>
  bill: Record<string, unknown>
  setBill: React.Dispatch<React.SetStateAction<Record<string, unknown>>>
  login: Record<string, unknown>
  setLogin: React.Dispatch<React.SetStateAction<Record<string, unknown>>>
  printer: Record<string, unknown>
  setPrinter: React.Dispatch<React.SetStateAction<Record<string, unknown>>>
  installedPrinters: string[]
  onInstalledPrintersChange?: (printers: string[]) => void
  printLogPath?: string
  spoolerRunning?: boolean
  saving: boolean
  onSave: (body: Record<string, unknown>) => void
}

/** Show the picture, not the path.
 *
 *  This printed the file name and the folder it came from, so the shop could
 *  not tell whether it had picked the right image until it printed a bill.
 *  The engine serves the shop's own logo at /api/settings/pharmacy/logo; the
 *  stamp busts the cache after a new one is chosen.
 */
function LogoPreview({ path, stamp }: { path: string; stamp: number }) {
  const [failed, setFailed] = useState(false)
  if (!path) return <Note>No logo selected</Note>
  // The demonstration copy has no engine to serve the shop's own logo, and an
  // <img> does not go through the fetch interceptor -- it would reach for
  // 127.0.0.1 and sit there. Show the name, not a broken picture.
  if (IS_DEMO) {
    return (
      <div className="settings-logo-preview">
        <Note>Selected: {path.replace(/^.*[\\/]/, '') || path}</Note>
      </div>
    )
  }
  const name = path.replace(/^.*[\\/]/, '')
  return (
    <div className="settings-logo-preview">
      {failed ? null : (
        <img
          src={`${getApiBase()}/api/settings/pharmacy/logo?t=${stamp}`}
          alt="Bill logo"
          style={{ maxHeight: 90, maxWidth: 260, display: 'block', marginBottom: 6 }}
          onError={() => setFailed(true)}
        />
      )}
      <Note>Selected: {name || path}</Note>
      {failed ? (
        <p className="muted" style={{ fontSize: 12 }}>
          The picture could not be shown. Choose it again.
        </p>
      ) : null}
    </div>
  )
}

function PrintSlotEditor({
  n,
  slot,
  shortcut,
  sizeModes,
  opts,
  onPatchSlot,
  onShortcut,
}: {
  n: 1 | 2
  slot: Record<string, unknown>
  shortcut: string
  sizeModes: Opt[]
  opts: SettingsBundle['options']
  onPatchSlot: (patch: Record<string, unknown>) => void
  onShortcut: (v: string) => void
}) {
  const paper = String(slot.paper_size ?? (n === 1 ? 'A5' : 'A6'))
  const maxC = maxCopiesForPaper(paper)
  const copies = Math.max(
    1,
    Math.min(maxC, Number(slot.copies ?? (n === 1 ? 2 : 1))),
  )

  return (
    <Frame title={`Print Sales ${n}`}>
      <div className="settings-form-row">
        <Field label="Button label">
          <input
            className="settings-input"
            value={String(slot.label ?? `Print Sales ${n}`)}
            onChange={(e) => onPatchSlot({ label: e.target.value })}
          />
        </Field>
        <Field label="Paper">
          <select
            className="settings-input"
            value={paper}
            onChange={(e) => {
              const nextPaper = e.target.value
              const nextMax = maxCopiesForPaper(nextPaper)
              onPatchSlot({
                paper_size: nextPaper,
                copies: Math.min(copies, nextMax),
              })
            }}
          >
            {opts.paper_sizes.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Bill copies">
          <input
            className="settings-input"
            type="number"
            min={1}
            max={maxC}
            value={copies}
            onChange={(e) =>
              onPatchSlot({
                copies: Math.max(
                  1,
                  Math.min(maxC, Number(e.target.value) || 1),
                ),
              })
            }
          />
        </Field>
      </div>
      <div className="settings-form-row">
        <Field label="Size">
          <select
            className="settings-input"
            // Blank = inherit Paper & Copies. The slot used to have only two
            // states, and slot 1 shipped saying "Full size", so the shop's own
            // Bill size and Shrink % were overridden before they could apply.
            value={String(slot.bill_size_mode ?? '')}
            onChange={(e) => onPatchSlot({ bill_size_mode: e.target.value })}
          >
            <option value="">Inherit (Paper &amp; Copies)</option>
            {sizeModes.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Half position">
          <select
            className="settings-input"
            value={String(slot.a6_source_half ?? 'bottom')}
            // One dropdown, both keys: the engine picks a6_source_half for an
            // A6 slot and a5_single_copy_position for an A5 one, so writing
            // only the first made this control dead on any A5 slot.
            onChange={(e) =>
              onPatchSlot({
                a6_source_half: e.target.value,
                a5_single_copy_position: e.target.value,
              })
            }
          >
            {opts.half_positions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="A4 2-copy layout">
          <select
            className="settings-input"
            value={String(slot.a4_two_copy_layout ?? 'side_by_side')}
            onChange={(e) =>
              onPatchSlot({ a4_two_copy_layout: e.target.value })
            }
          >
            {opts.a4_two_copy_layouts.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Shortcut">
          <input
            className="settings-input"
            value={shortcut}
            onChange={(e) => onShortcut(e.target.value)}
          />
        </Field>
      </div>
      <Note>
        Max bill copies for {paper}: {maxC} (A6: 1 · A5: 1–2 · A4: 1–4).
      </Note>
    </Frame>
  )
}

export function PharmacyPanel(props: Props) {
  const {
    sectionId,
    opts,
    profile,
    setProfile,
    bill,
    setBill,
    login,
    setLogin,
    printer,
    setPrinter,
    installedPrinters,
    onInstalledPrintersChange,
    printLogPath,
    spoolerRunning,
    saving,
    onSave,
  } = props

  const [confirmPwd, setConfirmPwd] = useState('')
  const [profileErr, setProfileErr] = useState('')
  const [printerMsg, setPrinterMsg] = useState('')
  const [printerWarn, setPrinterWarn] = useState('')
  const [testing, setTesting] = useState(false)
  const [alignMsg, setAlignMsg] = useState('')
  const [aligning, setAligning] = useState(false)
  const [busy, setBusy] = useState(false)
  const logoInputRef = useRef<HTMLInputElement>(null)
  // Bumped after a new logo is saved so the <img> reloads.
  const [logoStamp, setLogoStamp] = useState(0)

  const sizeModes = asSizeModes(opts.bill_size_modes)
  const defaults = opts.bill_field_defaults || {}
  const groups =
    opts.bill_field_groups ||
    (opts.bill_fields?.length
      ? [{ group: 'Show on Printed Bill', items: opts.bill_fields }]
      : [])

  const isDotMatrix = String(bill.bill_size_mode || 'normal') === 'dot_matrix'
  const logoPath = String(profile.logo_path ?? '')

  const printerChoices = useMemo(
    () => ['(Use Windows default)', ...installedPrinters.filter(Boolean)],
    [installedPrinters],
  )

  const patchSlot = (
    slotKey: 'print_slot_1' | 'print_slot_2',
    patch: Record<string, unknown>,
  ) => {
    const cur = (bill[slotKey] || {}) as Record<string, unknown>
    setBill((b) => ({ ...b, [slotKey]: { ...cur, ...patch } }))
  }

  /** Send the chosen picture to the engine and keep where it landed.
   *
   *  This used to ask the ENGINE to open a Tk file dialog. The desktop engine
   *  is a windowless sidecar built with tkinter excluded, so the import raised,
   *  a bare except swallowed it, and the route answered HTTP 200 with
   *  ok:false — which chooseLogo never looked at. Pressing Choose Image greyed
   *  the button for an instant and did nothing else: no picture, no message.
   */
  async function uploadLogoFile(file: File) {
    setBusy(true)
    setProfileErr('')
    try {
      const data = await imageFileToBase64(file)
      const res = await pharmacyAction({
        action: 'upload_logo',
        filename: file.name,
        data_base64: data,
      })
      // A 200 carrying ok:false is a refusal, not a save.
      if (res.ok === false) {
        setProfileErr(String(res.error || 'Could not use that picture.'))
        return
      }
      const path = String(res.path || '')
      if (path) {
        setProfile((p) => ({ ...p, logo_path: path }))
        setLogoStamp(Date.now())
      }
    } catch (e) {
      setProfileErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function removeLogo() {
    setProfile((p) => ({ ...p, logo_path: '' }))
  }

  function saveProfile() {
    setProfileErr('')
    const pwd = String(login.password ?? '')
    if (login.enabled && pwd) {
      if (pwd.length < 4) {
        setProfileErr('Password must be at least 4 characters.')
        return
      }
      if (pwd !== confirmPwd) {
        setProfileErr('Password and Confirm Password do not match.')
        return
      }
    }
    onSave({ profile, bill, login })
    setConfirmPwd('')
  }

  async function refreshPrinters() {
    setBusy(true)
    setPrinterMsg('')
    setPrinterWarn('')
    try {
      const res = await pharmacyAction({ action: 'refresh_printers' })
      const list = (res.installed_printers as string[]) || []
      onInstalledPrintersChange?.(list)
      if (res.warning) setPrinterWarn(String(res.warning))
      else setPrinterMsg(`Loaded ${list.length} printer(s).`)
    } catch (e) {
      setPrinterMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function refreshSumatra() {
    setBusy(true)
    setPrinterMsg('')
    try {
      const res = await pharmacyAction({ action: 'refresh_sumatra' })
      const path = String(res.sumatra_path || '')
      setPrinter((p) => ({ ...p, sumatra_path: path }))
      setPrinterMsg(String(res.message || path || 'SumatraPDF refreshed.'))
    } catch (e) {
      setPrinterMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function browseSumatra() {
    setBusy(true)
    setPrinterMsg('')
    try {
      const res = await pharmacyAction({ action: 'browse_sumatra' })
      const path = String(res.path || '')
      if (path) setPrinter((p) => ({ ...p, sumatra_path: path }))
    } catch (e) {
      setPrinterMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function onAlignmentTest() {
    setAligning(true)
    setAlignMsg('')
    try {
      const res = await printAlignmentTest(bill)
      setAlignMsg(res.message || (res.ok ? 'Alignment test sent.' : 'Alignment test failed.'))
    } catch (e) {
      setAlignMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setAligning(false)
    }
  }

  async function onTestPrint() {
    setTesting(true)
    setPrinterMsg('')
    try {
      const selected = String(printer.selected_printer || '')
      const res = await testPrinter(selected || undefined)
      setPrinterMsg(res.message || (res.ok ? 'Test page sent.' : 'Test failed.'))
    } catch (e) {
      setPrinterMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setTesting(false)
    }
  }

  /* ─── Profile ─────────────────────────────────────────────────────────── */
  if (sectionId === 'profile') {
    return (
      <>
        <PanelTitle>Pharmacy Profile</PanelTitle>
        <Frame title="Pharmacy Information">
          <Field label="Pharmacy Name">
            <input
              className="settings-input"
              value={String(profile.name ?? '')}
              onChange={(e) =>
                setProfile((p) => ({ ...p, name: e.target.value }))
              }
            />
          </Field>
          <Field label="Address">
            <textarea
              className="settings-input settings-textarea"
              rows={3}
              value={String(profile.address ?? '')}
              onChange={(e) =>
                setProfile((p) => ({ ...p, address: e.target.value }))
              }
            />
          </Field>
          <Field label="Phone">
            <input
              className="settings-input"
              value={String(profile.phone ?? '')}
              onChange={(e) =>
                setProfile((p) => ({ ...p, phone: e.target.value }))
              }
            />
          </Field>
          <Field label="Email">
            <input
              className="settings-input"
              value={String(profile.email ?? '')}
              onChange={(e) =>
                setProfile((p) => ({ ...p, email: e.target.value }))
              }
            />
          </Field>
          <Field label="GSTIN">
            <input
              className="settings-input"
              value={String(profile.gstin ?? '')}
              onChange={(e) =>
                setProfile((p) => ({ ...p, gstin: e.target.value }))
              }
            />
          </Field>
          <Field label="DL Number">
            <input
              className="settings-input"
              value={String(profile.dl_number ?? '')}
              onChange={(e) =>
                setProfile((p) => ({ ...p, dl_number: e.target.value }))
              }
            />
          </Field>
          <Field label="FSSAI Number">
            <input
              className="settings-input"
              value={String(profile.fssai_number ?? '')}
              onChange={(e) =>
                setProfile((p) => ({ ...p, fssai_number: e.target.value }))
              }
            />
          </Field>

          <Field label="FSSAI on bill">
            <Check
              label="Print FSSAI on sale bills"
              checked={Boolean(profile.show_fssai_on_bill)}
              onChange={(v) =>
                setProfile((p) => ({ ...p, show_fssai_on_bill: v }))
              }
            />
          </Field>
          <Field label="GST on bill">
            <Check
              label="Print GST amount on sale bills"
              checked={Boolean(
                bill.show_gst !== undefined ? bill.show_gst : true,
              )}
              onChange={(v) => setBill((b) => ({ ...b, show_gst: v }))}
            />
          </Field>
          <Field label="Discount on bill">
            <Check
              label="Print applied discount on sale bills"
              checked={Boolean(
                bill.show_discount !== undefined ? bill.show_discount : true,
              )}
              onChange={(v) => setBill((b) => ({ ...b, show_discount: v }))}
            />
          </Field>
          <Field label="Enable GST">
            <Check
              label="Calculate GST on sale items"
              checked={Boolean(profile.gst_enabled)}
              onChange={(v) => setProfile((p) => ({ ...p, gst_enabled: v }))}
            />
          </Field>

          <Field label="Bill Logo">
            <div className="settings-inline-row">
              <span className="settings-note" style={{ flex: 1, margin: 0 }}>
                {logoPath || 'No logo selected'}
              </span>
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                // Fired straight from the click, with nothing awaited first:
                // the browser only opens a picker while the user's activation
                // is still live, and an await spends it.
                onClick={() => logoInputRef.current?.click()}
              >
                Choose Image
              </button>
              <button
                type="button"
                className="settings-link-btn"
                onClick={removeLogo}
              >
                Remove
              </button>
              <input
                ref={logoInputRef}
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp,image/bmp"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  // Clear it, or choosing the same file twice fires nothing.
                  e.target.value = ''
                  if (f) void uploadLogoFile(f)
                }}
              />
            </div>
          </Field>
          <LogoPreview path={logoPath} stamp={logoStamp} />
        </Frame>

        <SaveBtn
          label="Save Profile"
          saving={saving}
          onClick={saveProfile}
        />

        <Frame title="App Login">
          <Note>
            When enabled, the app asks for this username and password every time
            it opens.
          </Note>
          <Check
            label="Require login when the app opens"
            checked={Boolean(login.enabled)}
            onChange={(v) => setLogin((l) => ({ ...l, enabled: v }))}
          />
          <Field label="Username">
            <input
              className="settings-input"
              disabled={!login.enabled}
              value={String(login.username ?? '')}
              onChange={(e) =>
                setLogin((l) => ({ ...l, username: e.target.value }))
              }
            />
          </Field>
          <Field label="Password">
            <input
              className="settings-input"
              type="password"
              disabled={!login.enabled}
              value={String(login.password ?? '')}
              onChange={(e) =>
                setLogin((l) => ({ ...l, password: e.target.value }))
              }
            />
          </Field>
          <Field label="Confirm Password">
            <input
              className="settings-input"
              type="password"
              disabled={!login.enabled}
              value={confirmPwd}
              onChange={(e) => setConfirmPwd(e.target.value)}
            />
          </Field>
          <Note>
            Leave password blank when saving to keep the current password.
          </Note>
        </Frame>
        {profileErr ? <p className="error">{profileErr}</p> : null}
      </>
    )
  }

  /* ─── Bill Template ───────────────────────────────────────────────────── */
  if (sectionId === 'bill_template') {
    return (
      <>
        <PanelTitle>Bill Template</PanelTitle>
        <Frame title="Bill Template">
          <Field label="Bill template">
            <select
              className="settings-input"
              value={String(bill.template || 'classic')}
              onChange={(e) =>
                setBill((b) => ({ ...b, template: e.target.value }))
              }
            >
              {opts.templates.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Note>
            GST Vertical Rotated (Classic) matches the standard pharmacy GST
            receipt.
          </Note>
        </Frame>
        <SaveBtn
          label="Save Bill Print Style"
          saving={saving}
          onClick={() => onSave({ bill })}
        />
      </>
    )
  }

  /* ─── Bill Fields ─────────────────────────────────────────────────────── */
  if (sectionId === 'bill_fields') {
    return (
      <>
        <PanelTitle>Bill Fields</PanelTitle>
        <Frame title="Show on Printed Bill">
          {groups.length ? (
            groups.map((g) => (
              <Frame key={g.group} title={g.group}>
                <div className="settings-check-grid-3">
                  {g.items.map((f) => {
                    const fallback =
                      defaults[f.key] ?? f.key !== 'show_customer_due'
                    return (
                      <Check
                        key={f.key}
                        label={f.label}
                        checked={Boolean(
                          bill[f.key] !== undefined ? bill[f.key] : fallback,
                        )}
                        onChange={(v) =>
                          setBill((b) => ({ ...b, [f.key]: v }))
                        }
                      />
                    )
                  })}
                </div>
              </Frame>
            ))
          ) : (
            <Note>
              Bill field groups did not load. Restart the desktop window so the
              local engine refreshes.
            </Note>
          )}
        </Frame>
        <Note>
          Store header, Invoice header, Medicine table, and Totals/footer match
          the classic Pharmacy Profile → Bill Fields checkboxes (same saved
          keys).
        </Note>
        <SaveBtn
          label="Save Bill Print Style"
          saving={saving}
          onClick={() => onSave({ bill })}
        />
      </>
    )
  }

  /* ─── Bill Text ───────────────────────────────────────────────────────── */
  if (sectionId === 'bill_text') {
    return (
      <>
        <PanelTitle>Bill Text Lines</PanelTitle>
        <Frame title="Bill Text Lines">
          <Field label="Blessing line (top)">
            <div className="settings-inline-row">
              <input
                className="settings-input"
                value={String(bill.blessing_line ?? 'SHREE GANESHAY NAMAH')}
                onChange={(e) =>
                  setBill((b) => ({ ...b, blessing_line: e.target.value }))
                }
              />
              <Check
                label="Print on bill"
                checked={Boolean(
                  bill.show_blessing !== undefined
                    ? bill.show_blessing
                    : defaults.show_blessing ?? true,
                )}
                onChange={(v) => setBill((b) => ({ ...b, show_blessing: v }))}
              />
            </div>
          </Field>
          <Field label="Recovery wish (footer)">
            <input
              className="settings-input"
              value={String(
                bill.recovery_wish_line ?? 'I WISH FOR YOUR SPEEDY RECOVERY.',
              )}
              onChange={(e) =>
                setBill((b) => ({ ...b, recovery_wish_line: e.target.value }))
              }
            />
          </Field>
          <Field label="GST strip line (e.g. HAVE A NICE DAY)">
            <input
              className="settings-input"
              value={String(bill.gst_day_line ?? 'HAVE A NICE DAY')}
              onChange={(e) =>
                setBill((b) => ({ ...b, gst_day_line: e.target.value }))
              }
            />
          </Field>
          <Note>
            Uncheck “Print on bill” to hide Shree Ganeshay Namah. Same option is
            under Bill Fields → Invoice header.
          </Note>
        </Frame>
        <SaveBtn
          label="Save Bill Print Style"
          saving={saving}
          onClick={() => onSave({ bill })}
        />
      </>
    )
  }

  /* ─── Bill Logo ───────────────────────────────────────────────────────── */
  if (sectionId === 'bill_logo') {
    return (
      <>
        <PanelTitle>Bill Logo</PanelTitle>
        <Frame title="Bill Logo (from Pharmacy Profile)">
          <Check
            label="Small logo — top right of invoice"
            checked={Boolean(bill.show_logo_top_right)}
            onChange={(v) =>
              setBill((b) => ({ ...b, show_logo_top_right: v }))
            }
          />
          <Field label="Top margin (mm)">
            <input
              className="settings-input"
              type="number"
              step={0.5}
              min={0}
              max={25}
              value={Number(bill.logo_top_right_margin_top ?? 1.2)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  logo_top_right_margin_top: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Right margin (mm)">
            <input
              className="settings-input"
              type="number"
              step={0.5}
              min={0}
              max={25}
              value={Number(bill.logo_top_right_margin_right ?? 1.5)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  logo_top_right_margin_right: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Check
            label="Center watermark behind medicines"
            checked={Boolean(bill.show_logo_center_watermark)}
            onChange={(v) =>
              setBill((b) => ({ ...b, show_logo_center_watermark: v }))
            }
          />
          <Field label="Watermark transparency %">
            <input
              className="settings-input"
              type="number"
              min={5}
              max={80}
              value={Number(bill.logo_watermark_opacity ?? 15)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  logo_watermark_opacity: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Note>
            Upload the logo image under Pharmacy Profile. You can enable one or
            both positions.
          </Note>
          {logoPath ? <LogoPreview path={logoPath} stamp={logoStamp} /> : null}
        </Frame>
        <SaveBtn
          label="Save Bill Print Style"
          saving={saving}
          onClick={() => onSave({ bill })}
        />
      </>
    )
  }

  /* ─── Paper & Copies ──────────────────────────────────────────────────── */
  if (sectionId === 'bill_paper') {
    return (
      <>
        <PanelTitle>Paper &amp; Copies</PanelTitle>
        <Frame title="Paper &amp; Copies">
          {/* Paper size, A6 source half and A4 two-copy layout used to sit
              here as well. They never won: every real print goes through a
              Print Sales slot, and the slot's own value silently overrode
              whatever was chosen on this pane, so changing it here did nothing
              on paper. One place decides, and it is the slot. */}
          <Note>
            Paper size, A6 half, A5 half and A4 layout are set per print slot,
            under Print Sales 1 / Print Sales 2 — the slot always decides what
            comes out of the printer.
          </Note>
          {/* This number is real, but only on the direct Print Bill path.
              F7/F8/F9 go through a Print Sales slot, and the slot deliberately
              ignores it so 2 bill copies cannot silently become 4 sheets — see
              get_print_slot_page_copies. Saying so is better than a control
              that looks global and is not. */}
          <Note>
            Page copies: how many times each sheet is sent to the printer on the
            direct <b>Print Bill</b> path. F7 / F8 / F9 use the copies set on
            their own Print Sales slot instead.
          </Note>
          <Field label="Page copies (Print Bill)">
            <input
              className="settings-input"
              type="number"
              min={1}
              max={10}
              value={Number(bill.copies ?? 1)}
              onChange={(e) =>
                setBill((b) => ({ ...b, copies: Number(e.target.value) }))
              }
            />
          </Field>
          <Field label="Bill size">
            <select
              className="settings-input"
              value={String(bill.bill_size_mode || 'normal')}
              onChange={(e) =>
                setBill((b) => ({ ...b, bill_size_mode: e.target.value }))
              }
            >
              {sizeModes.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Shrink % (dot matrix)">
            <input
              className="settings-input"
              type="number"
              min={85}
              max={98}
              disabled={!isDotMatrix}
              value={Number(bill.bill_size_pct ?? 92)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  bill_size_pct: Number(e.target.value),
                }))
              }
            />
          </Field>
          {!isDotMatrix ? (
            <Note>Shrink % applies only when Bill size is Dot matrix.</Note>
          ) : null}
          <Field label="Medicines per bill page">
            <input
              className="settings-input"
              type="number"
              min={1}
              max={50}
              value={Number(bill.items_per_bill_page ?? 10)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  items_per_bill_page: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Dot matrix line spacing">
            <input
              className="settings-input"
              type="number"
              min={22}
              max={32}
              value={Number(bill.dot_matrix_line_spacing ?? 30)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  dot_matrix_line_spacing: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Dot matrix top margin (cm)">
            <input
              className="settings-input"
              type="number"
              step={0.1}
              min={0}
              max={3}
              value={Number(bill.dot_matrix_top_offset_cm ?? 0.8)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  dot_matrix_top_offset_cm: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Dot matrix slip height (cm)">
            <input
              className="settings-input"
              type="number"
              step={0.1}
              min={0}
              max={30}
              value={Number(bill.dot_matrix_slip_height_cm ?? 0)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  dot_matrix_slip_height_cm: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Dot matrix bottom margin (cm)">
            <input
              className="settings-input"
              type="number"
              step={0.1}
              min={0}
              max={5}
              value={Number(bill.dot_matrix_bottom_margin_cm ?? 1.0)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  dot_matrix_bottom_margin_cm: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Dot matrix print width (cm)">
            <input
              className="settings-input"
              type="number"
              step={0.1}
              min={5}
              max={20}
              value={Number(bill.dot_matrix_print_width_cm ?? 12.9)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  dot_matrix_print_width_cm: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Dot matrix tear-off handled by">
            <select
              className="settings-input"
              disabled={Number(bill.dot_matrix_slip_height_cm ?? 0) <= 0}
              value={String(bill.dot_matrix_tear_mode || 'software')}
              onChange={(e) =>
                setBill((b) => ({ ...b, dot_matrix_tear_mode: e.target.value }))
              }
            >
              {(opts.dot_matrix_tear_modes || [
                { value: 'software', label: 'Satpuda (recommended)' },
                { value: 'printer', label: "Printer's own auto tear-off" },
              ]).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Dot matrix tear-off eject (cm)">
            <input
              className="settings-input"
              type="number"
              step={0.1}
              min={0}
              max={8}
              disabled={
                Number(bill.dot_matrix_slip_height_cm ?? 0) <= 0 ||
                String(bill.dot_matrix_tear_mode || 'software') !== 'software'
              }
              value={Number(bill.dot_matrix_tear_gap_cm ?? 4.0)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  dot_matrix_tear_gap_cm: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Dot matrix left offset (cm)">
            <input
              className="settings-input"
              type="number"
              step={0.1}
              min={0}
              max={5}
              value={Number(bill.dot_matrix_left_offset_cm ?? 0)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  dot_matrix_left_offset_cm: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Dot matrix tear feed (cm)">
            <input
              className="settings-input"
              type="number"
              step={0.1}
              min={0}
              max={6}
              disabled={Number(bill.dot_matrix_slip_height_cm ?? 0) > 0}
              value={Number(bill.dot_matrix_tear_feed_cm ?? 2.5)}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  dot_matrix_tear_feed_cm: Number(e.target.value),
                }))
              }
            />
          </Field>
          <Field label="Dot matrix alignment">
            <button
              type="button"
              className="settings-action-btn"
              disabled={aligning}
              onClick={() => void onAlignmentTest()}
            >
              {aligning ? 'Printing…' : 'Print alignment test'}
            </button>
          </Field>
          {alignMsg ? <Note>{alignMsg}</Note> : null}
          <Field label="Dot matrix bill style">
            <select
              className="settings-input"
              value={String(bill.dot_matrix_style || 'compact')}
              onChange={(e) =>
                setBill((b) => ({ ...b, dot_matrix_style: e.target.value }))
              }
            >
              <option value="compact">New slip (no blessing, no GST, more medicines)</option>
              <option value="classic">Old slip (blessing, GST INVOICE, GST, wish line)</option>
            </select>
          </Field>
          <Field label="Dot matrix slip extras">
            <Check
              label="Total printed bold (printer strikes the line twice)"
              checked={Boolean(bill.dot_matrix_bold_total ?? true)}
              onChange={(v) => setBill((b) => ({ ...b, dot_matrix_bold_total: v }))}
            />
            <Check
              label='Item count line ("8 aushadhe, 59 nag")'
              checked={Boolean(bill.dot_matrix_item_count ?? true)}
              onChange={(v) => setBill((b) => ({ ...b, dot_matrix_item_count: v }))}
            />
            <Check
              label="Show the customer's due on the slip when money is owed"
              checked={Boolean(bill.dot_matrix_due_lines ?? true)}
              onChange={(v) => setBill((b) => ({ ...b, dot_matrix_due_lines: v }))}
            />
          </Field>
          <Field label="Dot matrix table borders">
            <Check
              label="Show vertical | borders (off = horizontal lines only)"
              checked={
                String(bill.dot_matrix_style || 'compact') === 'compact'
                  ? false
                  : Boolean(bill.dot_matrix_vertical_borders ?? true)
              }
              disabled={String(bill.dot_matrix_style || 'compact') === 'compact'}
              onChange={(v) =>
                setBill((b) => ({ ...b, dot_matrix_vertical_borders: v }))
              }
            />
          </Field>
          <Field label="A4 single-copy position">
            <select
              className="settings-input"
              value={String(bill.a4_single_copy_position || 'bottom')}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  a4_single_copy_position: e.target.value,
                }))
              }
            >
              {opts.half_positions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Note>
            Medicines per bill page: max lines on one printed bill; extra
            medicines continue on the next page/slip (Continued…). For A6 dot
            matrix use 8–10 so lines fit on paper. Dot matrix line spacing
            28–32 = more space between lines (default 30). Top offset cm:
            reverse-feed before print to start bill higher on A6 slip (default
            0.8). Slip height cm: measure one slip from perforation to
            perforation; every bill then moves the paper by exactly one slip
            (0 = off) - measure one slip from perforation to perforation.
            Top and bottom margin cm: blank space kept at the top and bottom of
            every slip; the medicine table always fills whatever is left, so the
            bill is the same shape on every slip. Print width cm: the paper
            width minus the tractor holes (14.5 cm slip - 0.8 cm each side =
            12.9). Tear-off handled by Satpuda: after each bill the slip is
            fed out by Tear-off eject cm so the perforation clears the tear
            edge, and the next bill pulls back exactly that same distance - no
            more - so it starts on the top of the next slip. Raise Tear-off
            eject until the slip is fully out past the tear bar (4 cm to start,
            up to 8); it cannot drift, because both ends use the one number.
            Turn the printer's own Auto tear off OFF, or the two fight each
            other - it is in the printer's Default Setting mode, the app cannot
            change it. Top margin cm: how far BELOW
            the perforation the first line prints. Left offset cm: moves the
            whole bill right of the
            printer&apos;s first column (nothing prints left of it - if the bill
            is too far right at 0, move the paper left in the printer). Tear
            feed cm: how far the paper moves up after a bill when no slip height
            is set. Print alignment test uses the values on screen, so try a
            value, print, and save once it lands right. Turn off vertical
            borders for horizontal-rule-only bills.
            Shrink % fits the bill on A5/A6 without clipping borders.
          </Note>
        </Frame>
        <SaveBtn
          label="Save Bill Print Style"
          saving={saving}
          onClick={() => onSave({ bill })}
        />
      </>
    )
  }

  /* ─── Sales Print Buttons ─────────────────────────────────────────────── */
  if (sectionId === 'bill_sales') {
    const slot1 = (bill.print_slot_1 || {}) as Record<string, unknown>
    const slot2 = (bill.print_slot_2 || {}) as Record<string, unknown>
    return (
      <>
        <PanelTitle>Sales Print Buttons</PanelTitle>
        <Frame title="Print Sales Buttons (Sales Page)">
          <PrintSlotEditor
            n={1}
            slot={slot1}
            shortcut={String(bill.print_slot_1_key ?? 'F7')}
            sizeModes={sizeModes}
            opts={opts}
            onPatchSlot={(patch) => patchSlot('print_slot_1', patch)}
            onShortcut={(v) =>
              setBill((b) => ({ ...b, print_slot_1_key: v }))
            }
          />
          <PrintSlotEditor
            n={2}
            slot={slot2}
            shortcut={String(bill.print_slot_2_key ?? 'F8')}
            sizeModes={sizeModes}
            opts={opts}
            onPatchSlot={(patch) => patchSlot('print_slot_2', patch)}
            onShortcut={(v) =>
              setBill((b) => ({ ...b, print_slot_2_key: v }))
            }
          />
          <Note>
            Bill copies = identical slips of the same bill on one sheet (A5:
            1–2, A6: 1, A4: 1–4). Long bills (12+ lines) become multiple pages —
            one sheet per 12 medicines; copies duplicates that page only. e.g.
            36 lines → 3 sheets; A5×2 = 3 A5 pages with 2 identical slips each.
            Non-final pages show Continued...; last page shows Total. Half
            position = A5/A6 single-copy placement.
          </Note>
          <Field label="Enter on Online (Cash mode)">
            <select
              className="settings-input"
              value={String(bill.cash_online_enter_action || 'save_bill')}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  cash_online_enter_action: e.target.value,
                }))
              }
            >
              {opts.enter_actions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Enter on Rounding (Due mode)">
            <select
              className="settings-input"
              value={String(bill.due_rounding_enter_action || 'save_bill')}
              onChange={(e) =>
                setBill((b) => ({
                  ...b,
                  due_rounding_enter_action: e.target.value,
                }))
              }
            >
              {opts.enter_actions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Note>
            Choose what happens when you press Enter after payment on the Sales
            page.
          </Note>
        </Frame>
        <SaveBtn
          label="Save Bill Print Style"
          saving={saving}
          onClick={() => onSave({ bill })}
        />
      </>
    )
  }

  /* ─── Printer Setup ───────────────────────────────────────────────────── */
  if (sectionId === 'printer') {
    const blank = '(Use Windows default)'
    const sel = (v: unknown) => {
      const s = String(v || '')
      return s || blank
    }
    return (
      <>
        <PanelTitle>Printer Setup</PanelTitle>
        <Frame title="Silent Printer Setup (Windows)">
          <Note>
            Invoices print directly to the selected printer via SumatraPDF.
            Paper size, orientation, tray, and quality use Windows printer
            preferences.
          </Note>
          {spoolerRunning === false ? (
            <p className="error">
              Print Spooler is stopped. Start it before printing (Services →
              Print Spooler → Start).
            </p>
          ) : null}

          <Field label="Default printer">
            <div className="settings-inline-row">
              <select
                className="settings-input"
                value={sel(printer.selected_printer)}
                onChange={(e) =>
                  setPrinter((p) => ({
                    ...p,
                    selected_printer:
                      e.target.value === blank ? '' : e.target.value,
                  }))
                }
              >
                {printerChoices.map((p) => (
                  <option key={p || 'default'} value={p}>
                    {p}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={() => void refreshPrinters()}
              >
                Refresh
              </button>
            </div>
          </Field>

          <Field label="Print Sales 1 printer">
            <select
              className="settings-input"
              value={sel(printer.print_slot_1_printer)}
              onChange={(e) =>
                setPrinter((p) => ({
                  ...p,
                  print_slot_1_printer:
                    e.target.value === blank ? '' : e.target.value,
                }))
              }
            >
              {printerChoices.map((p) => (
                <option key={`s1-${p}`} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Print Sales 2 printer">
            <select
              className="settings-input"
              value={sel(printer.print_slot_2_printer)}
              onChange={(e) =>
                setPrinter((p) => ({
                  ...p,
                  print_slot_2_printer:
                    e.target.value === blank ? '' : e.target.value,
                }))
              }
            >
              {printerChoices.map((p) => (
                <option key={`s2-${p}`} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </Field>
          <Note>Leave slot printers blank to use the default printer.</Note>

          <Field label="SumatraPDF path">
            <div className="settings-inline-row">
              <input
                className="settings-input"
                value={String(printer.sumatra_path || '')}
                onChange={(e) =>
                  setPrinter((p) => ({ ...p, sumatra_path: e.target.value }))
                }
              />
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={() => void refreshSumatra()}
              >
                Refresh
              </button>
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={() => void browseSumatra()}
              >
                Browse…
              </button>
            </div>
          </Field>
          <Note>
            Auto-detects tools\SumatraPDF64.exe or SumatraPDF32.exe (dev and
            EXE).
          </Note>

          <Check
            label="Silent print (no Windows dialog) for Print Sales 1 / 2"
            checked={Boolean(printer.silent_print_enabled ?? true)}
            onChange={(v) =>
              setPrinter((p) => ({ ...p, silent_print_enabled: v }))
            }
          />
          <Check
            label="Print in black only (saves colour ink)"
            checked={Boolean(printer.print_black_only ?? true)}
            onChange={(v) =>
              setPrinter((p) => ({ ...p, print_black_only: v }))
            }
          />
          <Note>
            Silent print and the Windows print dialog (used when silent print
            is off or fails) ask the printer for grayscale, so bills use the
            black cartridge instead of a mix of colour inks. Dot matrix
            printing is not affected.
          </Note>

          <Field label="Printer type">
            <select
              className="settings-input"
              value={String(printer.printer_type || 'standard')}
              onChange={(e) =>
                setPrinter((p) => ({ ...p, printer_type: e.target.value }))
              }
            >
              {opts.printer_types.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Dot matrix print method">
            <select
              className="settings-input"
              disabled={String(printer.printer_type || 'standard') !== 'dot_matrix'}
              value={String(printer.dot_matrix_print_method || 'auto')}
              onChange={(e) =>
                setPrinter((p) => ({
                  ...p,
                  dot_matrix_print_method: e.target.value,
                }))
              }
            >
              {(opts.dot_matrix_print_methods || [
                { value: 'auto', label: 'Auto' },
                { value: 'raw', label: 'RAW ESC/P' },
                { value: 'gdi', label: 'Windows driver' },
              ]).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Note>
            For LX-310: select &quot;EPSON LX-310 ESC/P&quot; (native driver),
            NOT &quot;EPSON LX-310&quot; (Class Driver).
          </Note>
          <Note>
            RAW ESC/P sends the bill straight to the printer: it starts at the
            printer&apos;s first column and the Top offset / Slip height in
            Paper &amp; Copies apply exactly. Use it whenever the printer takes
            it (any Epson-compatible 9-pin). Windows driver draws the bill
            through the driver; it now starts top-left too, and with a Slip
            height the page is one slip long. Auto picks RAW for an ESC/P driver
            and the Windows driver for class / generic drivers.
          </Note>
          <Note>
            Print debug log: {printLogPath || 'config/print_log.txt'}
          </Note>
        </Frame>

        <div className="settings-inline-row">
          <SaveBtn
            label="Save Printer Settings"
            saving={saving}
            onClick={() => onSave({ printer })}
          />
          <button
            type="button"
            className="settings-action-btn"
            disabled={testing}
            onClick={() => void onTestPrint()}
          >
            Test Print
          </button>
        </div>
        {printerWarn ? <p className="error">{printerWarn}</p> : null}
        {printerMsg ? <Note>{printerMsg}</Note> : null}
      </>
    )
  }

  return <p className="muted">Unknown pharmacy section.</p>
}
