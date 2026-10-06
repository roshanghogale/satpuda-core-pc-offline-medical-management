import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { ensureLocalEngine } from '../backend'
import { formatExpiryMmYy } from '../expiryText'
import {
  autosavePurchaseBill,
  applyPurchaseImport,
  calcPurchaseBill,
  cancelPurchaseImport,
  discardPurchaseAutosave,
  fetchPurchaseForm,
  fetchPurchaseImportCapabilities,
  fetchPurchaseImportProgress,
  fetchPurchaseRuntimePrefs,
  fetchRecentPurchases,
  filesToImportPayload,
  loadLastPurchase,
  loadPurchaseById,
  lookupPurchaseMedicine,
  lookupSupplierByName,
  mergePurchaseLines,
  registerPurchaseMedicine,
  savePurchaseBill,
  searchPurchaseMedicines,
  startPurchaseImport,
  type LoadedPurchase,
  type PurchaseCalcResult,
  type PurchaseFormDefaults,
  type PurchaseImportCapabilities,
  type PurchaseLinePayload,
  type PurchaseRuntimePrefs,
  type ReorderPrefill,
} from '../pagesApi'
import {
  AlertDialog,
  GstSlabDialog,
  RecentPurchasesDialog,
  type AlertState,
} from './SalesDialogs'
import { ModernCombo } from './ModernCombo'
import { belowReturnedProblem, samePurchaseLine } from './billLineRules'
import { useLayoutRowCount } from '../layoutRows'
import { registerVoicePage, type VoiceHandler } from '../voice/voiceBus'
import {
  ActionBtn,
  DocTabBar,
  DocToolsDialog,
  Field,
  PageRoot,
  Panel,
  CappedTableWrap,
  ScheduleChip,
  SelectWrap,
  StatusLine,
} from './pageChrome'
import { BillScanProgressOverlay } from '../components/BillScanProgressOverlay'
import { dispatchDataChanged } from '../syncRefresh'

type LineItem = {
  medicine: string
  name: string
  medicine_id?: number | null
  type: string
  batch: string
  qty: number
  free: number
  rate: number
  mrp: number
  discPct: number
  gstPct: number
  amount: number
  manufacturer?: string
  expiry?: string
  pack?: string
  hsn?: string
  schedule?: string
  content?: string
  taxable?: number
  gstAmt?: number
  mrpTab?: number
  rateTab?: number
  tablets_per_stripe?: number
  _preserve_line_totals?: boolean
}

type PurchaseTab = {
  id: string
  title: string
  supplier: string
  supplierId: number | null
  phone: string
  address: string
  gstin: string
  dl: string
  purchaseDate: string
  billNumber: string
  gstMethod: string
  prevDue: number
  prevCredit: number
  items: LineItem[]
  overallDiscPct: string
  overallDisc: string
  rounding: string
  roundingTouched: boolean
  delivery: string
  cash: string
  online: string
  editingPurchaseId: number | null
  autosavePurchaseId: number | null
  editPrevDue: number | null
  editPrevCredit: number | null
  /** A saved bill opened for edit: qty already returned per medicine id. */
  returnedByMed?: Record<string, number>
  returnsNote?: string
  importBillMode: boolean
  importInvoiceSummary: Record<string, unknown> | null
  dirty: boolean
  reorderOrderId: number | null
}

