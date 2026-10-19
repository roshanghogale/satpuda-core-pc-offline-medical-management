/** Pharmacy Profile — mirrors ui/settings/settings_tabs/pharmacy_tab.py */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  imageFileToBase64,
  pharmacyAction,
  printAlignmentTest,
  testPrinter,
  type SettingsBundle,
} from '../../settingsApi'
import { getApiBase } from '../../api'
import { useBusinessMode } from '../../wholesale/mode'
import { IS_DEMO } from '../../demoMode'
import {
  Check,
  Field,
  Frame,
  Note,
  Help,
  PanelTitle,
  SaveBar,
  SaveBtn,
} from './SettingsChrome'
import '../../styles/pharmacy-settings.css'

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
  /** The shop's UPI ID (Sales & Billing -> UPI Payment QR), shown beside the bank details. */
  upiId?: string
  /** Opens Sales & Billing -> UPI Payment QR, where the UPI ID is kept. */
  onOpenUpi?: () => void
}

/** Bill print keys that sit beside their own item in the Pharmacy Profile window
 *  (owner, 18 Oct 2026), so Bill Fields does not offer them a second time. */
export const PROFILE_WINDOW_BILL_KEYS = new Set([
  'show_store_name',
  'show_store_address',
  'show_store_phone',
  'show_store_email',
  'show_store_gstin',
  'show_store_dl',
  'show_store_fssai',
  'show_store_bank',
  'print_signature_image',
])

/** A profile box with its label above it and its own "Show on bill" tick. */
function ProfileField({
  id,
  label,
  span,
  show,
  onShow,
  children,
}: {
  id: string
  label: string
  span?: boolean
  show?: boolean
  onShow?: (v: boolean) => void
  children: React.ReactNode
}) {
  return (
    <div className={`ph-prof-field${span ? ' ph-span-all' : ''}`}>
      <div className="ph-prof-head">
        <label htmlFor={id}>{label}</label>
        {onShow ? (
          <label className="ph-show-bill" title="Print this on the bill">
            <input
              type="checkbox"
              aria-label={`Show ${label} on bill`}
              checked={Boolean(show)}
              onChange={(e) => onShow(e.target.checked)}
            />
            <span>Show on bill</span>
          </label>
        ) : null}
      </div>
      {children}
    </div>
  )
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
        <Field label="Footer style">
          <select
            className="settings-input"
            value={String(slot.signature_position ?? '')}
            onChange={(e) => onPatchSlot({ signature_position: e.target.value })}
          >
            <option value="">Same as Bill Template</option>
            <option value="center">New — signature in the middle</option>
            <option value="right">Old — signature on the right</option>
          </select>
        </Field>
      </div>
      <Note>
        Max bill copies for {paper}: {maxC} (A6: 1 · A5: 1–2 · A4: 1–4).
      </Note>
    </Frame>
  )
}

/** Continuous dot matrix paper, perforation to perforation (it is made in inches). */
const SLIP_PRESETS: { cm: number; label: string }[] = [
  { cm: 7.62, label: '3 inch (7.62 cm)' },
  { cm: 8.89, label: '3.5 inch (8.89 cm)' },
  { cm: 10.16, label: '4 inch (10.16 cm) - 6 x 4 slip' },
  { cm: 11.43, label: '4.5 inch (11.43 cm)' },
  { cm: 12.7, label: '5 inch (12.7 cm)' },
  { cm: 13.97, label: '5.5 inch (13.97 cm)' },
  { cm: 15.24, label: '6 inch (15.24 cm)' },
  { cm: 27.94, label: '11 inch (27.94 cm)' },
  { cm: 30.48, label: '12 inch (30.48 cm)' },
]

