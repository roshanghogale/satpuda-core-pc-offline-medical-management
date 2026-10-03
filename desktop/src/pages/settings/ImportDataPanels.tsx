/** Import + Data & System settings — mirrors Tk Import / Database tabs. */

import { useEffect, useRef, useState } from 'react'
import { expiryProblem, formatExpiryMmYy } from '../../expiryText'
import {
  importAction,
  systemAction,
  type SettingsBundle,
} from '../../settingsApi'
import {
  Check,
  Field,
  Frame,
  Note,
  PanelTitle,
  SaveBtn,
} from './SettingsChrome'
import {
  FileImportReviewDialog,
  type FileImportBill,
} from '../FileImportReviewDialog'
import { IS_DEMO } from '../../demoMode'

type Opts = SettingsBundle['options']

/** One store as it exists on the server, redacted for this screen. */
type ServerStoreRow = {
  store_id: string
  store_key: string
  store_name: string
  is_this_pc?: boolean
  used_by_local_store?: string
}

type StoreRow = {
  store_key?: string
  display_name?: string
  [k: string]: unknown
}

/** One medicine on the shelf, as the Opening Stock form collects it. */
type OpeningRow = {
  name: string
  batch_no: string
  expiry_date: string
  type: string
  qty: string          // strips for tablets/capsules/bolus, units for the rest
  pack: string         // tabs per strip, or the pack size (500ml, 100gm)
  extra_medicine: string
  mrp: string
  rate: string
  gst_percent: string
  manufacturer: string
  hsn_code: string
  schedule: string
  content_drug: string
  supplier_name: string
}

function emptyOpeningRow(): OpeningRow {
  return {
    name: '', batch_no: '', expiry_date: '', type: 'Tablet',
    qty: '', pack: '', extra_medicine: '', mrp: '', rate: '', gst_percent: '',
    manufacturer: '', hsn_code: '', schedule: '', content_drug: '',
    supplier_name: '',
  }
}

const STRIP_TYPES = ['tablet', 'tablet pack', 'capsule', 'bolus', 'bolus pack']

/** Typed freely, because a shop may have its own; these are just the common ones. */
const OPENING_TYPE_SUGGESTIONS = [
  'Tablet', 'Tablet Pack', 'Capsule', 'Bolus', 'Bolus Pack', 'Syrup', 'Suspension',
  'Liquid', 'Injection', 'Injection - Vial', 'Drops', 'Eye Drops', 'Ear Drops',
  'Nasal Drops', 'Ointment', 'Cream', 'Gel', 'Lotion', 'Powder', 'Sachet',
  'Granules', 'Spray', 'Shampoo', 'Soap', 'Feed Supplement', 'Vaccine', 'Others',
]

/** A strip type counts strips and tablets; everything else counts bottles and
 *  tubes with a pack size - the same split the Purchase screen shows. */
function openingLabels(type: string) {
  const strip = STRIP_TYPES.includes((type || '').trim().toLowerCase())
  return {
    strip,
    qty: strip ? 'Strips (Qty)' : 'Units (Qty)',
    pack: strip ? 'Tabs/Strip' : 'Pack Size (e.g. 500ml)',
    extra: strip ? 'Loose tablets' : 'Extra units',
    // Money on a strip type is per strip, the same way the medicine editor
    // says it, so nobody types a per-tablet MRP into a per-strip box.
    mrp: strip ? 'MRP (per strip)' : 'MRP',
    rate: strip ? 'Rate (per strip)' : 'Rate (purchase)',
  }
}