function money(n: number) {
  return `₹${n.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

function fmtPrice(val: number): string {
  if (!Number.isFinite(val) || val <= 0) return ''
  const rounded2 = Math.round(val * 100) / 100
  if (Math.abs(val - rounded2) < 1e-9) return rounded2.toFixed(2)
  return String(Number(val.toFixed(4)))
}

/** Type 1129 → 11/29 (slash after month, max MM/YY). */
function isStripType(t: string, meta?: PurchaseRuntimePrefs['type_meta']) {
  if (meta && meta[t]) return Boolean(meta[t].strip)
  const x = (t || '').trim().toLowerCase()
  return x === 'tablet' || x === 'capsule' || x === 'bolus'
}

function typeMetaFor(
  t: string,
  prefs: PurchaseRuntimePrefs | null,
): {
  strip: boolean
  measure_unit: string
  qty_label: string
  free_label: string
  pack_label: string
  is_vial: boolean
  is_vaccine: boolean
} {
  const fromPrefs = prefs?.type_meta?.[t]
  if (fromPrefs) return fromPrefs
  const strip = isStripType(t)
  const low = (t || '').trim().toLowerCase()
  const is_vial = low === 'injection - vial'
  const is_vaccine = low === 'vaccine'
  return {
    strip,
    measure_unit: '',
    qty_label: strip
      ? 'Strips (Qty)'
      : is_vial
        ? 'Vials (Qty)'
        : 'Units (Qty)',
    free_label: strip
      ? 'Free Strips'
      : is_vial
        ? 'Free Vials'
        : 'Free Units',
    pack_label: strip ? 'Tabs/Strip' : 'Pack Size',
    is_vial,
    is_vaccine,
  }
}

function newTab(n: number, purchaseDate = ''): PurchaseTab {
  return {
    id: `p-${Date.now()}-${n}`,
    title: `Purchase ${n}`,
    supplier: '',
    supplierId: null,
    phone: '',
    address: '',
    gstin: '',
    dl: '',
    purchaseDate: purchaseDate || new Date().toISOString().slice(0, 10),
    billNumber: '',
    gstMethod: 'discount_before_gst',
    prevDue: 0,
    prevCredit: 0,
    items: [],
    overallDiscPct: '0',
    overallDisc: '0',
    rounding: '0',
    roundingTouched: false,
    delivery: '0',
    cash: '0',
    online: '0',
    editingPurchaseId: null,
    autosavePurchaseId: null,
    editPrevDue: null,
    editPrevCredit: null,
    importBillMode: false,
    importInvoiceSummary: null,
    dirty: false,
    reorderOrderId: null,
  }
}

function lineFromPayload(it: PurchaseLinePayload): LineItem {
  const qty = Number(it.qty) || 0
  const tps = Math.max(1, Number(it.tablets_per_stripe) || 1)
  // An edited purchase sends `unit`; a bill import sends `quantity_value`
  // (the cleaned pack) and `pack` (what the bill printed). Reading only
  // `unit` put every imported syrup and liquid on the shelf as a pack of 1.
  const pack =
    String(it.unit || it.quantity_value || it.pack || '').trim() ||
    (isStripType(it.type || '') ? String(tps) : '1')
  const mrp = Number(it.mrp) || 0
  const rate = Number(it.rate) || 0
  const amount = Number(it.item_amount ?? it.amount) || 0
  return {
    medicine: it.name,
    name: it.name,
    medicine_id: it.medicine_id ?? it.id ?? null,
    type: it.type || '',
    batch: it.batch || '',
    qty,
    free: Number(it.free_qty) || 0,
    rate,
    mrp,
    discPct: Number(it.discount_pct) || 0,
    gstPct: Number(it.gst_pct) || 0,
    amount,
    manufacturer: it.manufacturer || '',
    expiry: it.expiry || '',
    pack,
    hsn: it.hsn_code || '',
    schedule: it.schedule || '',
    content: it.content_drug || '',
    // A bill import names these import_taxable / import_gst_amt. Reading only
    // the short names left both at 0, so an imported line showed no taxable
    // value and no tax against an amount the bill clearly taxed.
    taxable: Number(it.taxable ?? it.import_taxable) || 0,
    gstAmt: Number(it.gst_amt ?? it.import_gst_amt) || 0,
    // A gel or syrup has no strips. Carrying the import's number through was
    // what put one bottle on the shelf as two hundred.
    tablets_per_stripe: isStripType(it.type || '') ? tps : 1,
    mrpTab: isStripType(it.type || '') ? mrp / tps : undefined,
    rateTab: isStripType(it.type || '') ? rate / tps : undefined,
    _preserve_line_totals:
      Boolean(it._preserve_line_totals) || amount > 0,
  }
}

// Editable fields of a purchase line, in the order they appear in the table.
// Left/Right and Enter walk this list.

function tabPayload(tab: PurchaseTab) {
  return {
    supplier_name: tab.supplier,
    supplier_phone: tab.phone,
    supplier_address: tab.address,
    gstin: tab.gstin,
    dl: tab.dl,
    bill_number: tab.billNumber,
    purchase_date: tab.purchaseDate,
    gst_calc_method: tab.gstMethod,
    overall_discount: Number(tab.overallDisc) || 0,
    discount_pct: Number(tab.overallDiscPct) || 0,
    rounding: Number(tab.rounding) || 0,
    expenditure: Number(tab.delivery) || 0,
    cash_paid: Number(tab.cash) || 0,
    online_paid: Number(tab.online) || 0,
    previous_due: tab.prevDue,
    previous_credit: tab.prevCredit,
    editing_purchase_id: tab.editingPurchaseId || undefined,
    autosave_purchase_id: tab.autosavePurchaseId || undefined,
    edit_previous_due: tab.editPrevDue ?? tab.prevDue,
    edit_previous_credit: tab.editPrevCredit ?? tab.prevCredit,
    import_bill_mode: tab.importBillMode || undefined,
    import_invoice_summary: tab.importInvoiceSummary || undefined,
    reorder_order_id: tab.reorderOrderId || undefined,
    items: tab.items.map((it) => ({
      medicine_id: it.medicine_id || null,
      name: it.name,
      type: it.type,
      batch: it.batch,
      expiry: it.expiry,
      qty: it.qty,
      free_qty: it.free,
      rate: it.rate,
      mrp: it.mrp,
      gst_pct: it.gstPct,
      discount_pct: it.discPct,
      hsn_code: it.hsn,
      manufacturer: it.manufacturer,
      schedule: it.schedule,
      content_drug: it.content,
      unit: it.pack,
      tablets_per_stripe: it.tablets_per_stripe,
      quantity_value: it.pack,
      item_amount: it.amount,
      amount: it.amount,
      taxable: it.taxable,
      gst_amt: it.gstAmt,
      _preserve_line_totals: it._preserve_line_totals,
    })),
  }
}

export function PurchasePage({
  editPurchaseId = null,
  reorderPrefill = null,
  onEditConsumed,
  onReorderPrefillConsumed,
  syncRefreshNonce = 0,
  active,
}: {
  editPurchaseId?: number | null
  reorderPrefill?: ReorderPrefill | null
  onEditConsumed?: () => void
  onReorderPrefillConsumed?: () => void
  syncRefreshNonce?: number
  /** Required on purpose. Every page stays mounted once visited, so a page
   *  that does not know whether it is on screen keeps answering the
   *  keyboard from behind another one. An optional prop defaulting to true
   *  let exactly that omission through the compiler. */
  active: boolean
}) {
  const [defaults, setDefaults] = useState<PurchaseFormDefaults | null>(null)
  const [prefs, setPrefs] = useState<PurchaseRuntimePrefs | null>(null)
  const [medSuggestions, setMedSuggestions] = useState<
    { name: string; source?: string; id?: number }[]
  >([])
  const [medSearchLoading, setMedSearchLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [alert, setAlert] = useState<AlertState | null>(null)
  const [gstSlabOpen, setGstSlabOpen] = useState(false)
  const [recentOpen, setRecentOpen] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [recentPurchases, setRecentPurchases] = useState<
    {
      id: number
      purchase_no: string
      purchase_date: string
      supplier: string
      total: number
    }[]
  >([])
  const [importCaps, setImportCaps] =
    useState<PurchaseImportCapabilities | null>(null)
  const [importing, setImporting] = useState(false)
  const [importStatus, setImportStatus] = useState('')
  /** importing, readable from the window keydown handler and the progress poll. */
  const importingRef = useRef(false)
  importingRef.current = importing
  const progressIdRef = useRef('')
  // The preview round-trip was removed when its GET started 404ing, which left
  // PurchaseImportReviewDialog with no code path that could ever open it. The
  // wait is shown by BillScanProgressOverlay now.
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const [tabs, setTabs] = useState<PurchaseTab[]>([newTab(1)])
  const [activeTab, setActiveTab] = useState(0)
  const tab = tabs[activeTab] || tabs[0]

  // A row edit belongs to the bill it was opened on. Tabs are separate bills,
  // so leaving the index set across a switch would send the update to a line in
  // the wrong one.
  useEffect(() => {
    // Ending the edit is not enough: the other bill's medicine, batch and
    // quantity were still sitting in the form, so the next Add put a line from
    // the previous bill onto this one.
    setEditingIdx(null)
    clearMedicineFieldsRef.current()
  }, [activeTab])
  const purchaseRows = useLayoutRowCount('purchase_rows')

  const [medicine, setMedicine] = useState('')
  const [medType, setMedType] = useState('')
  const [manufacturer, setManufacturer] = useState('')
  const [batch, setBatch] = useState('')
  // Empty, the way the classic billing screen has always created it
  // (ui/billing/purchase_form.py:279 — and note the discount entry three lines
  // down DOES seed a '0', so the omission there is deliberate). A pre-filled
  // '1' plus a programmatic focus that does not select means the caret lands
  // after it and the next digit APPENDS: typing 4 in a hurry gives 14.
  /** True for the moment between a line being added and the medicine box
   *  regaining focus, so a second Enter cannot re-fire "add" on a cleared form. */
  const justAddedRef = useRef(false)
  const [strips, setStrips] = useState('')
  const [tabsPerStrip, setTabsPerStrip] = useState('1')
  const [freeStrips, setFreeStrips] = useState('0')
  const [hsn, setHsn] = useState('')
  const [gstPct, setGstPct] = useState('12')
  const [schedule, setSchedule] = useState('')
  const [expiry, setExpiry] = useState('')
  const [rate, setRate] = useState('0')
  const [mrp, setMrp] = useState('0')
  const [tabletMrp, setTabletMrp] = useState('')
  const [tabletRate, setTabletRate] = useState('')
  const [discPct, setDiscPct] = useState('0')
  const [content, setContent] = useState('')
  const [vaccineUnit, setVaccineUnit] = useState('ml')
  const [calc, setCalc] = useState<PurchaseCalcResult | null>(null)

  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  // The global key handler is registered once; it reads the row being edited
  // through a ref so it does not go stale between renders.
  const editingIdxRef = useRef<number | null>(null)
  editingIdxRef.current = editingIdx
  // An index alone is not an identity: deleting another line shifts every row
  // below it, and the pending Update then landed on whatever moved into that
  // slot. Remember the line and find it again when the update is applied.
  const editingLineRef = useRef<LineItem | null>(null)
  // Guards against a stale calc response painting over a newer one.
  const calcSeqRef = useRef(0)
  const tableRef = useRef<HTMLTableElement | null>(null)
  const medRef = useRef<HTMLInputElement | null>(null)
  const cashRef = useRef<HTMLInputElement | null>(null)
  const priceFromTablet = useRef(false)

  const tabRef = useRef(tab)
  const tabsRef = useRef(tabs)
  const activeRef = useRef(activeTab)
  const prefsRef = useRef(prefs)
  tabRef.current = tab
  tabsRef.current = tabs
  activeRef.current = activeTab
  prefsRef.current = prefs

  const NAV = 'purchase'

  const showAlert = useCallback((a: AlertState) => setAlert(a), [])

  const patchTab = useCallback(
    (patch: Partial<PurchaseTab>, opts?: { quiet?: boolean; idx?: number }) => {
      const idx = opts?.idx ?? activeTab
      setTabs((all) =>
        all.map((t, i) =>
          i === idx
            ? { ...t, ...patch, dirty: opts?.quiet ? t.dirty : true }
            : t,
        ),
      )
    },
    [activeTab],
  )

  const focusNavOrder = useCallback((order: number) => {
    const scoped = document.querySelector<HTMLElement>(
      `.desktop-page[data-nav-chain="${NAV}"] [data-nav-order="${order}"]`,
    )
    scoped?.focus()
    if (scoped instanceof HTMLInputElement) {
      try {
        scoped.select()
      } catch {
        /* ignore */
      }
    }
  }, [])

  const reloadForm = useCallback(async () => {
    const [d, p, caps] = await Promise.all([
      fetchPurchaseForm(),
      fetchPurchaseRuntimePrefs(),
      fetchPurchaseImportCapabilities().catch(() => null),
    ])
    setDefaults(d)
    setPrefs(p)
    if (caps) setImportCaps(caps)
    if (d.medicines?.length) {
      setMedSuggestions(d.medicines)
    }
    void searchPurchaseMedicines('', 50)
      .then((sug) => {
        if (sug.medicines?.length) setMedSuggestions(sug.medicines)
      })
      .catch(() => {
        /* keep defaults.medicines seed if search fails */
      })
    setTabs((all) =>
      all.map((t, i) =>
        i === 0 && !t.dirty
          ? {
              ...t,
              purchaseDate: d.form.purchase_date || t.purchaseDate,
              gstMethod: p.default_gst_calc_method || t.gstMethod,
            }
          : t,
      ),
    )
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError('')
      try {
        const engine = await ensureLocalEngine()
        if (!engine.ok) {
          if (!cancelled) setError(engine.error)
          return
        }
        await reloadForm()
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [reloadForm])

  useEffect(() => {
    if (!syncRefreshNonce) return
    void reloadForm()
  }, [syncRefreshNonce, reloadForm])

  useEffect(() => {
    const onLayout = () => void reloadForm()
    window.addEventListener('satpuda:layout-config-changed', onLayout)
    return () =>
      window.removeEventListener('satpuda:layout-config-changed', onLayout)
  }, [reloadForm])

  // Debounced Python calc
  useEffect(() => {
    if (!tab) return
    if (!tab.items.length) {
      setCalc(null)
      return
    }
    const handle = window.setTimeout(() => {
      const seq = ++calcSeqRef.current
      void (async () => {
        try {
          const res = await calcPurchaseBill({
            ...tabPayload(tab),
            auto_rounding: !tab.roundingTouched,
            // The preview does not need the supplier's balance, and fetching it
            // Online costs several thousand-row queries over the network on
            // every edit. The page already has it from the supplier lookup.
            skip_party_due: true,
          })
          // A slower earlier request must not paint over a newer answer. Without
          // this the totals could settle on the figures from the edit BEFORE
          // last, which is a large part of why they looked stuck.
          if (seq !== calcSeqRef.current) return
          if (!res.ok) return
          setCalc(res)
          if (
            !tab.roundingTouched &&
            res.calc &&
            typeof res.calc.rounding === 'number'
          ) {
            const r = String(res.calc.rounding)
            if (r !== tab.rounding) {
              patchTab({ rounding: r }, { quiet: true })
            }
          }
        } catch {
          /* ignore live calc errors */
        }
      })()
    }, 280)
    return () => window.clearTimeout(handle)
  }, [
    tab,
    patchTab,
  ])

  // Autosave — OFF by default
  useEffect(() => {
    if (!prefs?.autosave_enabled) return
    const ms = Math.max(30, prefs.autosave_interval_seconds || 120) * 1000
    const id = window.setInterval(() => {
      void (async () => {
        const cur = tabRef.current
        if (!cur?.dirty || !cur.items.length) return
        if (cur.editingPurchaseId && !cur.autosavePurchaseId) return
        try {
          const res = await autosavePurchaseBill(tabPayload(cur))
          if (res.ok && res.autosave_purchase_id) {
            patchTab(
              {
                autosavePurchaseId: res.autosave_purchase_id,
                dirty: false,
                title: res.purchase_no || cur.title,
              },
              { quiet: true },
            )
            setNote(
              `Autosaved draft ${res.purchase_no || res.autosave_purchase_id}`,
            )
          }
        } catch {
          /* ignore */
        }
      })()
    }, ms)
    return () => window.clearInterval(id)
  }, [prefs?.autosave_enabled, prefs?.autosave_interval_seconds, patchTab])

  const suppliers = defaults?.suppliers || []
  const medicineTypes =
    prefs?.medicine_types?.length
      ? prefs.medicine_types
      : defaults?.medicine_types?.length
        ? defaults.medicine_types
        : [
            'Bolus',
            'Bolus Pack',
            'Capsule',
            'Cream',
            'Drops',
            'Ear Drops',
            'Eye Drops',
            'Feed Supplement',
            'Gel',
            'Granules',
            'Inhaler',
            'Injection',
            'Injection - Vial',
            'Instrument / Medical Device',
            'Liniment',
            'Liquid',
            'Lotion',
            'Nasal Drops',
            'Ointment',
            'Others',
            'Powder',
            'Sachet',
            'Shampoo',
            'Soap',
            'Spray',
            'Suspension',
            'Syrup',
            'Tablet',
            'Tablet Pack',
            'Vaccine',
          ]
  const scheduleOptions =
    prefs?.schedules?.length
      ? prefs.schedules
      : defaults?.schedules?.length
        ? defaults.schedules
        : ['', 'H', 'H1', 'X', 'G', 'K', 'C', 'C1', 'P', 'N', 'M']
  const masterListLoading =
    prefs?.app_mode === 'medical' && prefs?.master_ready === false
  const activeTypeMeta = typeMetaFor(medType, prefs)
  const stripMode = activeTypeMeta.strip
  const tps = Math.max(1, Number(tabsPerStrip) || 1)

  useEffect(() => {
    if (!stripMode) {
      setTabletMrp('')
      setTabletRate('')
      return
    }
    if (priceFromTablet.current) {
      priceFromTablet.current = false
      return
    }
    const sm = Number(mrp) || 0
    const sr = Number(rate) || 0
    setTabletMrp(sm > 0 ? fmtPrice(sm / tps) : '')
    setTabletRate(sr > 0 ? fmtPrice(sr / tps) : '')
  }, [mrp, rate, tps, stripMode])

  const onStripMrpChange = (v: string) => {
    setMrp(v)
  }
  const onStripRateChange = (v: string) => {
    setRate(v)
  }
  const onTabletMrpChange = (v: string) => {
    priceFromTablet.current = true
    setTabletMrp(v)
    const tm = Number(v) || 0
    setMrp(tm > 0 ? fmtPrice(tm * tps) : '0')
  }
  const onTabletRateChange = (v: string) => {
    priceFromTablet.current = true
    setTabletRate(v)
    const tr = Number(v) || 0
    setRate(tr > 0 ? fmtPrice(tr * tps) : '0')
  }

  const clearMedicineFieldsRef = useRef(() => {})
  const clearMedicineFields = () => {
    // Clearing the fields also ends any row edit they were holding. Without
    // this the button stayed on Update and the next medicine added would have
    // overwritten the line that happened to be open.
    setEditingIdx(null)
    setMedicine('')
    setMedType(defaults?.medicine_types?.[0] || prefs?.medicine_types?.[0] || '')
    setManufacturer('')
    setBatch('')
    setStrips('')
    setTabsPerStrip('1')
    setFreeStrips('0')
    setVaccineUnit('ml')
    setHsn('')
    setGstPct('12')
    setSchedule('')
    setExpiry('')
    setRate('0')
    setMrp('0')
    setTabletMrp('')
    setTabletRate('')
    setDiscPct('0')
    setContent('')
  }
  clearMedicineFieldsRef.current = clearMedicineFields

  const onMedTypeChange = (next: string) => {
    const prevMeta = typeMetaFor(medType, prefs)
    setMedType(next)
    const meta = typeMetaFor(next, prefs)
    if (meta.strip) {
      // Switching Liquid/Powder → Tablet must not keep 200ML as tabs/strip.
      const n = Number(tabsPerStrip)
      const packLooksBad =
        !Number.isFinite(n) || n <= 0 || n > 60 || /[a-zA-Z]/.test(String(tabsPerStrip || ''))
      if (next.toLowerCase() === 'bolus' || !prevMeta.strip || packLooksBad) {
        setTabsPerStrip('1')
      }
    } else if (meta.measure_unit) {
      setTabsPerStrip(meta.measure_unit)
    }
  }

  const onPickSupplier = (name: string) => {
    void applySupplierBalance(name, { force: true })
  }

  const onSupplierTyped = (name: string) => {
    void applySupplierBalance(name, { force: false })
  }

  const applySupplierBalance = useCallback(
    async (name: string, opts?: { force?: boolean }) => {
      const trimmed = name.trim()
      const force = opts?.force === true
      if (!trimmed) {
        patchTab({
          // Keep in-progress spaces while typing ("RAM " → "RAM MEDICINE").
          supplier: force ? '' : name,
          supplierId: null,
          prevDue: 0,
          prevCredit: 0,
        })
        return
      }
      const local = suppliers.find(
        (x) => x.name.toLowerCase() === trimmed.toLowerCase(),
      )
      patchTab({
        // Typing must keep spaces; only Enter/pick snaps to the stored name.
        supplier: force ? local?.name || trimmed : name,
        supplierId: local?.id ?? null,
        phone: local?.phone || (force ? '' : tabRef.current?.phone || ''),
        address: local?.address || (force ? '' : tabRef.current?.address || ''),
        gstin: local?.gstin || (force ? '' : tabRef.current?.gstin || ''),
        dl: local?.dl || (force ? '' : tabRef.current?.dl || ''),
        prevDue: Number(local?.due) || 0,
        prevCredit: Number(local?.credit) || 0,
      })
      if (!force) return
      if (tabRef.current?.editingPurchaseId) return
      try {
        const res = await lookupSupplierByName(trimmed, true)
        if (!res.found || !res.supplier) {
          if (!local) {
            patchTab({
              supplier: trimmed,
              supplierId: null,
              prevDue: 0,
              prevCredit: 0,
            })
          }
          return
        }
        const s = res.supplier
        patchTab({
          supplier: s.name || trimmed,
          supplierId: s.id || null,
          phone: s.phone || '',
          address: s.address || '',
          gstin: s.gstin || '',
          dl: s.dl || '',
          prevDue: Number(s.due) || 0,
          prevCredit: Number(s.credit) || 0,
        })
        setDefaults((d) => {
          if (!d) return d
          const list = [...(d.suppliers || [])]
          const idx = list.findIndex(
            (x) =>
              x.id === s.id ||
              x.name.toLowerCase() === (s.name || '').toLowerCase(),
          )
          const row = {
            id: s.id,
            name: s.name || trimmed,
            phone: s.phone || '',
            address: s.address || '',
            gstin: s.gstin || '',
            dl: s.dl || '',
            due: Number(s.due) || 0,
            credit: Number(s.credit) || 0,
          }
          if (idx < 0) list.push(row)
          else list[idx] = { ...list[idx], ...row }
          return { ...d, suppliers: list }
        })
      } catch {
        /* keep optimistic */
      }
    },
    [suppliers, patchTab],
  )

  /** Returns the medicine's type, for callers that must know how to read a pack. */
  const onPickMedicine = async (name: string): Promise<string> => {
    const trimmed = name.trim()
    if (trimmed.length < 1) return ''
    try {
      const res = await lookupPurchaseMedicine(trimmed)
      if (!res.ok || !res.details) return ''
      const d = res.details
      const nextType = d.type || medType
      onMedTypeChange(nextType)
      setManufacturer(d.manufacturer || '')
      setHsn(d.hsn_code || '')
      setGstPct(String(d.gst_percent || 12))
      setMrp(String(d.mrp || 0))
      setRate(String(d.rate || 0))
      setSchedule(d.schedule || '')
      setContent(d.content_drug || '')
      setBatch(d.batch_no || '')
      setExpiry(formatExpiryMmYy(d.expiry || ''))
      if (d.unit) {
        const meta = typeMetaFor(nextType, prefs)
        if (meta.strip) {
          setTabsPerStrip(
            String(d.unit).replace(/[^\d.]/g, '') || '1',
          )
        } else {
          setTabsPerStrip(String(d.unit))
        }
      }
      if (d.discount_pct) setDiscPct(String(d.discount_pct))
      return nextType
    } catch {
      /* ignore */
    }
    return ''
  }

  const refreshMedicineSuggestions = useCallback(async (q: string) => {
    setMedSearchLoading(true)
    try {
      const sug = await searchPurchaseMedicines(q.trim(), 50)
      setMedSuggestions(sug.medicines || [])
      if (prefsRef.current?.app_mode === 'medical' && sug.master_ready === false) {
        setNote('Loading medicines… (master list preparing)')
      }
    } catch {
      /* ignore */
    } finally {
      setMedSearchLoading(false)
    }
  }, [])

  useEffect(() => {
    const q = medicine.trim()
    const handle = window.setTimeout(() => {
      void refreshMedicineSuggestions(q)
    }, 80)
    return () => window.clearTimeout(handle)
  }, [medicine, refreshMedicineSuggestions])

  useEffect(() => {
    if (prefs?.app_mode !== 'medical' || prefs?.master_ready !== false) return
    const id = window.setInterval(() => {
      void fetchPurchaseRuntimePrefs().then((p) => {
        setPrefs(p)
        if (p.master_ready) {
          void refreshMedicineSuggestions(medicine)
          setNote('')
        }
      })
    }, 2000)
    return () => window.clearInterval(id)
  }, [prefs?.app_mode, prefs?.master_ready, medicine, refreshMedicineSuggestions])

  const addItem = () => {
    // Swallowed while the form is being cleared after an add (see the guard at
    // the end of this function).
    if (justAddedRef.current) return
    const name = medicine.trim()
    if (!name) {
      // Sales does not scold here either: an empty medicine box just bounces
      // focus back to it (SalesPage's add path). The counter is either going on
      // to the next medicine or has finished with the items, so say nothing and
      // put them where they were heading.
      focusNavOrder(9)
      return
    }
    if (!medType.trim()) {
      showAlert({
        title: 'Missing Information',
        message: 'Please select medicine type.',
        kind: 'warning',
        focusAfterClose: () => focusNavOrder(10),
      })
      return
    }
    if (!batch.trim()) {
      showAlert({
        title: 'Missing Information',
        message: 'Please enter batch number.',
        kind: 'warning',
        focusAfterClose: () => focusNavOrder(22),
      })
      return
    }
    const exp = expiry.trim()
    if (!exp || !/^\d{1,2}\/\d{2}$/.test(exp)) {
      showAlert({
        title: 'Expiry',
        message: 'Please enter expiry as MM/YY (e.g. 08/27).',
        kind: 'warning',
        focusAfterClose: () => focusNavOrder(23),
      })
      return
    }
    const qty = Math.max(0, Number(strips) || 0)
    if (qty <= 0) {
      showAlert({
        title: 'Quantity',
        message: 'Quantity must be greater than zero.',
        kind: 'warning',
        focusAfterClose: () => focusNavOrder(11),
      })
      return
    }
    const free = Math.max(0, Number(freeStrips) || 0)
    const rateN = Number(rate) || 0
    const mrpN = Number(mrp) || 0
    const type = medType || 'Tablet'
    const meta = typeMetaFor(type, prefs)
    const tpsN = meta.strip
      ? Math.max(1, Number(tabsPerStrip) || 1)
      : 1
    const packValue = meta.strip
      ? String(tpsN)
      : (tabsPerStrip || meta.measure_unit || '1').trim()
    const autoUnit = meta.is_vaccine
      ? vaccineUnit
      : meta.strip
        ? ''
        : meta.measure_unit || packValue
    const line: LineItem = {
      medicine: name.toUpperCase(),
      name: name.toUpperCase(),
      medicine_id: null,
      type,
      batch: batch.trim().toUpperCase(),
      qty,
      free,
      rate: rateN,
      mrp: mrpN,
      discPct: Number(discPct) || 0,
      gstPct: Number(gstPct) || 0,
      amount: 0,
      manufacturer: manufacturer.trim(),
      expiry: expiry.trim(),
      pack: packValue,
      hsn: hsn.trim(),
      schedule: schedule === '—' ? '' : schedule,
      content: content.trim(),
      tablets_per_stripe: meta.strip ? tpsN : undefined,
      mrpTab: meta.strip ? mrpN / tpsN : undefined,
      rateTab: meta.strip ? rateN / tpsN : undefined,
    }
    void registerPurchaseMedicine({
      name: line.name,
      manufacturer: line.manufacturer,
      mrp: line.mrp,
      content_drug: line.content,
      type: line.type,
      unit: autoUnit || packValue,
      pack: packValue,
    }).catch(() => undefined)
    const marker = editingLineRef.current
    const at =
      editingIdx === null
        ? null
        : marker
          ? tabRef.current.items.indexOf(marker)
          : editingIdx
    if (editingIdx !== null && at === -1) {
      // The line was deleted while it was open. Do not write the form over
      // whichever line took its place.
      setEditingIdx(null)
      editingLineRef.current = null
      clearMedicineFields()
      showAlert({
        title: 'Line removed',
        message: 'That line is no longer on the bill, so it was not updated.',
        kind: 'warning',
      })
      return
    }
    if (at !== null && at >= 0 && tabRef.current.items[at]) {
      // Keep what the row carried that the form does not hold -- the medicine
      // id above all, so correcting a quantity does not turn a known medicine
      // back into a fresh one and lose its stock and history.
      const prev = tabRef.current.items[at]
      const edited = { ...prev, ...line, medicine_id: prev.medicine_id ?? line.medicine_id }
      const all = tabRef.current.items
      // Same name AND same batch is one line: an edit that lands on another
      // line's name and batch adds to that line.
      const dupAt = all.findIndex((m, i) => i !== at && samePurchaseLine(m, edited))
      const next =
        dupAt >= 0
          ? all.flatMap((m, i) =>
              i === at
                ? []
                : i === dupAt
                  ? [{ ...m, qty: (Number(m.qty) || 0) + edited.qty, free: (Number(m.free) || 0) + edited.free }]
                  : [m],
            )
          : all.map((m, i) => (i === at ? edited : m))
      if (purchaseReturnsBlock(next)) return
      patchTab({ items: next })
      setEditingIdx(null)
    } else {
      // Owner, 2026-09-11: the same name AND batch merges (qty added); a
      // different batch or name stays a line of its own.
      const all = tabRef.current.items
      const same = all.findIndex((m) => samePurchaseLine(m, line))
      if (same >= 0) {
        patchTab({
          items: all.map((m, i) =>
            i === same
              ? { ...m, qty: (Number(m.qty) || 0) + qty, free: (Number(m.free) || 0) + free }
              : m,
          ),
        })
        setNote(`${line.name} batch ${line.batch} was already on the bill — quantity added to that line.`)
      } else {
        patchTab({ items: [...all, line] })
      }
    }
    clearMedicineFields()
    // A line was just added. The form is cleared synchronously but the focus
    // move below is deferred, and for that window document.activeElement is
    // still the Content/Drug input, which carries data-nav-enter="add". The
    // global Enter handler dispatches that unconditionally -- no e.repeat
    // guard, no in-flight guard -- so a second Enter (held key, or the counter
    // moving on quickly) re-fired "add" against a form that had just been
    // emptied, and the empty-name branch popped "Please enter medicine name."
    // Close the window instead of apologising for it.
    justAddedRef.current = true
    window.setTimeout(() => {
      justAddedRef.current = false
      medRef.current?.focus()
      try {
        medRef.current?.select()
      } catch {
        /* ignore */
      }
    }, 50)
  }

  /** A saved bill with returns against it: no line may go below what went
   *  back to the supplier, nor be removed. True when `next` was refused. */
  const purchaseReturnsBlock = (next: LineItem[]): boolean => {
    const cur = tabRef.current
    if (!cur) return false
    const why = belowReturnedProblem(
      next,
      cur.editingPurchaseId ? cur.returnedByMed : undefined,
      (l) => l.medicine_id,
      (l) => String(l.name || l.medicine || ''),
      cur.items,
    )
    if (!why) return false
    showAlert({ title: 'Returned on this bill', message: why, kind: 'warning' })
    return true
  }

  const removeItem = (idx: number) => {
    const next = tabRef.current.items.filter((_, i) => i !== idx)
    if (purchaseReturnsBlock(next)) return
    patchTab({ items: next })
  }

  // Every value on a purchase line is editable from the keyboard.
  //
  //   F2            focus the list
  //   Up / Down     move between rows
  //   Enter         start editing the focused row
  //   Left / Right  move between fields
  //   Enter         next field, and on the last field save the row
  //   Escape        abandon the edit
  //
  // Bills come in with the odd wrong batch, expiry or rate, and until now only
  // Qty, Free and Disc% could be corrected here -- everything else meant
  // deleting the line and typing it again.
  const startLineEdit = (idx: number) => {
    const it = tabRef.current.items[idx]
    if (!it) return
    setEditingIdx(idx)
    editingLineRef.current = it
    setMedicine(String(it.medicine ?? it.name ?? ''))
    setMedType(String(it.type ?? ''))
    setManufacturer(String(it.manufacturer ?? ''))
    setBatch(String(it.batch ?? ''))
    setStrips(String(it.qty ?? ''))
    // The line's own pack first. tablets_per_stripe is 1 on every non-strip
    // line by design, so reading it here flattened "1LTR" to "1" the moment
    // anyone opened the line -- and Save wrote that 1 back onto the medicine.
    setTabsPerStrip(String(it.pack || it.tablets_per_stripe || '1'))
    setFreeStrips(String(it.free ?? '0'))
    setHsn(String(it.hsn ?? ''))
    setGstPct(String(it.gstPct ?? '0'))
    setSchedule(String(it.schedule ?? ''))
    setExpiry(String(it.expiry ?? ''))
    setRate(String(it.rate ?? '0'))
    setMrp(String(it.mrp ?? '0'))
    setTabletRate(it.rateTab != null ? String(it.rateTab) : '')
    setTabletMrp(it.mrpTab != null ? String(it.mrpTab) : '')
    setDiscPct(String(it.discPct ?? '0'))
    setContent(String(it.content ?? ''))
    // Land on Quantity: a line is nearly always reopened to correct the count
    // or the rate, and both are a step apart from there.
    window.setTimeout(() => focusNavOrder(11), 30)
  }

  const cancelLineEdit = () => {
    const idx = editingIdxRef.current
    setEditingIdx(null)
    editingLineRef.current = null
    clearMedicineFields()
    window.setTimeout(() => {
      const rows = tableRef.current?.querySelectorAll<HTMLElement>(
        'tbody tr[tabindex="0"]',
      )
      if (rows && idx != null) rows[Math.min(idx, rows.length - 1)]?.focus()
    }, 20)
  }

  const clearForm = async () => {
    const cur = tabRef.current
    if (cur?.autosavePurchaseId) {
      try {
        await discardPurchaseAutosave(cur.autosavePurchaseId)
      } catch {
        /* ignore */
      }
    }
    const blank = newTab(
      activeTab + 1,
      defaults?.form.purchase_date || '',
    )
    // Keep the tab's own name ("Purchase 2") across a Clear, never a number
    // carried over from the purchase that was being edited or autosaved.
    const keepTitle =
      Boolean(cur?.title) &&
      !cur?.editingPurchaseId &&
      !cur?.autosavePurchaseId &&
      /^Purchase \d+$/.test(cur?.title || '')
    blank.title = keepTitle ? cur!.title : blank.title
    blank.gstMethod =
      prefs?.default_gst_calc_method || blank.gstMethod
    blank.importBillMode = false
    blank.importInvoiceSummary = null
    blank.dirty = false
    setTabs((all) => all.map((t, i) => (i === activeTab ? blank : t)))
    setCalc(null)
    setNote('')
    clearMedicineFields()
  }

  const applyImportedForm = (
    form: NonNullable<
      import('../pagesApi').PurchaseImportApplyResult['form']
    >,
    opts: {
      replace: boolean
      importBillMode: boolean
      importInvoiceSummary: Record<string, unknown> | null
      message?: string
    },
  ) => {
    const items = (form.items || []).map(lineFromPayload)
    const cur = tabRef.current
    patchTab({
      supplier: form.supplier_name || (opts.replace ? '' : cur.supplier),
      phone: form.supplier_phone || (opts.replace ? '' : cur.phone),
      address: form.supplier_address || (opts.replace ? '' : cur.address),
      gstin: form.gstin || (opts.replace ? '' : cur.gstin),
      dl: form.dl || (opts.replace ? '' : cur.dl),
      billNumber: form.bill_number || cur.billNumber,
      purchaseDate: form.purchase_date || cur.purchaseDate,
      gstMethod: form.gst_calc_method || cur.gstMethod,
      overallDisc: String(form.overall_discount ?? 0),
      // The bill prints rupees; show the matching percent too instead of a
      // hard '0', which left the percent box blank on every imported bill.
      overallDiscPct: String(form.discount_pct ?? 0),
      delivery: String(form.expenditure ?? 0),
      cash: String(form.cash_paid ?? 0),
      online: String(form.online_paid ?? 0),
      roundingTouched: false,
      items: opts.replace ? items : [...cur.items, ...items],
      importBillMode: opts.importBillMode,
      importInvoiceSummary: opts.importInvoiceSummary,
      // Importing INTO a purchase that is being edited must keep editing it.
      // Clearing the id turned the edit into a new bill, so saving wrote a
      // second purchase and put the same goods into stock twice.
      editingPurchaseId: cur.editingPurchaseId ?? null,
      title: cur.editingPurchaseId
        ? cur.title
        : form.bill_number
          ? `Import ${form.bill_number}`
          : cur.title,
      dirty: true,
    })
    if (opts.message) {
      showAlert({
        title: 'Import Complete',
        message: opts.message,
        kind: 'info',
      })
      setNote(
        `Imported ${items.length} item(s)${
          form.bill_number ? ` · ${form.bill_number}` : ''
        }`,
      )
    }
  }

  /** Re-enter the apply pass after a confirm.
   *
   *  goApply's finally already lowered `importing` when the confirm went up,
   *  which is right -- the question must not be asked behind a dimmed,
   *  cursor:wait screen. But the ANSWER starts another engine round trip, and
   *  online that one is not cheap: apply runs a live store lookup per row, so a
   *  forty-line bill was forty silent network calls with nothing moving.
   */
  const reenterImportApply = (
    token: string,
    flags: Parameters<typeof runImportApply>[1],
  ) => {
    setImporting(true)
    void (async () => {
      try {
        await runImportApply(token, flags)
      } catch (e) {
        showAlert({
          title: 'Import Error',
          message: e instanceof Error ? e.message : String(e),
          kind: 'error',
        })
      } finally {
        setImporting(false)
      }
    })()
  }

  const runImportApply = async (
    token: string,
    flags: {
      skip_invalid?: boolean
      continue_count_mismatch?: boolean
      replace_existing?: boolean
      has_existing_items?: boolean
      /** Add the parsed lines to this saved purchase instead of starting a new one. */
      merge_into_purchase_id?: number
      /** A second purchase for a bill number that is already on the books. */
      allow_duplicate?: boolean
    },
  ) => {
    const res = await applyPurchaseImport({
      import_token: token,
      progress_id: progressIdRef.current,
      ...flags,
    })
    if (res.need_confirm) {
      // The same invoice number is already a purchase. Usually this is the
      // second photo of one bill, not a second bill (store 127 held the same
      // bill as purchases 35 and 36), so the first answer adds the medicines
      // that purchase does not have yet to the purchase itself.
      if (res.code === 'bill_already_saved' && res.existing) {
        const existingId = res.existing.purchase_id
        showAlert({
          title: 'Bill Already Saved',
          message: res.message || 'This bill is already saved.',
          kind: 'confirm',
          confirmLabel: 'Merge',
          altLabel: 'Naveen bill banav',
          cancelLabel: 'Radd',
          onConfirm: () => {
            reenterImportApply(token, {
              ...flags,
              merge_into_purchase_id: existingId,
            })
          },
          onAlt: () => {
            reenterImportApply(token, { ...flags, allow_duplicate: true })
          },
          onCancel: () => {
            void cancelPurchaseImport(token)
          },
        })
        return
      }
      if (res.code === 'replace_or_append') {
        showAlert({
          title: 'Existing Purchase Items',
          message: res.message || 'Replace existing items?',
          kind: 'confirm',
          confirmLabel: 'Replace',
          cancelLabel: 'Append',
          onConfirm: () => {
            reenterImportApply(token, {
              ...flags,
              has_existing_items: true,
              replace_existing: true,
            })
          },
          onCancel: () => {
            reenterImportApply(token, {
              ...flags,
              has_existing_items: true,
              replace_existing: false,
            })
          },
        })
        return
      }
      showAlert({
        title:
          res.code === 'item_count_mismatch'
            ? 'Item count mismatch'
            : 'Some Rows Skipped',
        message: res.message || 'Continue?',
        kind: 'confirm',
        confirmLabel: 'Continue',
        onConfirm: () => {
          reenterImportApply(token, {
            ...flags,
            skip_invalid:
              res.code === 'skip_invalid' ? true : flags.skip_invalid,
            continue_count_mismatch:
              res.code === 'item_count_mismatch'
                ? true
                : flags.continue_count_mismatch,
            has_existing_items: Boolean(tabRef.current.items.length),
          })
        },
        onCancel: () => {
          void cancelPurchaseImport(token)
        },
      })
      return
    }
    // Merged: the answer is the SAVED purchase opened to edit, with only the
    // missing lines appended and editing_purchase_id set, so the next Save
    // updates that one bill instead of writing a second one.
    if (res.ok && res.merged && res.loaded) {
      const added = res.items_added ?? 0
      applyLoaded(res.loaded, {
        dirty: true,
        note: `${res.loaded.purchase_no || ''} open for edit · ${added} new line(s) added`,
      })
      showAlert({
        title: 'Bill Open For Edit',
        message: res.message || 'The saved bill is open for edit.',
        kind: 'info',
      })
      return
    }
    if (!res.ok || !res.form) {
      showAlert({
        title: 'Import Error',
        message: res.error || 'Import failed.',
        kind: 'error',
      })
      return
    }
    applyImportedForm(res.form, {
      replace: res.replace_existing !== false,
      importBillMode: Boolean(res.import_bill_mode),
      importInvoiceSummary: res.import_invoice_summary || null,
      message: res.message,
    })
  }

  const continueAfterStart = async (
    started: Awaited<ReturnType<typeof startPurchaseImport>>,
  ) => {
    if (!started.ok || !started.import_token) {
      showAlert({
        title: 'Import Error',
        message: started.error || 'Could not parse bill.',
        kind: 'error',
      })
      return
    }
    const token = started.import_token
    const flags = {
      has_existing_items: Boolean(tabRef.current.items.length),
      skip_invalid: started.confirmations?.skip_invalid ? undefined : true,
      continue_count_mismatch: started.confirmations?.item_count_mismatch
        ? undefined
        : true,
    }
    const goApply = async () => {
      try {
        await runImportApply(token, flags)
      } catch (e) {
        showAlert({
          title: 'Import Error',
          message: e instanceof Error ? e.message : String(e),
          kind: 'error',
        })
      } finally {
        setImporting(false)
      }
    }

    if (started.may_need_more_pages) {
      setImporting(false)
      showAlert({
        title: 'More pages?',
        message:
          `This bill may be incomplete (${started.page_count || 1} page(s) scanned).\n\n` +
          'Continue with what was found, or Cancel and select all pages together?',
        kind: 'confirm',
        confirmLabel: 'Continue',
        cancelLabel: 'Cancel',
        onConfirm: () => {
          setImporting(true)
          void goApply()
        },
        onCancel: () => {
          void cancelPurchaseImport(token)
        },
      })
      return
    }
    // Same as classic: parse then fill the Purchase form. Do not require a
    // separate preview GET (that route 404'd and showed "Not found").
    await goApply()
  }

  const beginImportWithFiles = async (files: FileList | File[]) => {
    const list = Array.from(files)
    if (!list.length) return
    progressIdRef.current = `imp-${Date.now()}-${Math.random().toString(36).slice(2)}`
    setImportStatus('')
    setImporting(true)
    try {
      const payload = await filesToImportPayload(list)
      await continueAfterStart(
        await startPurchaseImport({
          files: payload,
          progress_id: progressIdRef.current,
        }),
      )
    } catch (e) {
      showAlert({
        title: 'Import Error',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  // Open the file chooser in the SAME tick as the button click.
  //
  // This used to ask the engine to open a native Windows dialog first and only
  // fall back to this input if that failed. Two things went wrong. The engine
  // runs as a hidden background process, so a dialog it opens is not reliably
  // visible -- the user sees nothing and the request never returns. And the
  // fallback could not work either: awaiting that request spends the browser's
  // user-activation, after which a programmatic .click() on a file input is
  // silently ignored. Pressing Import simply did nothing.
  //
  // The webview's own input needs no round trip and carries the click straight
  // through, so it is the only picker the desktop uses now.
  // While a scan is running, ask the engine what stage it is on. The parse and
  // enrich passes have always emitted these lines; nothing was listening, so the
  // overlay showed one frozen sentence for the whole wait.
  useEffect(() => {
    if (!importing) return
    let stopped = false
    const id = progressIdRef.current
    const tick = async () => {
      const r = await fetchPurchaseImportProgress(id)
      if (!stopped && r.status) setImportStatus(r.status)
    }
    void tick()
    const h = window.setInterval(() => void tick(), 700)
    return () => {
      stopped = true
      window.clearInterval(h)
    }
  }, [importing])

  const openImportPicker = () => {
    if (importingRef.current) return
    fileInputRef.current?.click()
  }

  const applyLoaded = (
    loaded: LoadedPurchase,
    /** A merge hands back a bill with lines it has not saved yet, so the tab is
     *  dirty and the note says what was added rather than "loaded for edit". */
    opts?: { dirty?: boolean; note?: string },
  ) => {
    if (!loaded.ok || !loaded.form) {
      showAlert({
        title: 'Load Purchase',
        message: loaded.error || 'Could not load purchase.',
        kind: 'error',
      })
      return
    }
    const f = loaded.form
    patchTab({
      supplier: f.supplier_name,
      supplierId: f.supplier_id ?? null,
      phone: f.supplier_phone,
      address: f.supplier_address,
      gstin: f.gstin,
      dl: f.dl,
      billNumber: f.bill_number,
      purchaseDate: f.purchase_date,
      gstMethod: f.gst_calc_method || 'discount_before_gst',
      overallDisc: String(f.overall_discount ?? 0),
      overallDiscPct: String(f.discount_pct ?? 0),
      rounding: String(f.rounding ?? 0),
      roundingTouched: true,
      delivery: String(f.expenditure ?? 0),
      cash: String(f.cash_paid ?? 0),
      online: String(f.online_paid ?? 0),
      prevDue: f.previous_due || 0,
      prevCredit: f.previous_credit || 0,
      items: (f.items || []).map(lineFromPayload),
      editingPurchaseId: loaded.editing_purchase_id ?? null,
      autosavePurchaseId: loaded.autosave_purchase_id ?? null,
      returnedByMed: loaded.returned_by_medicine || {},
      returnsNote: loaded.returns_note || '',
      editPrevDue: loaded.edit_payment_snapshot?.previous_due ?? f.previous_due,
      editPrevCredit:
        loaded.edit_payment_snapshot?.previous_credit ?? f.previous_credit,
      importBillMode: false,
      importInvoiceSummary: null,
      title: loaded.purchase_no || tab.title,
      dirty: Boolean(opts?.dirty),
    })
    setNote(
      opts?.note || `Loaded ${loaded.purchase_no || loaded.purchase_id} for edit`,
    )
  }

  /** See SalesPage: derived from the prop so no blank frame gets through. */
  const editPending = Boolean(editPurchaseId)

  /** editPending, readable from the window keydown handlers, which capture an
   *  old render's closure. */
  const editPendingRef = useRef(false)
  editPendingRef.current = editPending

  useEffect(() => {
    if (!editPurchaseId) return
    let cancelled = false
    void (async () => {
      try {
        const loaded = await loadPurchaseById(editPurchaseId)
        if (cancelled) return
        applyLoaded(loaded)
      } catch (e) {
        if (!cancelled) {
          showAlert({
            title: 'Load Purchase',
            message: e instanceof Error ? e.message : String(e),
            kind: 'error',
          })
        }
      } finally {
        if (!cancelled) onEditConsumed?.()
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editPurchaseId])

  useEffect(() => {
    if (!reorderPrefill?.order_id) return
    let cancelled = false
    void (async () => {
      try {
        const engine = await ensureLocalEngine()
        if (!engine.ok || cancelled) return
        const pf = reorderPrefill
        if (pf.supplier_name) {
          onPickSupplier(pf.supplier_name)
        }
        patchTab({ reorderOrderId: pf.order_id })
        let pickedType = ''
        if (pf.medicine_name) {
          pickedType = await onPickMedicine(pf.medicine_name)
          setMedicine(pf.medicine_name)
        }
        if (pf.rate > 0) setRate(String(pf.rate))
        if (pf.quantity > 0) {
          const q =
            pf.quantity === Math.floor(pf.quantity)
              ? String(Math.floor(pf.quantity))
              : String(pf.quantity)
          setStrips(q)
        }
        if (pf.pack_size) {
          // A strip count is a number; a pack size is text the bill printed.
          // Stripping the letters from both turned 500ML into 500. When the
          // medicine is unknown the picker has already filled this box from
          // the catalogue, so the text is left as it stands.
          setTabsPerStrip(
            typeMetaFor(pickedType, prefs).strip
              ? pf.pack_size.replace(/[^\d.]/g, '') || '1'
              : pf.pack_size,
          )
        }
        setNote(
          pf.order_no
            ? `Reorder ${pf.order_no} — enter batch/expiry and add line`
            : 'Reorder prefill loaded — enter batch/expiry and add line',
        )
      } catch (e) {
        if (!cancelled) {
          showAlert({
            title: 'Reorder prefill',
            message: e instanceof Error ? e.message : String(e),
            kind: 'error',
          })
        }
      } finally {
        if (!cancelled) onReorderPrefillConsumed?.()
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reorderPrefill])

  /** "Junya bill madhe ughad": open the bill that is already saved and carry the
   *  rows on screen over to it.
   *
   *  The engine decides which of these rows that bill already has -- the same
   *  rule a re-imported bill photo goes through (core/purchase_line_merge.py) --
   *  so the two answers can never drift apart. Nothing on the tab is cleared if
   *  this fails. */
  const openSavedBillWithNewLines = async (purchaseId: number) => {
    setSaving(true)
    try {
      const res = await mergePurchaseLines({
        purchase_id: purchaseId,
        items: tabPayload(tabRef.current).items,
      })
      if (!res.ok || !res.loaded) {
        showAlert({
          title: 'Open Saved Bill',
          message: res.error || 'Could not open that purchase.',
          kind: 'error',
        })
        return
      }
      const added = res.items_added ?? 0
      applyLoaded(res.loaded, {
        dirty: true,
        note: `${res.loaded.purchase_no || purchaseId} open for edit · ${added} new line(s) added`,
      })
      showAlert({
        title: 'Bill Open For Edit',
        message: res.message || 'The saved bill is open for edit.',
        kind: 'info',
      })
    } catch (e) {
      showAlert({
        title: 'Open Saved Bill',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setSaving(false)
    }
  }

  const savePurchase = async (opts?: {
    /** The shop answered "Tari save kar" to the refusal below. */
    allowDuplicate?: boolean
    /** The tab was editing a saved purchase and the shop said: this is a new bill. */
    asNew?: boolean
    /** ...or said: yes, change the saved purchase into this bill. */
    replaceEdited?: boolean
  }): Promise<boolean> => {
    // F5 reaches this from the window listener even while the form is still
    // the blank tab waiting for the bill being edited.
    if (editPendingRef.current) return false
    if (editingIdxRef.current !== null) {
      showAlert({
        title: 'Finish the line first',
        message:
          'A medicine is open for editing. Press Update Medicine to apply it, '
          + 'or Escape to leave it unchanged, then save.',
        kind: 'warning',
      })
      return false
    }
    setSaving(true)
    try {
      const payload: Record<string, unknown> = tabPayload(tabRef.current)
      if (opts?.asNew) {
        // A new purchase: not the edit, and the supplier's due as it is today.
        delete payload.editing_purchase_id
        delete payload.edit_previous_due
        delete payload.edit_previous_credit
      }
      const res = await savePurchaseBill({
        ...payload,
        ...(opts?.allowDuplicate ? { allow_duplicate: true } : {}),
        ...(opts?.replaceEdited ? { confirm_replace_purchase: true } : {}),
      })
      // The tab still held a saved purchase for edit, but the supplier or bill
      // number on screen is another bill's (Shivkrupa 6 Oct 2026: VINOD bill 2199
      // imported into the tab editing TULJAI purchase 106 replaced 106). Nothing
      // was written; the shop picks.
      if (res.need_confirm && res.code === 'edit_other_bill') {
        const no = res.editing?.purchase_no || ''
        showAlert({
          title: 'Another bill?',
          message: res.message || 'This tab is editing a saved purchase.',
          kind: 'confirm',
          confirmLabel: 'Navin purchase save kar',
          altLabel: `Purchase ${no} badal`,
          cancelLabel: 'Radd',
          onConfirm: () => {
            void savePurchase({ ...opts, asNew: true, replaceEdited: false })
          },
          onAlt: () => {
            void savePurchase({ ...opts, asNew: false, replaceEdited: true })
          },
        })
        return false
      }
      // One supplier bill, one purchase. The engine refused this one BEFORE
      // writing anything (store 127 held the same bill as purchases 35 and 36,
      // one second apart, because the old check only warned afterwards), so the
      // rows are all still here and nothing may be cleared on any of the three
      // answers.
      if (res.need_confirm && res.code === 'duplicate_bill' && res.existing) {
        const existingId = res.existing.purchase_id
        showAlert({
          title: 'Bill Already Saved',
          message: res.message || 'This bill is already saved.',
          kind: 'confirm',
          confirmLabel: 'Junya bill madhe ughad',
          altLabel: 'Tari save kar',
          cancelLabel: 'Radd',
          onConfirm: () => {
            void openSavedBillWithNewLines(existingId)
          },
          onAlt: () => {
            void savePurchase({ ...opts, allowDuplicate: true })
          },
        })
        return false
      }
      if (!res.ok) {
        const title =
          res.code === 'supplier_required'
            ? 'Supplier Required'
            : res.code === 'no_items'
              ? 'No Items'
              : 'Save Purchase'
        showAlert({
          title,
          message: res.error || 'Save failed.',
          kind: 'warning',
          focusAfterClose:
            res.code === 'supplier_required'
              ? () => focusNavOrder(1)
              : res.code === 'no_items'
                ? () => focusNavOrder(9)
                : undefined,
        })
        return false
      }
      setNote(`Saved ${res.purchase_no}`)
      dispatchDataChanged(['medicines', 'purchases', 'suppliers'])
      await clearForm()
      // Say it plainly. The note in the corner was easy to miss, and a shop
      // that is not sure a bill saved will enter it a second time.
      showAlert({
        title: 'Purchase Saved',
        message:
          `Purchase ${res.purchase_no} saved.` +
          // Saved regardless; only pointed out.
          (res.warnings?.length ? `\n\nPlease check:\n• ${res.warnings.join('\n• ')}` : ''),
        kind: res.warnings?.length ? 'warning' : 'info',
        focusAfterClose: () => focusNavOrder(9),
      })
      return true
    } catch (e) {
      showAlert({
        title: 'Error',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
      return false
    } finally {
      setSaving(false)
    }
  }

  const recalculatePurchase = async () => {
    const cur = tabRef.current
    if (!cur.items.length) {
      showAlert({
        title: 'Recalculate',
        message: 'Add purchase items first.',
        kind: 'warning',
      })
      return
    }
    const before = Number(
      (calc?.calc as Record<string, number> | undefined)?.total_amount,
    ) || 0
    try {
      const res = await calcPurchaseBill({
        ...tabPayload(cur),
        auto_rounding: true,
        rounding: undefined,
        skip_party_due: true,
        // Recalculate means "work it out from these rows as if I had typed
        // them". An imported bill is otherwise pinned to the supplier's printed
        // footer, so a misread figure could never be corrected here.
        recalculate: true,
      })
      if (!res.ok) {
        showAlert({
          title: 'Recalculate',
          message: res.error || 'Could not recalculate.',
          kind: 'error',
        })
        return
      }
      setCalc(res)
      if (res.calc && typeof res.calc.rounding === 'number') {
        patchTab(
          {
            rounding: String(res.calc.rounding),
            roundingTouched: false,
          },
          { quiet: true },
        )
      }
      if (res.gst_calc_method) {
        patchTab({ gstMethod: res.gst_calc_method }, { quiet: true })
      }
      // From here the bill behaves like a hand-typed one: the rows are the
      // truth, and the GST Method dropdown affects the totals again.
      patchTab({ importBillMode: false }, { quiet: true })
      const after = Number(res.calc?.total_amount) || 0
      if (Math.abs(after - before) < 0.02 && before > 0) {
        showAlert({
          title: 'Recalculate',
          message:
            'Totals unchanged.\n\nTip: check GST % on each line, overall discount, and GST method.',
          kind: 'info',
        })
      } else {
        setNote(`Recalculated · bill ${money(after)}`)
      }
    } catch (e) {
      showAlert({
        title: 'Recalculate',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }

  const openGstSlab = async () => {
    if (!tabRef.current.items.length) {
      showAlert({
        title: 'GST Slab Table',
        message: 'Add purchase items first.',
        kind: 'warning',
      })
      return
    }
    try {
      const res = await calcPurchaseBill({
        ...tabPayload(tabRef.current),
        auto_rounding: !tabRef.current.roundingTouched,
      })
      if (res.ok) setCalc(res)
    } catch {
      /* use last calc */
    }
    setGstSlabOpen(true)
  }

  const openRecent = async () => {
    try {
      const res = await fetchRecentPurchases(5)
      setRecentPurchases(res.purchases || [])
      setRecentOpen(true)
    } catch (e) {
      showAlert({
        title: 'Recent Purchases',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }

  const openLast = async () => {
    try {
      applyLoaded(await loadLastPurchase())
    } catch (e) {
      showAlert({
        title: 'Last Purchase',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }

  const addTab = () => {
    setTabs((all) => [
      ...all,
      newTab(all.length + 1, defaults?.form.purchase_date || ''),
    ])
    setActiveTab(tabs.length)
  }

  const closeTab = async (index?: number) => {
    const all = tabsRef.current
    if (all.length <= 1) return
    const idx = index ?? activeRef.current
    const cur = all[idx]
    if (cur?.dirty) {
      showAlert({
        title: 'Close Tab',
        message: 'This purchase has unsaved changes. Close anyway?',
        kind: 'confirm',
        confirmLabel: 'Close',
        onConfirm: () => {
          void doCloseTab(idx)
        },
      })
      return
    }
    await doCloseTab(idx)
  }

  const doCloseTab = async (index: number) => {
    const all = tabsRef.current
    if (all.length <= 1) return
    const cur = all[index]
    if (cur?.autosavePurchaseId) {
      try {
        await discardPurchaseAutosave(cur.autosavePurchaseId)
      } catch {
        /* ignore */
      }
    }
    const next = all.filter((_, i) => i !== index)
    setTabs(next)
    setActiveTab((curIdx) => {
      if (index < curIdx) return curIdx - 1
      if (index === curIdx) return Math.min(index, next.length - 1)
      return curIdx
    })
  }

  const actionsRef = useRef({
    savePurchase,
    clearForm,
    openRecent,
    openLast,
    addTab,
    closeTab,
    openGstSlab,
    recalculatePurchase,
    addItem: () => {},
    pickMedicine: async (_n: string) => '',
    applySupplier: async (_n: string, _o?: { force?: boolean }) => {},
  })
  actionsRef.current = {
    savePurchase,
    clearForm,
    openRecent,
    openLast,
    addTab,
    closeTab,
    openGstSlab,
    recalculatePurchase,
    addItem,
    pickMedicine: onPickMedicine,
    applySupplier: applySupplierBalance,
  }

  useEffect(() => {
    const onNav = (e: Event) => {
      const ce = e as CustomEvent<{ action?: string }>
      const action = ce.detail?.action
      if (!action) return
      const a = actionsRef.current
      if (action === 'add') {
        a.addItem()
        return
      }
      if (action === 'save') {
        void a.savePurchase()
        return
      }
      if (action === 'supplier-enter') {
        const name = (
          document.querySelector(
            `.desktop-page[data-nav-chain="${NAV}"] [data-nav-order="1"]`,
          ) as HTMLInputElement | null
        )?.value?.trim()
        if (name) void a.applySupplier(name, { force: true })
        focusNavOrder(2)
        return
      }
      if (action === 'to-medicine') {
        focusNavOrder(9)
        return
      }
      if (action === 'medicine-enter') {
        const name = (
          document.querySelector(
            `.desktop-page[data-nav-chain="${NAV}"] [data-nav-order="9"]`,
          ) as HTMLInputElement | null
        )?.value?.trim()
        if (!name) {
          focusNavOrder(27)
          return
        }
        void a.pickMedicine(name)
        focusNavOrder(10)
      }
    }
    document.addEventListener('satpuda-nav-action', onNav)
    return () => document.removeEventListener('satpuda-nav-action', onNav)
  }, [focusNavOrder])

  useEffect(() => {
    // Every page stays mounted once visited and is only hidden with
    // display:none, so a window listener on a hidden page still fires. Without
    // this, one F5 saved the sale AND the purchase, F3 opened a bill on a page
    // nobody was looking at, and F12 left a dialog waiting on the other screen.
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      const key = e.key
      const ctrl = e.ctrlKey
      const shift = e.shiftKey
      const a = actionsRef.current

      // A bill scan is a blocking modal, but a window keydown listener is not a
      // DOM descendant of it -- so Shift+F2 went straight past the dim and
      // started a SECOND parse (two more Gemini calls, and whichever finished
      // first tore the overlay down under the other), and F5 saved a purchase
      // from a form the import had not filled in yet.
      if (importingRef.current) {
        e.preventDefault()
        e.stopPropagation()
        return
      }

      if (key === 'Escape' && editingIdxRef.current !== null) {
        e.preventDefault()
        cancelLineEdit()
        return
      }
      if (key === 'F2') {
        if (shift) {
          e.preventDefault()
          openImportPicker()
          return
        }
        e.preventDefault()
        const rows = tableRef.current?.querySelectorAll<HTMLElement>(
          'tbody tr[tabindex="0"]',
        )
        rows?.[0]?.focus()
        return
      }
      if (key === 'F5' && !shift) {
        e.preventDefault()
        void a.savePurchase()
        return
      }
      if (key === 'F6') {
        e.preventDefault()
        focusNavOrder(27)
        return
      }
      if (key === 'F7' && !ctrl && !shift) {
        e.preventDefault()
        void a.openGstSlab()
        return
      }
      if (key === 'F8' && !ctrl && !shift) {
        e.preventDefault()
        void a.recalculatePurchase()
        return
      }
      if (key === 'F9' && !ctrl && !shift) {
        e.preventDefault()
        void a.clearForm()
        return
      }
      if (key === 'F10') {
        e.preventDefault()
        void a.openRecent()
        return
      }
      if (key === 'F11') {
        e.preventDefault()
        void a.openLast()
        return
      }
      if (key === 'F12') {
        e.preventDefault()
        setToolsOpen((open) => !open)
        return
      }
      if (key === 'F3' && !ctrl && !shift) {
        e.preventDefault()
        a.addTab()
        return
      }
      if (key === 'F4' && !ctrl && !shift) {
        e.preventDefault()
        void a.closeTab()
        return
      }
      if (key === 'End') {
        const tag = (e.target as HTMLElement)?.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
          e.preventDefault()
          cashRef.current?.focus()
          try {
            cashRef.current?.select()
          } catch {
            /* ignore */
          }
        }
        return
      }
      if (ctrl && shift && (key === 'c' || key === 'C')) {
        e.preventDefault()
        void a.clearForm()
        return
      }
      if (ctrl && shift && (key === 'n' || key === 'N')) {
        e.preventDefault()
        a.addTab()
        return
      }
      if (ctrl && shift && (key === 'w' || key === 'W')) {
        e.preventDefault()
        void a.closeTab()
        return
      }
      // Ctrl+Alt+arrow is what the in-app shortcut list has always advertised
      // as the arrow-key alternative, and it matched no branch until now -- an
      // owner who read the list and tried it concluded there was no way to
      // switch bills except the dialog.
      // Alt is required on the arrows: a bare Ctrl+Left is word-navigation
      // inside a text field, and the counter types in those all day.
      if (
        ctrl &&
        (key === 'PageUp' || key === '[' || (e.altKey && key === 'ArrowLeft'))
      ) {
        e.preventDefault()
        setActiveTab((i) => Math.max(0, i - 1))
        return
      }
      if (
        ctrl &&
        (key === 'PageDown' || key === ']' || (e.altKey && key === 'ArrowRight'))
      ) {
        e.preventDefault()
        setActiveTab((i) =>
          Math.min(tabsRef.current.length - 1, i + 1),
        )
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [focusNavOrder, active])

  /** Settings -> Layout & Lists -> Column Visibility -> "Purchase — Items List".
   *  These ticks were saved and read back and nothing on this screen had ever
   *  consulted them, so the table drew all fifteen columns whatever was set. */
  const colOn = (key: string) => {
    const vis = prefs?.column_visibility
    if (!vis || !(key in vis)) return true
    return Boolean(vis[key])
  }
  const showColRaw = {
    medicine: colOn('Medicine'),
    type: colOn('Type'),
    batch: colOn('Batch'),
    expiry: colOn('Expiry'),
    qty: colOn('Qty'),
    hsn: colOn('HSN'),
    schedule: colOn('Schedule'),
    free: colOn('Free'),
    mrp: colOn('MRP'),
    rate: colOn('Rate'),
    disc: colOn('Disc%'),
    gstPct: colOn('GST%'),
    taxable: colOn('Taxable'),
    gstAmt: colOn('GST Amt'),
    amount: colOn('Amount'),
  }
  // Same rule the engine uses for every other list (get_visible_columns
  // returns everything when the selection is empty): unticking all of them is
  // not a request for a blank table.
  const showCol = Object.values(showColRaw).some(Boolean)
    ? showColRaw
    : (Object.fromEntries(
        Object.keys(showColRaw).map((k) => [k, true]),
      ) as typeof showColRaw)
  // +1 for the row-actions column, which is not configurable.
  const purchaseColSpan =
    Object.values(showCol).filter(Boolean).length + 1

  const tabTitles = useMemo(
    () => tabs.map((t) => t.title + (t.dirty ? ' •' : '')),
    [tabs],
  )

  if (!tab) return null

  const summary = (calc?.calc || {}) as Record<string, number>
  const gross = Number(summary.gross_subtotal ?? summary.gross) || 0
  const subtotal = Number(summary.subtotal) || 0
  const totalGst = Number(summary.total_gst) || 0
  const cgst = Number(summary.cgst) || totalGst / 2
  const sgst = Number(summary.sgst) || totalGst / 2
  const totalAmount = Number(summary.total_amount) || 0
  const finalAmount = Number(summary.final_amount) || totalAmount
  const needToPay = Number(summary.need_to_pay) || finalAmount
  const due = Number(summary.due) || 0
  const credit = Number(summary.current_credit) || 0
  const paid =
    Number(summary.amount_paid) ||
    (Number(tab.cash) || 0) + (Number(tab.online) || 0)

  // ── Voice (test build): "recent purchase bills", "GST slab dakhav", "navin purchase tab" ──
  // Opens the same windows the buttons / F-keys open. Nothing here saves.
  const voiceHandlerRef = useRef<VoiceHandler>(async () => null)
  voiceHandlerRef.current = async (cmd) => {
    if (cmd.intent !== 'page_action') return null
    const act = String(cmd.args?.action || '')
    if (act === 'recent_bills') {
      await openRecent()
      return { ok: true, say: 'Recent purchase bills ughadle' }
    }
    if (act === 'gst_slab') {
      await openGstSlab()
      return { ok: true, say: 'GST Slab ughadla' }
    }
    if (act === 'tools') {
      setToolsOpen(true)
      return { ok: true, say: 'Purchase tabs & tools ughadle' }
    }
    if (act === 'new_tab') {
      addTab()
      return { ok: true, say: 'Navin purchase tab ughadla' }
    }
    if (act === 'import_bill') {
      if (importing) return { ok: false, say: 'Aadhicha import chalu aahe' }
      openImportPicker()
      return { ok: true, say: 'Import bill: file nivda' }
    }
    return { ok: false, say: `Purchase var "${act}" he kaam voice var nahi` }
  }
  useEffect(() => registerVoicePage('purchase', () => voiceHandlerRef.current), [])

  return (
    <PageRoot
      navChain={NAV}
      className={`bill-page${editPending ? ' is-edit-loading' : ''}`}
    >
      <StatusLine
        error={error}
        loading={(loading && !defaults) || editPending}
      />
      <AlertDialog alert={alert} onClose={() => setAlert(null)} />
      <GstSlabDialog
        open={gstSlabOpen}
        calc={(calc?.calc as Record<string, unknown>) || null}
        gstMethod={tab.gstMethod}
        importSummary={tab.importInvoiceSummary}
        onClose={() => setGstSlabOpen(false)}
      />
      <RecentPurchasesDialog
        open={recentOpen}
        purchases={recentPurchases}
        onClose={() => setRecentOpen(false)}
        onPick={(id) => {
          setRecentOpen(false)
          void loadPurchaseById(id).then(applyLoaded)
        }}
      />
      <input
        ref={fileInputRef}
        type="file"
        accept={
          importCaps?.accept ||
          '.pdf,.csv,.xlsx,.xls,.jpg,.jpeg,.png,.bmp,.tif,.tiff,.webp'
        }
        multiple
        style={{ display: 'none' }}
        onChange={(e) => {
          const files = e.target.files
          if (files?.length) void beginImportWithFiles(files)
        }}
      />

      <BillScanProgressOverlay open={importing} status={importStatus || undefined} />

      <DocToolsDialog
        open={toolsOpen}
        title="Purchase tabs & tools"
        onClose={() => setToolsOpen(false)}
        tabs={tabTitles}
        active={activeTab}
        addLabel="New purchase"
        onSelect={(i) => {
          setActiveTab(i)
          setToolsOpen(false)
        }}
        onAdd={() => {
          addTab()
          setToolsOpen(false)
        }}
        onCloseTab={(i) => void closeTab(i)}
        hint="F5 save · F6 discount · F3 new tab · F4 close tab"
        tools={[
          {
            label: 'New purchase',
            detail: 'Open another purchase tab',
            kbd: 'F3',
            onClick: () => {
              addTab()
              setToolsOpen(false)
            },
          },
          {
            label: 'Close this purchase',
            detail: 'Last tab cannot be closed',
            kbd: 'F4',
            disabled: tabs.length <= 1,
            onClick: () => {
              void closeTab()
              setToolsOpen(false)
            },
          },
          {
            label: importing ? 'Importing…' : 'Import bill',
            detail: 'PDF / Excel / photo invoice',
            kbd: 'Shift+F2',
            disabled: importing,
            onClick: () => {
              setToolsOpen(false)
              openImportPicker()
            },
          },
          {
            label: 'Recent bills',
            detail: 'Pick a recent purchase to load',
            kbd: 'F10',
            onClick: () => {
              setToolsOpen(false)
              void openRecent()
            },
          },
          {
            label: 'Last bill',
            detail: 'Load the last saved purchase',
            kbd: 'F11',
            onClick: () => {
              setToolsOpen(false)
              void openLast()
            },
          },
        ]}
      />


      {/* The list of open bills lives on the page now, not only inside the
          Tabs & Tools modal. Creating a bill and switching to one used to cost
          two interactions each, with the form covered in between. */}
      <DocTabBar
        tabs={tabTitles}
        active={activeTab}
        onSelect={setActiveTab}
        onAdd={() => addTab()}
        onCloseTab={(i) => void closeTab(i)}
        hint="F3 new · F4 close · Ctrl+[ / ] switch"
        right={
          <button
            type="button"
            className={`btn btn-tabbtn${toolsOpen ? ' is-open' : ''}`}
            onClick={() => setToolsOpen(true)}
          >
            Tabs &amp; Tools
            <span className="kbd">F12</span>
          </button>
        }
      />

      <div className="top-split">
        <Panel title="Supplier Information">
          <div className="supplier-grid">
            <Field label="Supplier Name">
              <ModernCombo
                value={tab.supplier}
                navOrder={1}
                navChain={NAV}
                navEnter="supplier-enter"
                placeholder="Search supplier…"
                voiceField="supplier"
                minChars={0}
                filterLocal
                listLabel="Suppliers"
                items={suppliers.map((s) => ({
                  id: String(s.id ?? s.name),
                  label: s.name,
                  meta: s.phone || undefined,
                }))}
                onChange={(v) => onSupplierTyped(v)}
                onPick={(it) => onPickSupplier(it.label)}
              />
            </Field>
            <Field label="Address">
              <input
                type="text"
                value={tab.address}
                data-nav-order={2}
                data-nav-chain={NAV}
                onChange={(e) => patchTab({ address: e.target.value })}
              />
            </Field>
            <Field label="Phone">
              <input
                type="text"
                className="mono"
                value={tab.phone}
                data-nav-order={3}
                data-nav-chain={NAV}
                onChange={(e) => patchTab({ phone: e.target.value })}
              />
            </Field>
            <Field label="GSTIN">
              <input
                type="text"
                className="mono"
                value={tab.gstin}
                data-nav-order={4}
                data-nav-chain={NAV}
                onChange={(e) => patchTab({ gstin: e.target.value })}
              />
            </Field>
            <Field label="DL Numbers">
              <input
                type="text"
                className="mono"
                value={tab.dl}
                data-nav-order={5}
                data-nav-chain={NAV}
                onChange={(e) => patchTab({ dl: e.target.value })}
              />
            </Field>
            <Field label="Purchase Date" hint="(YYYY-MM-DD)">
              <input
                type="text"
                className="mono"
                value={tab.purchaseDate}
                data-nav-order={6}
                data-nav-chain={NAV}
                onChange={(e) => patchTab({ purchaseDate: e.target.value })}
              />
            </Field>
            <Field label="Bill Number">
              <input
                type="text"
                className="mono"
                value={tab.billNumber}
                data-nav-order={7}
                data-nav-chain={NAV}
                data-nav-enter="to-medicine"
                onChange={(e) => patchTab({ billNumber: e.target.value })}
              />
            </Field>
            <Field label="GST Method">
              <SelectWrap>
                <select
                  value={tab.gstMethod}
                  onChange={(e) =>
                    // Choosing a method also releases an imported bill from its
                    // printed footer -- otherwise the footer wins and the
                    // dropdown appears to do nothing.
                    patchTab({
                      gstMethod: e.target.value,
                      importBillMode: false,
                    })
                  }
                  tabIndex={-1}
                  title="Not in Classic Enter chain — change with mouse if needed"
                >
                  {(
                    prefs?.gst_calc_methods || [
                      {
                        value: 'discount_before_gst',
                        label: 'Discount before GST',
                      },
                      {
                        value: 'discount_after_gst',
                        label: 'Discount after GST',
                      },
                    ]
                  ).map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </SelectWrap>
            </Field>
          </div>
        </Panel>

        <Panel title="Medicine Details">
          <div className="med-grid">
            <Field label="Medicine Name" className="span3">
              <ModernCombo
                value={medicine}
                inputRef={medRef}
                voiceField="medicine"
                navOrder={9}
                navChain={NAV}
                navEnter="medicine-enter"
                enterPicksHighlight={false}
                placeholder={
                  masterListLoading
                    ? 'Loading medicines… (local list available)'
                    : 'Click or type to search medicine…'
                }
                minChars={0}
                openOnFocus
                filterLocal={false}
                loading={medSearchLoading}
                maxVisible={50}
                listLabel="Medicines"
                emptyText={
                  masterListLoading
                    ? 'Loading master list… type to search local medicines'
                    : 'No matches — type a name or pick from the list'
                }
                items={medSuggestions.map((m, i) => ({
                  id: String(m.id ?? `${m.name}-${i}`),
                  label: m.name,
                  meta: m.source || undefined,
                }))}
                onFocus={() => void refreshMedicineSuggestions(medicine)}
                onChange={(v) => setMedicine(v)}
                onPick={(it) => {
                  setMedicine(it.label)
                  void onPickMedicine(it.label)
                }}
                onEnter={(picked) => {
                  const q = (
                    picked?.label ||
                    (
                      document.querySelector(
                        `.desktop-page[data-nav-chain="${NAV}"] [data-nav-order="9"]`,
                      ) as HTMLInputElement | null
                    )?.value ||
                    medicine
                  ).trim()
                  if (!q) {
                    focusNavOrder(27)
                    return
                  }
                  if (!picked) void onPickMedicine(q)
                  focusNavOrder(10)
                }}
              />
            </Field>
            <Field label="Type" className="span2">
              <ModernCombo
                value={medType}
                navOrder={10}
                navChain={NAV}
                enterPicksHighlight
                placeholder="Select or type…"
                minChars={0}
                filterLocal
                openOnFocus
                maxVisible={40}
                listLabel="Medicine types"
                items={medicineTypes.map((t) => ({ id: t, label: t }))}
                onChange={onMedTypeChange}
                onPick={(it) => onMedTypeChange(it.label)}
                onEnter={() => focusNavOrder(11)}
              />
            </Field>
            <Field label={activeTypeMeta.qty_label}>
              <input
                type="text"
                className="mono"
                value={strips}
                data-nav-order={11}
                data-nav-chain={NAV}
                onChange={(e) => setStrips(e.target.value)}
                // The keyboard routes select via focusNavOrder; a mouse click
                // did not, which is how a stale quantity got typed onto here.
                onFocus={(e) => e.currentTarget.select()}
              />
            </Field>
            <Field
              label={
                stripMode
                  ? activeTypeMeta.pack_label
                  : `${activeTypeMeta.pack_label}${
                      activeTypeMeta.measure_unit
                        ? ` (e.g. ${activeTypeMeta.measure_unit})`
                        : ''
                    }`
              }
            >
              <input
                type="text"
                className="mono"
                value={tabsPerStrip}
                data-nav-order={12}
                data-nav-chain={NAV}
                onChange={(e) => setTabsPerStrip(e.target.value)}
              />
            </Field>
            {activeTypeMeta.is_vaccine ? (
              <Field label="Vaccine unit">
                <SelectWrap>
                  <select
                    value={vaccineUnit}
                    onChange={(e) => setVaccineUnit(e.target.value)}
                    tabIndex={-1}
                  >
                    <option value="ml">ml</option>
                    <option value="Doses">Doses</option>
                  </select>
                </SelectWrap>
              </Field>
            ) : null}
            <Field label={activeTypeMeta.free_label}>
              <input
                type="text"
                className="mono"
                value={freeStrips}
                data-nav-order={13}
                data-nav-chain={NAV}
                onChange={(e) => setFreeStrips(e.target.value)}
              />
            </Field>
            <Field label="HSN Code">
              <input
                type="text"
                className="mono"
                value={hsn}
                data-nav-order={14}
                data-nav-chain={NAV}
                onChange={(e) => setHsn(e.target.value)}
              />
            </Field>
            <Field label="GST %">
              <input
                type="text"
                className="mono"
                value={gstPct}
                data-nav-order={15}
                data-nav-chain={NAV}
                list="purchase-gst-pct"
                onChange={(e) => setGstPct(e.target.value)}
              />
              <datalist id="purchase-gst-pct">
                {['0', '5', '12', '18', '28'].map((g) => (
                  <option key={g} value={g} />
                ))}
              </datalist>
            </Field>
            <Field
              label="MRP"
              hint={stripMode ? 'strip / per tab' : undefined}
              className="span2"
            >
              {stripMode ? (
                <div className="price-split">
                  <div className="price-half">
                    <input
                      type="text"
                      className="mono"
                      value={mrp}
                      data-nav-order={16}
                      data-nav-chain={NAV}
                      onChange={(e) => onStripMrpChange(e.target.value)}
                    />
                  </div>
                  <div className="price-half">
                    <input
                      type="text"
                      className="mono"
                      value={tabletMrp}
                      data-nav-order={17}
                      data-nav-chain={NAV}
                      onChange={(e) => onTabletMrpChange(e.target.value)}
                    />
                  </div>
                </div>
              ) : (
                <input
                  type="text"
                  className="mono"
                  value={mrp}
                  data-nav-order={16}
                  data-nav-chain={NAV}
                  onChange={(e) => onStripMrpChange(e.target.value)}
                />
              )}
            </Field>
            <Field
              label="Rate"
              hint={stripMode ? 'strip / per tab' : 'per unit'}
              className="span2"
            >
              {stripMode ? (
                <div className="price-split">
                  <div className="price-half">
                    <input
                      type="text"
                      className="mono"
                      value={rate}
                      data-nav-order={18}
                      data-nav-chain={NAV}
                      onChange={(e) => onStripRateChange(e.target.value)}
                    />
                  </div>
                  <div className="price-half">
                    <input
                      type="text"
                      className="mono"
                      value={tabletRate}
                      data-nav-order={19}
                      data-nav-chain={NAV}
                      onChange={(e) => onTabletRateChange(e.target.value)}
                    />
                  </div>
                </div>
              ) : (
                <input
                  type="text"
                  className="mono"
                  value={rate}
                  data-nav-order={18}
                  data-nav-chain={NAV}
                  onChange={(e) => onStripRateChange(e.target.value)}
                />
              )}
            </Field>
            <Field label="Discount %">
              <input
                type="text"
                className="mono"
                value={discPct}
                data-nav-order={20}
                data-nav-chain={NAV}
                onChange={(e) => setDiscPct(e.target.value)}
              />
            </Field>
            <Field label="Manufacturer" className="span2">
              <input
                type="text"
                value={manufacturer}
                data-nav-order={21}
                data-nav-chain={NAV}
                onChange={(e) => setManufacturer(e.target.value)}
              />
            </Field>
            <Field label="Batch No">
              <input
                type="text"
                className="mono"
                value={batch}
                data-nav-order={22}
                data-nav-chain={NAV}
                onChange={(e) => setBatch(e.target.value)}
              />
            </Field>
            <Field label="Expiry (MM/YY)">
              <input
                type="text"
                className="mono"
                value={expiry}
                placeholder="MM/YY"
                inputMode="numeric"
                maxLength={5}
                data-nav-order={23}
                data-nav-chain={NAV}
                onChange={(e) => {
                  const el = e.target
                  const prev = expiry
                  const start = el.selectionStart ?? el.value.length
                  const deleting = el.value.length < prev.length
                  const formatted = formatExpiryMmYy(el.value, deleting)
                  setExpiry(formatted)
                  window.requestAnimationFrame(() => {
                    let pos = start
                    if (
                      formatted.length > prev.length &&
                      formatted.includes('/') &&
                      start >= 2
                    ) {
                      pos = start + (formatted.length - prev.length)
                    }
                    pos = Math.min(Math.max(pos, 0), formatted.length)
                    try {
                      el.setSelectionRange(pos, pos)
                    } catch {
                      /* ignore */
                    }
                  })
                }}
                onBlur={() => setExpiry(formatExpiryMmYy(expiry))}
              />
            </Field>
            <Field label="Schedule">
              <ModernCombo
                value={schedule}
                navOrder={24}
                navChain={NAV}
                enterPicksHighlight={false}
                placeholder="Schedule code…"
                minChars={0}
                filterLocal
                openOnFocus
                maxVisible={20}
                listLabel="Schedules"
                items={scheduleOptions.map((s) => ({
                  id: s || '__none__',
                  label: s || '(none)',
                }))}
                onChange={(v) => setSchedule(v === '(none)' ? '' : v)}
                onPick={(it) => setSchedule(it.label === '(none)' ? '' : it.label)}
                onEnter={() => focusNavOrder(25)}
              />
            </Field>
            <Field label="Content / Drug" className="span3">
              <input
                type="text"
                value={content}
                data-nav-order={25}
                data-nav-chain={NAV}
                data-nav-enter="add"
                onChange={(e) => setContent(e.target.value)}
              />
            </Field>
            <div className="addmed-row">
              <span className="addmed-note">
                {tab.items.length
                  ? `${tab.items.length} line(s) in bill`
                  : 'Add medicines to this purchase'}
              </span>
              {/* The second copy of "Tabs & Tools" -- the tab bar above the
                  form carries the same button. Two of the same control on one
                  screen, this one sitting beside Add Medicine where it reads
                  like part of the medicine row. */}
              <ActionBtn
                label={editingIdx !== null ? 'Update Medicine' : 'Add Medicine'}
                onClick={addItem}
                navOrder={26}
                navChain={NAV}
                navAction="add"
              />
            </div>
          </div>
        </Panel>
      </div>

      <Panel
        title="Purchase Items"
        table
        className="bill-items-panel"
        headRight={
          <span className="count">
            {tab.items.length} item{tab.items.length === 1 ? '' : 's'}
          </span>
        }
      >
        <CappedTableWrap visibleRows={purchaseRows}>
          <table className="sat-table" ref={tableRef}>
            <thead>
              <tr>
                {showCol.medicine ? <th>Medicine</th> : null}
                {showCol.type ? <th>Type</th> : null}
                {showCol.batch ? <th>Batch</th> : null}
                {showCol.expiry ? <th>Expiry</th> : null}
                {showCol.qty ? <th className="numsm">Qty</th> : null}
                {showCol.hsn ? <th>HSN</th> : null}
                {showCol.schedule ? <th>Sch.</th> : null}
                {showCol.free ? <th className="numsm">Free</th> : null}
                {showCol.mrp ? <th className="numsm">MRP</th> : null}
                {showCol.rate ? <th className="numsm">Rate</th> : null}
                {showCol.disc ? <th className="numsm">Disc%</th> : null}
                {showCol.gstPct ? <th className="numsm">GST%</th> : null}
                {showCol.taxable ? <th className="numsm">Taxable</th> : null}
                {showCol.gstAmt ? <th className="numsm">GST Amt</th> : null}
                {showCol.amount ? <th className="num">Amount</th> : null}
                <th />
              </tr>
            </thead>
            <tbody>
              {tab.items.length === 0 ? (
                <tr>
                  <td colSpan={purchaseColSpan} className="muted">
                    No items added
                  </td>
                </tr>
              ) : (
                tab.items.map((it, i) => {
                  const calcLine = calc?.items?.[i]
                  // Show each row the way the supplier's bill prints it: the
                  // line's own qty x rate, WITHOUT the bill-level discount
                  // spread into it. The engine still applies that discount to
                  // the totals below -- and to GST -- exactly as the paper bill
                  // does at its foot. Apportioning it per row meant not one line
                  // matched the bill even though every total did.
                  const taxable =
                    Number(calcLine?.line_taxable ?? calcLine?.taxable ?? it.taxable) || 0
                  const gstAmt =
                    Number(calcLine?.line_gst_amt ?? calcLine?.gst_amt ?? it.gstAmt) || 0
                  const amount =
                    Number(
                      calcLine?.line_amount ??
                        calcLine?.item_amount ??
                        calcLine?.amount ??
                        it.amount,
                    ) || 0
                  return (
                  <tr
                    key={`${it.name}-${it.batch}-${i}`}
                    tabIndex={0}
                    className={editingIdx === i ? 'row-editing' : undefined}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        startLineEdit(i)
                      }
                      if (e.key === 'Delete' || e.key === 'Backspace') {
                        e.preventDefault()
                        removeItem(i)
                      }
                      if (e.key === 'ArrowDown') {
                        e.preventDefault()
                        const rows =
                          tableRef.current?.querySelectorAll<HTMLElement>(
                            'tbody tr[tabindex="0"]',
                          )
                        rows?.[Math.min(i + 1, (rows.length || 1) - 1)]?.focus()
                      }
                      if (e.key === 'ArrowUp') {
                        e.preventDefault()
                        const rows =
                          tableRef.current?.querySelectorAll<HTMLElement>(
                            'tbody tr[tabindex="0"]',
                          )
                        rows?.[Math.max(0, i - 1)]?.focus()
                      }
                    }}
                  >
                    {showCol.medicine ? (
                      <td>
                        {editingIdx === i ? (
                          null
                        ) : (
                          <>
                            <span className="med-name">{it.medicine}</span>
                            {it.manufacturer ? (
                              <span className="med-sub">{it.manufacturer}</span>
                            ) : null}
                          </>
                        )}
                      </td>
                    ) : null}
                    {showCol.type ? (
                      <td>{it.type || '—'}</td>
                    ) : null}
                    {showCol.batch ? (
                      <td className="mono">
                        {it.batch || '—'}
                      </td>
                    ) : null}
                    {showCol.expiry ? (
                      <td className="mono">
                        {it.expiry || '—'}
                      </td>
                    ) : null}
                    {showCol.qty ? (
                      <td className="numsm mono">
                        {it.qty}
                        {tab.editingPurchaseId && Number(tab.returnedByMed?.[String(it.medicine_id)] || 0) > 0 ? (
                          <div className="note" data-returned="1">
                            ↩ {tab.returnedByMed?.[String(it.medicine_id)]} returned
                          </div>
                        ) : null}
                      </td>
                    ) : null}
                    {showCol.hsn ? (
                      <td className="mono">{it.hsn || '—'}</td>
                    ) : null}
                    {showCol.schedule ? (
                      <td>
                        {<ScheduleChip value={it.schedule} />}
                      </td>
                    ) : null}
                    {showCol.free ? (
                      <td className="numsm mono free-badge">
                        {it.free}
                      </td>
                    ) : null}
                    {showCol.mrp ? (
                      <td className="numsm mono">
                        {it.mrp.toFixed(2)}
                      </td>
                    ) : null}
                    {showCol.rate ? (
                      <td className="numsm mono">
                        {it.rate.toFixed(2)}
                      </td>
                    ) : null}
                    {showCol.disc ? (
                      <td className="numsm mono">
                        {it.discPct}
                      </td>
                    ) : null}
                    {showCol.gstPct ? (
                      <td className="numsm">
                        {<span className="gst-badge">{it.gstPct}%</span>}
                      </td>
                    ) : null}
                    {showCol.taxable ? (
                      <td className="numsm mono">{taxable.toFixed(2)}</td>
                    ) : null}
                    {showCol.gstAmt ? (
                      <td className="numsm mono">{gstAmt.toFixed(2)}</td>
                    ) : null}
                    {showCol.amount ? (
                      <td className="num mono">
                        {amount.toFixed(2)}
                      </td>
                    ) : null}
                    <td>
                      <div className="row-actions">
                        <button
                          type="button"
                          className="icon-btn del"
                          onClick={() => removeItem(i)}
                        >
                          ✕
                        </button>
                      </div>
                    </td>
                  </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </CappedTableWrap>
      </Panel>

      <Panel
        title="Purchase Summary"
        className="summary-panel purchase-summary"
        bare
      >
        <div className="summary-top">
          <div className="summary-editcol">
            <h3>Discount &amp; Payment</h3>
            <div className="sfield-row">
              <div className="sfield">
                <label>Overall Disc %</label>
                <input
                  className="mono"
                  value={tab.overallDiscPct}
                  data-nav-order={27}
                  data-nav-chain={NAV}
                  onChange={(e) => {
                    const pct = Number(e.target.value) || 0
                    const base = subtotal || 0
                    patchTab({
                      overallDiscPct: e.target.value,
                      overallDisc: String(
                        Math.round(((base * pct) / 100) * 100) / 100,
                      ),
                    })
                  }}
                />
              </div>
              <div className="sfield">
                <label>Overall Disc ₹</label>
                <input
                  className="mono"
                  value={tab.overallDisc}
                  data-nav-order={28}
                  data-nav-chain={NAV}
                  onChange={(e) =>
                    patchTab({ overallDisc: e.target.value })
                  }
                />
              </div>
              <div className="sfield">
                <label>Rounding</label>
                <input
                  className="mono"
                  value={tab.rounding}
                  data-nav-order={29}
                  data-nav-chain={NAV}
                  onChange={(e) =>
                    patchTab({
                      rounding: e.target.value,
                      roundingTouched: true,
                    })
                  }
                />
              </div>
              <div className="sfield">
                <label>Delivery / Exp.</label>
                <input
                  className="mono"
                  value={tab.delivery}
                  data-nav-order={30}
                  data-nav-chain={NAV}
                  onChange={(e) => patchTab({ delivery: e.target.value })}
                />
              </div>
              <div className="sfield">
                <label>Cash Paid</label>
                <input
                  ref={cashRef}
                  className="mono"
                  value={tab.cash}
                  data-nav-order={31}
                  data-nav-chain={NAV}
                  onChange={(e) => patchTab({ cash: e.target.value })}
                />
              </div>
              <div className="sfield">
                <label>Online Paid</label>
                <input
                  className="mono"
                  value={tab.online}
                  data-nav-order={32}
                  data-nav-chain={NAV}
                  data-nav-enter="save"
                  onChange={(e) => patchTab({ online: e.target.value })}
                />
              </div>
            </div>
            <div className="purchase-pay-actions">
              <ActionBtn
                label={importing ? 'Importing…' : 'Import Bill'}
                variant="neutral"
                kbd="Shift+F2"
                disabled={importing}
                onClick={openImportPicker}
              />
              <ActionBtn
                label="GST Slab"
                variant="neutral"
                kbd="F7"
                onClick={() => void openGstSlab()}
              />
              <ActionBtn
                label="Recalculate"
                variant="neutral"
                kbd="F8"
                onClick={() => void recalculatePurchase()}
              />
              <ActionBtn
                label="Clear"
                variant="neutral"
                kbd="F9"
                onClick={() => void clearForm()}
              />
              <ActionBtn
                label={saving ? 'Saving…' : 'Save Purchase'}
                variant="primary"
                kbd="F5"
                disabled={saving}
                onClick={() => void savePurchase()}
              />
            </div>
          </div>
          <div className="summary-totals">
            <div className="tot-row">
              <span>Gross</span>
              <strong className="mono">{money(gross || subtotal + (Number(summary.total_discount) || 0))}</strong>
            </div>
            <div className="tot-row">
              <span>Taxable</span>
              <strong className="mono">{money(subtotal)}</strong>
            </div>
            <div className="tot-row">
              <span>CGST</span>
              <strong className="mono">{money(cgst)}</strong>
            </div>
            <div className="tot-row">
              <span>SGST</span>
              <strong className="mono">{money(sgst)}</strong>
            </div>
            <div className="tot-row">
              <span>Total GST</span>
              <strong className="mono">{money(totalGst)}</strong>
            </div>
            <div className="tot-row">
              <span>Bill Total</span>
              <strong className="mono">{money(totalAmount)}</strong>
            </div>
            <div className="tot-row">
              <span>Final (incl. delivery)</span>
              <strong className="mono">{money(finalAmount)}</strong>
            </div>
            <div className="tot-row">
              <span>Previous Due</span>
              <strong className="mono">{money(tab.prevDue)}</strong>
            </div>
            <div className="tot-row">
              <span>Previous Credit</span>
              <strong className="mono">{money(tab.prevCredit)}</strong>
            </div>
            <div className="tot-row">
              <span>Need to Pay</span>
              <strong className="mono">{money(needToPay)}</strong>
            </div>
            <div className="tot-row">
              <span>Paid</span>
              <strong className="mono">{money(paid)}</strong>
            </div>
            <div className="tot-row accent">
              <span>Due</span>
              <strong className="mono">{money(due)}</strong>
            </div>
            {credit > 0 ? (
              <div className="tot-row">
                <span>Credit</span>
                <strong className="mono">{money(credit)}</strong>
              </div>
            ) : null}
          </div>
        </div>
        {tab.editingPurchaseId && tab.returnsNote ? (
          <div className="summary-actions" data-returns-note="1">
            <p className="note">{tab.returnsNote}</p>
          </div>
        ) : null}
        {note ? (
          <div className="summary-actions">
            <p className="status-ok">{note}</p>
          </div>
        ) : null}
      </Panel>
    </PageRoot>
  )
}
