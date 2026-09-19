import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { AppNavigate } from '../App'
import { ensureLocalEngine } from '../backend'
import {
  autosaveSalesBill,
  buildQuickSaleLine,
  buildSalesLine,
  calcSalesBill,
  discardAutosave,
  fetchMedicineBatches,
  fetchRecentSales,
  fetchSalesBillHint,
  fetchSalesForm,
  fetchSalesRuntimePrefs,
  listAutosaveSessions,
  lookupCustomerByName,
  loadLastSale,
  loadSaleById,
  printSalesBill,
  resumeAutosave,
  saveSalesBill,
  type CustomerDetail,
  type LoadedSale,
  type MedicineBatch,
  type SalesCalcResult,
  type SalesFormDefaults,
  type SalesLinePayload,
  type SalesRuntimePrefs,
} from '../pagesApi'
import { mutateContact } from '../settingsApi'
import { VILLAGES_CHANGED_EVENT, dispatchPaymentsChanged } from '../syncRefresh'
import { useLayoutRowCount } from '../layoutRows'
import {
  billDiscountLossMessages,
  billMargin,
  overallDiscFromPct,
  discountLossPrompt,
  itemDiscountLoss,
  lineMargin,
  type MarginLine,
} from '../salesMargin'
import {
  AlertDialog,
  QuickSaleDialog,
  RecentSalesDialog,
  RecoveredSalesDialog,
  type AlertState,
  type RecoveredSale,
} from './SalesDialogs'
import { TwoStepMedicinePicker } from './TwoStepMedicinePicker'
import { belowReturnedProblem } from './billLineRules'
import { ModernCombo } from './ModernCombo'
import { RowContextMenu } from './MedicineEditDialog'
import {
  SalesReturnDialog,
  isSalesReturnKey,
  type SalesReturnSaved,
} from './SalesReturnDialog'
import {
  ActionBtn,
  DocTabBar,
  DocToolsDialog,
  Field,
  Note,
  PageRoot,
  Panel,
  CappedTableWrap,
  ScheduleChip,
  SelectWrap,
  StatusLine,
} from './pageChrome'

type LineItem = SalesLinePayload & {
  medicine: string
  disc: number
  quick_add?: boolean
  id: number
}

type SaleTab = {
  id: string
  title: string
  customer: string
  customerId: number | null
  phone: string
  doctor: string
  doctorPhone: string
  address: string
  paymentMode: string
  billDate: string
  prevDue: number
  prevCredit: number
  items: LineItem[]
  overallDiscPct: string
  overallDisc: string
  rounding: string
  roundingTouched: boolean
  cash: string
  online: string
  editingSaleId: number | null
  autosaveSaleId: number | null
  autosaveToken: string | null
  editPrevDue: number | null
  dirty: boolean
  billNoHint: string
  /** A saved bill opened for edit: qty already returned per medicine id. */
  returnedByMed?: Record<string, number>
  returnsNote?: string
}

/** Two lines of one medicine (same id = same batch) as one: the qty added,
 *  the item discounts added and capped at the new base (classic add_medicine). */
function combineSalesLines(existing: LineItem, line: LineItem): LineItem {
  const qty2 = existing.qty + line.qty
  const disc2 = existing.medicine_discount + line.medicine_discount
  const base = Math.round(qty2 * existing.rate * 100) / 100
  const capped = Math.min(disc2, base)
  return {
    ...existing,
    qty: qty2,
    medicine_discount: capped,
    disc: capped,
    original_amount: base,
    amount: Math.round((base - capped) * 100) / 100,
  }
}