export function ImportPanel({
  sectionId,
  importPrefs,
  setImportPrefs,
  schedules,
  saving,
  onSaveImport,
}: {
  sectionId: string
  importPrefs: Record<string, unknown>
  setImportPrefs: React.Dispatch<React.SetStateAction<Record<string, unknown>>>
  opts: Opts
  schedules: string[]
  saving: boolean
  onSaveImport: () => void
}) {
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [webUrl, setWebUrl] = useState('')
  const [mobileInfo, setMobileInfo] = useState('')
  const [mobileQr, setMobileQr] = useState('')
  const [mobileQrError, setMobileQrError] = useState('')
  const [mobileJson, setMobileJson] = useState('')
  const [mobilePreview, setMobilePreview] = useState('')
  const [fileStatus, setFileStatus] = useState('')
  const [fileJson, setFileJson] = useState('')
  const [fileToken, setFileToken] = useState('')
  const [fileCount, setFileCount] = useState(0)
  const [fileIndex, setFileIndex] = useState(0)
  const [fileBill, setFileBill] = useState<FileImportBill | null>(null)
  const [fileReviewOpen, setFileReviewOpen] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  // Opening stock: the shelf a shop already had on the day it started, which no
  // purchase bill in the system ever brought in.
  const [openingText, setOpeningText] = useState('')
  // Rows typed into the form above the paste box. They are sent as `rows`,
  // which the engine already accepts, so nothing here builds CSV by hand.
  const [openingList, setOpeningList] = useState<OpeningRow[]>([])
  const [openingDraft, setOpeningDraft] = useState<OpeningRow>(emptyOpeningRow())
  const [openingRows, setOpeningRows] = useState<Record<string, unknown>[]>([])
  const [openingProblems, setOpeningProblems] = useState<string[]>([])
  const openingFileRef = useRef<HTMLInputElement>(null)
  const openingNameRef = useRef<HTMLInputElement>(null)
  // The labels follow the type: a strip type counts strips and tablets, a
  // bottle counts units and carries a printed pack size.
  const openingMeta = openingLabels(openingDraft.type)

  /** Park the typed medicine on the list and get ready for the next one. */
  function addOpeningRow() {
    if (!openingDraft.name.trim()) return
    setOpeningList((rows) => [...rows, openingDraft])
    // Type, GST and manufacturer repeat down a shelf; the rest does not.
    setOpeningDraft((d) => ({
      ...emptyOpeningRow(),
      type: d.type,
      gst_percent: d.gst_percent,
      manufacturer: d.manufacturer,
      schedule: d.schedule,
      supplier_name: d.supplier_name,
    }))
    openingNameRef.current?.focus()
  }

  const scheduleList = schedules?.length ? schedules : []

  async function runImport(action: string, extra: Record<string, unknown> = {}) {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await importAction({ action, ...extra })
      if (action === 'start_web') {
        const url = String(res.url || '')
        setWebUrl(url)
        const opened = Boolean(res.opened)
        setMsg(
          url
            ? opened
              ? `Web entry started and opened: ${url}`
              : `Web entry: ${url} (open this URL in your browser)`
            : String(res.message || 'Started.'),
        )
        if (url && !opened) {
          try {
            window.open(url, '_blank', 'noopener,noreferrer')
          } catch {
            /* ignore */
          }
        }
      } else if (action === 'start_mobile') {
        const url = String(res.url || '')
        setMobileInfo(url || JSON.stringify(res))
        setMobileQr(String(res.qr || ''))
        setMobileQrError(String(res.qr_error || ''))
        setMsg(url ? `WiFi URL: ${url}` : 'Mobile import server started.')
      } else if (action === 'stop_mobile') {
        setMobileInfo('')
        setMobileQr('')
        setMobileQrError('')
        setMsg('WiFi receiver stopped.')
      } else if (action === 'mobile_status') {
        const url = String(res.url || '')
        if (url) setMobileInfo(url)
        // The engine says the receiver is down: do not leave a dead address
        // (and its QR) on the screen for the shop to type into the phone.
        else if (res.running === false) setMobileInfo('')
        setMobileQr(String(res.qr || ''))
        setMobileQrError(String(res.qr_error || ''))
        if (res.last_preview) {
          setMobilePreview(JSON.stringify(res.last_preview, null, 2))
        }
        if (res.has_payload && res.last_raw) {
          setMobileJson(String(res.last_raw))
          setMsg('Received JSON from phone.')
        }
      } else if (action === 'parse_mobile') {
        setMobilePreview(JSON.stringify(res, null, 2))
        setMsg(`Detected ${res.export_type || 'data'} (${res.count ?? 0} items).`)
      } else if (action === 'import_mobile') {
        const parts = [
          `Inserted ${res.inserted ?? res.saved ?? 0}`,
          res.updated != null ? `updated ${res.updated}` : null,
          res.skipped ? `skipped ${res.skipped}` : null,
        ].filter(Boolean)
        setMsg(
          `${parts.join(', ')}. Refresh Inventory to see medicines.` +
            (res.sync_warning ? ` Online sync: ${res.sync_warning}` : ''),
        )
        // The receiver keeps the last payload; remember it was imported so the
        // background check does not put the same list back on screen.
        if (res.ok !== false) importedRawRef.current = mobileJson
        setMobileJson('')
        setMobilePreview('')
      } else if (action === 'status') {
        setFileStatus(String(res.message || 'OK'))
      } else if (action === 'parse_file_import') {
        const token = String(res.token || '')
        const count = Number(res.count || 0)
        setFileToken(token)
        setFileCount(count)
        setFileIndex(0)
        setMsg(`${count} bill(s) loaded. Review each bill, then submit all.`)
        if (token && count > 0) {
          await loadFileBill(token, 0)
          setFileReviewOpen(true)
        }
      } else if (action === 'submit_file_import') {
        const saved = Number(res.saved ?? 0)
        const total = Number(res.total ?? fileCount)
        const errs = Array.isArray(res.errors) ? res.errors : []
        setFileReviewOpen(false)
        setFileToken('')
        setFileBill(null)
        setFileJson('')
        if (errs.length) {
          setErr(`Saved ${saved}/${total}. ${errs.join('; ')}`)
        } else {
          setMsg(`All ${saved} purchase(s) saved successfully.`)
        }
      }
      if (res.ok === false && res.error) {
        setErr(String(res.error))
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function loadFileBill(token: string, index: number) {
    const res = await importAction({
      action: 'get_file_import_bill',
      token,
      index,
    })
    if (res.ok === false && res.error) {
      throw new Error(String(res.error))
    }
    setFileIndex(index)
    setFileBill((res.bill as FileImportBill) || null)
  }

  async function syncFileBill(bill: FileImportBill) {
    if (!fileToken) return
    setFileBill(bill)
    await importAction({
      action: 'update_file_import_bill',
      token: fileToken,
      index: fileIndex,
      bill,
    })
  }

  async function goFilePrev() {
    if (!fileToken || fileIndex <= 0 || !fileBill) return
    setBusy(true)
    try {
      await importAction({
        action: 'update_file_import_bill',
        token: fileToken,
        index: fileIndex,
        bill: fileBill,
      })
      await loadFileBill(fileToken, fileIndex - 1)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function goFileNext() {
    if (!fileToken || !fileBill) return
    setBusy(true)
    try {
      await importAction({
        action: 'update_file_import_bill',
        token: fileToken,
        index: fileIndex,
        bill: fileBill,
      })
      await loadFileBill(fileToken, fileIndex + 1)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function submitAllFileBills() {
    if (!fileToken || !fileBill) return
    if (
      !window.confirm(
        `Save all ${fileCount} purchase(s) to the database?`,
      )
    ) {
      return
    }
    setBusy(true)
    setErr('')
    try {
      await importAction({
        action: 'update_file_import_bill',
        token: fileToken,
        index: fileIndex,
        bill: fileBill,
      })
      await runImport('submit_file_import', { token: fileToken })
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function cancelFileImport() {
    if (fileToken) {
      if (
        !window.confirm('Close without saving? Unsaved changes will be lost.')
      ) {
        return
      }
      try {
        await importAction({ action: 'cancel_file_import', token: fileToken })
      } catch {
        /* ignore */
      }
    }
    setFileReviewOpen(false)
    setFileToken('')
    setFileBill(null)
  }

  // What the phone sent is shown the moment it arrives. Before, the PC sat on
  // "WiFi URL: ..." until someone thought to press Refresh status, so a person
  // who had just pressed Send on the phone saw nothing happen at all. This asks
  // the engine quietly every three seconds while the receiver is running and
  // nothing is waiting to be imported -- no spinner, no message unless
  // something actually arrived. A payload already imported is not offered again.
  const importedRawRef = useRef('')
  useEffect(() => {
    if (sectionId !== 'mobile' || !mobileInfo || mobileJson) return
    let cancelled = false
    const tick = async () => {
      try {
        const res = await importAction({ action: 'mobile_status' })
        if (cancelled) return
        if (res.running === false) {
          setMobileInfo('')
          setMobileQr('')
          return
        }
        const raw = String(res.last_raw || '')
        if (res.has_payload && raw && raw !== importedRawRef.current) {
          const preview = (res.last_preview || {}) as Record<string, unknown>
          setMobileJson(raw)
          setMobilePreview(JSON.stringify(preview, null, 2))
          const count = Number(preview.count ?? 0)
          const kind = String(preview.export_type || 'data')
          const device = String(preview.device_name || 'the phone')
          setMsg(`Received ${count} ${kind} from ${device}. Check the preview, then press Import.`)
        }
      } catch {
        /* the engine is busy or restarting: try again on the next tick */
      }
    }
    void tick()
    const timer = window.setInterval(() => void tick(), 3000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [sectionId, mobileInfo, mobileJson])

  function onLoadOpeningFile(file: File | null) {
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      setOpeningText(String(reader.result || ''))
      setMsg(`Loaded ${file.name}`)
    }
    reader.onerror = () => setErr('Could not read file.')
    reader.readAsText(file)
  }

  async function runOpening(action: 'opening_stock_preview' | 'opening_stock_apply') {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const typed = openingList.filter((r) => r.name.trim())
      const body = typed.length
        ? {
            rows: typed.map((r) => ({
              name: r.name.trim(),
              batch_no: r.batch_no.trim(),
              expiry_date: r.expiry_date.trim(),
              type: r.type,
              // The engine's "unit" is the pack: tabs per strip on a strip
              // type, the printed size on anything else.
              unit: r.pack.trim(),
              stock_qty: r.qty.trim(),
              extra_medicine: r.extra_medicine.trim(),
              mrp: r.mrp.trim(),
              rate: r.rate.trim(),
              gst_percent: r.gst_percent.trim(),
              manufacturer: r.manufacturer.trim(),
              hsn_code: r.hsn_code.trim(),
              schedule: r.schedule.trim(),
              content_drug: r.content_drug.trim(),
              supplier_name: r.supplier_name.trim(),
            })),
          }
        : openingText.trim().startsWith('{') || openingText.trim().startsWith('[')
          ? { json: openingText }
          : { csv: openingText }
      const res = await importAction({ action, ...body })
      if (!res.ok) {
        setErr(String(res.error || 'Could not read those rows.'))
        return
      }
      setOpeningProblems((res.problems as string[]) || [])
      if (action === 'opening_stock_preview') {
        setOpeningRows((res.rows as Record<string, unknown>[]) || [])
        setMsg(String(res.message || ''))
      } else {
        setOpeningRows([])
        setOpeningList([])
        setMsg(String(res.message || 'Opening stock added.'))
        if (res.sync_warning) setErr(`Saved here, but the server said: ${String(res.sync_warning)}`)
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function onLoadJsonFile(file: File | null) {
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      setFileJson(String(reader.result || ''))
      setMsg(`Loaded ${file.name}`)
    }
    reader.onerror = () => setErr('Could not read file.')
    reader.readAsText(file)
  }

  if (sectionId === 'purchase_bill') {
    return (
      <>
        <PanelTitle>Purchase Bill</PanelTitle>
        <Frame title="Bill photo reading">
          <Check
            label="Enable bill photo reading"
            checked={Boolean(importPrefs.gemini_enabled)}
            onChange={(v) =>
              setImportPrefs((i) => ({ ...i, gemini_enabled: v }))
            }
          />
          <Note>
            {importPrefs.gemini_configured
              ? 'API key is built into this app (same as EXE / web). Store users do not enter a key.'
              : 'Bill photo import is not available in this build.'}
          </Note>
          <Field label="Fallback schedule">
            {scheduleList.length ? (
              <select
                className="settings-input"
                value={String(importPrefs.fallback_schedule ?? '')}
                onChange={(e) =>
                  setImportPrefs((i) => ({
                    ...i,
                    fallback_schedule: e.target.value,
                  }))
                }
              >
                <option value="">(none)</option>
                {scheduleList.map((s, idx) => (
                  <option key={`${s}-${idx}`} value={s}>
                    {s || '(empty)'}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="settings-input"
                value={String(importPrefs.fallback_schedule ?? '')}
                onChange={(e) =>
                  setImportPrefs((i) => ({
                    ...i,
                    fallback_schedule: e.target.value,
                  }))
                }
              />
            )}
          </Field>
          <Note>
            File / image purchase bill import runs in Purchase (Shift+F2) using
            the same Python parsers as classic. Photo reading is included in the
            build — only enable/disable and fallback schedule are saved here.
          </Note>
        </Frame>
        <SaveBtn
          label="Save Import Prefs"
          saving={saving}
          onClick={onSaveImport}
        />
      </>
    )
  }

  if (sectionId === 'web') {
    return (
      <>
        <PanelTitle>Web Entry</PanelTitle>
        <Frame title="Web purchase entry">
          <button
            type="button"
            className="settings-action-btn"
            disabled={busy}
            onClick={() => runImport('start_web')}
          >
            Start Web Entry Server
          </button>
          {webUrl ? (
            <Note>
              Open in browser: <strong>{webUrl}</strong>
            </Note>
          ) : (
            <Note>Starts the local web purchase entry page and shows its URL.</Note>
          )}
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
      </>
    )
  }

  if (sectionId === 'file_import') {
    return (
      <>
        <PanelTitle>Import Data</PanelTitle>
        <Frame title="Import purchase JSON">
          <Note>
            Paste or load JSON with{' '}
            <code>{'{"bills": [ ... ]}'}</code> — same format as classic Settings
            → Import Data. Review each bill, then submit all at once.
          </Note>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json,.txt,application/json"
            style={{ display: 'none' }}
            onChange={(e) => onLoadJsonFile(e.target.files?.[0] ?? null)}
          />
          <textarea
            className="settings-input"
            rows={8}
            value={fileJson}
            onChange={(e) => setFileJson(e.target.value)}
            placeholder='{"bills":[{"supplier":{"name":"..."},"purchase_date":"2026-01-01","bill_number":"INV-1","items":[...]}]}'
          />
          <div className="settings-inline-actions">
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => fileInputRef.current?.click()}
            >
              Load from file
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy || !fileJson.trim()}
              onClick={() =>
                runImport('parse_file_import', { json: fileJson })
              }
            >
              Parse JSON
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => {
                setFileJson('')
                setMsg('')
                setErr('')
              }}
            >
              Clear
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => runImport('status')}
            >
              Status
            </button>
          </div>
          {fileStatus ? <Note>{fileStatus}</Note> : null}
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
        <Frame title="JSON format guide">
          <Note>
            Each bill: supplier (name, address, phone, gstin, dl_numbers),
            purchase_date (YYYY-MM-DD), bill_number, amount_paid / cash_paid /
            online_paid, overall_discount, items[]. Each item: medicine_name,
            type, batch_no, expiry_date (MM/YY), qty, rate, gst_percent,
            item_discount, mrp, schedule, etc. purchase_no is auto-generated.
          </Note>
        </Frame>
        <FileImportReviewDialog
          open={fileReviewOpen}
          index={fileIndex}
          count={fileCount}
          bill={fileBill}
          busy={busy}
          onClose={() => void cancelFileImport()}
          onPrev={() => void goFilePrev()}
          onNext={() => void goFileNext()}
          onSubmitAll={() => void submitAllFileBills()}
          onBillChange={(b) => void syncFileBill(b)}
        />
      </>
    )
  }

  if (sectionId === 'mobile') {
    return (
      <>
        <PanelTitle>Mobile Import</PanelTitle>
        <Frame title="Receive from Android (WiFi)">
          <Note>
            Start the receiver, then on the phone open Satpuda &rarr; Settings
            &rarr; Mobile Import and type this PC&apos;s address. The QR below
            carries the same address for a scanner app. Or paste / load JSON
            exported from the Satpuda Android app.
          </Note>
          <div className="settings-inline-actions">
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => runImport('start_mobile')}
            >
              Start WiFi receiver
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => runImport('mobile_status')}
            >
              Refresh status
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => runImport('stop_mobile')}
            >
              Stop server
            </button>
          </div>
          {mobileInfo ? (
            <div className="mobile-receiver">
              {mobileQr ? (
                <img
                  className="mobile-qr"
                  src={mobileQr}
                  alt="QR code of this PC's Mobile Import address"
                  width={180}
                  height={180}
                />
              ) : null}
              <div className="mobile-receiver-address">
                <label className="settings-note" htmlFor="mobile-receive-url">
                  PC address (type this on the phone)
                </label>
                <input
                  id="mobile-receive-url"
                  className="settings-input"
                  readOnly
                  value={mobileInfo}
                  onFocus={(e) => e.currentTarget.select()}
                />
                {!mobileQr ? (
                  <Note>
                    The QR could not be drawn on this PC. Type the address
                    above on the phone instead &mdash; it is the same thing.
                    {mobileQrError ? ` (${mobileQrError})` : ''}
                  </Note>
                ) : null}
                <Note>
                  Phone and PC must be on the same network: shop router, home
                  WiFi, or turn on the phone hotspot and connect this PC to it.
                  No internet needed. Allow Windows Firewall if it asks.
                </Note>
              </div>
            </div>
          ) : null}
        </Frame>
        <Frame title="JSON payload">
          <textarea
            className="settings-input"
            rows={8}
            value={mobileJson}
            onChange={(e) => setMobileJson(e.target.value)}
            placeholder='Paste {"export_type":"medicines", ...} here'
          />
          <div className="settings-inline-actions">
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => runImport('parse_mobile', { json: mobileJson })}
            >
              Parse preview
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() =>
                runImport('import_mobile', mobileJson ? { json: mobileJson } : { use_last_received: true })
              }
            >
              Import
            </button>
          </div>
          {mobilePreview ? (
            <pre className="settings-note mobile-preview">{mobilePreview}</pre>
          ) : null}
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
      </>
    )
  }

  if (sectionId === 'opening_stock') {
    return (
      <>
        <PanelTitle>Opening Stock</PanelTitle>
        <Frame title="Add the stock already on the shelf (no supplier, no bill)">
          <Note>
            For the medicine a shop already had when it started on Satpuda: type
            or paste the rows here and they go straight into Inventory. No
            supplier is created, no purchase bill is invented and no payment is
            recorded. This is the same form as the Satpuda Loader app on the
            phone, and it writes through the same import, so a shelf can be
            typed on either one - phone rows arrive by QR / Import from mobile.
          </Note>
          <Note>
            Fill the medicine in, press Add to list, and repeat. Nothing is
            written until you press Add to Inventory. A medicine already on the
            shelf with the same name and batch is updated, not added twice.
          </Note>
          <datalist id="opening-med-types">
            {OPENING_TYPE_SUGGESTIONS.map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
          {/* Enter walks to the next box, the same as the Purchase page; Enter
              on the last box (or Ctrl+Enter anywhere) adds the medicine. Enter
              used to add it from whichever box it was pressed in, so a
              medicine went onto the list half typed. */}
          <div
            className="opening-grid"
            onKeyDown={(e) => {
              if (e.key !== 'Enter' || e.nativeEvent.isComposing) return
              const target = e.target as HTMLElement
              if (target.tagName !== 'INPUT' && target.tagName !== 'SELECT') return
              e.preventDefault()
              const boxes = Array.from(
                e.currentTarget.querySelectorAll<HTMLElement>('input, select'),
              ).filter((el) => !(el as HTMLInputElement).disabled)
              const at = boxes.indexOf(target)
              const next = at >= 0 ? boxes[at + 1] : undefined
              if (e.ctrlKey || !next) {
                if (openingDraft.name.trim()) addOpeningRow()
                else openingNameRef.current?.focus()
                return
              }
              next.focus()
              if (next instanceof HTMLInputElement) next.select()
            }}
          >
            <label className="field opening-span2">
              <span className="field-label">Medicine name</span>
              <input
                className="settings-input"
                ref={openingNameRef}
                value={openingDraft.name}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, name: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">Type</span>
              <input
                className="settings-input"
                list="opening-med-types"
                value={openingDraft.type}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, type: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">Batch No</span>
              <input
                className="settings-input"
                value={openingDraft.batch_no}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, batch_no: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">Expiry (MM/YY)</span>
              <input
                className="settings-input"
                inputMode="numeric"
                maxLength={5}
                placeholder="08/27"
                value={openingDraft.expiry_date}
                onChange={(e) => {
                  // The slash is the field's job, the same as on the Purchase
                  // page. This box used to ask for MM/YYYY while every other
                  // one in the product asked for MM/YY.
                  const el = e.target
                  const deleting = el.value.length < openingDraft.expiry_date.length
                  setOpeningDraft((d) => ({
                    ...d,
                    expiry_date: formatExpiryMmYy(el.value, deleting),
                  }))
                }}
                onBlur={(e) =>
                  setOpeningDraft((d) => ({
                    ...d,
                    expiry_date: formatExpiryMmYy(e.target.value),
                  }))
                }
                title={expiryProblem(openingDraft.expiry_date) || 'Expiry as MM/YY'}
              />
            </label>
            <label className="field">
              <span className="field-label">{openingMeta.qty}</span>
              <input
                className="settings-input"
                type="number"
                min={0}
                value={openingDraft.qty}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, qty: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">{openingMeta.pack}</span>
              <input
                className="settings-input"
                placeholder={openingMeta.strip ? '10' : '500ml'}
                value={openingDraft.pack}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, pack: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">{openingMeta.extra}</span>
              <input
                className="settings-input"
                type="number"
                min={0}
                value={openingDraft.extra_medicine}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, extra_medicine: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">{openingMeta.mrp}</span>
              <input
                className="settings-input"
                type="number"
                step="0.01"
                min={0}
                value={openingDraft.mrp}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, mrp: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">{openingMeta.rate}</span>
              <input
                className="settings-input"
                type="number"
                step="0.01"
                min={0}
                value={openingDraft.rate}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, rate: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">GST %</span>
              <input
                className="settings-input"
                type="number"
                step="0.01"
                min={0}
                value={openingDraft.gst_percent}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, gst_percent: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">
                Manufacturer <span className="opt">(optional)</span>
              </span>
              <input
                className="settings-input"
                value={openingDraft.manufacturer}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, manufacturer: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">
                HSN <span className="opt">(optional)</span>
              </span>
              <input
                className="settings-input"
                value={openingDraft.hsn_code}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, hsn_code: e.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">
                Schedule <span className="opt">(optional)</span>
              </span>
              <input
                className="settings-input"
                value={openingDraft.schedule}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, schedule: e.target.value }))
                }
              />
            </label>
            <label className="field opening-span2">
              <span className="field-label">
                Content / Drug <span className="opt">(optional)</span>
              </span>
              <input
                className="settings-input"
                value={openingDraft.content_drug}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, content_drug: e.target.value }))
                }
              />
            </label>
            <label className="field opening-span2">
              <span className="field-label">
                Supplier <span className="opt">(reference only - no bill, no due)</span>
              </span>
              <input
                className="settings-input"
                value={openingDraft.supplier_name}
                onChange={(e) =>
                  setOpeningDraft((d) => ({ ...d, supplier_name: e.target.value }))
                }
              />
            </label>
          </div>
          <div className="settings-inline-actions">
            <button
              type="button"
              className="settings-action-btn"
              disabled={!openingDraft.name.trim()}
              onClick={addOpeningRow}
            >
              Add to list
            </button>
            <span className="muted">
              {openingList.length
                ? openingList.length + ' medicine(s) waiting'
                : 'Nothing in the list yet'}
            </span>
            {openingList.length ? (
              <button
                type="button"
                className="settings-action-btn"
                onClick={() => setOpeningList([])}
              >
                Clear list
              </button>
            ) : null}
          </div>
          {openingList.length ? (
            <div className="settings-table-wrap">
              <table className="settings-table">
                <thead>
                  <tr>
                    <th>Medicine</th>
                    <th>Type</th>
                    <th>Batch</th>
                    <th>Expiry</th>
                    <th>Qty</th>
                    <th>Pack</th>
                    <th>Extra</th>
                    <th>MRP</th>
                    <th>Rate</th>
                    <th>GST%</th>
                    <th>Supplier</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {openingList.map((r, i) => (
                    <tr key={r.name + '-' + i}>
                      <td>{r.name}</td>
                      <td>{r.type}</td>
                      <td>{r.batch_no}</td>
                      <td>{r.expiry_date}</td>
                      <td>{r.qty}</td>
                      <td>{r.pack}</td>
                      <td>{r.extra_medicine}</td>
                      <td>{r.mrp}</td>
                      <td>{r.rate}</td>
                      <td>{r.gst_percent}</td>
                      <td>{r.supplier_name}</td>
                      <td>
                        <button
                          type="button"
                          className="settings-link-btn"
                          onClick={() =>
                            setOpeningList((rows) => rows.filter((_x, j) => j !== i))
                          }
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <details className="opening-paste">
            <summary>Or paste rows from a file instead</summary>
          <Note>
            The same rows the data-loading app sends.
            First line names the columns:{' '}
            <code>name,batch_no,expiry_date,type,unit,stock_qty,extra_medicine,mrp,rate,gst_percent</code>
            . Only <code>name</code> is required, <code>unit</code> is the pack
            (tabs per strip, or 500ml), and for Tablet, Bolus and Capsule{' '}
            <code>stock_qty</code> is STRIPS with <code>extra_medicine</code> the
            loose pieces. The list above is used when it has rows in it.
          </Note>
            <input
              ref={openingFileRef}
              type="file"
              accept=".csv,.txt,.json,text/csv,application/json"
              style={{ display: 'none' }}
              onChange={(e) => onLoadOpeningFile(e.target.files?.[0] ?? null)}
            />
            <textarea
              className="settings-input"
              rows={8}
              value={openingText}
              onChange={(e) => setOpeningText(e.target.value)}
              placeholder={'name,batch_no,expiry_date,type,unit,stock_qty,extra_medicine,mrp,rate,gst_percent\nAMOXYCILLIN 500MG,B1204,08/27,Tablet,1x10,12,4,85.50,68.40,12'}
            />
            <div className="settings-inline-actions">
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={() => openingFileRef.current?.click()}
              >
                Load from file
              </button>
            </div>
          </details>
          <div className="settings-inline-actions">
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy || (!openingText.trim() && !openingList.length)}
              onClick={() => void runOpening('opening_stock_preview')}
            >
              Check rows
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy || (!openingText.trim() && !openingList.length)}
              onClick={() => void runOpening('opening_stock_apply')}
            >
              Add to Inventory
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy || (!openingText.trim() && !openingRows.length)}
              onClick={() => {
                setOpeningText('')
                setOpeningRows([])
                setOpeningProblems([])
                setMsg('')
                setErr('')
              }}
            >
              Clear
            </button>
          </div>
          {openingRows.length ? (
            <div className="list-table-panel">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Medicine</th>
                    <th>Batch</th>
                    <th>Expiry</th>
                    <th>Type</th>
                    <th>Pack</th>
                    <th>Qty</th>
                    <th>Loose</th>
                    <th>MRP</th>
                    <th>Rate</th>
                  </tr>
                </thead>
                <tbody>
                  {openingRows.slice(0, 200).map((r, i) => (
                    <tr key={`${String(r.name)}-${String(r.batch_no)}-${i}`}>
                      <td>{String(r.name ?? '')}</td>
                      <td>{String(r.batch_no ?? '')}</td>
                      <td>{String(r.expiry_date ?? '')}</td>
                      <td>{String(r.type ?? '')}</td>
                      <td>{String(r.unit ?? '')}</td>
                      <td>{String(r.stock_qty ?? '')}</td>
                      <td>{String(r.extra_medicine ?? '')}</td>
                      <td>{String(r.mrp ?? '')}</td>
                      <td>{String(r.rate ?? '')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {openingRows.length > 200 ? (
                <Note>Showing the first 200 of {openingRows.length} rows.</Note>
              ) : null}
            </div>
          ) : null}
          {openingProblems.length ? (
            <ul className="settings-note">
              {openingProblems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          ) : null}
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
      </>
    )
  }

  return <p className="muted">Unknown import section.</p>
}

export function DataSystemPanel({
  sectionId,
  system,
  setSystem,
  opts,
  saving,
  onSaveSystem,
  onStoreSwitched,
  onRequestRestart,
}: {
  sectionId: string
  system: Record<string, unknown>
  setSystem: React.Dispatch<React.SetStateAction<Record<string, unknown>>>
  opts: Opts
  saving: boolean
  onSaveSystem: () => void
  onStoreSwitched?: () => void | Promise<void>
  onRequestRestart?: (message?: string) => void
}) {
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [newStoreName, setNewStoreName] = useState('')
  // "Connect this PC to a store on the server" — the screen the
  // StoreNotLinkedOnServer message has always told people to open, and which
  // until now did not exist. Loaded only when asked for: it is an admin call
  // to the server, not something to fire on every visit to Settings.
  const [serverStores, setServerStores] = useState<ServerStoreRow[] | null>(null)
  const [joinPickId, setJoinPickId] = useState('')
  const [joinTypedName, setJoinTypedName] = useState('')
  const [linkError, setLinkError] = useState('')
  // Held for this settings session only, so the owner types the PIN once
  // while doing a run of store work instead of for every click.
  const [adminPin, setAdminPin] = useState('')
  const [adminPinSet, setAdminPinSet] = useState(false)
  const [switchNeedsPin, setSwitchNeedsPin] = useState(false)
  const [newPin, setNewPin] = useState('')
  const [autoBackup, setAutoBackup] = useState(false)
  const [backupStatus, setBackupStatus] = useState('')
  const [backupOnlineMode, setBackupOnlineMode] = useState(false)
  const [backupServerConfigured, setBackupServerConfigured] = useState(false)
  const [driveBackups, setDriveBackups] = useState<
    { id?: string; name?: string; label?: string }[]
  >([])
  const [usbBackups, setUsbBackups] = useState<
    { path?: string; name?: string; label?: string }[]
  >([])
  const [confirmWipe, setConfirmWipe] = useState('')
  const [wipePassword, setWipePassword] = useState('')
  const [adminUser, setAdminUser] = useState('')
  const [adminPass, setAdminPass] = useState('')
  const [adminLoggedIn, setAdminLoggedIn] = useState(false)
  const [backupCfgStore, setBackupCfgStore] = useState('')
  const [backupCfgFolder, setBackupCfgFolder] = useState('')
  const [expiryEnabled, setExpiryEnabled] = useState(true)
  const [expiryDate, setExpiryDate] = useState('')
  const [expiryApply, setExpiryApply] = useState(true)
  // Typed at the moment of the edit, sent once, never stored. The expiry is
  // written on the Satpuda server and signed there; this screen is only where
  // the request is typed, so it needs the credential the server checks -- not
  // the shop's own local PIN, which every copy of the build could be made to
  // accept.
  const [expiryAdminUser, setExpiryAdminUser] = useState('')
  const [expiryAdminPass, setExpiryAdminPass] = useState('')
  const [activationDate, setActivationDate] = useState('')
  const [serverProject, setServerProject] = useState('')
  // Shown, not hard-coded: the PC may be on the direct host or, after a
  // failover, on the slower tunnel. A fixed hostname here would lie.
  const [serverApiBase, setServerApiBase] = useState('')
  const [serverStoreId, setServerStoreId] = useState('')
  const [serverConfigured, setServerConfigured] = useState(false)
  const [serverSource, setServerSource] = useState('')
  const [, setServerJson] = useState('')
  const [androidStore, setAndroidStore] = useState('')
  const [androidKey, setAndroidKey] = useState('')
  const [androidOnline, setAndroidOnline] = useState(false)
  const [androidModeLabel, setAndroidModeLabel] = useState('')
  const [updateInfo, setUpdateInfo] = useState<Record<string, unknown> | null>(
    null,
  )
  const [updateNotes, setUpdateNotes] = useState('')
  const [autoCheckUpdates, setAutoCheckUpdates] = useState(true)
  const [voice, setVoice] = useState({
    enabled: false,
    name: '',
    language: 'en',
    tts: true,
  })

  const stores = (Array.isArray(system.stores) ? system.stores : []) as StoreRow[]
  const activeKey = String(system.active_store_key || '')
  const isSatellite = Boolean(system.is_satellite)
  const activeStoreName = String(
    stores.find((s) => String(s.store_key || '') === activeKey)?.display_name || '',
  )

  // Nothing ever asked the engine for the admin state, so "Switching stores
  // also needs the PIN" always rendered OFF even when it was on, and "Remove
  // PIN" -- which only shows once a PIN exists -- never appeared at all.
  useEffect(() => {
    if (sectionId !== 'stores') return
    let cancelled = false
    void (async () => {
      try {
        const res = await systemAction({ action: 'admin_status' })
        if (cancelled) return
        setAdminPinSet(Boolean(res.pin_set))
        setSwitchNeedsPin(Boolean(res.store_switch_needs_pin))
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sectionId])

  useEffect(() => {
    if (sectionId !== 'updates') return
    let cancelled = false
    void (async () => {
      try {
        const res = await systemAction({ action: 'get_updates_info' })
        if (cancelled) return
        setAutoCheckUpdates(Boolean(res.auto_check))
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sectionId])

  useEffect(() => {
    if (sectionId !== 'my_assist') return
    let cancelled = false
    ;(async () => {
      try {
        const res = await systemAction({ action: 'get_voice' })
        if (cancelled) return
        setVoice({
          enabled: Boolean(res.enabled),
          name: String(res.name ?? ''),
          language: String(res.language ?? 'en'),
          tts: Boolean(res.tts),
        })
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sectionId])

  useEffect(() => {
    if (sectionId !== 'backup') return
    let cancelled = false
    void (async () => {
      try {
        const res = await systemAction({ action: 'backup_status' })
        if (cancelled) return
        setAutoBackup(Boolean(res.auto_backup))
        setBackupOnlineMode(Boolean(res.online_mode))
        setBackupServerConfigured(Boolean(res.server_configured))
        // backup_status returns folder_ok and creds_ok separately. This line
        // used to name backup_creds.dat for EVERY unconfigured state — and
        // that file was never the missing one. What went missing from the
        // build was the Drive FOLDER, so the screen sent the shop looking in
        // the wrong place.
        setBackupStatus(
          res.configured
            ? `Configured for ${res.store_name || 'store'}` +
                (res.usb_connected ? ' · USB drive connected' : '')
            : !res.folder_ok && res.creds_ok
              ? 'No Google Drive folder is set for this PC. Settings → Data & System → Administrator → Drive folder ID.' +
                (res.usb_connected
                  ? ' A USB drive is connected, so Backup Now still saves a copy there.'
                  : '')
              : res.folder_ok && !res.creds_ok
                ? 'Google Drive credentials are missing or invalid on this PC (backup_creds.dat).'
                : 'Google Drive backup is not configured on this PC: no Drive folder and no credentials.',
        )
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sectionId])

  async function loadAdminTools() {
    setBusy(true)
    setErr('')
    try {
      const cfg = await systemAction({ action: 'admin_get_backup_config' })
      setBackupCfgStore(String(cfg.store_name || ''))
      setBackupCfgFolder(String(cfg.folder_id || ''))
      // Cache-first so Admin Tools open without a server round-trip freeze.
      const ex = await systemAction({
        action: 'admin_get_expiry',
        force_server: false,
      })
      const exp = (ex.expiry as Record<string, unknown>) || {}
      setExpiryEnabled(Boolean(exp.enabled ?? true))
      setExpiryDate(String(exp.expiry_date || ''))
      setExpiryApply(Boolean(ex.apply_expiry_check ?? true))
      setActivationDate(String(ex.activation_date || ''))

      const credsRes = await systemAction({ action: 'admin_get_server_creds' })
      setServerProject(String(credsRes.project_id || ''))
      setServerApiBase(String(credsRes.api_base || ''))
      setServerStoreId(String(credsRes.store_id || ''))
      setServerConfigured(Boolean(credsRes.configured))
      setServerSource(String(credsRes.source || ''))
      setServerJson(String(credsRes.json_text || ''))

      const ak = await systemAction({ action: 'admin_get_android_key' })
      setAndroidStore(String(ak.store_name || ''))
      setAndroidKey(String(ak.android_key || ''))
      setAndroidOnline(Boolean(ak.online_mode))
      setAndroidModeLabel(String(ak.sync_mode_label || ''))
      if (ak.online_mode && !ak.android_key) {
        await run('admin_ensure_android_key')
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function applySystemResult(
    action: string,
    body: Record<string, unknown>,
    res: Record<string, unknown>,
  ) {
    if (res.ok === false && res.error) {
      setErr(String(res.error))
    } else {
      setMsg(
        String(
          res.message ||
            res.result ||
            (res.path ? `Exported: ${res.path}` : '') ||
            'Done.',
        ),
      )
    }
    if (typeof res.android_key === 'string' && res.android_key) {
      setAndroidKey(res.android_key)
    }
    if (action === 'admin_save_server_creds' && res.ok !== false) {
      setServerConfigured(true)
      setServerSource('override')
    }
    if (typeof res.pin_set === 'boolean') setAdminPinSet(Boolean(res.pin_set))
    if (typeof res.store_switch_needs_pin === 'boolean') {
      setSwitchNeedsPin(Boolean(res.store_switch_needs_pin))
    }
    if (action === 'list_server_stores') {
      if (Array.isArray(res.server_stores)) {
        setServerStores(res.server_stores as ServerStoreRow[])
        setJoinPickId('')
        setJoinTypedName('')
      }
      setLinkError(String(res.link_error || ''))
    }
    if (action === 'forget_server_store' && res.ok !== false) {
      setServerStores(null)
      setJoinPickId('')
      setJoinTypedName('')
      setLinkError('')
    }
    if (action === 'join_server_store' && res.ok !== false) {
      setServerStores(null)
      setJoinPickId('')
      setJoinTypedName('')
      setLinkError('')
      onRequestRestart?.(
        String(res.message || 'This PC is now connected to that store. Restart the app.'),
      )
    }
    if (action === 'create_store' && Array.isArray(res.stores)) {
      setSystem((s) => ({
        ...s,
        stores: res.stores,
        // The new store is NOT active any more; keep the panel honest about
        // which one is.
        active_store_key: res.active_store_key ?? s.active_store_key,
      }))
      setNewStoreName('')
    }
    if (
      (action === 'add_server_store' ||
        action === 'remove_store' ||
        action === 'unlock_store_switching') &&
      Array.isArray(res.stores)
    ) {
      setSystem((s) => ({
        ...s,
        stores: res.stores,
        active_store_key: res.active_store_key ?? s.active_store_key,
        ...(action === 'unlock_store_switching' ? { is_satellite: false } : {}),
      }))
      if (res.message) setMsg(String(res.message))
    }
    if (action === 'add_server_store' && res.ok !== false) {
      setServerStores(null)
      setJoinPickId('')
      setJoinTypedName('')
    }
    if (action === 'switch_store' && res.active_store_key) {
      setSystem((s) => ({
        ...s,
        active_store_key: res.active_store_key,
      }))
      await onStoreSwitched?.()
    }
    if (action === 'set_auto_backup') {
      setAutoBackup(Boolean(res.auto_backup ?? body.enabled))
    }
    if (action === 'list_drive_backups' && Array.isArray(res.backups)) {
      setDriveBackups(res.backups as { id?: string; name?: string; label?: string }[])
    }
    if (action === 'list_usb_backups' && Array.isArray(res.backups)) {
      setUsbBackups(res.backups as { path?: string; name?: string; label?: string }[])
    }
    if (
      (action === 'restore_drive_backup' || action === 'restore_usb_backup') &&
      res.needs_restart
    ) {
      onRequestRestart?.(String(res.message || 'Database restored.'))
    }
    if (
      (action === 'sync_from_drive' || action === 'restore_active_store') &&
      res.needs_restart
    ) {
      onRequestRestart?.(String(res.message || 'Database synced from Drive.'))
    }
    if (action === 'danger_wipe' && res.needs_restart) {
      onRequestRestart?.(
        String(
          res.message ||
            'All local data deleted. Restart the app to continue.',
        ),
      )
    }
    if (action === 'list_stores' && Array.isArray(res.stores)) {
      setSystem((s) => ({
        ...s,
        stores: res.stores,
        active_store_key: res.active_store_key ?? s.active_store_key,
      }))
    }
    if (action === 'admin_get_expiry' && res.expiry) {
      const exp = (res.expiry as Record<string, unknown>) || {}
      setExpiryEnabled(Boolean(exp.enabled ?? true))
      setExpiryDate(String(exp.expiry_date || ''))
      setExpiryApply(Boolean(res.apply_expiry_check ?? true))
      setActivationDate(String(res.activation_date || ''))
    }
    if (action === 'check_updates') {
      setUpdateInfo((res.info as Record<string, unknown>) || null)
      setUpdateNotes(String(res.notes || ''))
    }
    if (
      (action === 'verify_server_sync' || action === 'verify_server_sync') &&
      res.collections
    ) {
      const lines: string[] = [
        `Store: ${String(res.store_id || '')}`,
        '',
        'Collection           Local  Server  ',
        '----------------------------------------',
      ]
      const cols = res.collections as Record<
        string,
        { local?: number; server?: number; match?: boolean }
      >
      for (const [col, row] of Object.entries(cols).sort(([a], [b]) =>
        a.localeCompare(b),
      )) {
        const mark = row.match ? 'OK' : 'DIFF'
        const remote = row.server ?? 0
        lines.push(
          `${col.padEnd(20)} ${String(row.local ?? 0).padStart(6)} ${String(remote).padStart(8)}  ${mark}`,
        )
      }
      if (res.sales_total_local != null && res.sales_total_server != null) {
        lines.push('')
        lines.push(
          `Sales total local:    ${Number(res.sales_total_local).toLocaleString('en-IN')}`,
        )
        lines.push(
          `Sales total Server:   ${Number(res.sales_total_server).toLocaleString('en-IN')}`,
        )
      }
      const issues = Array.isArray(res.issues) ? res.issues : []
      if (issues.length) {
        lines.push('')
        lines.push('Issues:')
        for (const issue of issues.slice(0, 8)) {
          lines.push(`• ${String(issue)}`)
        }
      }
      setMsg(lines.join('\n'))
      if (!res.match) {
        setErr(String(res.message || 'Mismatch found.'))
      }
    }
  }

  /** A restore that would discard this device's newer bills stops and says which; the
   *  shop confirms, and only then is it sent again with confirm_loss. */
  async function run(action: string, body: Record<string, unknown> = {}) {
    const res = await runOnce(action, body)
    if (res && (res as Record<string, unknown>).code === 'would_lose') {
      const why = String((res as Record<string, unknown>).error || '')
      if (window.confirm(`${why}

OK = tari restore kara (he jaatil) · Cancel = thamba`)) {
        return runOnce(action, { ...body, confirm_loss: true })
      }
      setErr(`Restore thambavla. ${why}`)
    }
    return res
  }

  async function runOnce(action: string, body: Record<string, unknown> = {}) {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      let res = await systemAction(
        adminPin ? { action, admin_pin: adminPin, ...body } : { action, ...body },
      )
      // Creating, renaming, deleting or switching a store can hand one shop
      // another shop's books, so the owner can put those behind a PIN. Ask for
      // it here and retry, rather than showing a failure the shop cannot act on.
      if (
        res.ok === false &&
        (res.code === 'admin_pin_required' || res.code === 'admin_pin_wrong')
      ) {
        const prompt =
          res.code === 'admin_pin_wrong'
            ? 'Wrong administrator PIN. Try again:'
            : 'Administrator PIN:'
        const pin = window.prompt(prompt) || ''
        if (!pin) {
          setErr('Cancelled — the administrator PIN is needed for this.')
          return res
        }
        res = await systemAction({ action, ...body, admin_pin: pin })
        if (res.ok === false && res.code === 'admin_pin_wrong') {
          setErr('Wrong administrator PIN.')
          return res
        }
        setAdminPin(pin)
      }
      // The VENDOR administrator, which is a different thing from the PIN
      // above: the PIN is the shop owner's, this is Satpuda's own sign-in for
      // actions that touch the whole account (the store list, joining a store,
      // rotating a pairing key). It used to be a username and password
      // compiled into the build, so these ran as the administrator of every
      // shop without anybody asking. Now the engine refuses with this code and
      // the credentials come from the two fields in Administrator Tools —
      // typed, used for one request, and never stored.
      if (res.ok === false && res.code === 'admin_credential_required') {
        const user = expiryAdminUser.trim()
        const pass = expiryAdminPass
        if (!user || !pass) {
          setErr(
            'This needs the Satpuda administrator username and password. ' +
              'Open Administrator Tools, type them in the two fields there, ' +
              'and press this again.',
          )
          return res
        }
        res = await systemAction({
          action,
          ...body,
          admin_username: user,
          admin_password: pass,
        })
        if (res.ok === false) {
          setErr(String(res.error || 'The Satpuda server refused the administrator sign-in.'))
          return res
        }
      }
      if (res.background) {
        setMsg(String(res.message || 'Running in background…'))
        const started = Date.now()
        while (Date.now() - started < 30 * 60 * 1000) {
          await new Promise((r) => setTimeout(r, 500))
          const st = await systemAction({ action: 'heavy_job_status' })
          const message = String(st.message || 'Working…')
          setMsg(message)
          if (st.done) {
            const finalRes = {
              ...(typeof st.result === 'object' && st.result
                ? (st.result as Record<string, unknown>)
                : {}),
              ok: st.ok !== false,
              error: st.error,
              message: st.message || message,
            }
            await applySystemResult(action, body, finalRes)
            return finalRes
          }
        }
        setErr('Background job is still running. Check again in a moment.')
        return res
      }
      await applySystemResult(action, body, res)
      return res
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setBusy(false)
    }
  }

  if (sectionId === 'stores') {
    return (
      <>
        <PanelTitle>Stores &amp; Startup Alerts</PanelTitle>
        <Frame title="Stores">
          <Note>
            Active store: <strong>{activeKey || '—'}</strong>
            {'. '}
            Switching reopens the store database immediately. Use Restart App
            for a full clean UI reload.
          </Note>
          <div style={{ marginBottom: 8 }}>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() =>
                onRequestRestart?.(
                  'Restart the desktop app to fully reload all pages and the local data engine.',
                )
              }
            >
              Restart App…
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    'Replace the active store database from the latest Drive backup? The app will restart.',
                  )
                ) {
                  void run('restore_active_store')
                }
              }}
            >
              Restore Active Store from Drive
            </button>
          </div>
          {isSatellite ? (
            /* A counter PC restored from a backup is linked to ONE store.
               Switching and creating cannot work here, and the engine now
               refuses both -- so stop offering them, the way the old screen
               did. Restart and Restore stay: those do work. */
            <Note>
              This device is linked to one store only:{' '}
              <strong>{activeStoreName || activeKey || '—'}</strong>. Store
              switching and creation are disabled on this device.{' '}
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={() => {
                  if (
                    window.confirm(
                      'Let this PC switch, add and remove stores?\n\n' +
                        'Needs the Satpuda administrator username and password ' +
                        '(typed in Administrator Tools).',
                    )
                  ) {
                    void run('unlock_store_switching')
                  }
                }}
              >
                Allow other stores on this PC
              </button>
            </Note>
          ) : (
          <>
          <table className="settings-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Key</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {stores.map((st) => {
                const key = String(st.store_key || '')
                const name = String(st.display_name || key)
                const isActive = key === activeKey
                return (
                  <tr key={key}>
                    <td>
                      {name}
                      {isActive ? ' (active)' : ''}
                    </td>
                    <td>{key}</td>
                    <td>
                      <button
                        type="button"
                        className="settings-action-btn"
                        disabled={busy || isActive || !key}
                        onClick={() => run('switch_store', { store_key: key })}
                      >
                        Switch
                      </button>
                      <button
                        type="button"
                        className="settings-action-btn"
                        disabled={busy || isActive || !key || stores.length < 2}
                        title={
                          isActive
                            ? 'Switch to another store first'
                            : 'Take this store off this PC (its files are kept)'
                        }
                        onClick={() => {
                          const typed = window.prompt(
                            `Remove "${name}" from this PC?\n\n` +
                              'Its files are moved to a "removed_stores" folder, not deleted, ' +
                              'and nothing on the server is deleted.\n\n' +
                              'Type the store name to confirm:',
                          )
                          if (typed == null) return
                          void run('remove_store', { store_key: key, confirm_name: typed.trim() })
                        }}
                      >
                        Remove from PC
                      </button>
                    </td>
                  </tr>
                )
              })}
              {!stores.length ? (
                <tr>
                  <td colSpan={3}>No stores registered</td>
                </tr>
              ) : null}
            </tbody>
          </table>
          <div className="settings-inline-row">
            <Field label="New store name">
              <input
                className="settings-input"
                value={newStoreName}
                onChange={(e) => setNewStoreName(e.target.value)}
              />
            </Field>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy || !newStoreName.trim()}
              onClick={async () => {
                const name = newStoreName.trim()
                // Creating a store no longer makes it the one that opens
                // tomorrow. It used to, silently: the registry was re-pointed
                // at the new EMPTY store while the running app kept the old
                // database open, so nothing looked wrong until the next launch.
                const offerSwitch = (r: Record<string, unknown> | undefined) => {
                  const key = String(r?.created_store_key || '')
                  if (!key) return
                  const ok = window.confirm(
                    `Store "${name}" created. It is not active yet.\n\n` +
                      'Switch to it now? (restarts the app)',
                  )
                  if (ok) void run('switch_store', { store_key: key })
                }
                let res = await run('create_store', { name })
                // Online, a name another shop already holds is refused: taking
                // it would pair this PC into that shop's account. Offer the
                // deliberate join instead of leaving the shop stuck.
                const code = (res as Record<string, unknown> | undefined)?.code
                if (code === 'name_taken_on_server') {
                  const join = window.confirm(
                    `The server already has a store named "${name}".\n\n` +
                      'OK  — connect this device to THAT store (its data will appear here).\n' +
                      'Cancel — go back and choose a different name.',
                  )
                  if (!join) return
                  res = await run('create_store', { name, connect_existing: true })
                }
                offerSwitch(res as Record<string, unknown> | undefined)
              }}
            >
              Create Store
            </button>
          </div>
          </>
          )}
        </Frame>
        {IS_DEMO ? null : (
        <Frame title="Connect this PC to a store on the server">
          <div className="settings-hint">
            Use this when this PC's records are on the server but the app opens
            empty, or when it says it is not linked to any store. Pick the shop
            this PC belongs to and it will read that shop's books from now on.
            Nothing on this PC is uploaded into it.
          </div>
          {linkError ? <Note>{linkError}</Note> : null}
          <div className="settings-inline-row">
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => void run('list_server_stores')}
            >
              {serverStores ? 'Refresh the list' : 'Show stores on the server'}
            </button>
            {serverStores ? (
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={() => {
                  setServerStores(null)
                  setJoinPickId('')
                  setJoinTypedName('')
                }}
              >
                Close
              </button>
            ) : null}
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => {
                if (
                  !window.confirm(
                    'Forget which store on the server this PC belongs to?\n\n' +
                      'It will go back to matching by store key on the next ' +
                      'connection. Use this only if this PC was connected to ' +
                      'the wrong store.',
                  )
                ) {
                  return
                }
                void run('forget_server_store', { store_key: activeKey })
              }}
            >
              Forget this PC's store
            </button>
          </div>
          {serverStores && !serverStores.length ? (
            <Note>
              The server answered with no stores at all. That is not the same as
              your shop being gone — check the internet and try again before
              concluding anything.
            </Note>
          ) : null}
          {serverStores && serverStores.length ? (
            <>
              <table className="settings-table">
                <thead>
                  <tr>
                    <th />
                    <th>Store on the server</th>
                    <th>Id</th>
                    <th>Key</th>
                  </tr>
                </thead>
                <tbody>
                  {serverStores.map((s) => {
                    const taken = String(s.used_by_local_store || '')
                    return (
                      <tr key={s.store_id}>
                        <td>
                          <input
                            type="radio"
                            name="server-store"
                            checked={joinPickId === s.store_id}
                            disabled={busy || !!taken}
                            onChange={() => {
                              setJoinPickId(s.store_id)
                              setJoinTypedName('')
                            }}
                          />
                        </td>
                        <td>
                          <strong>{s.store_name}</strong>
                          {s.is_this_pc ? ' (this PC is on this one)' : ''}
                          {taken ? (
                            <div className="settings-hint">
                              Already connected to “{taken}” on this PC.{' '}
                              {!isSatellite ? (
                                <button
                                  type="button"
                                  className="settings-action-btn"
                                  disabled={busy}
                                  onClick={() => void run('switch_store', { store_key: taken })}
                                >
                                  Switch to it
                                </button>
                              ) : null}
                            </div>
                          ) : null}
                        </td>
                        <td className="mono">{s.store_id}</td>
                        <td className="mono">{s.store_key}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              {joinPickId ? (
                <>
                  <Note>
                    <em>Connect this PC</em> makes the store open now read the books of{' '}
                    <strong>
                      {serverStores.find((s) => s.store_id === joinPickId)?.store_name}
                    </strong>{' '}
                    (id {joinPickId}) as the store{' '}
                    <strong>{activeStoreName || activeKey}</strong>. Two shops
                    can share a name, so type the store name exactly as it is
                    shown above to confirm. <em>Add as another store</em> keeps
                    this store and puts the chosen one in the Switch list.
                  </Note>
                  <div className="settings-inline-row">
                    <Field label="Type the store name to confirm">
                      <input
                        className="settings-input"
                        value={joinTypedName}
                        onChange={(e) => setJoinTypedName(e.target.value)}
                        placeholder="exactly as shown above"
                      />
                    </Field>
                    <button
                      type="button"
                      className="settings-action-btn"
                      disabled={
                        busy ||
                        joinTypedName.trim().toLowerCase() !==
                          String(
                            serverStores.find((s) => s.store_id === joinPickId)
                              ?.store_name || '\u0000',
                          )
                            .trim()
                            .toLowerCase()
                      }
                      onClick={() =>
                        void run('join_server_store', {
                          store_key: activeKey,
                          store_id: joinPickId,
                          confirm_name: joinTypedName.trim(),
                        })
                      }
                    >
                      Connect this PC
                    </button>
                    {!isSatellite ? (
                      <button
                        type="button"
                        className="settings-action-btn"
                        title="Keep this store as it is and add the chosen one beside it, for Switch"
                        disabled={
                          busy ||
                          joinTypedName.trim().toLowerCase() !==
                            String(
                              serverStores.find((s) => s.store_id === joinPickId)
                                ?.store_name || '\u0000',
                            )
                              .trim()
                              .toLowerCase()
                        }
                        onClick={async () => {
                          const r = (await run('add_server_store', {
                            store_id: joinPickId,
                            confirm_name: joinTypedName.trim(),
                          })) as Record<string, unknown> | null
                          const newKey = String(
                            r?.created_store_key ||
                              (r?.code === 'already_on_pc' ? r?.store_key : '') ||
                              '',
                          )
                          if (
                            newKey &&
                            window.confirm(
                              String(r?.message || r?.error || 'Store added.') +
                                '\n\nSwitch to it now?',
                            )
                          ) {
                            void run('switch_store', { store_key: newKey })
                          }
                        }}
                      >
                        Add as another store on this PC
                      </button>
                    ) : null}
                  </div>
                </>
              ) : null}
            </>
          ) : null}
        </Frame>
        )}
        <Frame title="Administrator">
          <div className="settings-hint">
            Creating, renaming and deleting a store can move a shop's data
            somewhere it does not belong. Set a PIN and only the owner can do
            them. Switching between stores stays open unless you tighten it
            below, so an assistant can change shop and nothing else.
          </div>
          <div className="settings-inline-row">
            <Field label={adminPinSet ? 'New PIN' : 'Set a PIN'}>
              <input
                className="settings-input"
                type="password"
                value={newPin}
                onChange={(e) => setNewPin(e.target.value)}
                placeholder="at least 4 characters"
              />
            </Field>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy || newPin.trim().length < 4}
              onClick={async () => {
                await run('admin_set_pin', { new_pin: newPin.trim() })
                setNewPin('')
                await run('list_stores')
              }}
            >
              {adminPinSet ? 'Change PIN' : 'Set PIN'}
            </button>
            {adminPinSet ? (
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={async () => {
                  await run('admin_clear_pin')
                  setAdminPin('')
                  await run('list_stores')
                }}
              >
                Remove PIN
              </button>
            ) : null}
          </div>
          <Check
            label="Switching stores also needs the PIN"
            checked={switchNeedsPin}
            onChange={async (v) => {
              await run('admin_set_switch_policy', {
                store_switch_needs_pin: v,
              })
              await run('list_stores')
            }}
          />
        </Frame>
        <Frame title="Startup alerts">
          <Check
            label="Startup alerts enabled"
            checked={Boolean(system.startup_alerts_enabled)}
            onChange={(v) =>
              setSystem((s) => ({ ...s, startup_alerts_enabled: v }))
            }
          />
        </Frame>
        <Frame title="History pages">
          <Field label="Sales / Purchase history default">
            <select
              className="settings-input"
              value={String(system.history_scope || 'current_fy')}
              onChange={(e) =>
                setSystem((s) => ({ ...s, history_scope: e.target.value }))
              }
            >
              <option value="current_fy">Current financial year only</option>
              <option value="all">All dates</option>
            </select>
          </Field>
          <Note>
            Default is current FY (1 Apr – 31 Mar). Choose a date range on the
            history page to see older bills. Synced to the server for Android when
            Online mode is on.
          </Note>
        </Frame>
        {msg ? <p className="settings-note">{msg}</p> : null}
        {err ? <p className="error">{err}</p> : null}
        <SaveBtn
          label="Save System Prefs"
          saving={saving}
          onClick={onSaveSystem}
        />
      </>
    )
  }

  if (sectionId === 'export') {
    const fmt = String(system.export_format || 'csv')
    return (
      <>
        <PanelTitle>Export Data</PanelTitle>
        <Frame title="Export">
          <Field label="Default export format">
            <select
              className="settings-input"
              value={fmt}
              onChange={(e) =>
                setSystem((s) => ({ ...s, export_format: e.target.value }))
              }
            >
              {opts.export_formats.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </Field>
          <div className="settings-inline-row">
            {(
              [
                ['sales', 'Export Sales'],
                ['purchases', 'Export Purchases'],
                ['inventory', 'Export Inventory'],
                ['all', 'Export All'],
              ] as const
            ).map(([kind, label]) => (
              <button
                key={kind}
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={() => run('export', { kind, format: fmt })}
              >
                {label}
              </button>
            ))}
          </div>
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
        <SaveBtn
          label="Save System Prefs"
          saving={saving}
          onClick={onSaveSystem}
        />
      </>
    )
  }

  if (sectionId === 'maintenance') {
    return (
      <>
        <PanelTitle>Data Maintenance</PanelTitle>
        <Frame title="Maintenance">
          <button
            type="button"
            className="settings-action-btn"
            disabled={busy}
            onClick={() => run('normalize_names')}
          >
            Normalize names
          </button>
          <Note>Normalizes medicine names in the active database.</Note>
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
      </>
    )
  }

  if (sectionId === 'backup') {
    return (
      <>
        <PanelTitle>Google Drive Backup</PanelTitle>
        <Frame title="Backup">
          {backupStatus ? <Note>{backupStatus}</Note> : null}
          <Check
            label="Auto backup enabled"
            checked={autoBackup}
            onChange={(v) => {
              setAutoBackup(v)
              void run('set_auto_backup', { enabled: v })
            }}
          />
          <div className="settings-inline-actions">
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => run('backup_now')}
            >
              Backup Now
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => run('list_drive_backups')}
            >
              List Drive backups
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    'Sync from Drive replaces the local database with a chosen backup. Continue?',
                  )
                ) {
                  void run('list_drive_backups').then(() => {
                    /* user picks from list below, then Sync */
                  })
                }
              }}
            >
              Sync from Drive…
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => run('list_usb_backups')}
            >
              List USB backups
            </button>
            {backupOnlineMode && backupServerConfigured ? (
              <>
                <button
                  type="button"
                  className="settings-action-btn"
                  disabled={busy}
                  onClick={() => {
                    if (
                      window.confirm(
                        'PUSH — Upload the ACTIVE store only to Satpuda Core Server?\n\n' +
                          'Each store has its own server partition (store_pk). ' +
                          'Data does not mix with other stores.\n\n' +
                          'Use “Push All Stores” for every store on this PC.',
                      )
                    ) {
                      void run('push_to_server')
                    }
                  }}
                >
                  Push to Server
                </button>
                <button
                  type="button"
                  className="settings-action-btn"
                  disabled={busy}
                  onClick={() => {
                    if (
                      window.confirm(
                        'PUSH ALL — Upload EVERY local store on this PC?\n\n' +
                          'Each store is paired separately and written only under ' +
                          'that store’s store_pk. Data is not merged across stores.',
                      )
                    ) {
                      void run('push_all_stores_to_server')
                    }
                  }}
                >
                  Push All Stores
                </button>
                <button
                  type="button"
                  className="settings-action-btn"
                  disabled={busy}
                  onClick={() => {
                    if (
                      window.confirm(
                        'PULL FROM SERVER — disaster recovery full replace?\n\n' +
                          'Day-to-day sync is automatic (revision SyncEngine). ' +
                          'Use this only when this PC is badly out of date or recovering.\n\n' +
                          'Local business rows are cleared, then a clean download runs.',
                      )
                    ) {
                      void run('pull_from_server')
                    }
                  }}
                >
                  Pull from Server
                </button>
                <button
                  type="button"
                  className="settings-action-btn"
                  disabled={busy}
                  onClick={() => void run('verify_server_sync')}
                >
                  Verify Server Sync
                </button>
              </>
            ) : null}
          </div>
          {backupOnlineMode ? (
            <Note>
              Online: each save pushes live; SyncEngine pulls revision deltas
              (WebSocket + 45s safety). Manual: Push to Server / Push All Stores.
              Pull from Server = disaster-recovery full replace only. Verify compares
              counts.
            </Note>
          ) : null}
          {msg ? (
            String(msg).includes('Collection') ? (
              <pre className="settings-note mobile-preview">{msg}</pre>
            ) : (
              <p className="settings-note">
                {busy ? `Working… ${msg}` : msg}
              </p>
            )
          ) : null}
          {err ? <p className="error">{err}</p> : null}
          {driveBackups.length ? (
            <div className="settings-table-wrap">
              <table className="settings-table">
                <tbody>
                  {driveBackups.map((b) => (
                    <tr key={String(b.id || b.name)}>
                      <td>{b.label || b.name}</td>
                      <td>
                        <button
                          type="button"
                          className="settings-action-btn"
                          disabled={busy}
                          onClick={() => {
                            if (
                              window.confirm(
                                `Restore backup "${b.name}"? The app will restart.`,
                              )
                            ) {
                              void run('restore_drive_backup', {
                                file_id: b.id,
                                store_key: activeKey,
                              })
                            }
                          }}
                        >
                          Restore
                        </button>
                        <button
                          type="button"
                          className="settings-action-btn"
                          disabled={busy}
                          onClick={() => {
                            if (
                              window.confirm(
                                `Sync from "${b.name}"? Local DB will be replaced and the app will restart.`,
                              )
                            ) {
                              void run('sync_from_drive', { file_id: b.id })
                            }
                          }}
                        >
                          Sync
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {usbBackups.length ? (
            <div className="settings-table-wrap">
              <table className="settings-table">
                <tbody>
                  {usbBackups.map((b) => (
                    <tr key={String(b.path || b.name)}>
                      <td>{b.label || b.name}</td>
                      <td>
                        <button
                          type="button"
                          className="settings-action-btn"
                          disabled={busy}
                          onClick={() => {
                            if (
                              window.confirm(
                                `Restore USB backup "${b.name}"? The local database will be replaced and the app will restart.`,
                              )
                            ) {
                              void run('restore_usb_backup', {
                                path: b.path,
                                store_key: activeKey,
                              })
                            }
                          }}
                        >
                          Restore
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {msg && !(String(msg).includes('Collection')) ? (
            <p className="settings-note">{msg}</p>
          ) : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
      </>
    )
  }

  if (sectionId === 'updates') {
    return (
      <>
        <PanelTitle>App Updates</PanelTitle>
        <Frame title="Updates">
          <Check
            label="Check for updates automatically once per day"
            checked={autoCheckUpdates}
            onChange={(v) => {
              setAutoCheckUpdates(v)
              void run('set_auto_check_updates', { enabled: v })
            }}
          />
          <div className="settings-inline-actions">
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={async () => {
                const res = (await run('check_updates')) as Record<string, unknown>
                if (res?.info) {
                  setUpdateInfo(res.info as Record<string, unknown>)
                  setUpdateNotes(String(res.notes || ''))
                }
              }}
            >
              Check for Updates
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={
                busy || !updateInfo?.available || !updateInfo?.can_install_via_installer
              }
              onClick={() => run('install_update')}
            >
              Install Update
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => run('reinstall_installer')}
            >
              Reinstall Installer
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => run('open_installer')}
            >
              Open Installer
            </button>
            <button
              type="button"
              className="settings-action-btn"
              disabled={busy}
              onClick={() => run('open_releases')}
            >
              Open Releases Page
            </button>
          </div>
          {updateInfo?.latest_version ? (
            <Note>
              Installed v{String(updateInfo.current_version || '—')} · Latest v
              {String(updateInfo.latest_version)}
            </Note>
          ) : null}
          {updateNotes ? (
            <pre className="settings-note mobile-preview">{updateNotes}</pre>
          ) : null}
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
      </>
    )
  }

  if (sectionId === 'my_assist') {
    return (
      <>
        <PanelTitle>My Assist</PanelTitle>
        <Frame title="Voice assistant">
          <Check
            label="Enable voice assistant"
            checked={voice.enabled}
            onChange={(v) => setVoice((x) => ({ ...x, enabled: v }))}
          />
          <Field label="Assistant name">
            <input
              className="settings-input"
              value={voice.name}
              onChange={(e) =>
                setVoice((x) => ({ ...x, name: e.target.value }))
              }
            />
          </Field>
          <Field label="Language">
            <input
              className="settings-input"
              value={voice.language}
              onChange={(e) =>
                setVoice((x) => ({ ...x, language: e.target.value }))
              }
            />
          </Field>
          <Check
            label="Text-to-speech"
            checked={voice.tts}
            onChange={(v) => setVoice((x) => ({ ...x, tts: v }))}
          />
          <SaveBtn
            label="Save Voice Settings"
            saving={busy}
            onClick={() =>
              void run('save_voice', {
                enabled: voice.enabled,
                name: voice.name,
                language: voice.language,
                tts: voice.tts,
              })
            }
          />
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
      </>
    )
  }

  if (sectionId === 'admin') {
    return (
      <>
        <PanelTitle>Administrator</PanelTitle>
        <Frame title="Administrator login">
          <Note>
            Restricted tools: Drive backup folder, expiry.dat editor, sync mode,
            server connection info, and Android store connection key.
          </Note>
          {!adminLoggedIn ? (
            <>
              <Field label="Username">
                <input
                  className="settings-input"
                  value={adminUser}
                  onChange={(e) => setAdminUser(e.target.value)}
                />
              </Field>
              <Field label="Password">
                <input
                  className="settings-input"
                  type="password"
                  value={adminPass}
                  onChange={(e) => setAdminPass(e.target.value)}
                />
              </Field>
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={async () => {
                  const res = await run('admin_login', {
                    username: adminUser,
                    password: adminPass,
                  })
                  if (res?.ok) {
                    setAdminLoggedIn(true)
                    try {
                      await loadAdminTools()
                    } catch (e) {
                      setErr(e instanceof Error ? e.message : String(e))
                    }
                  } else {
                    setErr('Invalid username or password.')
                  }
                }}
              >
                Administrator Login
              </button>
            </>
          ) : (
            <>
              <Note>Logged in as administrator.</Note>
              <Frame title="Global Master Medicines">
                <Note>
                  Server holds one global catalog. Normal Push/Pull never touches it.
                  Download replaces the local snapshot; Push stock enriches the server
                  from inventory.
                </Note>
                <div className="settings-btn-row">
                  <button
                    type="button"
                    className="settings-action-btn"
                    disabled={busy}
                    onClick={() => run('admin_download_master', {})}
                  >
                    Download Master (replace local)
                  </button>
                  <button
                    type="button"
                    className="settings-action-btn"
                    disabled={busy}
                    onClick={() => run('admin_push_stock_master', {})}
                  >
                    Push Stock → Master
                  </button>
                </div>
              </Frame>
              <Field label="Store name (Drive backup)">
                <input
                  className="settings-input"
                  value={backupCfgStore}
                  readOnly
                  style={{ opacity: 0.7 }}
                />
              </Field>
              {/* Typing here saved a name nothing read: every reader resolves
                  the Drive subfolder from the ACTIVE store, and the next backup
                  wrote that back over whatever was typed. Making this field
                  rename the store is what caused the store-opened-empty
                  incident, so it stays read-only and the rename lives where the
                  rest of the store identity does. */}
              <Note>
                The Drive subfolder always follows the active store's name.
                Rename a store from Settings → Data &amp; System → Stores.
              </Note>
              <Field label="Drive folder ID">
                <input
                  className="settings-input"
                  value={backupCfgFolder}
                  onChange={(e) => setBackupCfgFolder(e.target.value)}
                />
              </Field>
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={() =>
                  run('admin_save_backup_config', {
                    store_name: backupCfgStore,
                    folder_id: backupCfgFolder,
                  })
                }
              >
                Save Drive Backup Settings
              </button>
              <Note>
                The expiry is stored and signed on the Satpuda server, in Online
                and Offline mode alike, so this needs the internet and the
                Satpuda administrator password. Nothing is changed on this
                computer unless the server accepts it. For old stores, type the
                real activation date here.
              </Note>
              <Field label="Activation date (YYYY-MM-DD)">
                <input
                  className="settings-input"
                  value={activationDate}
                  onChange={(e) => setActivationDate(e.target.value)}
                  placeholder="e.g. 2024-06-15"
                />
              </Field>
              <Check
                label="Apply expiry check (master switch)"
                checked={expiryApply}
                onChange={setExpiryApply}
              />
              <Check
                label="Expiry enabled"
                checked={expiryEnabled}
                onChange={setExpiryEnabled}
              />
              <Field label="Expiry date (YYYY-MM-DD)">
                <input
                  className="settings-input"
                  value={expiryDate}
                  onChange={(e) => setExpiryDate(e.target.value)}
                />
              </Field>
              <Field label="Satpuda administrator username">
                <input
                  className="settings-input"
                  autoComplete="off"
                  value={expiryAdminUser}
                  onChange={(e) => setExpiryAdminUser(e.target.value)}
                />
              </Field>
              <Field label="Satpuda administrator password">
                <input
                  className="settings-input"
                  type="password"
                  autoComplete="off"
                  value={expiryAdminPass}
                  onChange={(e) => setExpiryAdminPass(e.target.value)}
                />
              </Field>
              <button
                type="button"
                className="settings-action-btn"
                disabled={busy || !expiryAdminUser.trim() || !expiryAdminPass}
                onClick={() => {
                  run('admin_save_expiry', {
                    apply_expiry_check: expiryApply,
                    enabled: expiryEnabled,
                    expiry_date: expiryDate,
                    activation_date: activationDate,
                    admin_username: expiryAdminUser.trim(),
                    admin_password: expiryAdminPass,
                  })
                  // Held only for the length of one request.
                  setExpiryAdminPass('')
                }}
              >
                Save Activation &amp; Expiry
              </button>
              <Field label="Sync mode">
                <select
                  className="settings-input"
                  value={String(system.sync_mode || 'offline')}
                  onChange={(e) =>
                    setSystem((s) => ({ ...s, sync_mode: e.target.value }))
                  }
                >
                  {opts.sync_modes.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </Field>
              <SaveBtn
                label="Save Sync Mode"
                saving={saving}
                onClick={onSaveSystem}
              />
              <Frame title="Server Connection">
                <Note>
                  API: {serverApiBase || '—'} | Project:{' '}
                  {serverProject || 'satpuda-core-server'} | Store:{' '}
                  {serverStoreId || '—'} | Ready:{' '}
                  {serverConfigured ? 'Yes' : 'No'}
                  {serverSource === 'server' ? ' (Satpuda Core Server)' : ''}
                  . Online is server-first: lists load from the server; switching
                  Online does not download the full DB into SQLite.
                </Note>
              </Frame>
              <Frame title="Android Store Connection Key">
                <Note>
                  Use this SC- key when activating Satpuda Core on Android. Enter
                  the same store name and this key on the phone to pair with
                  Satpuda Core Server.
                </Note>
                {!androidOnline ? (
                  <Note>
                    Sync mode is {androidModeLabel || 'Offline'}. Switch to
                    Online (Server) in Sync Mode above first.
                  </Note>
                ) : null}
                <Field label="Store">
                  <input
                    className="settings-input"
                    readOnly
                    value={androidStore}
                  />
                </Field>
                <Field label="Key">
                  <input
                    className="settings-input"
                    readOnly
                    value={androidKey || '(not generated yet)'}
                  />
                </Field>
                <div className="settings-inline-actions">
                  <button
                    type="button"
                    className="settings-action-btn"
                    disabled={busy || !androidOnline}
                    onClick={() => void run('admin_ensure_android_key')}
                  >
                    Generate / Refresh
                  </button>
                  <button
                    type="button"
                    className="settings-action-btn"
                    disabled={busy || !androidOnline}
                    onClick={() => {
                      if (
                        !window.confirm(
                          'This invalidates the old Android key. Devices using the old key must be re-activated. Continue?',
                        )
                      ) {
                        return
                      }
                      void run('admin_regenerate_android_key')
                    }}
                  >
                    Regenerate New Key
                  </button>
                  <button
                    type="button"
                    className="settings-action-btn"
                    disabled={!androidKey || androidKey.startsWith('(')}
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(androidKey)
                        setMsg('Android key copied to clipboard.')
                      } catch (e) {
                        setErr(
                          e instanceof Error ? e.message : 'Copy failed.',
                        )
                      }
                    }}
                  >
                    Copy Key
                  </button>
                </div>
              </Frame>
            </>
          )}
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
      </>
    )
  }

  if (sectionId === 'danger') {
    return (
      <>
        <PanelTitle>Danger Zone</PanelTitle>
        <Frame title="Delete All Tables">
          <Note>
            This will permanently delete <strong>all local data</strong> for this
            store. Server cloud data is not wiped here (use the admin dashboard).
            This action cannot be undone.
          </Note>
          <Field label="Administrator password">
            <input
              className="settings-input"
              type="password"
              value={wipePassword}
              onChange={(e) => setWipePassword(e.target.value)}
              autoComplete="off"
            />
          </Field>
          <Field label='Type "DELETE ALL TABLES" to confirm'>
            <input
              className="settings-input"
              value={confirmWipe}
              onChange={(e) => setConfirmWipe(e.target.value)}
              placeholder="DELETE ALL TABLES"
            />
          </Field>
          <button
            type="button"
            className="settings-action-btn danger-text"
            disabled={
              busy ||
              confirmWipe !== 'DELETE ALL TABLES' ||
              !wipePassword.trim()
            }
            onClick={() => {
              if (
                !window.confirm(
                  'This will permanently delete ALL local data for this store. Are you sure?',
                )
              ) {
                return
              }
              void run('danger_wipe', {
                confirm: 'DELETE ALL TABLES',
                password: wipePassword,
              })
            }}
          >
            DELETE ALL TABLES
          </button>
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </Frame>
      </>
    )
  }

  return <p className="muted">Unknown data &amp; system section.</p>
}