export function PharmacyPanel(props: Props) {
  // Bank details print only on the wholesale invoice (owner, 19 Oct 2026), so a
  // retail store never sees the frame.
  const { isWholesale } = useBusinessMode()
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
    upiId,
    onOpenUpi,
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
  // The store-header ticks and the signature picture live in the Pharmacy
  // Profile window beside their own item; nothing is offered in two places.
  const groups = (
    opts.bill_field_groups ||
    (opts.bill_fields?.length
      ? [{ group: 'Show on Printed Bill', items: opts.bill_fields }]
      : [])
  )
    .map((g) => ({
      ...g,
      items: g.items.filter((f) => !PROFILE_WINDOW_BILL_KEYS.has(f.key)),
    }))
    .filter((g) => g.items.length > 0)
  const billOn = (key: string) =>
    Boolean(bill[key] !== undefined ? bill[key] : defaults[key] ?? true)
  const setBillKey = (key: string, v: boolean) =>
    setBill((b) => ({ ...b, [key]: v }))

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

  // The pharmacist's signature picture for the bill's signature box (owner,
  // 17 Oct 2026). Saved at once and carried with the store like the logo.
  const sigInputRef = useRef<HTMLInputElement>(null)
  const [sigUri, setSigUri] = useState('')
  const [sigMsg, setSigMsg] = useState('')
  useEffect(() => {
    if (IS_DEMO) return
    let live = true
    pharmacyAction({ action: 'get_signature' })
      .then((res) => {
        if (live && res.ok !== false) setSigUri(String(res.data_uri || ''))
      })
      .catch(() => {})
    return () => {
      live = false
    }
  }, [])

  async function uploadSignatureFile(file: File) {
    setBusy(true)
    setSigMsg('')
    try {
      const data = await imageFileToBase64(file)
      const res = await pharmacyAction({
        action: 'upload_signature',
        filename: file.name,
        data_base64: data,
      })
      if (res.ok === false) {
        setSigMsg(String(res.error || 'Could not use that picture.'))
        return
      }
      setSigUri(String(res.data_uri || ''))
      setSigMsg(String(res.message || 'Signature saved.'))
    } catch (e) {
      setSigMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function removeSignature() {
    setBusy(true)
    setSigMsg('')
    try {
      const res = await pharmacyAction({ action: 'remove_signature' })
      if (res.ok === false) {
        setSigMsg(String(res.error || 'Could not remove the signature.'))
        return
      }
      setSigUri('')
      setSigMsg(String(res.message || 'Signature removed.'))
    } catch (e) {
      setSigMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
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
  // One window for the shop's details AND how each one shows on the bill (owner,
  // 18 Oct 2026: "why a separate Bill Logo section? choose the logo and where it
  // goes in the profile window itself"). Every item carries its own "Show on
  // bill" tick, bound to the same bill print keys Bill Fields used to hold; the
  // old Bill Logo nav entry opens this window.
  if (sectionId === 'profile' || sectionId === 'bill_logo') {
    const text = (key: string) => String(profile[key] ?? '')
    const setText = (key: string) => (
      e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
    ) => setProfile((p) => ({ ...p, [key]: e.target.value }))
    // FSSAI has two switches the templates both need (the profile's own flag and
    // the bill's store-header key). One tick here sets both.
    const fssaiOn = Boolean(profile.show_fssai_on_bill) && billOn('show_store_fssai')
    const logoTop = Boolean(bill.show_logo_top_right)
    const logoMark = Boolean(bill.show_logo_center_watermark)
    const logoPlace = logoTop && logoMark ? 'both' : logoTop ? 'top_right' : logoMark ? 'watermark' : 'none'
    const setLogoPlace = (v: string) =>
      setBill((b) => ({
        ...b,
        show_logo_top_right: v === 'top_right' || v === 'both',
        show_logo_center_watermark: v === 'watermark' || v === 'both',
      }))
    return (
      <>
        <PanelTitle>Pharmacy Profile</PanelTitle>
        <Frame title="Pharmacy Information">
          {/* Two columns, label above the box, "Show on bill" beside the label. */}
          <div className="ph-grid-2">
            <ProfileField
              id="ph-name"
              label="Pharmacy Name"
              span
              show={billOn('show_store_name')}
              onShow={(v) => setBillKey('show_store_name', v)}
            >
              <input id="ph-name" className="settings-input" value={text('name')} onChange={setText('name')} />
            </ProfileField>
            <ProfileField
              id="ph-address"
              label="Address"
              span
              show={billOn('show_store_address')}
              onShow={(v) => setBillKey('show_store_address', v)}
            >
              <textarea
                id="ph-address"
                className="settings-input settings-textarea"
                rows={3}
                value={text('address')}
                onChange={setText('address')}
              />
            </ProfileField>
            <ProfileField
              id="ph-phone"
              label="Phone"
              show={billOn('show_store_phone')}
              onShow={(v) => setBillKey('show_store_phone', v)}
            >
              <input id="ph-phone" className="settings-input" value={text('phone')} onChange={setText('phone')} />
            </ProfileField>
            <ProfileField
              id="ph-email"
              label="Email"
              show={billOn('show_store_email')}
              onShow={(v) => setBillKey('show_store_email', v)}
            >
              <input id="ph-email" className="settings-input" value={text('email')} onChange={setText('email')} />
            </ProfileField>
            <ProfileField
              id="ph-gstin"
              label="GSTIN"
              show={billOn('show_store_gstin')}
              onShow={(v) => setBillKey('show_store_gstin', v)}
            >
              <input id="ph-gstin" className="settings-input" value={text('gstin')} onChange={setText('gstin')} />
            </ProfileField>
            <ProfileField
              id="ph-dl"
              label="DL Number"
              show={billOn('show_store_dl')}
              onShow={(v) => setBillKey('show_store_dl', v)}
            >
              <input id="ph-dl" className="settings-input" value={text('dl_number')} onChange={setText('dl_number')} />
            </ProfileField>
            <ProfileField
              id="ph-fssai"
              label="FSSAI Number"
              show={fssaiOn}
              onShow={(v) => {
                setProfile((p) => ({ ...p, show_fssai_on_bill: v }))
                setBillKey('show_store_fssai', v)
              }}
            >
              <input
                id="ph-fssai"
                className="settings-input"
                value={text('fssai_number')}
                onChange={setText('fssai_number')}
              />
            </ProfileField>
          </div>
        </Frame>

        {isWholesale ? (
        <Frame title="Bank Details">
          <div className="ph-grid-2">
            <ProfileField id="ph-bank-name" label="Bank Name">
              <input
                id="ph-bank-name"
                className="settings-input"
                value={text('bank_name')}
                onChange={setText('bank_name')}
              />
            </ProfileField>
            <ProfileField id="ph-bank-ac" label="A/c No.">
              <input
                id="ph-bank-ac"
                className="settings-input"
                value={text('bank_account_no')}
                onChange={setText('bank_account_no')}
              />
            </ProfileField>
            <ProfileField id="ph-bank-ifsc" label="IFSC">
              <input
                id="ph-bank-ifsc"
                className="settings-input"
                style={{ textTransform: 'uppercase' }}
                value={text('bank_ifsc')}
                onChange={setText('bank_ifsc')}
              />
            </ProfileField>
            <ProfileField id="ph-bank-branch" label="Branch">
              <input
                id="ph-bank-branch"
                className="settings-input"
                value={text('bank_branch')}
                onChange={setText('bank_branch')}
              />
            </ProfileField>
          </div>
          <div className="ph-bank-foot">
            <Check
              label="Show bank details on bill"
              checked={billOn('show_store_bank')}
              onChange={(v) => setBillKey('show_store_bank', v)}
            />
            <span className="ph-upi">
              UPI ID: <b>{upiId || 'not set'}</b>
              {onOpenUpi ? (
                <button type="button" className="ph-link-btn" onClick={onOpenUpi}>
                  {upiId ? 'Change' : 'Set'} under UPI Payment QR
                </button>
              ) : null}
            </span>
          </div>
        </Frame>
        ) : null}

        <div className="ph-media-pair">
          <Frame title="Bill Logo">
            <LogoPreview path={logoPath} stamp={logoStamp} />
            <div className="ph-btn-row">
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                // Fired straight from the click, with nothing awaited first:
                // the browser only opens a picker while the user's activation
                // is still live, and an await spends it.
                onClick={() => logoInputRef.current?.click()}
              >
                {logoPath ? 'Change Logo' : 'Choose Image'}
              </button>
              {logoPath ? (
                <button
                  type="button"
                  className="settings-action-btn ph-btn-remove"
                  onClick={removeLogo}
                >
                  Remove
                </button>
              ) : null}
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
            <div className="ph-fields ph-logo-place">
              <Field label="Where on the bill">
                <select
                  className="settings-input"
                  value={logoPlace}
                  onChange={(e) => setLogoPlace(e.target.value)}
                >
                  <option value="none">Not printed</option>
                  <option value="top_right">Small, top right</option>
                  <option value="watermark">Watermark behind medicines</option>
                  <option value="both">Both</option>
                </select>
              </Field>
              {logoTop ? (
                <>
                  <Field label="Top margin (mm)">
                    <input
                      className="settings-input ph-num"
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
                      className="settings-input ph-num"
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
                </>
              ) : null}
              {logoMark ? (
                <Field label="Watermark transparency %">
                  <input
                    className="settings-input ph-num"
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
              ) : null}
            </div>
          </Frame>

          <Frame title="Bill Signature">
            {sigUri ? (
              <div className="settings-logo-preview">
                <img
                  src={sigUri}
                  alt="Bill signature"
                  style={{
                    maxHeight: 70,
                    maxWidth: 260,
                    display: 'block',
                    marginBottom: 6,
                    // a checkerboard shows whether the background is transparent
                    backgroundImage:
                      'repeating-conic-gradient(#ddd 0% 25%, #fff 0% 50%)',
                    backgroundSize: '12px 12px',
                  }}
                />
              </div>
            ) : (
              <Note>No signature — the box stays empty for a hand signature</Note>
            )}
            <div className="ph-btn-row">
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={() => sigInputRef.current?.click()}
              >
                {sigUri ? 'Change Signature' : 'Upload Signature'}
              </button>
              {sigUri ? (
                <button
                  type="button"
                  className="settings-action-btn ph-btn-remove"
                  disabled={busy}
                  onClick={() => void removeSignature()}
                >
                  Remove
                </button>
              ) : null}
              <input
                ref={sigInputRef}
                type="file"
                accept="image/png,image/webp,image/gif,image/jpeg,image/bmp"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  e.target.value = ''
                  if (f) void uploadSignatureFile(f)
                }}
              />
            </div>
            {sigMsg ? <Note>{sigMsg}</Note> : null}
            <Check
              label="Show signature picture on bill"
              checked={billOn('print_signature_image')}
              onChange={(v) => setBillKey('print_signature_image', v)}
            />
            <Help>
              Use a PNG with a transparent background. It prints in the
              signature box (between{' '}
              &quot;For {String(profile.name || 'shop')}&quot; and the caption)
              on every standard bill, not on dot matrix slips. Untick it to
              leave the box for a hand signature for a while.
            </Help>
          </Frame>
        </div>

        {/* The GST amount and discount rows are bill totals: they are ticked
            under Bill Fields -> Totals and footer, not here as well. */}
        <Frame title="GST">
          <Check
            label="Calculate GST on sale items"
            checked={Boolean(profile.gst_enabled)}
            onChange={(v) => setProfile((p) => ({ ...p, gst_enabled: v }))}
          />
        </Frame>

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
          <div className="ph-grid-3">
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
          </div>
          <Note>
            Leave password blank when saving to keep the current password.
          </Note>
        </Frame>

        {/* One Save for everything in this window: the profile (bank details
            too), every "Show on bill" tick, the logo's place, and the login. */}
        <SaveBar
          label="Save Profile"
          saving={saving}
          onClick={saveProfile}
          err={profileErr}
        />
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
        <Frame title="Bill footer style">
          <Field label="Signature box">
            <select
              className="settings-input"
              value={String(bill.signature_position || 'center')}
              onChange={(e) =>
                setBill((b) => ({ ...b, signature_position: e.target.value }))
              }
            >
              <option value="center">New — in the middle, between the message and Total</option>
              <option value="right">Old — on the right under Total, with "For SHOP NAME"</option>
            </select>
          </Field>
          <Note>
            Standard bills and dot matrix slips alike. Each print button (Sales Print
            Buttons) can use its own style.
          </Note>
        </Frame>
        <Frame title="Print All copy labels">
          <Field label="Customer copy">
            <input
              className="settings-input"
              type="text"
              maxLength={120}
              value={String(bill.copy_label_customer ?? 'CUSTOMER COPY')}
              onChange={(e) => setBill((b) => ({ ...b, copy_label_customer: e.target.value }))}
            />
          </Field>
          <Field label="Store copy">
            <input
              className="settings-input"
              type="text"
              maxLength={120}
              value={String(bill.copy_label_store ?? 'STORE COPY')}
              onChange={(e) => setBill((b) => ({ ...b, copy_label_store: e.target.value }))}
            />
          </Field>
          <Note>
            Printed under each bill exactly as typed when Print All prints a Customer or
            Store copy, e.g. "Store copy : For Record Purpose Only". Left empty, the words
            CUSTOMER COPY / STORE COPY are printed.
          </Note>
        </Frame>
        <SaveBar
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
          {/* One frame, a small heading per group: a frame per group inside
              this frame was a box in a box. */}
          {groups.length ? (
            groups.map((g) => (
              <div key={g.group} className="ph-check-group">
                <h4>{g.group}</h4>
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
              </div>
            ))
          ) : (
            <Note>
              Bill field groups did not load. Restart the app.
            </Note>
          )}
          <Help>
            The shop&apos;s own details (name, address, phone, email, GSTIN, DL,
            FSSAI, bank details), the logo and the signature picture are shown
            or hidden in Pharmacy Profile, beside each item.
          </Help>
        </Frame>
        <SaveBar
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
            <div className="ph-input-check">
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
        <SaveBar
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
            Paper size, A6 half, A5 half and A4 layout are set on the Print
            Sales 1 / 2 button — that button always decides what comes out of
            the printer.
          </Note>
          <div className="ph-fields">
            <Field label="Page copies (Print Bill)">
              <input
                className="settings-input ph-num"
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
            <Field label="Medicines per bill page">
              <input
                className="settings-input ph-num"
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
          </div>
          {/* This number is real, but only on the direct Print Bill path.
              F7/F8/F9 go through a Print Sales slot, and the slot deliberately
              ignores it so 2 bill copies cannot silently become 4 sheets — see
              get_print_slot_page_copies. Saying so is better than a control
              that looks global and is not. */}
          <Help>
            <ul className="ph-help-list">
              <li>
                Page copies: how many times each sheet is sent to the printer by
                the <b>Print</b> button. F7 / F8 / F9 use the copies set on their
                own Print Sales 1 / 2 button instead.
              </li>
              <li>
                Medicines per bill page: max lines on one printed bill; extra
                medicines continue on the next page/slip (Continued…). For A6
                dot matrix use 8–10 so lines fit on paper.
              </li>
              <li>
                Bill size Dot matrix shrinks the bill by Shrink % (under
                Advanced — dot matrix); Shrink % fits the bill on A5/A6 without
                clipping borders.
              </li>
            </ul>
          </Help>
        </Frame>

        {/* Expert settings for continuous dot matrix paper, folded so the
            common settings above come first (owner, 18 Oct 2026). It opens by
            itself when Bill size is Dot matrix. */}
        <Frame title="Advanced — dot matrix">
          <details className="ph-advanced" open={isDotMatrix}>
            <summary>Dot matrix slip, margins, tear-off and slip style</summary>
            <div className="ph-advanced-body">
              <h4>Bill size</h4>
              <div className="ph-fields">
                <Field label="Shrink % (dot matrix)">
                  <input
                    className="settings-input ph-num"
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
              </div>
              {!isDotMatrix ? (
                <Note>Shrink % applies only when Bill size is Dot matrix.</Note>
              ) : null}

              <h4>Slip and margins</h4>
              <div className="ph-fields">
                <Field label="Dot matrix paper (slip height)">
                  {/* Continuous paper is made in inches. Every bill moves the paper by
                      this height, so a few mm off puts every next bill a few mm lower. */}
                  <select
                    className="settings-input"
                    value={
                      SLIP_PRESETS.find(
                        (p) => Math.abs(p.cm - Number(bill.dot_matrix_slip_height_cm ?? 0)) < 0.005,
                      )?.cm.toString() ?? 'custom'
                    }
                    onChange={(e) => {
                      if (e.target.value === 'custom') return
                      setBill((b) => ({
                        ...b,
                        dot_matrix_slip_height_cm: Number(e.target.value),
                      }))
                    }}
                  >
                    {SLIP_PRESETS.map((p) => (
                      <option key={p.cm} value={p.cm.toString()}>
                        {p.label}
                      </option>
                    ))}
                    <option value="custom">Other (type cm below)</option>
                  </select>
                </Field>
                <Field label="Dot matrix slip height (cm)">
                  <input
                    className="settings-input ph-num"
                    type="number"
                    step={0.01}
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
                <Field label="Dot matrix top margin (cm)">
                  <input
                    className="settings-input ph-num"
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
                <Field label="Dot matrix bottom margin (cm)">
                  <input
                    className="settings-input ph-num"
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
                    className="settings-input ph-num"
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
                <Field label="Dot matrix left offset (cm)">
                  <input
                    className="settings-input ph-num"
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
                <Field label="Dot matrix line spacing">
                  <input
                    className="settings-input ph-num"
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
              </div>

              <h4>Tear-off</h4>
              <div className="ph-fields">
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
                    className="settings-input ph-num"
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
                <Field label="Dot matrix tear feed (cm)">
                  <input
                    className="settings-input ph-num"
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
              </div>

              <h4>Slip style</h4>
              <div className="ph-fields">
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
              </div>
              <div className="settings-check-grid-3">
                <Check
                  label="Total printed bold (printer strikes the line twice)"
                  checked={Boolean(bill.dot_matrix_bold_total ?? true)}
                  onChange={(v) => setBill((b) => ({ ...b, dot_matrix_bold_total: v }))}
                />
                <Check
                  label="Whole bill printed bold (darker, prints a little slower)"
                  checked={Boolean(bill.dot_matrix_bold_all ?? false)}
                  onChange={(v) => setBill((b) => ({ ...b, dot_matrix_bold_all: v }))}
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
              </div>

              <h4>Alignment</h4>
              <div className="ph-btn-row">
                <button
                  type="button"
                  className="settings-action-btn"
                  disabled={aligning}
                  onClick={() => void onAlignmentTest()}
                >
                  {aligning ? 'Printing…' : 'Print alignment test'}
                </button>
                {alignMsg ? <span className="ph-inline-msg">{alignMsg}</span> : null}
              </div>
              <Note>
                The test uses the values on screen: try a value, print, and save
                once it lands right.
              </Note>

              <Help>
                <ul className="ph-help-list">
                  <li>
                    Dot matrix line spacing 28–32 = more space between lines
                    (default 30).
                  </li>
                  <li>
                    Top offset cm: reverse-feed before print to start bill higher
                    on A6 slip (default 0.8). Top margin cm: how far BELOW the
                    perforation the first line prints.
                  </li>
                  <li>
                    Slip height cm: measure one slip from perforation to
                    perforation; every bill then moves the paper by exactly one
                    slip (0 = off).
                  </li>
                  <li>
                    Top and bottom margin cm: blank space kept at the top and
                    bottom of every slip; the medicine table always fills
                    whatever is left, so the bill is the same shape on every
                    slip.
                  </li>
                  <li>
                    Print width cm: the paper width minus the tractor holes
                    (14.5 cm slip - 0.8 cm each side = 12.9).
                  </li>
                  <li>
                    Tear-off handled by Satpuda: after each bill the slip is fed
                    out by Tear-off eject cm so the perforation clears the tear
                    edge, and the next bill pulls back exactly that same
                    distance - no more - so it starts on the top of the next
                    slip. Raise Tear-off eject until the slip is fully out past
                    the tear bar (4 cm to start, up to 8); it cannot drift,
                    because both ends use the one number.
                  </li>
                  <li>
                    Turn the printer&apos;s own Auto tear off OFF, or the two
                    fight each other - it is in the printer&apos;s Default
                    Setting mode, the app cannot change it.
                  </li>
                  <li>
                    Left offset cm: moves the whole bill right of the
                    printer&apos;s first column (nothing prints left of it - if
                    the bill is too far right at 0, move the paper left in the
                    printer).
                  </li>
                  <li>
                    Tear feed cm: how far the paper moves up after a bill when no
                    slip height is set.
                  </li>
                  <li>
                    Print alignment test uses the values on screen, so try a
                    value, print, and save once it lands right.
                  </li>
                  <li>Turn off vertical borders for horizontal-rule-only bills.</li>
                  <li>Shrink % fits the bill on A5/A6 without clipping borders.</li>
                </ul>
              </Help>
            </div>
          </details>
        </Frame>
        <SaveBar
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
          <Help>
            Bill copies = identical slips of the same bill on one sheet (A5:
            1–2, A6: 1, A4: 1–4). Long bills (12+ lines) become multiple pages —
            one sheet per 12 medicines; copies duplicates that page only. e.g.
            36 lines → 3 sheets; A5×2 = 3 A5 pages with 2 identical slips each.
            Non-final pages show Continued...; last page shows Total. Half
            position = A5/A6 single-copy placement.
          </Help>
        </Frame>
        <Frame title="Enter after payment (Sales Page)">
          <Note>
            Choose what happens when you press Enter after payment on the Sales
            page.
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
        </Frame>
        <SaveBar
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
          </Note>
          {spoolerRunning === false ? (
            <p className="error">
              Print Spooler is stopped. Start it before printing (Services →
              Print Spooler → Start).
            </p>
          ) : null}

          <Field label="Default printer">
            <div className="ph-select-btn">
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
          <Note>Leave the printers for Print Sales 1/2 blank to use the default printer.</Note>

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
          <Note>
            For LX-310: select &quot;EPSON LX-310 ESC/P&quot; (native driver),
            NOT &quot;EPSON LX-310&quot; (Class Driver).
          </Note>

          <h4>Printing</h4>
          <div className="settings-check-grid-3">
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
          </div>
          <Help>
            <ul className="ph-help-list">
              <li>
                Paper size, orientation, tray, and quality use Windows printer
                preferences.
              </li>
              <li>
                Silent print and the Windows print dialog (used when silent
                print is off or fails) ask the printer for grayscale, so bills
                use the black cartridge instead of a mix of colour inks. Dot
                matrix printing is not affected.
              </li>
            </ul>
          </Help>
        </Frame>

        {/* SumatraPDF, the dot matrix print method and the debug log are
            rarely touched: folded, so the printers come first (owner,
            18 Oct 2026). */}
        <Frame title="Advanced">
          <details
            className="ph-advanced"
            open={String(printer.printer_type || 'standard') === 'dot_matrix'}
          >
            <summary>SumatraPDF, dot matrix print method, print log file</summary>
            <div className="ph-advanced-body">
              <Field label="SumatraPDF path">
                <div className="ph-select-btn">
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
                Auto-detects tools\SumatraPDF64.exe or SumatraPDF32.exe.
              </Note>

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
              <Help>
                RAW ESC/P sends the bill straight to the printer: it starts at
                the printer&apos;s first column and the Top offset / Slip height
                in Paper &amp; Copies apply exactly. Use it whenever the printer
                takes it (any Epson-compatible 9-pin). Windows driver draws the
                bill through the driver; it now starts top-left too, and with a
                Slip height the page is one slip long. Auto picks RAW for an
                ESC/P driver and the Windows driver for class / generic drivers.
              </Help>

              <Note>
                Print log file: {printLogPath || 'config/print_log.txt'}
              </Note>
            </div>
          </details>
        </Frame>

        {/* The panel's Save, kept in sight like every other panel's SaveBar,
            with Test Print beside it and the last printer message. */}
        <div className="settings-savebar">
          <SaveBtn
            label={saving ? 'Saving…' : 'Save Printer Settings'}
            saving={saving}
            onClick={() => onSave({ printer })}
          />
          <button
            type="button"
            className="settings-action-btn ph-btn-secondary"
            disabled={testing}
            onClick={() => void onTestPrint()}
          >
            Test Print
          </button>
          {printerWarn ? (
            <span className="settings-savebar-msg err">{printerWarn}</span>
          ) : null}
          {printerMsg ? (
            <span className="settings-savebar-msg">{printerMsg}</span>
          ) : null}
        </div>
      </>
    )
  }

  return <p className="muted">Unknown pharmacy section.</p>
}