function money(n: number) {
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function shortBillNo(raw: string | undefined | null): string {
  return String(raw || '')
    .trim()
    .split('/FY')[0]
    .trim()
}

function isPlaceholderBillNo(raw: string | undefined | null): boolean {
  const v = shortBillNo(raw)
  return !v || v === '—' || v === '–' || v === '-' || v === '---' || v === '…'
}

/** Next invoice label: SCB12 → SCB13. */
function bumpSalesBillNo(billNo: string | undefined | null): string {
  const short = shortBillNo(billNo)
  const m = short.match(/^(SCB)(\d+)$/i)
  if (m) return `SCB${Number(m[2]) + 1}`
  return ''
}

function invoiceDisplay(
  hint: string | undefined | null,
  fallback?: string | undefined | null,
): string {
  if (!isPlaceholderBillNo(hint)) return shortBillNo(hint)
  if (!isPlaceholderBillNo(fallback)) return shortBillNo(fallback)
  return bumpSalesBillNo(fallback) || 'SCB1'
}

/** Same as core.calc_engine.auto_round — ± adjustment to nearest rupee (half-up). */
function autoRoundAdj(amount: number): number {
  const n = Math.round((Number(amount) || 0) * 100) / 100
  const nearest = Math.floor(n + 0.5)
  return Math.round((nearest - n) * 100) / 100
}
function parseTabletsPerStrip(unit: string | undefined): number {
  const m = String(unit || '').match(/(\d+)/)
  const n = m ? Number(m[1]) : 1
  return Number.isFinite(n) && n > 0 ? n : 1
}

/** Match Python is_strip_count_type — tablet/capsule/bolus sold per tablet. */
function isStripCountType(type: string | undefined, unit?: string): boolean {
  const t = String(type || '')
    .trim()
    .toLowerCase()
  const u = String(unit || '')
    .trim()
    .toLowerCase()
  if (['tablet', 'bolus', 'capsule'].includes(t)) return true
  if (['d', 'tab', 'tabs', 'tablet', 'tablets'].includes(u)) return true
  if (/\d/.test(u) && (t.includes('tablet') || t.includes('capsule') || t.includes('bolus'))) {
    return true
  }
  return Boolean(u && /\d+\s*[x×]/i.test(u))
}

/** Python display_mrp_per_unit — MRP of a single tablet when strip-counted. */
function displayMrpPerUnit(med: {
  mrp?: number
  list_mrp?: number
  type?: string
  unit?: string
}): number {
  const list = Number(med.list_mrp ?? med.mrp ?? 0) || 0
  if (isStripCountType(med.type, med.unit)) {
    const tps = parseTabletsPerStrip(med.unit)
    return Math.round((list / tps) * 100) / 100
  }
  return Math.round(list * 100) / 100
}

/** Purchase/cost rate per tablet (inventory rate ÷ tabs/strip). */
function displayCostPerUnit(med: {
  purchase_rate?: number
  type?: string
  unit?: string
}): number {
  const purchase = Number(med.purchase_rate ?? 0) || 0
  if (isStripCountType(med.type, med.unit)) {
    const tps = parseTabletsPerStrip(med.unit)
    return Math.round((purchase / tps) * 100) / 100
  }
  return Math.round(purchase * 100) / 100
}

/** Pack divisor for a line the engine did not stamp with margin_div. */
function packDivisor(med: MarginLine): number {
  const type = med.type ?? undefined
  const unit = med.unit ?? undefined
  return isStripCountType(type, unit) ? parseTabletsPerStrip(unit) : 1
}

function fmtMoney2(n: number) {
  return (Number(n) || 0).toFixed(2)
}

function newTab(
  n: number,
  billDate = '',
  hint = '',
  defaultVillage = '',
): SaleTab {
  return {
    id: `t-${Date.now()}-${n}`,
    title: `Sale ${n}`,
    customer: '',
    customerId: null,
    phone: '',
    doctor: '',
    doctorPhone: '',
    address: defaultVillage,
    paymentMode: 'Cash',
    billDate,
    prevDue: 0,
    prevCredit: 0,
    items: [],
    overallDiscPct: '0',
    overallDisc: '0',
    rounding: '0',
    roundingTouched: false,
    cash: '0',
    online: '0',
    editingSaleId: null,
    autosaveSaleId: null,
    autosaveToken: null,
    editPrevDue: null,
    dirty: false,
    billNoHint: hint,
  }
}

function lineFromPayload(line: SalesLinePayload & { quick_add?: boolean; id?: number | null }): LineItem {
  return {
    ...line,
    id: Number(line.id) || 0,
    medicine: line.name,
    disc: Number(line.medicine_discount) || 0,
    quick_add: Boolean(line.quick_add),
  }
}

/** Same shape as the engine's uuid4().hex. */
function newAutosaveToken(): string {
  const b = new Uint8Array(16)
  crypto.getRandomValues(b)
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
}

function tabPayload(tab: SaleTab) {
  return {
    customer_name: tab.customer,
    customer_phone: tab.phone,
    customer_address: tab.address,
    doctor_name: tab.doctor,
    doctor_phone: tab.doctorPhone,
    bill_date: tab.billDate,
    payment_mode: tab.paymentMode,
    cash_paid: Number(tab.cash) || 0,
    online_paid: Number(tab.online) || 0,
    discount_pct: Number(tab.overallDiscPct) || 0,
    discount_rs: Number(tab.overallDisc) || 0,
    rounding: Number(tab.rounding) || 0,
    auto_rounding: !tab.roundingTouched,
    previous_due: tab.prevDue,
    previous_credit: tab.prevCredit,
    editing_sale_id: tab.editingSaleId || undefined,
    autosave_sale_id: tab.autosaveSaleId || undefined,
    autosave_token: tab.autosaveToken || undefined,
    edit_previous_due: tab.editPrevDue ?? tab.prevDue,
    items: tab.items.map((it) => ({
      id: it.id || null,
      quick_add: it.quick_add || false,
      name: it.name,
      batch: it.batch,
      expiry: it.expiry,
      qty: it.qty,
      rate: it.rate,
      amount: it.amount,
      original_amount: it.original_amount,
      medicine_discount: it.medicine_discount,
      schedule: it.schedule,
      type: it.type,
      display_type: it.display_type,
      gst_percent: it.gst_percent,
      location: it.location,
      unit: it.unit,
      mrp: it.mrp,
      list_mrp: it.list_mrp ?? it.mrp,
      purchase_rate: it.purchase_rate,
    })),
  }
}

export function SalesPage({
  editSaleId = null,
  onEditConsumed,
  syncRefreshNonce = 0,
  active,
  focusNonce = 0,
  onNavigate,
}: {
  editSaleId?: number | null
  onEditConsumed?: () => void
  syncRefreshNonce?: number
  /** Required on purpose. Every page stays mounted once visited, so a page
   *  that does not know whether it is on screen keeps answering the
   *  keyboard from behind another one. An optional prop defaulting to true
   *  let exactly that omission through the compiler. */
  active: boolean
  focusNonce?: number
  /** Opens another page (Returns history, from the Sales Return popup). */
  onNavigate?: AppNavigate
}) {
  const [defaults, setDefaults] = useState<SalesFormDefaults | null>(null)
  const [prefs, setPrefs] = useState<SalesRuntimePrefs | null>(null)
  const [customers, setCustomers] = useState<CustomerDetail[]>([])
  const [selectedMedId, setSelectedMedId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [alert, setAlert] = useState<AlertState | null>(null)
  const [recentOpen, setRecentOpen] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [recentSales, setRecentSales] = useState<
    { id: number; bill_no: string; bill_date: string; customer: string; total: number }[]
  >([])
  const [quickOpen, setQuickOpen] = useState(false)
  // Sales Return popup (Alt+R). A return is made here, never a trip to Returns.
  const [returnOpen, setReturnOpen] = useState(false)
  const [returnSaleId, setReturnSaleId] = useState<number | null>(null)
  const returnOpenRef = useRef(false)
  returnOpenRef.current = returnOpen
  // Every page stays mounted; a hidden one must not keep a popup up over
  // whatever screen the shop moved to.
  useEffect(() => {
    if (!active) setReturnOpen(false)
  }, [active])
  // Unfinished sales this device is still holding a real bill open for.
  const [recovered, setRecovered] = useState<RecoveredSale[]>([])
  const [recoverBusy, setRecoverBusy] = useState('')
  const recoveryAsked = useRef(false)
  const [lineMenu, setLineMenu] = useState<{
    x: number
    y: number
    index: number
  } | null>(null)

  const [tabs, setTabs] = useState<SaleTab[]>([newTab(1)])
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
  const billingRows = useLayoutRowCount('billing_rows')

  const [medSearch, setMedSearch] = useState('')
  const [batch, setBatch] = useState('')
  // Empty, the way the classic billing screen has always created it
  // (ui/billing/billing_form.py:153 — and note the discount entry three lines
  // down DOES seed a '0', so the omission there is deliberate). A pre-filled
  // '1' plus a programmatic focus that does not select means the caret lands
  // after it and the next digit APPENDS: typing 4 in a hurry gives 14.
  const [qty, setQty] = useState('')
  const [disc, setDisc] = useState('0')
  const [rate, setRate] = useState('0')
  const [mrp, setMrp] = useState('0')
  const [calc, setCalc] = useState<SalesCalcResult | null>(null)
  // Guards against a stale calc response painting over a newer one.
  const calcSeqRef = useRef(0)

  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  // An index alone is not an identity. Deleting another line shifts every row
  // below it, and the pending Update then landed on whatever had moved into
  // that slot -- overwriting an innocent line with the medicine in the form.
  // Remember the line itself and find it again when the update is applied.
  const editingLineRef = useRef<LineItem | null>(null)
  const editingIdxRef = useRef<number | null>(null)

  /** An Edit is on its way in.
   *
   *  Derived from the prop rather than held in state on purpose: an effect runs
   *  AFTER paint, so a flag set there still lets one frame of the blank tab
   *  through -- and that blank frame is the whole complaint. The prop is
   *  already true on the first render of the navigation and is nulled by
   *  onEditConsumed when the load settles. */
  const editPending = Boolean(editSaleId)

  /** editPending, readable from the window keydown handlers, which capture an
   *  old render's closure. */
  const editPendingRef = useRef(false)
  editPendingRef.current = editPending

  editingIdxRef.current = editingIdx

  const qtyRef = useRef<HTMLInputElement | null>(null)
  const medRef = useRef<HTMLInputElement | null>(null)
  const cashRef = useRef<HTMLInputElement | null>(null)
  const tableRef = useRef<HTMLTableElement | null>(null)
  const tabRef = useRef(tab)
  const tabsRef = useRef(tabs)
  const activeRef = useRef(activeTab)
  const prefsRef = useRef(prefs)
  const contactDirtyRef = useRef({ phone: false, address: false })
  const customerLookupSeq = useRef(0)
  const customerNameRef = useRef<HTMLInputElement | null>(null)
  const [nameFocusTick, setNameFocusTick] = useState(0)
  tabRef.current = tab
  tabsRef.current = tabs
  activeRef.current = activeTab
  prefsRef.current = prefs

  const NAV = 'sales'

/** A sale is settled now or it is owed. The Cash and Online boxes on the total
 *  panel already record how the money arrived, and choosing Due zeroes both, so
 *  offering Online, UPI or Cheque here only ever duplicated that. */
const SALE_PAYMENT_MODES = ['Cash', 'Due']

  const focusNavOrder = useCallback((order: number) => {
    const el = document.querySelector<HTMLElement>(
      `[data-nav-chain="${NAV}"][data-nav-order="${order}"], [data-nav-chain="${NAV}"] [data-nav-order="${order}"]`,
    )
    // Prefer exact match inside sales page
    const scoped = document.querySelector<HTMLElement>(
      `.desktop-page[data-nav-chain="${NAV}"] [data-nav-order="${order}"]`,
    )
    const target = scoped || el
    target?.focus()
    if (target instanceof HTMLInputElement) {
      try {
        target.select()
      } catch {
        /* ignore */
      }
    }
  }, [])

  const focusCustomerName = useCallback(() => {
    const el =
      customerNameRef.current ||
      document.querySelector<HTMLInputElement>(
        `.desktop-page[data-nav-chain="${NAV}"] [data-nav-order="2"]`,
      )
    if (!el) return false
    el.focus()
    try {
      el.select()
    } catch {
      /* ignore */
    }
    return document.activeElement === el
  }, [])

  const requestCustomerNameFocus = useCallback(() => {
    setNameFocusTick((n) => n + 1)
  }, [])

  const patchTab = useCallback(
    (patch: Partial<SaleTab>, opts?: { quiet?: boolean; idx?: number }) => {
      const idx = opts?.idx ?? activeTab
      setTabs((all) =>
        all.map((t, i) =>
          i === idx
            ? {
                ...t,
                ...patch,
                dirty: opts?.quiet ? t.dirty : true,
              }
            : t,
        ),
      )
    },
    [activeTab],
  )

  const showAlert = useCallback((a: AlertState) => setAlert(a), [])

  /** Focus target after closing validation alerts (doctor / customer / etc.). */
  const focusAfterCode = useCallback(
    (code?: string | null) => {
      if (code === 'doctor_required') return () => focusNavOrder(5)
      if (code === 'customer_required') return () => focusNavOrder(2)
      if (code === 'payment_required') {
        // Classic does two things here and both matter at the counter: it puts
        // the cursor in the Cash entry, and it BLANKS a Cash box still reading
        // '0' so the field visibly demands an amount instead of looking already
        // answered (ui/billing/billing_form.py:965-971 and :1022-1030). Sending
        // focus to the payment-mode dropdown instead left the '0' sitting there
        // and told the counter nothing about what to do.
        return () => {
          const t = tabRef.current
          const zero = (v: string) => ['', '0', '0.0', '0.00'].includes((v || '').trim())
          if (zero(t.cash) && zero(t.online)) {
            patchTab({ cash: '', online: '' })
          }
          const el = cashRef.current
          if (el) {
            el.focus()
            try {
              el.select()
            } catch {
              /* ignore */
            }
            return
          }
          focusNavOrder(16)
        }
      }
      if (code === 'out_of_stock' || code === 'insufficient_stock') {
        return () => {
          const el = qtyRef.current
          if (el) {
            el.focus()
            try {
              el.select()
            } catch {
              /* ignore */
            }
            return
          }
          focusNavOrder(10)
        }
      }
      if (
        code === 'expired' ||
        code === 'batch_required' ||
        code === 'not_available_yet'
      ) {
        return () => focusNavOrder(8)
      }
      return undefined
    },
    [focusNavOrder],
  )

  const reserved = useMemo(() => {
    const map: Record<string, number> = {}
    for (const it of tab?.items || []) {
      if (!it.id) continue
      const k = String(it.id)
      map[k] = (map[k] || 0) + (Number(it.qty) || 0)
    }
    return map
  }, [tab?.items])

  const reloadMeta = useCallback(async () => {
    const [d, p] = await Promise.all([
      fetchSalesForm(),
      fetchSalesRuntimePrefs(),
    ])
    let hint = invoiceDisplay(d.next_bill_hint)
    if (isPlaceholderBillNo(d.next_bill_hint)) {
      try {
        const rec = await fetchRecentSales(1)
        const last = rec.sales?.[0]?.bill_no || ''
        hint = bumpSalesBillNo(last) || 'SCB1'
      } catch {
        hint = 'SCB1'
      }
    }
    const nextDefaults = { ...d, next_bill_hint: hint }
    setDefaults(nextDefaults)
    setPrefs(p)
    setCustomers(d.customer_details || [])
    setTabs((all) =>
      all.map((t) => ({
        ...t,
        billDate: t.billDate || d.form.bill_date || '',
        billNoHint: t.editingSaleId
          ? t.billNoHint
          : invoiceDisplay(t.billNoHint, hint),
        paymentMode: t.paymentMode || d.form.payment_mode || 'Cash',
        address: t.address || d.default_village || '',
      })),
    )
    return { d: nextDefaults, p }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError('')
      try {
        const engine = await ensureLocalEngine()
        if (!engine.ok) {
          if (!cancelled) setError(engine.error)
          return
        }
        await reloadMeta()
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [reloadMeta])

  useEffect(() => {
    // editPending too: inviting the counter to type into a form that is about
    // to be overwritten by the bill being loaded is how the typing vanished.
    if (!active || loading || editPending) return
    let cancelled = false
    const tryFocus = () => {
      if (cancelled) return
      focusCustomerName()
    }
    const t0 = window.requestAnimationFrame(tryFocus)
    const t1 = window.setTimeout(tryFocus, 50)
    const t2 = window.setTimeout(tryFocus, 180)
    const t3 = window.setTimeout(tryFocus, 400)
    return () => {
      cancelled = true
      window.cancelAnimationFrame(t0)
      window.clearTimeout(t1)
      window.clearTimeout(t2)
      window.clearTimeout(t3)
    }
  }, [active, loading, editPending, focusNonce, nameFocusTick, tab?.id, focusCustomerName])

  useEffect(() => {
    if (!syncRefreshNonce) return
    void reloadMeta()
  }, [syncRefreshNonce, reloadMeta])

  // The Invoice No box follows the Bill Date. A back-dated bill takes the next number
  // of THAT date's financial year, and the hint used to be today's whatever the date.
  // A bill that already has its number (editing, or autosaved) keeps showing it.
  const hintTabId = tab?.id
  const hintBillDate = tab?.billDate || ''
  const hintHasNumber = Boolean(tab?.editingSaleId || tab?.autosaveSaleId)
  useEffect(() => {
    if (!hintTabId || !hintBillDate || hintHasNumber) return
    let cancelled = false
    fetchSalesBillHint(hintBillDate)
      .then((r) => {
        if (cancelled || isPlaceholderBillNo(r.next_bill_hint)) return
        setTabs((all) =>
          all.map((t) =>
            t.id === hintTabId &&
            t.billDate === hintBillDate &&
            !t.editingSaleId &&
            !t.autosaveSaleId
              ? { ...t, billNoHint: shortBillNo(r.next_bill_hint) }
              : t,
          ),
        )
      })
      .catch(() => {
        /* the last hint stays; the bill still takes its real number at save */
      })
    return () => {
      cancelled = true
    }
  }, [hintTabId, hintBillDate, hintHasNumber])

  // Purchase has always done this; Sales never did, so the billing column,
  // margin and discount settings did not reach this screen until a bill was
  // saved or the app was restarted.
  useEffect(() => {
    const onLayout = () => void reloadMeta()
    window.addEventListener('satpuda:layout-config-changed', onLayout)
    return () =>
      window.removeEventListener('satpuda:layout-config-changed', onLayout)
  }, [reloadMeta])

  useEffect(() => {
    const onVillages = () => {
      void reloadMeta()
    }
    window.addEventListener(VILLAGES_CHANGED_EVENT, onVillages)
    return () => window.removeEventListener(VILLAGES_CHANGED_EVENT, onVillages)
  }, [reloadMeta])

  const onBatchPicked = useCallback(
    (b: MedicineBatch) => {
      setMedSearch(b.name)
      setSelectedMedId(b.id)
      setBatch(b.batch || '')
      // Show per-tablet cost & MRP for strip types (same as Python selected table).
      const strip = isStripCountType(b.type, b.unit)
      const tps = parseTabletsPerStrip(b.unit)
      const listMrp = Number(b.mrp) || 0
      const purchase = Number(b.rate) || 0
      setMrp(
        String(
          strip ? Math.round((listMrp / tps) * 100) / 100 : listMrp,
        ),
      )
      setRate(
        String(
          strip ? Math.round((purchase / tps) * 100) / 100 : purchase,
        ),
      )
      window.setTimeout(() => {
        qtyRef.current?.focus()
        // Select too, like every other focus route in the app (keyboard.ts
        // focusNav, focusNavOrder, the row-edit path). This one omission sat on
        // the single most-travelled path at the counter: pick medicine, single
        // batch, type quantity.
        try {
          qtyRef.current?.select()
        } catch {
          /* ignore */
        }
      }, 30)
    },
    [],
  )

  const loadBatches = useCallback(
    async (name: string) => {
      if (!name.trim()) return
      try {
        const res = await fetchMedicineBatches(name.trim(), {
          bill_date: tabRef.current.billDate,
          reserved,
        })
        const list = res.batches || []
        if (list.length === 1) {
          onBatchPicked(list[0])
        } else if (list.length > 1 && !selectedMedId) {
          // Keep name; user picks in two-step UI. Focus qty if already have selection.
          setMedSearch(name)
        }
      } catch (e) {
        showAlert({
          title: 'Medicine',
          message: e instanceof Error ? e.message : String(e),
          kind: 'error',
        })
      }
    },
    [reserved, showAlert, onBatchPicked, selectedMedId],
  )

  const clearMedicineFieldsRef = useRef(() => {})
  const clearMedicineFields = () => {
    // Clearing the fields also ends any row edit they were holding. Without
    // this the button stayed on Update and the next medicine added would have
    // overwritten the line that happened to be open.
    setEditingIdx(null)
    editingLineRef.current = null
    setMedSearch('')
    setBatch('')
    setSelectedMedId(null)
    setQty('')
    setDisc('0')
    setRate('0')
    setMrp('0')
  }
  clearMedicineFieldsRef.current = clearMedicineFields

  // Python calc preview
  useEffect(() => {
    if (!tab) return
    const t = window.setTimeout(() => {
      const seq = ++calcSeqRef.current
      void (async () => {
        if (!tab.items.length) {
          setCalc(null)
          return
        }
        try {
          const res = await calcSalesBill({
            // The preview does not need the customer's live balance; resolving
            // it Online costs six thousand-row server queries per keystroke.
            // The page already holds it from the customer lookup.
            skip_party_due: true,
            items: tab.items,
            discount_pct: Number(tab.overallDiscPct) || 0,
            discount_rs: Number(tab.overallDisc) || 0,
            rounding: Number(tab.rounding) || 0,
            auto_rounding: !tab.roundingTouched,
            cash_paid: Number(tab.cash) || 0,
            online_paid: Number(tab.online) || 0,
            payment_mode: tab.paymentMode,
            previous_due: tab.prevDue,
            previous_credit: tab.prevCredit,
            customer_id: tab.customerId || undefined,
            customer_name: tab.customer || undefined,
          })
          // Ignore an older reply that arrives after a newer one -- it would
          // paint the totals from the edit before last.
          if (seq !== calcSeqRef.current) return
          if (res.ok) {
            setCalc(res)
            if (!tab.roundingTouched) {
              const r = Number(res.rounding ?? 0)
              const shown = r.toFixed(2)
              if (shown !== tab.rounding) {
                patchTab({ rounding: shown }, { quiet: true })
              }
            }
            // Keep Previous Due in sync with live party balance (Classic / Purchase parity).
            if (
              !tab.editingSaleId &&
              typeof res.previous_due === 'number' &&
              (res.previous_due !== tab.prevDue ||
                (res.previous_credit || 0) !== tab.prevCredit)
            ) {
              patchTab(
                {
                  prevDue: res.previous_due,
                  prevCredit: res.previous_credit || 0,
                },
                { quiet: true },
              )
            }
          }
        } catch {
          /* ignore */
        }
      })()
    }, 200)
    return () => window.clearTimeout(t)
  }, [
    tab?.items,
    tab?.overallDiscPct,
    tab?.overallDisc,
    tab?.rounding,
    tab?.roundingTouched,
    tab?.cash,
    tab?.online,
    tab?.paymentMode,
    tab?.prevDue,
    tab?.prevCredit,
    tab?.customerId,
    patchTab,
    tab,
  ])

  // Autosave timer — OFF by default (prefs.autosave_enabled)
  useEffect(() => {
    if (!prefs?.autosave_enabled) return
    const ms = Math.max(30, prefs.autosave_interval_seconds || 120) * 1000
    const id = window.setInterval(() => {
      void (async () => {
        const cur = tabRef.current
        if (!cur?.dirty || !cur.items.length) return
        if (cur.editingSaleId && !cur.autosaveSaleId) return
        // The token is minted HERE and put on the tab before the write. When
        // the engine minted it, the tab only learned it once the write came
        // back: an F7 in that window sent no token and made a second bill,
        // and a tab switch meanwhile landed this tab's bill on another tab.
        let token = cur.autosaveToken
        if (!token) {
          const minted = newAutosaveToken()
          const idx = activeRef.current
          token = minted
          tabRef.current = { ...cur, autosaveToken: minted }
          setTabs((all) =>
            all.map((t, i) =>
              i === idx && !t.autosaveToken ? { ...t, autosaveToken: minted } : t,
            ),
          )
        }
        const mine = token
        try {
          const res = await autosaveSalesBill(
            tabPayload({ ...cur, autosaveToken: mine }),
          )
          if (res.ok && res.autosave_sale_id) {
            setTabs((all) =>
              all.map((t) =>
                t.autosaveToken === mine
                  ? {
                      ...t,
                      autosaveSaleId: res.autosave_sale_id!,
                      autosaveToken: res.autosave_token || t.autosaveToken,
                      dirty: false,
                      billNoHint: res.bill_no || t.billNoHint,
                      title: res.bill_no || t.title,
                    }
                  : t,
              ),
            )
            // Autosave writes a REAL bill now, and every later tick updates
            // that same one -- so say the bill number, not "draft".
            setNote(
              res.counter
                ? `Autosaved into today's counter bill ${res.bill_no || ''}`.trim()
                : `Autosaved bill ${res.bill_no || res.autosave_sale_id}`,
            )
          } else if (!res.ok && res.code === 'bill_discarded' && res.error) {
            // The refused bill from this tab was discarded in Settings → Sync: nothing more
            // is saved from it, and the tab says so instead of failing quietly on every tick.
            setNote(res.error)
          }
        } catch {
          /* ignore */
        }
      })()
    }, ms)
    return () => window.clearInterval(id)
  }, [prefs?.autosave_enabled, prefs?.autosave_interval_seconds])

  // ── Unfinished sales ────────────────────────────────────────────────────
  //
  // Autosave writes a REAL bill on the first tick and updates that same bill
  // after that, so "which bill does this tab own" is money — and it used to
  // live only in this component's state. Close the app, crash, or lose power
  // mid-sale and the tab came back empty while a genuine Rs 20 bill sat on the
  // customer's account: the operator retyped the sale and the customer was
  // billed twice, for stock that moved twice.
  //
  // So the tab does not remember. It ASKS the engine, which holds the durable
  // session record on disk (core/autosave_session.py). That is also why this
  // is not a localStorage key: a cleared profile would lose it, a second
  // machine could not see it, and the Tk billing page — same engine, same
  // bills — could not read it at all.
  useEffect(() => {
    if (!defaults || recoveryAsked.current) return
    recoveryAsked.current = true
    void (async () => {
      try {
        const res = await listAutosaveSessions()
        if (!res.ok || !res.sessions?.length) return
        // A session a live tab already owns is not lost.
        const held = new Set(
          tabsRef.current
            .map((t) => t.autosaveToken || '')
            .filter((x) => x),
        )
        const loose = res.sessions.filter((sess) => !held.has(sess.token))
        if (!loose.length) return
        // Yesterday's leftovers first: they are the ones nobody is expecting.
        loose.sort((a, b) => Number(b.stale) - Number(a.stale))
        setRecovered(loose)
      } catch {
        /* an engine that cannot answer will be asked again next open */
      }
    })()
  }, [defaults])

  /** Put a recovered sale back on screen, still owning its bill.
   *
   *  The token comes back with it, and that is the whole trick: the next tick
   *  (and the save) is an UPDATE of the bill that already exists, and for a
   *  counter sale the token is what lets the engine subtract this form's own
   *  earlier lines out of the shared day bill before putting the current ones
   *  in. Resuming without it is what bills the day twice. */
  const resumeRecovered = async (rec: RecoveredSale) => {
    setRecoverBusy(rec.token)
    try {
      const res = await resumeAutosave(rec.token, rec.sale_id)
      if (!res.ok || !res.form) {
        setRecovered((all) =>
          // Unreadable for now, or refused by the server and waiting on Settings → Sync:
          // either way the sale is still recorded and must stay on this list.
          res.code === 'unavailable' || res.code === 'refused'
            ? all
            : all.filter((r) => r.token !== rec.token),
        )
        showAlert({
          title: 'Unfinished Sale',
          message:
            res.error ||
            'That sale could not be reopened. Check it in History before billing it again.',
          kind: res.code === 'unavailable' || res.code === 'refused' ? 'warning' : 'error',
        })
        return
      }
      const f = res.form
      const restored: SaleTab = {
        ...newTab(
          tabsRef.current.length + 1,
          f.bill_date || defaults?.form.bill_date || '',
          res.bill_no || '',
          defaults?.default_village || '',
        ),
        title: res.bill_no || rec.bill_no || 'Recovered',
        customer: f.customer_name,
        customerId: f.customer_id ?? null,
        phone: f.customer_phone,
        address: f.customer_address || defaults?.default_village || '',
        doctor: f.doctor_name,
        doctorPhone: f.doctor_phone || '',
        paymentMode: f.payment_mode || 'Cash',
        billDate: f.bill_date || defaults?.form.bill_date || '',
        prevDue: f.previous_due || 0,
        prevCredit: f.previous_credit || 0,
        items: (f.items || []).map((it) => lineFromPayload(it)),
        overallDiscPct: String(f.discount_pct ?? 0),
        overallDisc: String(f.discount_rs ?? 0),
        rounding: String(f.rounding ?? 0),
        roundingTouched: true,
        cash: String(f.cash_paid ?? 0),
        online: String(f.online_paid ?? 0),
        autosaveSaleId: res.autosave_sale_id ?? rec.sale_id,
        autosaveToken: res.autosave_token || rec.token,
        billNoHint: res.bill_no || rec.bill_no || '',
        // Not dirty: nothing has changed since the engine last wrote it, and a
        // tick that rewrites an identical bill is pure noise.
        dirty: false,
      }
      let landedOn = 0
      setTabs((all) => {
        const blankIdx = all.findIndex(
          (t) => !t.items.length && !t.customer.trim() && !t.autosaveSaleId,
        )
        if (blankIdx >= 0) {
          landedOn = blankIdx
          return all.map((t, i) => (i === blankIdx ? restored : t))
        }
        landedOn = all.length
        return [...all, restored]
      })
      setActiveTab(landedOn)
      setRecovered((all) => all.filter((r) => r.token !== rec.token))
      setNote(
        rec.counter
          ? `Resumed your part of today's counter bill ${res.bill_no || ''}`.trim()
          : `Resumed unfinished bill ${res.bill_no || rec.sale_id}`,
      )
    } catch (e) {
      showAlert({
        title: 'Unfinished Sale',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setRecoverBusy('')
    }
  }

  /** Take a recovered sale back off the books — bill, stock and balance.
   *
   *  A counter sale gives back only the lines THIS form put in the day bill;
   *  the rest of the day's counter sales stay exactly where they are. */
  const discardRecovered = (rec: RecoveredSale) => {
    showAlert({
      title: 'Discard Unfinished Sale',
      message:
        `${rec.held ? 'Unsaved sale' : `Bill ${rec.bill_no || rec.sale_id}`} — ${rec.items} item(s), ` +
        `₹${(Number(rec.total) || 0).toFixed(2)}.\n\n` +
        (rec.held
          ? 'Only the kept form is cleared. No bill was saved, so no stock or balance moves.'
          : rec.counter
            ? "Only these lines come out of today's counter bill. The rest of the day's counter sales stay."
            : 'The bill is removed and the stock and the customer balance are put back.'),
      kind: 'confirm',
      confirmLabel: 'Discard',
      cancelLabel: 'Keep',
      onConfirm: () => {
        void (async () => {
          setRecoverBusy(rec.token)
          try {
            const out = await discardAutosave(rec.sale_id, rec.token)
            // The engine says why (a bill the server refused is retried or discarded in
            // Settings → Sync, not here).
            if (!out.ok) throw new Error(out.error || 'The bill could not be reversed.')
            setRecovered((all) => all.filter((r) => r.token !== rec.token))
            setNote(`Discarded unfinished bill ${rec.bill_no || rec.sale_id}`)
          } catch (e) {
            showAlert({
              title: 'Discard Unfinished Sale',
              message:
                (e instanceof Error ? e.message : String(e)) +
                ' Nothing was changed — the sale is still recorded.',
              kind: 'error',
            })
          } finally {
            setRecoverBusy('')
          }
        })()
      },
    })
  }

  const applyCustomerBalance = useCallback(
    async (
      name: string,
      opts?: { force?: boolean; replaceContact?: boolean },
    ) => {
      const trimmed = name.trim()
      const force = opts?.force === true
      const replaceContact = opts?.replaceContact === true
      if (!trimmed) {
        patchTab({
          customer: '',
          customerId: null,
          prevDue: 0,
          prevCredit: 0,
        })
        return
      }
      if (replaceContact) {
        contactDirtyRef.current = { phone: false, address: false }
      }
      const seq = ++customerLookupSeq.current
      const local = customers.find(
        (x) => x.name.toLowerCase() === trimmed.toLowerCase(),
      )
      const fallbackVillage = (defaults?.default_village || '').trim()
      const duePatch = {
        customer: local?.name || trimmed,
        customerId: local?.id ?? null,
        prevDue: Number(local?.due) || 0,
        prevCredit: Number(local?.credit) || 0,
      }
      if (replaceContact) {
        patchTab({
          ...duePatch,
          phone: contactDirtyRef.current.phone
            ? tabRef.current?.phone || ''
            : local?.phone || '',
          address: contactDirtyRef.current.address
            ? tabRef.current?.address || ''
            : local?.address || fallbackVillage,
        })
      } else {
        patchTab(duePatch)
      }
      if (!force) return
      try {
        const res = await lookupCustomerByName(trimmed, true)
        if (seq !== customerLookupSeq.current) return
        if (!res.found || !res.customer) {
          if (!local) {
            patchTab({
              customer: trimmed,
              customerId: null,
              prevDue: 0,
              prevCredit: 0,
            })
          }
          return
        }
        const c = res.customer
        const contactPatch: Partial<SaleTab> = {
          customer: c.name || trimmed,
          customerId: c.id ?? null,
          prevDue: Number(c.due) || 0,
          prevCredit: Number(c.credit) || 0,
        }
        if (replaceContact && !contactDirtyRef.current.phone) {
          contactPatch.phone = c.phone || ''
        }
        if (replaceContact && !contactDirtyRef.current.address) {
          contactPatch.address = c.address || fallbackVillage
        }
        patchTab(contactPatch)
        setCustomers((all) => {
          const idx = all.findIndex(
            (x) =>
              x.id === c.id ||
              x.name.toLowerCase() === (c.name || '').toLowerCase(),
          )
          if (idx < 0) return [...all, c]
          const next = all.slice()
          next[idx] = { ...next[idx], ...c }
          return next
        })
      } catch {
        /* keep optimistic local balance */
      }
    },
    [customers, defaults?.default_village, patchTab],
  )

  const onPickCustomer = (name: string) => {
    void applyCustomerBalance(name, { force: true, replaceContact: true })
  }

  const rememberVillage = useCallback(async (raw: string) => {
    const text = raw.trim()
    if (!text) return
    setDefaults((d) => {
      if (!d) return d
      if ((d.villages || []).some((v) => v.toUpperCase() === text.toUpperCase())) {
        return d
      }
      return { ...d, villages: [...(d.villages || []), text] }
    })
    try {
      await mutateContact({
        kind: 'village',
        action: 'add',
        name: text,
        fields: { name: text },
      })
    } catch {
      /* keep the local dropdown row even if the save fails */
    }
  }, [])

  const openSalesReturn = () => {
    // A return always belongs to a SAVED bill. When this tab is editing one
    // the popup opens with it loaded; otherwise on its bill search. The owner
    // asked for this on the Sales screen itself -- no trip to Returns and back.
    setReturnSaleId(tabRef.current?.editingSaleId || null)
    setReturnOpen(true)
  }

  /** A return changed this customer's due / credit: show the new figures on
   *  every open bill for them. Quietly -- a balance refresh is not an edit, and
   *  marking a saved bill dirty would make its next print save it again. */
  const afterSalesReturn = async (r: SalesReturnSaved) => {
    dispatchPaymentsChanged('customer')
    const cur = tabRef.current
    if (cur?.editingSaleId && Number(cur.editingSaleId) === r.saleId) {
      setNote(
        `Return ${r.returnNo} saved against this bill — open the bill again before changing it.`,
      )
    }
    const name = r.customer.trim()
    if (!name) return
    try {
      const res = await lookupCustomerByName(name, true)
      const c = res.found ? res.customer : null
      if (!c) return
      const due = Number(c.due) || 0
      const credit = Number(c.credit) || 0
      const lower = (c.name || name).trim().toLowerCase()
      setTabs((all) =>
        all.map((t) =>
          !t.editingSaleId &&
          ((c.id != null && t.customerId === c.id) ||
            t.customer.trim().toLowerCase() === lower)
            ? { ...t, prevDue: due, prevCredit: credit }
            : t,
        ),
      )
      setCustomers((all) => {
        const idx = all.findIndex(
          (x) => x.id === c.id || x.name.toLowerCase() === lower,
        )
        if (idx < 0) return all
        const next = all.slice()
        next[idx] = { ...next[idx], ...c }
        return next
      })
    } catch {
      /* the popup already showed the saved return; the next pick fetches it */
    }
  }

  const clearForm = async (opts?: { keepTab?: boolean; hint?: string }) => {
    const cur = tabRef.current
    if (cur?.autosaveSaleId || cur?.autosaveToken) {
      try {
        await discardAutosave(cur.autosaveSaleId || 0, cur.autosaveToken || '')
      } catch {
        /* ignore */
      }
    }
    contactDirtyRef.current = { phone: false, address: false }
    // With no fresh number from a save, leave the hint empty so Invoice No
    // follows defaults.next_bill_hint, which reloadMeta() below refreshes.
    const nextHint = opts?.hint
      ? invoiceDisplay(opts.hint, defaults?.next_bill_hint)
      : ''
    const blank = newTab(
      activeTab + 1,
      defaults?.form.bill_date || '',
      nextHint,
      defaults?.default_village || '',
    )
    // Keep the tab's own name ("Sale 2") across a Clear, but never a bill
    // number. A tab that was editing a bill, or had autosaved one, carries that
    // bill's number as its title -- keeping it made the fresh bill after Clear
    // look like the old one.
    const keepTitle =
      Boolean(cur?.title) &&
      !cur?.editingSaleId &&
      !cur?.autosaveSaleId &&
      /^Sale \d+$/.test(cur?.title || '')
    blank.title = keepTitle ? cur!.title : blank.title
    blank.dirty = false
    setTabs((all) => all.map((t, i) => (i === activeTab ? blank : t)))
    setCalc(null)
    setNote('')
    clearMedicineFields()
    requestCustomerNameFocus()
    // Clear is "start a new bill without saving anything": fetch the real
    // next number instead of trusting what was on screen.
    if (!opts?.hint) void reloadMeta()
    if (!opts?.keepTab) {
      /* form cleared in place */
    }
  }

  const applyLoadedSale = (loaded: LoadedSale) => {
    if (!loaded.ok || !loaded.form) {
      showAlert({
        title: 'Load Sale',
        message: loaded.error || 'Could not load sale.',
        kind: 'error',
      })
      return
    }
    const f = loaded.form
    const items = (f.items || []).map((it) => lineFromPayload(it))
    patchTab({
      customer: f.customer_name,
      customerId: f.customer_id ?? null,
      phone: f.customer_phone,
      address: f.customer_address,
      doctor: f.doctor_name,
      doctorPhone: f.doctor_phone || '',
      billDate: f.bill_date,
      paymentMode: f.payment_mode || 'Cash',
      cash: String(f.cash_paid ?? 0),
      online: String(f.online_paid ?? 0),
      overallDiscPct: String(f.discount_pct ?? 0),
      overallDisc: String(f.discount_rs ?? 0),
      rounding: String(f.rounding ?? 0),
      roundingTouched: true,
      prevDue: f.previous_due || 0,
      prevCredit: f.previous_credit || 0,
      items,
      editingSaleId: loaded.editing_sale_id ?? null,
      autosaveSaleId: loaded.autosave_sale_id ?? null,
      autosaveToken: null,
      returnedByMed: loaded.returned_by_medicine || {},
      returnsNote: loaded.returns_note || '',
      editPrevDue: loaded.edit_payment_snapshot?.previous_due ?? f.previous_due,
      billNoHint: loaded.bill_no || '',
      title: loaded.bill_no || tab.title,
      dirty: false,
    })
    setNote(`Loaded ${loaded.bill_no || loaded.sale_id} for edit`)
  }

  useEffect(() => {
    if (!editSaleId) return
    let cancelled = false
    void (async () => {
      try {
        // No ensureLocalEngine() here: App boot already proved the engine, and
        // History proved it again a moment ago. It only added a round trip in
        // front of the fetch -- and when it failed, Edit was a silent no-op.
        const loaded = await loadSaleById(editSaleId)
        if (cancelled) return
        applyLoadedSale(loaded)
      } catch (e) {
        if (!cancelled) {
          showAlert({
            title: 'Load Sale',
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
  }, [editSaleId])

  const addItem = async () => {
    const name = medSearch.trim()
    if (!name) {
      // Nothing typed yet, so there is nothing to complain about. Adding a
      // medicine clears the form and returns focus after a beat; an Enter
      // pressed in that gap used to land here and throw up a warning about a
      // field the shop had just finished with. Send them back to typing.
      focusNavOrder(8)
      return
    }
    const medId = selectedMedId
    if (!medId) {
      showAlert({
        title: 'Batch Required',
        message: 'Please select a medicine batch (↓ then Enter in the list).',
        kind: 'warning',
        focusAfterClose: () => focusNavOrder(8),
      })
      return
    }
    try {
      const res = await buildSalesLine({
        medicine_id: medId,
        qty: Math.max(0, Number(qty) || 0),
        medicine_discount: Number(disc) || 0,
        bill_date: tab.billDate,
        doctor_name: tab.doctor,
        customer_name: tab.customer,
        reserved,
      })
      if (!res.ok || !res.line) {
        const title =
          res.code === 'doctor_required'
            ? 'Doctor Required'
            : res.code === 'customer_required'
              ? 'Missing Information'
              : res.code === 'expired'
                ? 'Expired Medicine'
                : res.code === 'not_available_yet'
                  ? 'Not Available'
                  : res.code === 'out_of_stock' || res.code === 'insufficient_stock'
                    ? 'Stock'
                    : 'Cannot Add'
        showAlert({
          title,
          message: res.error || 'Could not add medicine.',
          kind: 'warning',
          focusAfterClose: focusAfterCode(res.code),
        })
        return
      }
      if (res.warnings?.length) {
        showAlert({
          title: 'Margin Warning',
          message: `${res.warnings.join('\n')}\n\nAdd this medicine anyway?`,
          kind: 'confirm',
          confirmLabel: 'Add',
          onConfirm: () => {
            const line = lineFromPayload(res.line!)
            mergeOrPushLine(line, true)
          },
        })
        return
      }
      mergeOrPushLine(lineFromPayload(res.line))
    } catch (e) {
      showAlert({
        title: 'Error',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }

  const mergeOrPushLine = (line: LineItem, lossConfirmed = false) => {
    const prev = tabRef.current.items
    const existing = line.id ? prev.find((m) => m.id === line.id) : null
    let next: LineItem[]
    if (!existing) {
      next = [...prev, line]
    } else {
      const merged = combineSalesLines(existing, line)
      // Classic checks the MERGED line (billing_form.add_medicine, test_med):
      // the discount now covers qty2 units, so its margin is not the old one.
      const loss =
        lossConfirmed || prefs?.billing_margin_loss_warning === false
          ? null
          : itemDiscountLoss(merged, packDivisor)
      if (loss) {
        showAlert({
          title: 'Margin Warning',
          message: discountLossPrompt([loss]),
          kind: 'confirm',
          confirmLabel: 'Add',
          onConfirm: () => mergeOrPushLine(line, true),
        })
        return
      }
      next = prev.map((m) => (m.id === line.id ? merged : m))
    }
    patchTab({ items: next })
    clearMedicineFields()
    // Wait for cleared value to commit so focus opens the empty medicine list.
    window.setTimeout(() => medRef.current?.focus(), 40)
  }

  /** A saved bill with returns against it: no line may go below what was
   *  returned, nor be removed (the refund was paid on those units). True when
   *  `next` was refused -- the counter has been told why. */
  const returnsBlock = (next: LineItem[]): boolean => {
    const cur = tabRef.current
    const why = belowReturnedProblem(
      next,
      cur.editingSaleId ? cur.returnedByMed : undefined,
      (l) => l.id,
      (l) => String(l.name || l.medicine || ''),
      cur.items,
    )
    if (!why) return false
    showAlert({ title: 'Returned on this bill', message: why, kind: 'warning' })
    return true
  }

  const removeLineAt = (idx: number) => {
    const next = tabRef.current.items.filter((_, j) => j !== idx)
    if (returnsBlock(next)) return
    patchTab({ items: next })
  }

  const startLineEdit = (idx: number) => {
    const it = tabRef.current.items[idx]
    if (!it) return
    setEditingIdx(idx)
    editingLineRef.current = it
    // The line goes back into the fields it was typed into, the way the classic
    // billing screen has always done it, so every value is edited in one place
    // instead of in a cramped cell inside the table.
    setMedSearch(String(it.name || it.medicine || ''))
    setBatch(String(it.batch || ''))
    setSelectedMedId(it.id ?? null)
    setRate(String(it.rate ?? '0'))
    setMrp(String(it.mrp ?? '0'))
    setQty(String(it.qty))
    const storedRs = Number(it.medicine_discount || it.disc || 0)
    if (prefs?.item_discount_mode === 'percent') {
      const base0 = Math.round(Number(it.qty || 0) * Number(it.rate || 0) * 100) / 100
      const pct = base0 > 0 ? Math.round((storedRs / base0) * 10000) / 100 : 0
      setDisc(String(pct))
    } else {
      setDisc(String(storedRs))
    }
    window.setTimeout(() => {
      qtyRef.current?.focus()
      qtyRef.current?.select()
    }, 30)
  }

  const cancelLineEditRef = useRef(() => {})
  const cancelLineEdit = () => {
    setEditingIdx(null)
    editingLineRef.current = null
    clearMedicineFields()
    const rows = tableRef.current?.querySelectorAll<HTMLElement>('tbody tr[tabindex="0"]')
    if (rows && editingIdx != null && rows[editingIdx]) {
      rows[editingIdx].focus()
    }
  }

  cancelLineEditRef.current = cancelLineEdit

  const commitLineEdit = async () => {
    if (editingIdx === null) return
    const marker = editingLineRef.current
    const items = tabRef.current.items
    const idx = marker ? items.indexOf(marker) : editingIdx
    const it = idx >= 0 ? items[idx] : undefined
    if (!it) {
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
    const newQty = Math.max(0, Math.floor(Number(qty) || 0))
    const discInput = Math.max(0, Number(disc) || 0)
    if (newQty === 0) {
      const next = tabRef.current.items.filter((_, j) => j !== idx)
      if (returnsBlock(next)) return
      patchTab({ items: next })
      setEditingIdx(null)
      medRef.current?.focus()
      return
    }
    const targetMedId = selectedMedId ?? it.id
    if ((it.quick_add || !it.id) && !selectedMedId) {
      const base = Math.round(newQty * it.rate * 100) / 100
      const asRs =
        prefs?.item_discount_mode === 'percent'
          ? Math.round(((base * discInput) / 100) * 100) / 100
          : discInput
      const capped = Math.min(asRs, base)
      patchTab({
        items: tabRef.current.items.map((m, j) =>
          j === idx
            ? {
                ...m,
                qty: newQty,
                medicine_discount: capped,
                disc: capped,
                original_amount: base,
                amount: Math.round((base - capped) * 100) / 100,
              }
            : m,
        ),
      })
      setEditingIdx(null)
      return
    }
    // Reserved excluding this line — Python stock check via build-line
    const reservedMap: Record<string, number> = {}
    for (const [j, row] of tabRef.current.items.entries()) {
      if (j === idx || !row.id) continue
      const k = String(row.id)
      reservedMap[k] = (reservedMap[k] || 0) + (Number(row.qty) || 0)
    }
    try {
      const res = await buildSalesLine({
        medicine_id: targetMedId,
        qty: newQty,
        medicine_discount: discInput,
        bill_date: tabRef.current.billDate,
        doctor_name: tabRef.current.doctor,
        customer_name: tabRef.current.customer,
        reserved: reservedMap,
        // Same batch: keep the GST % this line was sold at. build-line would
        // otherwise price it at the medicine's rate today, and saving an edited
        // old bill rewrote its GST.
        ...(targetMedId === it.id && it.gst_percent != null
          ? { gst_percent: Number(it.gst_percent) }
          : {}),
      })
      if (!res.ok || !res.line) {
        showAlert({
          title: 'Cannot Update',
          message: res.error || 'Invalid quantity.',
          kind: 'warning',
        })
        return
      }
      const line = lineFromPayload(res.line)
      const finish = () => {
        // The same medicine (same id = same batch) is ONE line on a bill: an
        // edit that lands on another line's medicine adds to that line.
        const all = tabRef.current.items
        const dupAt = line.id ? all.findIndex((m, j) => j !== idx && m.id === line.id) : -1
        const next =
          dupAt >= 0
            ? all.flatMap((m, j) =>
                j === idx ? [] : j === dupAt ? [combineSalesLines(m, line)] : [m],
              )
            : all.map((m, j) => (j === idx ? line : m))
        if (returnsBlock(next)) return
        patchTab({ items: next })
        setEditingIdx(null)
        clearMedicineFields()
        window.setTimeout(() => {
          const rows =
            tableRef.current?.querySelectorAll<HTMLElement>('tbody tr[tabindex="0"]')
          rows?.[Math.min(idx, (rows.length || 1) - 1)]?.focus()
        }, 30)
      }
      // Classic's edit (billing_form.edit_quantity) re-validates the WHOLE bill
      // with the overall discount after the new qty / discount, on the margin
      // recomputed from cost. The edit used to go through with no warning.
      const lossMsgs =
        prefs?.billing_margin_loss_warning === false
          ? []
          : billDiscountLossMessages(
              tabRef.current.items.map((m, j) => (j === idx ? line : m)),
              tabRef.current.overallDisc,
              packDivisor,
            )
      if (lossMsgs.length) {
        showAlert({
          title: 'Margin Warning',
          message: discountLossPrompt(lossMsgs),
          kind: 'confirm',
          confirmLabel: 'Update',
          onConfirm: finish,
        })
        return
      }
      finish()
    } catch (e) {
      showAlert({
        title: 'Error',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }

  const saveSales = async (confirmLoss = false): Promise<boolean> => {
    if (savingRef.current) return false
    // The keyboard listener is on window, so F5 fires even while the form is
    // still the blank tab waiting for the bill being edited to arrive. Saving
    // there wrote an empty bill over a real one.
    if (editPendingRef.current) return false
    // A line still open for editing has its new values only in the form. Saving
    // now wrote the bill with the OLD values while the screen showed the new
    // ones, so the change looked applied and was not.
    if (editingIdxRef.current !== null) {
      showAlert({
        title: 'Finish the line first',
        message:
          'A medicine is open for editing. Press Enter on the last field to '
          + 'update it, or Escape to leave it unchanged, then save.',
        kind: 'warning',
      })
      return false
    }
    savingRef.current = true
    setSaving(true)
    // Finishing a cash bill without splitting the payment means the whole
    // amount was taken in cash. Nothing ever set this flag, so every such bill
    // was refused on the first press -- the counter typed the amount and saved
    // again, which is exactly the "not saved until saved a second time" report.
    // The engine works the figure out itself (see save_sale's pay_full branch):
    // the screen's total comes from a debounced preview and a discount typed a
    // moment earlier would not be in it yet.
    if (wantsPayFull()) payFullRef.current = true
    // The flag belongs to THIS save only. A held or repeated Enter, or a save
    // the engine refuses, must not leave the next bill marked paid in full.
    const payFull = payFullRef.current
    payFullRef.current = false
    try {
      const res = await saveSalesBill({
        ...tabPayload(tabRef.current),
        pay_full: payFull,
        confirm_discount_loss: confirmLoss,
      })
      if (res.need_confirm) {
        showAlert({
          title: 'Discount Loss',
          message: `${(res.messages || [res.error]).join('\n')}\n\nSave anyway?`,
          kind: 'confirm',
          confirmLabel: 'Save',
          onConfirm: () => {
            void saveSales(true)
          },
        })
        return false
      }
      if (!res.ok) {
        const title =
          res.code === 'doctor_required'
            ? 'Doctor Required'
            : res.code === 'customer_required'
              ? 'Missing Information'
              : res.code === 'payment_required'
                ? 'Payment Required'
                : res.code === 'profile_required'
                  ? 'Setup Required'
                  : 'Save Sales'
        showAlert({
          title,
          message: res.error || 'Save failed.',
          kind: 'warning',
          focusAfterClose: focusAfterCode(res.code),
        })
        return false
      }
      // res.pdf_path is never set on save -- the PDF is written on a thread
      // that reports to nobody -- so this line showed nothing for years.
      const savedTo = res.pdf_dir || ''
      setNote(
        `Saved ${res.bill_no}${res.merged_counter ? ' (merged counter sale)' : ''}${
          savedTo ? ` · Bill folder: ${savedTo}` : ''
        }`,
      )
      // Say it plainly. Enter now finishes the bill, so the counter needs to
      // see that it landed -- otherwise the same sale gets rung up twice.
      showAlert({
        title: 'Bill Saved',
        message:
          `Bill ${res.bill_no} saved.` +
          (res.merged_counter ? ' Merged into the counter sale.' : '') +
          (savedTo ? `\n\nSaved to:\n${savedTo}` : '') +
          // Saved regardless; only pointed out.
          (res.warnings?.length ? `\n\nPlease check:\n• ${res.warnings.join('\n• ')}` : ''),
        kind: res.warnings?.length ? 'warning' : 'info',
        focusAfterClose: () => focusNavOrder(2),
      })
      const nextHint = invoiceDisplay(
        res.next_bill_hint,
        bumpSalesBillNo(res.bill_no) || defaults?.next_bill_hint,
      )
      if (defaults) {
        setDefaults({ ...defaults, next_bill_hint: nextHint })
      }
      // The autosave session was closed by the save itself. Let go of our
      // handle BEFORE clearing, so clearForm cannot aim a discard at the bill
      // that was just saved.
      patchTab({ autosaveSaleId: null, autosaveToken: null })
      await clearForm({ hint: nextHint })
      requestCustomerNameFocus()
      try {
        await reloadMeta()
      } catch {
        /* ignore */
      }
      return true
    } catch (e) {
      showAlert({
        title: 'Error',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
      return false
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }

  const printSlot = async (slot: number, confirmLoss = false) => {
    if (savingRef.current) return
    savingRef.current = true
    setSaving(true)
    // Same arming as saveSales. Printing SAVES the bill first, so a cash bill
    // with nothing typed in Cash or Online was refused with "Cash or Online
    // amount must be greater than zero" on the first F7 / F8 / Print — the
    // original complaint, still true on every print path after the Save path
    // was fixed.
    if (wantsPayFull()) payFullRef.current = true
    const payFull = payFullRef.current
    payFullRef.current = false
    try {
      // Reprinting a bill opened for Edit used to save it again first, every
      // time -- an in-place UPDATE, so no duplicate, but a stock and dues
      // recompute and a server push for a bill that had not changed. Save only
      // when there is something to save; otherwise print the bill as it stands.
      const printTab = tabRef.current
      const needsSave = !printTab.editingSaleId || printTab.dirty
      const res = await printSalesBill({
        ...tabPayload(printTab),
        sale_id: printTab.editingSaleId || undefined,
        pay_full: payFull,
        slot,
        save_first: needsSave,
        mode: 'slot', // same as Tk F7/F8 → print_bill_with_slot (dot-matrix via Settings)
        confirm_discount_loss: confirmLoss,
      })
      if (res.need_confirm) {
        showAlert({
          title: 'Discount Loss',
          message: `${(res.messages || [res.error]).join('\n')}\n\nSave & print anyway?`,
          kind: 'confirm',
          confirmLabel: 'Print',
          onConfirm: () => {
            void printSlot(slot, true)
          },
        })
        return
      }
      if (!res.ok) {
        // In Online mode a new bill is QUEUED, not written locally: the save
        // returns a negative temporary id, so the print step cannot look it up
        // and fails with not_found. The bill IS saved. Leaving the form dirty
        // here meant the counter pressed F7 again and queued a SECOND sale
        // under a fresh uuid -- a duplicate bill with stock deducted twice.
        // Clear the form so that cannot happen, and say where the bill is.
        // The bill was SAVED whenever the server hands back a bill number or a
        // real sale id -- only the printing failed. Pressing F7 again would save
        // a SECOND bill and deduct stock twice, so clear the form and send them
        // to History to reprint. (not_found covers the older queued path where
        // no bill number comes back at all.)
        const savedButNotPrinted =
          res.code === 'not_found' ||
          Boolean((res as { bill_no?: string }).bill_no) ||
          Number((res as { sale_id?: number }).sale_id || 0) > 0
        if (savedButNotPrinted) {
          const billNo = (res as { bill_no?: string }).bill_no
          showAlert({
            title: billNo ? `Bill ${billNo} saved \u2014 print from History` : 'Bill saved \u2014 print from History',
            message:
              (billNo
                ? `Bill ${billNo} was saved successfully, but it could not be printed.`
                : 'The bill was saved, but it could not be printed yet.') +
              '\n\nDo NOT bill it again. Open Sales History and print it from there.' +
              (res.error ? `\n\nReason: ${res.error}` : ''),
            kind: 'warning',
          })
          await clearForm({})
          requestCustomerNameFocus()
          return
        }
        const title =
          res.code === 'doctor_required'
            ? 'Doctor Required'
            : res.code === 'customer_required'
              ? 'Missing Information'
              : res.code === 'payment_required'
                ? 'Payment Required'
                : 'Print Bill'
        showAlert({
          title,
          message: res.error || 'Print failed.',
          kind: 'warning',
          focusAfterClose: focusAfterCode(res.code),
        })
        return
      }
      const slotPrefs =
        slot === 1 ? prefsRef.current?.print_slot_1 : prefsRef.current?.print_slot_2
      const via = (res as { dot_matrix?: boolean }).dot_matrix
        ? 'dot-matrix'
        : 'PDF/printer'
      setNote(
        `Saved ${res.bill_no} · ${slotPrefs?.label || `Print ${slot}`} (${via})` +
          (prefsRef.current?.upi_qr_enabled ? ' · UPI QR from Settings' : ''),
      )
      // A long bill is split across sheets and pdf_path is only the LAST one,
      // so naming it read as "the first pages were not saved". Show the folder
      // whenever the file is not obviously the whole bill.
      const multiSheet = /_p\d+\.pdf$/i.test(res.pdf_path || '')
      const printedTo = res.dot_matrix
        ? ''
        : (multiSheet ? res.pdf_dir : res.pdf_path) || res.pdf_dir || ''
      showAlert({
        title: 'Bill Saved',
        message:
          `Bill ${res.bill_no} saved and sent to ${slotPrefs?.label || `Print ${slot}`}.` +
          // A dot-matrix slip goes straight to the printer and leaves no file,
          // so naming one would be a lie.
          (printedTo ? `\n\nSaved to:\n${printedTo}` : '') +
          (res.warnings?.length ? `\n\nPlease check:\n• ${res.warnings.join('\n• ')}` : ''),
        kind: res.warnings?.length ? 'warning' : 'info',
        focusAfterClose: () => focusNavOrder(2),
      })
      const nextHint = invoiceDisplay(
        res.next_bill_hint,
        bumpSalesBillNo(res.bill_no) || defaults?.next_bill_hint,
      )
      // The autosave session was closed by the save itself. Let go of our
      // handle BEFORE clearing, so clearForm cannot aim a discard at the bill
      // that was just saved.
      patchTab({ autosaveSaleId: null, autosaveToken: null })
      await clearForm({ hint: nextHint })
      requestCustomerNameFocus()
      try {
        await reloadMeta()
      } catch {
        /* ignore */
      }
    } catch (e) {
      showAlert({
        title: 'Print Bill',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }

  const silentReprint = async () => {
    const last = await loadLastSale()
    if (!last.ok || !last.sale_id) {
      showAlert({
        title: 'Reprint',
        message: last.error || 'No previous sale found.',
        kind: 'warning',
      })
      return
    }
    try {
      const res = await printSalesBill({
        sale_id: last.sale_id,
        bill_no: last.bill_no,
        slot: 1,
        mode: 'silent', // same as Tk F9 → print_bill_silent_with_slot
        save_first: false,
      })
      if (!res.ok) {
        showAlert({ title: 'Reprint', message: res.error || 'Failed', kind: 'error' })
        return
      }
      const via = (res as { dot_matrix?: boolean }).dot_matrix
        ? 'dot-matrix'
        : 'silent PDF'
      setNote(`Reprinted ${res.bill_no || last.bill_no} (${via}, Print Sales 1)`)
    } catch (e) {
      showAlert({
        title: 'Reprint',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }

  const openRecent = async () => {
    try {
      const res = await fetchRecentSales(5)
      setRecentSales(res.sales || [])
      setRecentOpen(true)
    } catch (e) {
      showAlert({
        title: 'Recent Sales',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }

  const openLast = async () => {
    try {
      const loaded = await loadLastSale()
      applyLoadedSale(loaded)
    } catch (e) {
      showAlert({
        title: 'Last Bill',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }

  const addTab = () => {
    setTabs((all) => [
      ...all,
      newTab(
        all.length + 1,
        defaults?.form.bill_date || '',
        defaults?.next_bill_hint || '',
        defaults?.default_village || '',
      ),
    ])
    setActiveTab(tabs.length)
    clearMedicineFields()
  }

  const closeTab = async (index?: number) => {
    const all = tabsRef.current
    if (all.length <= 1) return
    const idx = index ?? activeRef.current
    const cur = all[idx]
    if (cur?.dirty) {
      showAlert({
        title: 'Close Tab',
        message: 'This sale has unsaved changes. Close anyway?',
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
    if (cur?.autosaveSaleId || cur?.autosaveToken) {
      try {
        await discardAutosave(cur.autosaveSaleId || 0, cur.autosaveToken || '')
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
    if (index === activeRef.current) clearMedicineFields()
  }

  const actionsRef = useRef({
    markPayFull: () => {},
    offerCashThenFocus: async () => {},
    addItem: async () => {},
    addOrUpdateItem: async () => {},
    saveSales: async (_c?: boolean) => false as boolean,
    printSlot: async (_s: number, _c?: boolean) => {},
    clearForm: async () => {},
    loadBatches: async (_n: string) => {},
    runConfiguredEnter: (
      _k: 'cash_online_enter_action' | 'due_rounding_enter_action',
    ) => {},
  })

  /** Should this save tell the engine the whole bill was paid in cash?
   *
   *  True only when the shop finished with Enter on a Cash bill and typed
   *  nothing into Cash or Online. The AMOUNT is deliberately not computed here:
   *  the total on screen comes from a debounced preview, so a rounding or
   *  discount typed a moment before Enter is not in it yet, and banking that
   *  figure would leave a phantom credit or a due against someone who had paid
   *  in full. The engine works the total out from the same lines at save time. */
  const payFullRef = useRef(false)

  /** Ask the engine what this bill comes to, right now. */
  const freshPayable = async (): Promise<number> => {
    const t = tabRef.current
    if (!t.items.length) return 0
    try {
      const res = await calcSalesBill({
        skip_party_due: true,
        items: t.items,
        discount_pct: Number(t.overallDiscPct) || 0,
        discount_rs: Number(t.overallDisc) || 0,
        rounding: Number(t.rounding) || 0,
        auto_rounding: !t.roundingTouched,
        cash_paid: 0,
        online_paid: 0,
        payment_mode: t.paymentMode,
        previous_due: t.prevDue,
        previous_credit: t.prevCredit,
        customer_id: t.customerId || undefined,
        customer_name: t.customer || undefined,
      })
      if (res.ok) return Number(res.summary?.total_amount ?? 0) || 0
    } catch {
      /* fall through -- the shop types the amount itself */
    }
    return 0
  }
  // Enter now finishes the bill, and a key held down or pressed twice fired the
  // save twice -- two identical bills, the stock gone twice.
  const savingRef = useRef(false)
  // Classic warns as the overall discount is typed (billing_form
  // _on_disc_pct_change / _on_disc_rs_change -> validate_bill_discounts, a
  // warning whose answer is not used). A dialog per keystroke would steal the
  // field here, so the same check runs once the edited value leaves the field.
  const overallDiscAtFocusRef = useRef<string | null>(null)
  const overallDiscFocus = () => {
    overallDiscAtFocusRef.current = tabRef.current.overallDisc
  }
  const overallDiscBlur = (next: EventTarget | null) => {
    const before = overallDiscAtFocusRef.current
    overallDiscAtFocusRef.current = null
    const t = tabRef.current
    if (before === null || before === t.overallDisc) return
    if (prefs?.billing_margin_loss_warning === false) return
    const msgs = billDiscountLossMessages(t.items, t.overallDisc, packDivisor)
    if (!msgs.length) return
    showAlert({
      title: 'Margin Warning',
      message: discountLossPrompt(msgs, ''),
      kind: 'warning',
      focusAfterClose: next instanceof HTMLElement ? () => next.focus() : undefined,
    })
  }
  const wantsPayFull = () => {
    const t = tabRef.current
    if (t.paymentMode.toLowerCase() === 'due') return false
    return (Number(t.cash) || 0) + (Number(t.online) || 0) <= 0
  }

  const runConfiguredEnter = useCallback(
    (settingKey: 'cash_online_enter_action' | 'due_rounding_enter_action') => {
      const action =
        (settingKey === 'cash_online_enter_action'
          ? prefsRef.current?.cash_online_enter_action
          : prefsRef.current?.due_rounding_enter_action) || 'save_bill'
      if (action === 'print_slot_1') {
        void actionsRef.current.printSlot(1)
      } else if (action === 'print_slot_2') {
        void actionsRef.current.printSlot(2)
      } else {
        void actionsRef.current.saveSales(false)
      }
    },
    [],
  )

  actionsRef.current = {
    // saveSales sets this itself now; kept so the nav/Enter chain can still
    // arm it explicitly ahead of a save.
    markPayFull: () => {
      payFullRef.current = wantsPayFull()
    },
    offerCashThenFocus: async () => {
      const t = tabRef.current
      if (t.paymentMode.toLowerCase() === 'due') return
      if ((Number(t.cash) || 0) + (Number(t.online) || 0) <= 0) {
        const payable = await freshPayable()
        if (payable > 0) {
          const cash = payable.toFixed(2)
          tabRef.current = { ...tabRef.current, cash }
          patchTab({ cash })
        }
      }
      focusNavOrder(16)
    },
    addItem,
    addOrUpdateItem: async () => {
      if (editingIdx !== null) await commitLineEdit()
      else await addItem()
    },
    saveSales,
    printSlot,
    clearForm,
    loadBatches,
    runConfiguredEnter,
  }

  // Enter-field actions from data-nav-enter / data-nav-action
  useEffect(() => {
    const onNav = (e: Event) => {
      const ce = e as CustomEvent<{ action?: string }>
      const action = ce.detail?.action
      if (!action) return
      const a = actionsRef.current
      if (action === 'add') {
        void a.addOrUpdateItem()
        return
      }
      if (action === 'save') {
        void a.saveSales(false)
        return
      }
      if (action === 'print1') {
        void a.printSlot(1)
        return
      }
      if (action === 'print2') {
        void a.printSlot(2)
        return
      }
      if (action === 'clear') {
        void a.clearForm()
        return
      }
      if (action === 'online-enter') {
        if (tabRef.current.paymentMode.toLowerCase() !== 'due') {
          a.runConfiguredEnter('cash_online_enter_action')
        }
        return
      }
      if (action === 'rounding-enter') {
        // Rounding is the last figure the shop touches, so Enter there finishes
        // the bill -- save or print, whichever the settings say. Cash and Online
        // stay there to be typed into when a bill really is split.
        if (tabRef.current.paymentMode.toLowerCase() === 'due') {
          a.runConfiguredEnter('due_rounding_enter_action')
          return
        }
        // Fill Cash with what is owed and stop there. The shop can change it,
        // move on to Online, and the bill is saved by Enter on Online.
        void a.offerCashThenFocus()
        return
      }
      if (action === 'med-next') {
        void (async () => {
          const a = actionsRef.current
          const name = (
            document.querySelector(
              `.desktop-page[data-nav-chain="${NAV}"] [data-nav-order="8"]`,
            ) as HTMLInputElement | null
          )?.value?.trim()
          if (!name) {
            focusNavOrder(13)
            return
          }
          await a.loadBatches(name)
          focusNavOrder(10)
        })()
        return
      }
      if (action === 'phone-enter') {
        focusNavOrder(4)
        return
      }
      if (action === 'customer-enter') {
        const name = (
          document.querySelector(
            `.desktop-page[data-nav-chain="${NAV}"] [data-nav-order="2"]`,
          ) as HTMLInputElement | null
        )?.value?.trim()
        if (name) {
          void applyCustomerBalance(name, { force: true, replaceContact: true })
        }
        focusNavOrder(3)
      }
    }
    document.addEventListener('satpuda-nav-action', onNav)
    return () => document.removeEventListener('satpuda-nav-action', onNav)
  }, [focusNavOrder, applyCustomerBalance])

  // Shortcuts — match classic billing registry
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

      // The Sales Return popup answers its own keys (F5 save, F6 clear, Esc
      // close); this page stands down until it closes, or F5 would save the
      // bill behind it.
      if (returnOpenRef.current) return
      if (isSalesReturnKey(e)) {
        // Another popup is asking something: the key belongs to it.
        if (document.querySelector('.modal-backdrop')) return
        e.preventDefault()
        openSalesReturn()
        return
      }
      if (key === 'Escape' && editingIdxRef.current !== null) {
        e.preventDefault()
        cancelLineEditRef.current()
        return
      }
      if (key === 'F2') {
        e.preventDefault()
        const rows = tableRef.current?.querySelectorAll<HTMLElement>(
          'tbody tr[tabindex="0"]',
        )
        if (rows && rows.length) {
          rows[0].focus()
        }
        return
      }
      if (key === 'F5' && shift) {
        e.preventDefault()
        void a.clearForm()
        return
      }
      if (key === 'F5') {
        e.preventDefault()
        void a.saveSales(false)
        return
      }
      if (key === 'F6') {
        e.preventDefault()
        focusNavOrder(13)
        return
      }
      if (key === 'F7') {
        e.preventDefault()
        void a.printSlot(1)
        return
      }
      if (key === 'F8') {
        e.preventDefault()
        void a.printSlot(2)
        return
      }
      if (key === 'F9') {
        e.preventDefault()
        void silentReprint()
        return
      }
      if (key === 'F10') {
        e.preventDefault()
        void openRecent()
        return
      }
      if (key === 'F11') {
        e.preventDefault()
        void openLast()
        return
      }
      if (key === 'F12') {
        e.preventDefault()
        setToolsOpen((open) => !open)
        return
      }
      if (key === 'F3' && !ctrl && !shift) {
        e.preventDefault()
        addTab()
        return
      }
      if (key === 'F4' && !ctrl && !shift) {
        e.preventDefault()
        void closeTab()
        return
      }
      if (key === 'Insert' && !ctrl && !shift) {
        e.preventDefault()
        setQuickOpen(true)
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
      if (ctrl && !shift && (key === 'p' || key === 'P')) {
        e.preventDefault()
        void a.printSlot(1)
        return
      }
      if (ctrl && shift && (key === 'c' || key === 'C')) {
        e.preventDefault()
        void a.clearForm()
        return
      }
      if (ctrl && shift && (key === 'n' || key === 'N')) {
        e.preventDefault()
        addTab()
        return
      }
      if (ctrl && shift && (key === 'w' || key === 'W')) {
        e.preventDefault()
        void closeTab()
        return
      }
      if (ctrl && shift && (key === 'm' || key === 'M')) {
        e.preventDefault()
        setQuickOpen(true)
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
  })

  if (!tab) return null

  const dueMode = tab.paymentMode.toLowerCase() === 'due'
  const subtotal =
    calc?.summary.subtotal ?? tab.items.reduce((s, it) => s + it.amount, 0)
  const preRound =
    calc?.summary.pre_round_total ??
    Math.round((subtotal - (Number(tab.overallDisc) || 0)) * 100) / 100
  const roundingAdj = tab.roundingTouched
    ? Number(tab.rounding) || 0
    : typeof calc?.rounding === 'number'
      ? calc.rounding
      : autoRoundAdj(preRound)
  const total =
    calc?.summary.total_amount ??
    Math.round((preRound + roundingAdj) * 100) / 100
  const marginAsPct = prefs?.billing_margin_display_mode === 'percent'
  // Classic's total margin (margin_utils.total_net_margin / total_margin_percent),
  // recomputed from each line's cost every render: gross - item discounts - the
  // OVERALL discount in rupees, over the list-MRP value. Adding up the margins
  // frozen when each line was built left the overall discount out entirely.
  const billMarginNow = billMargin(tab.items, tab.overallDisc, packDivisor)
  const marginTotalRs = billMarginNow.rs
  const marginTotalPct = billMarginNow.pct
  const marginTotal = marginAsPct ? marginTotalPct : marginTotalRs
  const marginColLabel = marginAsPct ? 'Margin %' : 'Margin ₹'
  const discColLabel =
    prefs?.item_discount_mode === 'percent' ? 'Disc %' : 'Disc ₹'
  const showItemDisc = prefs?.billing_show_item_discount !== false
  const showLocation = Boolean(prefs?.show_location)
  const colOn = (key: string) => {
    const vis = prefs?.column_visibility
    if (!vis || !(key in vis)) return true
    return Boolean(vis[key])
  }
  const showColRaw = {
    medicine: colOn('Medicine'),
    batch: colOn('Batch'),
    expiry: colOn('Expiry'),
    qty: colOn('Qty'),
    type: colOn('Type'),
    rate: true, // cost rate — not in Layout keys; keep when MRP shown
    mrp: colOn('MRP'),
    disc: showItemDisc && colOn('Disc ₹'),
    margin: prefs == null || prefs.billing_show_margin_column !== false,
    amount: colOn('Amount'),
    schedule: colOn('Schedule'),
    location: showLocation && colOn('Location'),
  }
  // Same rule the engine uses for every other list (get_visible_columns
  // returns everything when the selection is empty): unticking all of them is
  // not a request for a blank table.
  const showCol = Object.values(showColRaw).some(Boolean)
    ? showColRaw
    : (Object.fromEntries(
        Object.keys(showColRaw).map((k) => [k, true]),
      ) as typeof showColRaw)
  // Rate mirrors MRP visibility so cost stays paired with sell rate.
  showCol.rate = showCol.mrp
  const totalPaid =
    calc?.payment.amount_paid ??
    (Number(tab.cash) || 0) + (Number(tab.online) || 0)
  const billDue = calc?.payment.due_amount ?? Math.max(0, total - totalPaid)
  /** What the customer still has to hand over, credit counted.
   *
   *  The engine's `total_due` clamps a customer's standing credit at their
   *  previous due, so someone holding Rs 500 credit with nothing owing was
   *  still shown the whole new bill and the credit never came off anything.
   *  `net_total_due` is the same figure with the credit spent; it is identical
   *  whenever there is no spare credit. The stored bookkeeping is untouched --
   *  the credit is consumed by the ledger, not by a write (see calc_engine).
   *
   *  The fallback matters too: `tab.prevDue + billDue` ignored prevCredit, so
   *  the old wrong number flashed back on every first paint and every in-flight
   *  preview. */
  const totalDue =
    calc?.payment.net_total_due ??
    calc?.payment.total_due ??
    Math.max(0, tab.prevDue - (tab.prevCredit || 0) + billDue)
  const creditApplied = calc?.payment.credit_applied ?? 0

  const gstPcts = tab.items
    .map((it) => Number(it.gst_percent) || 0)
    .filter((n) => n > 0)
  const gstUnique = [...new Set(gstPcts)]
  const gstLabel = !gstPcts.length
    ? 'No GST'
    : gstUnique.length === 1
      ? `${gstUnique[0]}% · in MRP`
      : 'Mixed · in MRP'

  const slot1 = prefs?.print_slot_1
  const slot2 = prefs?.print_slot_2
  const slotBtnLabel = (
    slot: { label?: string; paper_size?: string; copies?: number } | undefined,
    fallback: string,
    paperFallback: string,
    copiesFallback: number,
  ) => {
    const paper = slot?.paper_size || paperFallback
    const copies = Number(slot?.copies ?? copiesFallback) || copiesFallback
    return (
      <span className="print-btn-text">
        <span>{slot?.label || fallback}</span>
        <span className="print-btn-meta">
          {paper} · {copies} {copies === 1 ? 'copy' : 'copies'}
        </span>
      </span>
    )
  }

  return (
    <PageRoot
      navChain={NAV}
      className={`bill-page${editPending ? ' is-edit-loading' : ''}`}
    >
      <StatusLine
        error={error}
        loading={(loading && !defaults) || editPending}
      />
      {/* Before AlertDialog on purpose: AlertDialog mounts its portal only
          when an alert exists, so it lands later in <body> and stacks above
          this list -- the discard confirmation must not open behind it. */}
      <RecoveredSalesDialog
        open={recovered.length > 0}
        sales={recovered}
        busy={recoverBusy}
        onResume={(rec) => void resumeRecovered(rec)}
        onDiscard={discardRecovered}
        onClose={() => setRecovered([])}
      />
      <AlertDialog alert={alert} onClose={() => setAlert(null)} />
      <SalesReturnDialog
        open={returnOpen}
        initialSaleId={returnSaleId}
        onClose={() => {
          setReturnOpen(false)
          window.setTimeout(() => medRef.current?.focus(), 0)
        }}
        onSaved={(r) => void afterSalesReturn(r)}
        onOpenReturnsPage={
          onNavigate
            ? () => {
                setReturnOpen(false)
                onNavigate('returns', { returnsTab: 'sales' })
              }
            : undefined
        }
      />
      <RecentSalesDialog
        open={recentOpen}
        sales={recentSales}
        onClose={() => setRecentOpen(false)}
        onPick={(id) => {
          setRecentOpen(false)
          void loadSaleById(id).then(applyLoadedSale)
        }}
      />
      <QuickSaleDialog
        open={quickOpen}
        types={prefs?.medicine_types || []}
        packDefaults={prefs?.medicine_type_pack_defaults}
        onClose={() => setQuickOpen(false)}
        onSubmit={(data) => {
          void (async () => {
            try {
              const res = await buildQuickSaleLine({
                name: data.name,
                type: data.type,
                batch: data.batch,
                pack_size: data.pack_size,
                qty: Number(data.qty) || 0,
                rate: data.rate,
                mrp: data.mrp,
                schedule: data.schedule,
                doctor_name: tab.doctor,
              })
              if (!res.ok || !res.line) {
                // The doctor rule lives in the engine now. This screen used to
                // keep its own copy — "any schedule needs a doctor" — which knew
                // nothing about the shop's setting and nothing about H1 and X
                // being the two that always do.
                const doctorRequired = res.code === 'doctor_required'
                showAlert({
                  title: doctorRequired ? 'Doctor Required' : 'Add No Stock',
                  message: res.error || 'Failed',
                  kind: 'warning',
                  focusAfterClose: doctorRequired
                    ? focusAfterCode(res.code)
                    : undefined,
                })
                return
              }
              mergeOrPushLine(lineFromPayload(res.line))
              setQuickOpen(false)
              focusNavOrder(8)
            } catch (e) {
              showAlert({
                title: 'Add No Stock',
                message: e instanceof Error ? e.message : String(e),
                kind: 'error',
              })
            }
          })()
        }}
      />

      <DocToolsDialog
        open={toolsOpen}
        title="Sales tabs & tools"
        onClose={() => setToolsOpen(false)}
        tabs={tabs.map((t) => t.title + (t.dirty ? ' •' : ''))}
        active={activeTab}
        addLabel="New sale"
        onSelect={(i) => {
          setActiveTab(i)
          setToolsOpen(false)
        }}
        onAdd={() => {
          addTab()
          setToolsOpen(false)
        }}
        onCloseTab={(i) => void closeTab(i)}
        hint="F5 save · F9 reprint last · F3 new tab · F4 close tab"
        tools={[
          {
            label: 'New sale',
            detail: 'Open another sale tab',
            kbd: 'F3',
            onClick: () => {
              addTab()
              setToolsOpen(false)
            },
          },
          {
            label: 'Close this sale',
            detail: 'Last tab cannot be closed',
            kbd: 'F4',
            disabled: tabs.length <= 1,
            onClick: () => {
              void closeTab()
              setToolsOpen(false)
            },
          },
          {
            label: 'Recent bills',
            detail: 'Pick a recent sale to load',
            kbd: 'F10',
            onClick: () => {
              setToolsOpen(false)
              void openRecent()
            },
          },
          {
            label: 'Last bill',
            detail: 'Load the last saved sale',
            kbd: 'F11',
            onClick: () => {
              setToolsOpen(false)
              void openLast()
            },
          },
          {
            label: 'Add no stock',
            detail: 'Quick line without inventory',
            kbd: 'Insert',
            variant: 'warning',
            onClick: () => {
              setToolsOpen(false)
              setQuickOpen(true)
            },
          },
        ]}
      />


      {/* The list of open bills lives on the page now, not only inside the
          Tabs & Tools modal. Creating a bill and switching to one used to cost
          two interactions each, with the form covered in between. */}
      <DocTabBar
        tabs={tabs.map((t) => t.title + (t.dirty ? ' •' : ''))}
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

      <Panel title="Customer & Medicine" className="sales-header-panel">
        <div className="form-grid">
          {prefs?.payment_mode_enabled !== false &&
          String(prefs?.payment_mode_position || 'first') === 'first' ? (
            <Field label="Payment">
              <SelectWrap>
                <select
                  data-nav-order={1}
                  data-nav-chain={NAV}
                  value={tab.paymentMode}
                  onChange={(e) => {
                    const mode = e.target.value
                    if (mode.toLowerCase() === 'due') {
                      patchTab({ paymentMode: mode, cash: '0', online: '0' })
                    } else {
                      patchTab({ paymentMode: mode })
                    }
                  }}
                  onKeyDown={(e) => {
                    const k = e.key.toLowerCase()
                    if (k === 'd') {
                      e.preventDefault()
                      patchTab({ paymentMode: 'Due', cash: '0', online: '0' })
                    }
                    if (k === 'c') {
                      e.preventDefault()
                      patchTab({ paymentMode: 'Cash' })
                      cashRef.current?.focus()
                    }
                  }}
                >
                  {SALE_PAYMENT_MODES.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </SelectWrap>
            </Field>
          ) : null}
          <Field label="Customer Name">
            <ModernCombo
              value={tab.customer}
              navOrder={2}
              navChain={NAV}
              placeholder="Empty = Counter Sale"
              minChars={0}
              filterLocal
              listLabel="Customers"
              items={customers.map((c, i) => ({
                id: c.id != null ? String(c.id) : `name:${c.name}:${i}`,
                label: c.name,
                meta: c.phone || undefined,
              }))}
              onChange={(v) =>
                patchTab({ customer: v, customerId: null })
              }
              onPick={(it) => onPickCustomer(it.label)}
              onEnter={() => focusNavOrder(3)}
              inputRef={customerNameRef}
            />
          </Field>
          <Field label="Phone">
            <input
              type="tel"
              inputMode="numeric"
              className="mono"
              data-nav-order={3}
              data-nav-chain={NAV}
              data-nav-enter="phone-enter"
              value={tab.phone}
              onChange={(e) => {
                contactDirtyRef.current.phone = true
                patchTab({
                  phone: e.target.value.replace(/\D/g, ''),
                })
              }}
              onBlur={() => {
                const name = tabRef.current?.customer?.trim()
                if (name) void applyCustomerBalance(name, { force: true })
              }}
            />
          </Field>
          <Field label="Address (Village)">
            <ModernCombo
              value={tab.address}
              navOrder={4}
              navChain={NAV}
              placeholder="Village / address"
              minChars={0}
              filterLocal
              listLabel="Villages"
              items={(defaults?.villages || []).map((v) => ({
                id: v,
                label: v,
              }))}
              onChange={(v) => {
                contactDirtyRef.current.address = true
                patchTab({ address: v })
              }}
              onPick={(it) => {
                contactDirtyRef.current.address = true
                patchTab({ address: it.label })
                void rememberVillage(it.label)
              }}
              onEnter={() => {
                void rememberVillage(tabRef.current?.address || '')
                focusNavOrder(5)
              }}
              onBlur={() => {
                void rememberVillage(tabRef.current?.address || '')
              }}
            />
          </Field>
          {prefs?.payment_mode_enabled !== false &&
          String(prefs?.payment_mode_position || 'first') !== 'first' ? (
            <Field label="Payment">
              <SelectWrap>
                <select
                  data-nav-order={1}
                  data-nav-chain={NAV}
                  value={tab.paymentMode}
                  onChange={(e) => {
                    const mode = e.target.value
                    if (mode.toLowerCase() === 'due') {
                      patchTab({ paymentMode: mode, cash: '0', online: '0' })
                    } else {
                      patchTab({ paymentMode: mode })
                    }
                  }}
                >
                  {SALE_PAYMENT_MODES.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </SelectWrap>
            </Field>
          ) : null}
          <Field label="Previous Due">
            <input
              className="due-val mono"
              type="text"
              value={
                tab.prevCredit > 0
                  ? `Credit: ${money(tab.prevCredit)}`
                  : money(tab.prevDue)
              }
              readOnly
              tabIndex={-1}
            />
          </Field>
          <Field label="Doctor Name" optional>
            <ModernCombo
              value={tab.doctor}
              navOrder={5}
              navChain={NAV}
              placeholder="Doctor name"
              minChars={0}
              filterLocal
              listLabel="Doctors"
              items={(defaults?.doctors || []).map((d) => ({
                id: d,
                label: d,
              }))}
              onChange={(v) => patchTab({ doctor: v })}
              onPick={(it) => patchTab({ doctor: it.label })}
              onEnter={() => focusNavOrder(6)}
            />
          </Field>
          <Field label="Doctor Phone">
            <input
              type="text"
              className="mono"
              data-nav-order={6}
              data-nav-chain={NAV}
              value={tab.doctorPhone}
              onChange={(e) => patchTab({ doctorPhone: e.target.value })}
            />
          </Field>
          <Field label="Bill Date">
            <input
              type="date"
              className="mono"
              data-nav-order={7}
              data-nav-chain={NAV}
              value={tab.billDate}
              onChange={(e) => {
                patchTab({ billDate: e.target.value })
                clearMedicineFields()
              }}
            />
          </Field>
          <Field label="Invoice No">
            <input
              type="text"
              className="mono"
              value={
                tab.editingSaleId
                  ? shortBillNo(tab.billNoHint)
                  : invoiceDisplay(tab.billNoHint, defaults?.next_bill_hint)
              }
              readOnly
              tabIndex={-1}
              title="Next sale bill number (automatic)"
            />
          </Field>
          {/* The "Tabs & Tools" button used to sit here, in a form field with a
              blank label, taking a cell in this input grid. Once the tab bar
              moved onto the page it carried its own copy of the same button --
              so the screen showed the control twice, one of them wedged in
              among the customer and invoice inputs. The tab bar's is the one
              that belongs; this cell is gone. */}
        </div>
        <div className="medsel-grid">
          <Field label="Medicine">
            <TwoStepMedicinePicker
              billDate={tab.billDate}
              reserved={reserved}
              navChain={NAV}
              navOrder={8}
              value={medSearch}
              onValueChange={(name) => {
                setMedSearch(name)
                setSelectedMedId(null)
              }}
              onBatchPicked={onBatchPicked}
              onEmptyEnter={() => focusNavOrder(13)}
              inputRef={medRef}
            />
          </Field>
          <Field label="Batch">
            <input
              type="text"
              className="mono"
              data-nav-order={9}
              data-nav-chain={NAV}
              value={batch || (selectedMedId ? 'Selected' : '')}
              readOnly
              placeholder="Pick from list ↓"
              title="Chosen in step-2 batch list"
            />
          </Field>
          <Field label="Quantity">
            <input
              ref={qtyRef}
              type="text"
              className="mono"
              data-nav-order={10}
              data-nav-chain={NAV}
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              // Clicking in with the mouse selects too. focusNav/focusNavOrder
              // already select on the keyboard routes, but nothing covered a
              // plain click, so a stale value could still be typed onto.
              onFocus={(e) => e.currentTarget.select()}
            />
          </Field>
          <Field label="Cost ₹" hint="per tab">
            <input
              type="text"
              className="mono"
              value={rate}
              readOnly
              tabIndex={-1}
              title="Purchase cost per tablet/unit (read-only)"
            />
          </Field>
          <Field label="MRP" hint="per tab">
            <input
              type="text"
              className="mono"
              value={mrp}
              readOnly
              tabIndex={-1}
              title="MRP per tablet/unit — bill sell rate uses this"
            />
          </Field>
          {showCol.disc ? (
            <Field
              label={
                prefs?.item_discount_mode === 'percent' ? 'Disc %' : 'Disc ₹'
              }
            >
              <input
                type="text"
                className="mono"
                data-nav-order={11}
                data-nav-chain={NAV}
                data-nav-enter="add"
                value={disc}
                onChange={(e) => setDisc(e.target.value)}
              />
            </Field>
          ) : null}
          <Field label={'\u00a0'}>
            <ActionBtn
              label={editingIdx !== null ? 'Update Medicine' : 'Add Medicine'}
              onClick={() => void (editingIdx !== null ? commitLineEdit() : addItem())}
              className="btn-full"
              navOrder={12}
              navAction="add"
              navChain={NAV}
            />
          </Field>
          <div className="hint-text">
            ↓ list · Enter pick · right-click row: Edit Qty / Delete · Delete key
            removes line
          </div>
        </div>
      </Panel>

      <Panel
        title="Selected Medicines"
        table
        className="bill-items-panel"
        headRight={
          <span className="count">
            {tab.items.length} item{tab.items.length === 1 ? '' : 's'}
            {prefs?.autosave_enabled ? ' · autosave on' : ' · autosave off'}
          </span>
        }
      >
        <CappedTableWrap visibleRows={billingRows}>
          <table className="sat-table" ref={tableRef}>
            <thead>
              <tr>
                {showCol.medicine ? <th>Medicine</th> : null}
                {showCol.batch ? <th>Batch</th> : null}
                {showCol.expiry ? <th>Expiry</th> : null}
                {showCol.qty ? <th className="numsm">Qty</th> : null}
                {showCol.type ? <th>Type</th> : null}
                {showCol.rate ? <th className="numsm">Rate</th> : null}
                {showCol.mrp ? <th className="numsm">MRP</th> : null}
                {showCol.disc ? (
                  <th className="numsm">{discColLabel}</th>
                ) : null}
                {showCol.margin ? (
                  <th className="numsm">{marginColLabel}</th>
                ) : null}
                {showCol.amount ? <th className="num">Amount</th> : null}
                {showCol.schedule ? <th>Schedule</th> : null}
                {showCol.location ? <th>Location</th> : null}
                <th />
              </tr>
            </thead>
            <tbody>
              {tab.items.length === 0 ? (
                <tr>
                  <td
                    colSpan={
                      1 +
                      Object.values(showCol).filter(Boolean).length
                    }
                    className="muted"
                  >
                    No medicines added
                  </td>
                </tr>
              ) : (
                tab.items.map((it, i) => (
                  <tr
                    key={`${it.id || 'q'}-${i}`}
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (editingIdx === i) return
                      if (e.key === 'Delete' || e.key === 'Backspace') {
                        e.preventDefault()
                        removeLineAt(i)
                        return
                      }
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        startLineEdit(i)
                        return
                      }
                      if (e.key === 'ArrowDown') {
                        e.preventDefault()
                        const next = e.currentTarget
                          .nextElementSibling as HTMLElement | null
                        next?.focus()
                        return
                      }
                      if (e.key === 'ArrowUp') {
                        e.preventDefault()
                        const prev = e.currentTarget
                          .previousElementSibling as HTMLElement | null
                        if (prev?.tagName === 'TR') prev.focus()
                        else focusNavOrder(8)
                        return
                      }
                      if (e.key === 'Escape') {
                        e.preventDefault()
                        focusNavOrder(8)
                      }
                    }}
                    className={editingIdx === i ? 'row-editing' : undefined}
                    onDoubleClick={() => startLineEdit(i)}
                    onContextMenu={(e) => {
                      e.preventDefault()
                      setLineMenu({ x: e.clientX, y: e.clientY, index: i })
                    }}
                  >
                    {showCol.medicine ? (
                      <td>
                        <span className="med-name">
                          {it.medicine}
                          {it.quick_add ? ' *' : ''}
                        </span>
                      </td>
                    ) : null}
                    {showCol.batch ? (
                      <td className="mono">{it.batch || '—'}</td>
                    ) : null}
                    {showCol.expiry ? (
                      <td className="mono">{it.expiry || '—'}</td>
                    ) : null}
                    {showCol.qty ? (
                      <td className="numsm mono">
                        {it.qty}
                        {tab.editingSaleId && Number(tab.returnedByMed?.[String(it.id)] || 0) > 0 ? (
                          <div className="note" data-returned="1">
                            ↩ {tab.returnedByMed?.[String(it.id)]} returned
                          </div>
                        ) : null}
                      </td>
                    ) : null}
                    {showCol.type ? <td>{it.type || '—'}</td> : null}
                    {showCol.rate ? (
                      <td
                        className="numsm mono"
                        title="Purchase rate per tablet/unit"
                      >
                        {fmtMoney2(displayCostPerUnit(it))}
                      </td>
                    ) : null}
                    {showCol.mrp ? (
                      <td
                        className="numsm mono"
                        title="MRP per tablet/unit"
                      >
                        {fmtMoney2(displayMrpPerUnit(it))}
                      </td>
                    ) : null}
                    {showCol.disc ? (
                      <td className="numsm mono">
                        {Number(it.disc).toFixed(2)}
                      </td>
                    ) : null}
                    {showCol.margin ? (
                      <td className="numsm mono">
                        {(() => {
                          // Recomputed from cost like Classic's tree repaint,
                          // so a merged or re-discounted line is never stale.
                          const lm = lineMargin(it, packDivisor)
                          if (lm) return (marginAsPct ? lm.pct : lm.rs).toFixed(2)
                          return (
                            marginAsPct
                              ? Number(it.margin_pct ?? 0)
                              : Number(it.margin || 0)
                          ).toFixed(2)
                        })()}
                      </td>
                    ) : null}
                    {showCol.amount ? (
                      <td className="num mono">{Number(it.amount).toFixed(2)}</td>
                    ) : null}
                    {showCol.schedule ? (
                      <td>
                        <ScheduleChip value={it.schedule} />
                      </td>
                    ) : null}
                    {showCol.location ? (
                      <td>{it.location || '—'}</td>
                    ) : null}
                    <td>
                      <button
                        type="button"
                        className="icon-btn del"
                        onClick={() => removeLineAt(i)}
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </CappedTableWrap>
      </Panel>

      <Panel title="Billing Summary" className="summary-panel" bare>
        <div className="summary-grid">
          <div className="summary-col">
            <h3>Discount &amp; Charges</h3>
            <div className="sfield-row sfield-row-3">
              <div className="sfield">
                <label>Overall Disc %</label>
                <input
                  type="text"
                  className="mono"
                  data-sales-disc-pct="1"
                  data-nav-order={13}
                  data-nav-chain={NAV}
                  value={tab.overallDiscPct}
                  onFocus={overallDiscFocus}
                  onBlur={(e) => overallDiscBlur(e.relatedTarget)}
                  onChange={(e) => {
                    const pct = Number(e.target.value) || 0
                    patchTab({
                      overallDiscPct: e.target.value,
                      overallDisc: String(overallDiscFromPct(subtotal, pct)),
                    })
                  }}
                />
              </div>
              <div className="sfield">
                <label>Overall Disc ₹</label>
                <input
                  type="text"
                  className="mono"
                  data-nav-order={14}
                  data-nav-chain={NAV}
                  value={tab.overallDisc}
                  onFocus={overallDiscFocus}
                  onBlur={(e) => overallDiscBlur(e.relatedTarget)}
                  onChange={(e) => {
                    const rs = Number(e.target.value) || 0
                    patchTab({
                      overallDisc: e.target.value,
                      overallDiscPct:
                        subtotal > 0
                          ? String(Math.round((rs / subtotal) * 10000) / 100)
                          : '0',
                    })
                  }}
                />
              </div>
              <div className="sfield">
                <label>Rounding</label>
                <input
                  type="text"
                  className="mono"
                  data-nav-order={15}
                  data-nav-chain={NAV}
                  data-nav-enter="rounding-enter"
                  value={tab.rounding}
                  onChange={(e) =>
                    patchTab({ rounding: e.target.value, roundingTouched: true })
                  }
                />
              </div>
              <div className="sfield">
                <label>GST</label>
                <div
                  className="gst-note-box"
                  title="GST is already included in MRP. It is not added on top of the bill."
                >
                  {gstLabel}
                </div>
              </div>
              <div className="sfield">
                <label>Cash</label>
                <input
                  ref={cashRef}
                  type="text"
                  className="mono"
                  data-nav-order={16}
                  data-nav-chain={NAV}
                  value={tab.cash}
                  disabled={dueMode}
                  onChange={(e) => patchTab({ cash: e.target.value })}
                />
              </div>
              <div className="sfield">
                <label>Online</label>
                <input
                  type="text"
                  className="mono"
                  data-nav-order={17}
                  data-nav-chain={NAV}
                  data-nav-enter="online-enter"
                  value={tab.online}
                  disabled={dueMode}
                  onChange={(e) => patchTab({ online: e.target.value })}
                />
              </div>
            </div>
          </div>

          <div className="summary-col">
            <h3>Totals</h3>
            <div className="totals-list">
              <div className="totals-row">
                <span className="label">Subtotal</span>
                <span className="value">{money(total)}</span>
              </div>
              <div className="totals-row">
                <span className="label">Total Paid</span>
                <span className="value">{money(totalPaid)}</span>
              </div>
              {prefs == null || prefs.billing_show_total_margin !== false ? (
                <div className="totals-row">
                  <span className="label">
                    {marginAsPct ? 'Total Margin %' : 'Total Margin'}
                  </span>
                  <span className="value">
                    {marginAsPct
                      ? Number(marginTotal).toFixed(2)
                      : money(marginTotal)}
                  </span>
                </div>
              ) : null}
              <div className="totals-row divider emph amt">
                <span className="label">Total Amount</span>
                <span className="value">{money(total)}</span>
              </div>
              {creditApplied > 0 ? (
                <div className="totals-row">
                  <span className="label">Credit Used</span>
                  <span className="value">− {money(creditApplied)}</span>
                </div>
              ) : null}
              <div className="totals-row emph due">
                <span className="label">Total Due</span>
                <span className="value">{money(totalDue)}</span>
              </div>
            </div>
          </div>

          <div className="summary-col actions-col">
            <ActionBtn
              label={saving ? 'Saving…' : 'Save Sales'}
              kbd="F5"
              navOrder={18}
              navAction="save"
              navChain={NAV}
              onClick={() => void saveSales(false)}
            />
            <div className="print-row">
              <ActionBtn
                label={slotBtnLabel(slot1, 'Print Sales 1', 'A5', 2)}
                variant="secondary"
                kbd={slot1?.key || 'F7'}
                navOrder={20}
                navAction="print1"
                navChain={NAV}
                onClick={() => void printSlot(1)}
              />
              <ActionBtn
                label={slotBtnLabel(slot2, 'Print Sales 2', 'A6', 1)}
                variant="secondary"
                kbd={slot2?.key || 'F8'}
                navOrder={21}
                navAction="print2"
                navChain={NAV}
                onClick={() => void printSlot(2)}
              />
            </div>
            <div className="print-row">
              <ActionBtn
                label="Reprint last"
                variant="neutral"
                kbd="F9"
                navOrder={22}
                navAction="reprint"
                navChain={NAV}
                onClick={() => void silentReprint()}
              />
              <ActionBtn
                label="Sales Return"
                variant="neutral"
                kbd="Alt+R"
                onClick={openSalesReturn}
              />
              <ActionBtn
                label="Clear Form"
                variant="neutral"
                kbd="Shift+F5"
                navOrder={19}
                navAction="clear"
                navChain={NAV}
                onClick={() => void clearForm()}
              />
            </div>
          </div>
        </div>
        {tab.editingSaleId && tab.returnsNote ? (
          <div className="panel-body" style={{ paddingTop: 0 }} data-returns-note="1">
            <Note>{tab.returnsNote}</Note>
          </div>
        ) : null}
        {note ? (
          <div className="panel-body" style={{ paddingTop: 0 }}>
            <Note>{note}</Note>
          </div>
        ) : null}
      </Panel>

      <RowContextMenu
        menu={
          lineMenu
            ? { x: lineMenu.x, y: lineMenu.y }
            : null
        }
        onClose={() => setLineMenu(null)}
        items={[
          {
            label: 'Edit Quantity',
            onClick: () => {
              if (lineMenu) startLineEdit(lineMenu.index)
            },
          },
          { separator: true, label: '' },
          {
            label: 'Delete Line',
            danger: true,
            onClick: () => {
              if (!lineMenu) return
              removeLineAt(lineMenu.index)
            },
          },
        ]}
      />
    </PageRoot>
  )
}
