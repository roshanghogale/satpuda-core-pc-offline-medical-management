import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { norm, pickOption, registerVoicePage, setVoicePlace, type VoiceHandler } from '../voice/voiceBus'
import { focusTableSection, usePageHotkeys } from '../hooks/usePageHotkeys'
import { ensureLocalEngine } from '../backend'
import {
  deletePurchaseReturn,
  replacePurchaseReturn,
  deleteSalesReturn,
  fetchBulkPurchasePrefill,
  fetchPurchaseReturnDetails,
  fetchReturnsSummary,
  loadPurchaseReturnBill,
  loadSalesReturnBill,
  lookupDisposalMedicine,
  saveBulkPurchaseReturn,
  savePurchaseReturn,
  savePurchaseReturnPdf,
  saveSalesReturn,
  searchMedicines,
  searchPurchaseReturnBills,
  searchSalesReturnBills,
  submitDisposal,
  type BulkPurchasePrefill,
  type LoadedPurchaseReturnBill,
  type LoadedSalesReturnBill,
  type ReturnsBundle,
} from '../pagesApi'
import { ModernCombo } from './ModernCombo'
import { AlertDialog, type AlertState } from './SalesDialogs'
import { partTabletProblem, tabletCountNote } from './tabletCount'
import { dispatchPaymentsChanged } from '../syncRefresh'
// The sales-return money rules live in one module, shared with the Sales
// Return popup on the Sales page, so the two screens cannot drift apart.
import {
  checkSalesReturnAdd,
  checkSalesReturnSave,
  discountAsPct,
  matchLabeledBill,
  parseBillSearch,
  salesBillInfo as describeSalesBill,
  salesRefundPreview as calcSalesRefundPreview,
  salesRemainingQty as calcSalesRemainingQty,
  salesReturnBody,
  salesReturnSavedMessage,
  salesSettleHint as calcSalesSettleHint,
  type RefundSettle,
  type SalesReturnLine,
} from './salesReturnLogic'
import {
  ActionBar,
  ActionBtn,
  DataTable,
  Field,
  FilterBar,
  Note,
  PageRoot,
  SectionFrame,
  StatusLine,
  SummaryBar,
} from './pageChrome'

type Tab = 'sales' | 'purchase' | 'disposal' | 'bulk'

type Props = {
  initialTab?: Tab
  bulkPrefill?: BulkPurchasePrefill
  /** Sales -> Sales Return: open the sales tab with this saved bill loaded. */
  salesPrefillSaleId?: number
  /** Open the purchase tab with this saved return loaded for editing. */
  editPurchaseReturnId?: number
  disposalPrefill?: {
    medicine_name: string
    batch_no: string
    expiry_date: string
    available_qty: number
  }
  syncRefreshNonce?: number
  /** Required on purpose. Every page stays mounted once visited, so a page
   *  that does not know whether it is on screen keeps answering the
   *  keyboard from behind another one. An optional prop defaulting to true
   *  let exactly that omission through the compiler. */
  active: boolean
}

function money(n: number) {
  return `₹${n.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function ReturnsPage({
  initialTab = 'sales',
  bulkPrefill,
  disposalPrefill,
  salesPrefillSaleId,
  editPurchaseReturnId,
  syncRefreshNonce = 0,
  active,
}: Props) {
  const [tab, setTab] = useState<Tab>(initialTab)
  const [data, setData] = useState<ReturnsBundle | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [alert, setAlertState] = useState<AlertState | null>(null)
  /** The last box shown, so a spoken add / save can say WHY it did not go through. */
  const lastAlertRef = useRef<AlertState | null>(null)
  const setAlert = useCallback((a: AlertState | null) => {
    lastAlertRef.current = a
    setAlertState(a)
  }, [])
  const [salesHistSel, setSalesHistSel] = useState<number | null>(null)
  const [purchaseHistSel, setPurchaseHistSel] = useState<number | null>(null)
  const [purchaseDetail, setPurchaseDetail] = useState<{
    return_no: string
    return_date: string
    purchase_no: string
    supplier: string
    refund_amount: number
    reason: string
    items: {
      name: string
      batch: string
      qty: number
      rate: number
      amount: number
    }[]
  } | null>(null)

  // Sales return workflow
  const [salesQuery, setSalesQuery] = useState('')
  const [salesMedQuery, setSalesMedQuery] = useState('')
  const [salesBills, setSalesBills] = useState<
    { sale_id: number; label: string }[]
  >([])
  const [salesBill, setSalesBill] = useState<LoadedSalesReturnBill | null>(null)
  // Read through a ref: loadSales is re-created each render and is also called
  // from handlers held by the dropdown, which would otherwise see a stale bill.
  const salesBillRef = useRef<LoadedSalesReturnBill | null>(null)
  salesBillRef.current = salesBill
  const [salesDisc, setSalesDisc] = useState('0')
  const [salesReason, setSalesReason] = useState('')
  const [salesBillMedPick, setSalesBillMedPick] = useState('')
  // A bill can carry the same medicine twice on different batches. Matching the
  // typed name alone always returned the first of them, so a return went
  // against the wrong batch. The dropdown knows the id; keep it.
  const [salesBillMedId, setSalesBillMedId] = useState<number | null>(null)
  const [salesReturnQtyInput, setSalesReturnQtyInput] = useState('')
  const [salesReturnItems, setSalesReturnItems] = useState<SalesReturnLine[]>([])
  const [salesMedNames, setSalesMedNames] = useState<string[]>([])
  const [salesBillInfo, setSalesBillInfo] = useState('')
  const [salesPickInfo, setSalesPickInfo] = useState('')
  const [salesSettle, setSalesSettle] = useState<RefundSettle>('ledger')

  // Purchase return workflow
  const [purchaseQuery, setPurchaseQuery] = useState('')
  const [purchaseBills, setPurchaseBills] = useState<
    { purchase_id: number; label: string }[]
  >([])
  const [purchaseBill, setPurchaseBill] =
    useState<LoadedPurchaseReturnBill | null>(null)
  const purchaseBillRef = useRef<LoadedPurchaseReturnBill | null>(null)
  purchaseBillRef.current = purchaseBill
  const [purchaseDisc, setPurchaseDisc] = useState('0')
  const [purchaseReason, setPurchaseReason] = useState('')
  /** Set while a SAVED return is being edited: Save then replaces it. */
  const [editReturn, setEditReturn] = useState<{
    id: number
    returnNo: string
    oldQty: Record<number, number>
  } | null>(null)
  const [purchaseBillMedPick, setPurchaseBillMedPick] = useState('')
  const [purchaseBillMedId, setPurchaseBillMedId] = useState<number | null>(null)
  const [purchaseReturnQtyInput, setPurchaseReturnQtyInput] = useState('')
  const [purchaseReturnItems, setPurchaseReturnItems] = useState<
    {
      medicine_id: number
      name: string
      batch: string
      qty: number
      rate: number
      amount: number
      type?: string
      is_tablet?: boolean
      tablets_per_stripe?: number
    }[]
  >([])
  const [purchaseBillInfo, setPurchaseBillInfo] = useState('')
  const [purchasePickInfo, setPurchasePickInfo] = useState('')

  // Disposal workflow
  const [dispMed, setDispMed] = useState('')
  const [dispBatch, setDispBatch] = useState('')
  const [dispQty, setDispQty] = useState('1')
  const [dispReason, setDispReason] = useState('Expired / damaged')
  const [dispMode, setDispMode] = useState<'writeoff' | 'return'>('writeoff')
  const [dispLookup, setDispLookup] = useState<Record<string, unknown> | null>(
    null,
  )
  const [medSuggestions, setMedSuggestions] = useState<
    { name: string; batch: string }[]
  >([])

  // Bulk purchase return (near/expiry grouped by purchase bill)
  const [bulkData, setBulkData] = useState<BulkPurchasePrefill | null>(null)
  const [bulkIdx, setBulkIdx] = useState(0)
  const [bulkLoading, setBulkLoading] = useState(false)

  const salesBillListRef = useRef<HTMLDivElement>(null)
  const salesQtyTableRef = useRef<HTMLDivElement>(null)
  const salesHistoryRef = useRef<HTMLDivElement>(null)
  const purchaseBillListRef = useRef<HTMLDivElement>(null)
  const purchaseQtyTableRef = useRef<HTMLDivElement>(null)
  const purchaseHistoryRef = useRef<HTMLDivElement>(null)
  const disposalHistoryRef = useRef<HTMLDivElement>(null)
  const bulkItemsRef = useRef<HTMLDivElement>(null)
  const bulkWriteoffRef = useRef<HTMLDivElement>(null)
  const salesSearchRef = useRef<HTMLInputElement>(null)

  /** Reload the two returns lists, at most once per moment.
   *
   *  Saving a return calls this AND fires the payments-changed event, which
   *  the sync poller turns into a refresh nonce that this page also watches --
   *  so one save fetched both lists twice, four store reads end to end, right
   *  when the counter wants the screen back. Collapsing it here rather than
   *  deleting one of the two callers keeps the guarantee that the list is
   *  reloaded even when the poller is not running. */
  const summaryInFlight = useRef<Promise<void> | null>(null)
  const refreshSummary = useCallback(async () => {
    if (summaryInFlight.current) return summaryInFlight.current
    const run = (async () => {
      try {
        const bundle = await fetchReturnsSummary()
        setData(bundle)
      } finally {
        summaryInFlight.current = null
      }
    })()
    summaryInFlight.current = run
    return run
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
        const bundle = await fetchReturnsSummary()
        if (!cancelled) setData(bundle)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!syncRefreshNonce) return
    void refreshSummary()
  }, [syncRefreshNonce, refreshSummary])

  useEffect(() => {
    setTab(initialTab)
  }, [initialTab])

  const loadBulkPrefill = useCallback(async () => {
    setBulkLoading(true)
    setError('')
    try {
      const data = await fetchBulkPurchasePrefill()
      if (!data.ok) {
        setError(data.error || 'Could not load bulk return data.')
        return
      }
      if (data.empty) {
        setBulkData(null)
        setNote('No near-expiry or expired stock to return.')
        return
      }
      setBulkData(data)
      setBulkIdx(0)
      const groups = data.purchase_groups?.length || 0
      const wo = data.writeoff_lines?.length || 0
      setNote(
        `Loaded ${groups} purchase bill(s)${wo ? ` and ${wo} write-off item(s)` : ''}.`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBulkLoading(false)
    }
  }, [])

  useEffect(() => {
    if (initialTab === 'bulk' && !bulkPrefill) {
      void loadBulkPrefill()
    }
  }, [initialTab, loadBulkPrefill, bulkPrefill])

  useEffect(() => {
    if (!bulkPrefill?.ok || bulkPrefill.empty) return
    setTab('bulk')
    setBulkData(bulkPrefill)
    setBulkIdx(0)
    const groups = bulkPrefill.purchase_groups?.length || 0
    const wo = bulkPrefill.writeoff_lines?.length || 0
    setNote(
      `Loaded ${groups} purchase bill(s)${wo ? ` and ${wo} write-off item(s)` : ''}.`,
    )
  }, [bulkPrefill])

  useEffect(() => {
    if (!disposalPrefill?.medicine_name) return
    setTab('disposal')
    setDispMed(disposalPrefill.medicine_name)
    setDispBatch(disposalPrefill.batch_no || '')
    setDispQty(String(disposalPrefill.available_qty || 1))
    setNote(`Prefilled ${disposalPrefill.medicine_name} from alert.`)
  }, [disposalPrefill])

  const salesRefundPreview = useMemo(
    () => calcSalesRefundPreview(salesReturnItems, salesDisc),
    [salesReturnItems, salesDisc],
  )

  const salesSettleHint = useMemo(
    () =>
      calcSalesSettleHint(
        salesRefundPreview,
        Number(salesBill?.previous_due || 0),
        salesSettle,
      ),
    [salesRefundPreview, salesBill, salesSettle],
  )

  const salesRemainingQty = useCallback(
    (medicineId: number, baseRemaining: number) =>
      calcSalesRemainingQty(salesReturnItems, medicineId, baseRemaining),
    [salesReturnItems],
  )

  const purchaseRefundPreview = useMemo(() => {
    let total = purchaseReturnItems.reduce(
      (sum, it) => sum + (it.amount || it.qty * it.rate),
      0,
    )
    return Math.max(0, total - (Number(purchaseDisc) || 0))
  }, [purchaseReturnItems, purchaseDisc])

  const purchaseRemainingQty = useCallback(
    (medicineId: number, baseRemaining: number) => {
      const used = purchaseReturnItems
        .filter((r) => r.medicine_id === medicineId)
        .reduce((s, r) => s + r.qty, 0)
      // Editing a saved return: its own quantity is given back first, so it
      // counts as returnable again.
      const back = editReturn?.oldQty[medicineId] || 0
      return Math.max(0, baseRemaining + back - used)
    },
    [purchaseReturnItems, editReturn],
  )

  const searchSales = async (opts?: { autoLoadSingle?: boolean }) => {
    setError('')
    try {
      const q = parseBillSearch(salesQuery)
      const res = await searchSalesReturnBills(q, salesMedQuery.trim())
      const bills = (res.bills || []).map((b) => ({
        sale_id: b.sale_id,
        label: b.label,
      }))
      setSalesBills(bills)
      if (
        opts?.autoLoadSingle &&
        salesMedQuery.trim() &&
        bills.length === 1
      ) {
        await loadSales(bills[0].sale_id)
        return
      }
      if (bills.length === 0) {
        if (!salesBill) setSalesBillInfo('No matching bills found.')
      } else if (!salesBill) {
        setSalesBillInfo(`${bills.length} bill(s) found — pick one and Load.`)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  /** The whole medicine list, fetched once per visit rather than per focus.
   *
   *  This is bound to onFocus, and the shop tabs between the Bill No and
   *  Medicine boxes constantly while hunting for a bill -- so it re-downloaded
   *  200 medicine names every time the cursor landed, over the network, for a
   *  list that does not change while a return is being written up. */
  const salesMedNamesLoaded = useRef(false)
  const refreshSalesMedicineNames = async (force = false) => {
    if (salesMedNamesLoaded.current && !force) return
    try {
      const res = await searchMedicines('', 200)
      const names = Array.from(
        new Set((res.medicines || []).map((m) => m.name).filter(Boolean)),
      )
      salesMedNamesLoaded.current = true
      setSalesMedNames(names)
    } catch {
      /* ignore */
    }
  }

  const loadSales = async (saleId: number) => {
    setError('')
    // Same bill again: keep what has already been entered.
    if (salesBillRef.current && Number(salesBillRef.current.sale_id) === Number(saleId)) {
      return salesBillRef.current
    }
    try {
      const loaded = await loadSalesReturnBill(saleId)
      if (!loaded.ok) {
        setAlert({
          title: 'Load bill',
          message: loaded.error || 'Bill not found.',
          kind: 'warning',
        })
        return null
      }
      setSalesBill(loaded)
      // The box is a PERCENTAGE -- the engine hands it to calc_return_refund as
      // discount_pct. A bill stores its overall discount in RUPEES, so putting
      // that number straight in turned a Rs 50 discount into 50% off the refund.
      setSalesDisc(String(discountAsPct(loaded.discount, loaded.bill_total)))
      setSalesReturnItems([])
      setSalesBillMedPick('')
      setSalesReturnQtyInput('')
      setSalesPickInfo('Pick a medicine from the loaded bill, enter qty, then Add to Return.')
      setSalesBillInfo(describeSalesBill(loaded))
      setNote(`Loaded ${loaded.bill_no}`)
      return loaded
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return null
    }
  }

  const addSalesReturnItem = () => {
    const r = checkSalesReturnAdd(
      salesBill,
      salesReturnItems,
      salesBillMedId,
      salesBillMedPick,
      salesReturnQtyInput,
    )
    if (!r.ok) {
      setAlert({ ...r.alert, kind: 'warning' })
      return
    }
    setSalesReturnItems((rows) => [...rows, r.line])
    setSalesReturnQtyInput('')
    setSalesPickInfo(`Added ${r.line.name} × ${r.line.qty}. Add more or save.`)
  }

  const removeSalesReturnItem = (medicineId: number) => {
    setSalesReturnItems((rows) =>
      rows.filter((r) => r.medicine_id !== medicineId),
    )
  }

  // Set before the request goes out. F5 is not stopped by the disabled button,
  // so two quick presses saved the same return twice -- two refunds.
  const salesSavingRef = useRef(false)
  /** True when the return was saved (voice says so; the screen shows its own note). */
  const saveSales = async (): Promise<boolean> => {
    if (salesSavingRef.current) return false
    const pre = checkSalesReturnSave(salesBill, salesReturnItems, salesDisc)
    if (pre || !salesBill) {
      setAlert({
        ...(pre || { title: 'Load bill', message: 'Search and load a sales bill first.' }),
        kind: 'warning',
      })
      return false
    }
    salesSavingRef.current = true
    setSaving(true)
    try {
      const res = await saveSalesReturn(
        salesReturnBody(salesBill, salesReturnItems, salesDisc, salesReason, salesSettle),
      )
      if (!res.ok) {
        setAlert({
          title: 'Save failed',
          message: res.error || 'Could not save return.',
          kind: 'error',
        })
        return false
      }
      const msg = salesReturnSavedMessage(res, salesSettle)
      clearSalesForm()
      setNote(msg)
      dispatchPaymentsChanged('customer')
      await refreshSummary()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    } finally {
      salesSavingRef.current = false
      setSaving(false)
    }
  }

  const deleteSelectedSalesReturn = async () => {
    const ids = data?.sales_returns.row_ids || []
    const id =
      salesHistSel != null && salesHistSel >= 0 ? Number(ids[salesHistSel] || 0) : 0
    if (!id) {
      setAlert({
        title: 'Delete Return',
        message: 'Select a sales return in history first.',
        kind: 'error',
      })
      return
    }
    const row = data?.sales_returns.rows?.[salesHistSel!] || []
    const label = String(row[0] || id)
    if (
      !window.confirm(
        `Delete sales return ${label}?\nStock will be reduced again and the return will sync as deleted.`,
      )
    ) {
      return
    }
    setSaving(true)
    setError('')
    try {
      const res = await deleteSalesReturn(id)
      if (!res.ok) {
        setAlert({
          title: 'Delete failed',
          message: res.error || 'Could not delete return.',
          kind: 'error',
        })
        return
      }
      if (res.warning) {
        // The return was deleted but the refund entry could not be checked on
        // the server. Saying "deleted" and nothing else would hide a refund
        // still charged to the customer, so this has to be a dialog, not a note.
        setAlert({
          title: 'Check the customer ledger',
          message: res.warning,
          kind: 'warning',
        })
      } else {
        setNote(
          res.queued
            ? `Return ${label} delete queued — will sync.`
            : `Return ${label} deleted.`,
        )
      }
      setSalesHistSel(null)
      await refreshSummary()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const deleteSelectedPurchaseReturn = async () => {
    const ids = data?.purchase_returns.row_ids || []
    const id =
      purchaseHistSel != null && purchaseHistSel >= 0
        ? Number(ids[purchaseHistSel] || 0)
        : 0
    if (!id) {
      setAlert({
        title: 'Delete Return',
        message: 'Select a purchase return in history first.',
        kind: 'error',
      })
      return
    }
    const row = data?.purchase_returns.rows?.[purchaseHistSel!] || []
    const label = String(row[0] || id)
    if (
      !window.confirm(
        `Delete purchase return ${label}?\nStock will be increased again and the return will sync as deleted.`,
      )
    ) {
      return
    }
    setSaving(true)
    setError('')
    try {
      const res = await deletePurchaseReturn(id)
      if (!res.ok) {
        setAlert({
          title: 'Delete failed',
          message: res.error || 'Could not delete return.',
          kind: 'error',
        })
        return
      }
      setNote(
        res.queued
          ? `Return ${label} delete queued — will sync.`
          : `Return ${label} deleted.`,
      )
      setPurchaseHistSel(null)
      await refreshSummary()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const selectedPurchaseReturnId = () => {
    const ids = data?.purchase_returns.row_ids || []
    if (purchaseHistSel == null || purchaseHistSel < 0) return 0
    return Number(ids[purchaseHistSel] || 0)
  }

  const viewPurchaseReturnDetails = async () => {
    const id = selectedPurchaseReturnId()
    if (!id) {
      setAlert({
        title: 'View Details',
        message: 'Select a purchase return in history first.',
        kind: 'error',
      })
      return
    }
    try {
      const res = await fetchPurchaseReturnDetails(id)
      if (!res.ok) {
        setAlert({
          title: 'Not Found',
          message: res.error || 'Return record not found.',
          kind: 'warning',
        })
        return
      }
      setPurchaseDetail({
        return_no: res.return_no || '',
        return_date: res.return_date || '',
        purchase_no: res.purchase_no || '',
        supplier: res.supplier || '',
        refund_amount: Number(res.refund_amount || 0),
        reason: res.reason || '',
        items: (res.items || []).map((it) => ({
          name: it.name,
          batch: it.batch,
          qty: it.qty,
          rate: it.rate,
          amount: it.amount,
        })),
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const saveSelectedPurchaseReturnPdf = async () => {
    const id = selectedPurchaseReturnId()
    if (!id) {
      setAlert({
        title: 'Save PDF',
        message: 'Select a purchase return in history first.',
        kind: 'error',
      })
      return
    }
    setSaving(true)
    try {
      const res = await savePurchaseReturnPdf(id)
      if (!res.ok) {
        setAlert({
          title: 'Save PDF',
          message: res.error || 'Could not save PDF.',
          kind: 'error',
        })
        return
      }
      setNote(`PDF saved: ${res.path || res.pdf_path || res.return_no || ''}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  /** Load a saved purchase return for EDITING (Save then replaces it). */
  const editPurchaseReturnById = async (id: number) => {
    setSaving(true)
    setError('')
    try {
      const res = await fetchPurchaseReturnDetails(id)
      if (!res.ok || !res.purchase_id) {
        setAlert({
          title: 'Edit Return',
          message: res.error || 'Return record not found.',
          kind: 'warning',
        })
        return
      }
      setTab('purchase')
      // A bill already on screen short-circuits loadPurchase; edit always
      // starts from a fresh copy of the bill.
      purchaseBillRef.current = null
      await loadPurchase(Number(res.purchase_id))
      // Pack facts (tablet, strip size) come from the BILL -- the history
      // lines carry none, and a strip must never go back as one tablet.
      const bill = await loadPurchaseReturnBill(Number(res.purchase_id))
      const pack = new Map(
        (bill.ok ? bill.items || [] : []).map((it) => [Number(it.medicine_id), it]),
      )
      const oldQty: Record<number, number> = {}
      for (const it of res.items || []) {
        oldQty[it.medicine_id] = (oldQty[it.medicine_id] || 0) + Number(it.qty || 0)
      }
      setEditReturn({ id, returnNo: res.return_no || `#${id}`, oldQty })
      setPurchaseReason(res.reason || '')
      setPurchaseReturnItems(
        (res.items || []).map((it) => {
          const src = pack.get(Number(it.medicine_id))
          return {
            medicine_id: it.medicine_id,
            name: it.name,
            batch: it.batch,
            qty: it.qty,
            rate: it.rate,
            amount: it.amount,
            type: src?.type || '',
            is_tablet: Boolean(src?.is_tablet),
            tablets_per_stripe: Number(src?.tablets_per_stripe || 1),
          }
        }),
      )
      setNote(
        `Editing ${res.return_no} on ${res.purchase_no}. Change the lines, then Save — the old return is taken back and the corrected one saved.`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    if (!editPurchaseReturnId) return
    void editPurchaseReturnById(editPurchaseReturnId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editPurchaseReturnId])

  useEffect(() => {
    if (!salesPrefillSaleId) return
    setTab('sales')
    void loadSales(salesPrefillSaleId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [salesPrefillSaleId])

  const loadPurchaseReturnFromHistory = async () => {
    const id = selectedPurchaseReturnId()
    if (!id) {
      setAlert({
        title: 'Load Purchase',
        message: 'Select a purchase return in history first.',
        kind: 'error',
      })
      return
    }
    setSaving(true)
    try {
      const res = await fetchPurchaseReturnDetails(id)
      if (!res.ok || !res.purchase_id) {
        setAlert({
          title: 'Load Purchase',
          message: res.error || 'Return record not found.',
          kind: 'warning',
        })
        return
      }
      await loadPurchase(Number(res.purchase_id))
      setPurchaseReason(res.reason || '')
      const lines = res.items || []
      setPurchaseReturnItems(
        lines.map((it) => ({
          medicine_id: it.medicine_id,
          name: it.name,
          batch: it.batch,
          qty: it.qty,
          rate: it.rate,
          amount: it.amount,
          type: '',
          is_tablet: false,
          tablets_per_stripe: 1,
        })),
      )
      setNote(
        `Purchase ${res.purchase_no} loaded with return ${res.return_no} items for review. This is for review only — to change a saved return, use Edit Return.`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const searchPurchase = async (opts?: { autoLoadSingle?: boolean }) => {
    setError('')
    try {
      const res = await searchPurchaseReturnBills(parseBillSearch(purchaseQuery))
      const bills = (res.purchases || []).map((b) => ({
        purchase_id: b.purchase_id,
        label: b.label,
      }))
      setPurchaseBills(bills)
      if (opts?.autoLoadSingle && purchaseQuery.trim() && bills.length === 1) {
        await loadPurchase(bills[0].purchase_id)
        return
      }
      if (bills.length === 0) {
        if (!purchaseBill) setPurchaseBillInfo('No matching purchase bills found.')
      } else if (!purchaseBill) {
        setPurchaseBillInfo(`${bills.length} bill(s) found — pick one and Load.`)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    if (tab === 'sales' && !salesBill && !parseBillSearch(salesQuery)) {
      void searchSales()
    } else if (
      tab === 'purchase' &&
      !purchaseBill &&
      !parseBillSearch(purchaseQuery)
    ) {
      void searchPurchase()
    }
    // Load recent bills when opening a tab with an empty search box.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  const loadPurchase = async (purchaseId: number) => {
    setError('')
    if (
      purchaseBillRef.current &&
      Number(purchaseBillRef.current.purchase_id) === Number(purchaseId)
    ) {
      return purchaseBillRef.current
    }
    try {
      const loaded = await loadPurchaseReturnBill(purchaseId)
      if (!loaded.ok) {
        setAlert({
          title: 'Load bill',
          message: loaded.error || 'Purchase not found.',
          kind: 'warning',
        })
        return null
      }
      setPurchaseBill(loaded)
      setPurchaseReturnItems([])
      setPurchaseBillMedPick('')
      setPurchaseReturnQtyInput('')
      setPurchasePickInfo(
        'Pick a medicine from the loaded purchase, enter qty, then Add to Return.',
      )
      setPurchaseBillInfo(
        `Loaded ${loaded.bill_label} — ${loaded.supplier} (${loaded.purchase_date})` +
          ` | Bill ${money(loaded.bill_total || 0)}` +
          ` | Paid ${money(loaded.bill_paid || 0)}` +
          ` | Bill due ${money(loaded.bill_due || 0)}` +
          ` | Supplier due ${money(loaded.previous_due || 0)}` +
          (loaded.previous_credit
            ? ` | Credit ${money(loaded.previous_credit)}`
            : ''),
      )
      setNote(`Loaded ${loaded.bill_label}`)
      return loaded
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return null
    }
  }

  const addPurchaseReturnItem = () => {
    if (!purchaseBill?.items?.length) {
      setAlert({
        title: 'Load bill',
        message: 'Load a purchase bill first.',
        kind: 'warning',
      })
      return
    }
    const pickName = purchaseBillMedPick.trim()
    const item =
      (purchaseBillMedId != null
        ? purchaseBill.items.find(
            (it) => Number(it.medicine_id) === purchaseBillMedId,
          )
        : null) ||
      purchaseBill.items.find(
        (it) => it.name.toLowerCase() === pickName.toLowerCase(),
      )
    if (!item) {
      setAlert({
        title: 'Medicine',
        message: 'Pick a medicine from the loaded purchase.',
        kind: 'warning',
      })
      return
    }
    const qty = Number(purchaseReturnQtyInput) || 0
    const remaining = purchaseRemainingQty(item.medicine_id, item.remaining_qty)
    if (qty <= 0) {
      setAlert({
        title: 'Return qty',
        message: 'Enter a valid return quantity.',
        kind: 'warning',
      })
      return
    }
    if (qty > remaining) {
      setAlert({
        title: 'Return qty',
        message: `Cannot return more than ${remaining} for ${item.name}.`,
        kind: 'warning',
      })
      return
    }
    // Fractional strips are fine; a part tablet is not (owner, 2026-09-11).
    const part = partTabletProblem(item.name, qty, item.is_tablet, item.tablets_per_stripe)
    if (part) {
      setAlert({ title: 'Return qty', message: part, kind: 'warning' })
      return
    }
    if (purchaseReturnItems.some((r) => r.medicine_id === item.medicine_id)) {
      setAlert({
        title: 'Duplicate',
        message: `${item.name} is already in the return list. Remove it first to change qty.`,
        kind: 'warning',
      })
      return
    }
    const amount = Math.round(qty * (item.rate || 0) * 100) / 100
    setPurchaseReturnItems((rows) => [
      ...rows,
      {
        medicine_id: item.medicine_id,
        name: item.name,
        batch: item.batch,
        qty,
        rate: item.rate || 0,
        amount,
        type: item.type,
        is_tablet: item.is_tablet,
        tablets_per_stripe: item.tablets_per_stripe,
      },
    ])
    setPurchaseReturnQtyInput('')
    setPurchasePickInfo(`Added ${item.name} × ${qty}. Add more or save.`)
  }

  const removePurchaseReturnItem = (medicineId: number) => {
    setPurchaseReturnItems((rows) =>
      rows.filter((r) => r.medicine_id !== medicineId),
    )
  }

  /** True when the return was saved. */
  const savePurchase = async (): Promise<boolean> => {
    if (!purchaseBill?.purchase_id) {
      setAlert({
        title: 'Load bill',
        message: 'Search and load a purchase bill first.',
        kind: 'warning',
      })
      return false
    }
    const items = purchaseReturnItems.map((it) => ({
      medicine_id: it.medicine_id,
      qty: it.qty,
      rate: it.rate,
      type: it.type,
      is_tablet: it.is_tablet,
      tablets_per_stripe: it.tablets_per_stripe,
    }))
    if (!items.length) {
      setAlert({
        title: 'Return qty',
        message: 'Enter return quantity for at least one item.',
        kind: 'warning',
      })
      return false
    }
    setSaving(true)
    try {
      const body = {
        purchase_id: purchaseBill.purchase_id,
        supplier_id: purchaseBill.supplier_id,
        supplier_name: purchaseBill.supplier || '',
        bill_label: purchaseBill.bill_label || '',
        items,
        discount: Number(purchaseDisc) || 0,
        reason: purchaseReason.trim(),
      }
      // Editing a saved return: the engine takes the old one back in full and
      // saves these lines as the corrected return (replace_purchase_return).
      const res = editReturn
        ? await replacePurchaseReturn({ ...body, return_id: editReturn.id })
        : await savePurchaseReturn(body)
      if (!res.ok) {
        setAlert({
          title: 'Save failed',
          message: res.error || 'Could not save return.',
          kind: 'error',
        })
        return false
      }
      let msg = `Saved ${res.return_no} — credit ${money(res.refund_amount || 0)}`
      if (editReturn) {
        msg = `${editReturn.returnNo} corrected — saved as ${res.return_no}, credit ${money(res.refund_amount || 0)}`
      }
      if (res.return_id && window.confirm('Save purchase return PDF now?')) {
        try {
          const pdf = await savePurchaseReturnPdf(Number(res.return_id))
          if (pdf.ok) {
            msg = `Saved ${res.return_no} — PDF: ${pdf.path || pdf.pdf_path || ''}`
          }
        } catch {
          /* ignore pdf errors after save */
        }
      }
      clearPurchaseForm()
      setNote(msg)
      await refreshSummary()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    } finally {
      setSaving(false)
    }
  }

  const lookupDisposal = async () => {
    setError('')
    try {
      const res = await lookupDisposalMedicine({
        name: dispMed.trim(),
        batch: dispBatch.trim(),
      })
      if (!res.ok) {
        setAlert({
          title: 'Lookup',
          message: res.error || 'Medicine not found.',
          kind: 'warning',
        })
        return
      }
      setDispLookup({
        medicine_id: res.medicine_id,
        name: res.name,
        batch: res.batch,
        stock_qty: res.stock_qty,
        purchase: res.purchase,
      })
      if (res.batch) setDispBatch(String(res.batch))
      setNote(`Stock: ${res.stock_qty ?? 0}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const saveDisposal = async () => {
    const medId = Number(dispLookup?.medicine_id || 0)
    const qty = Number(dispQty) || 0
    if (medId <= 0) {
      setAlert({
        title: 'Lookup',
        message: 'Look up a medicine first.',
        kind: 'warning',
      })
      return
    }
    if (qty <= 0) {
      setAlert({
        title: 'Quantity',
        message: 'Enter a valid quantity.',
        kind: 'warning',
      })
      return
    }
    setSaving(true)
    try {
      const res = await submitDisposal({
        lines: [
          {
            medicine_id: medId,
            qty,
            reason: dispReason.trim() || 'Write-off',
            batch: dispBatch.trim(),
            mode: dispMode,
            purchase: dispLookup?.purchase,
          },
        ],
      })
      if (!res.ok) {
        setAlert({
          title: 'Submit failed',
          message: res.error || 'Could not submit.',
          kind: 'error',
        })
        return
      }
      setNote(`Saved ${(res.disposal_nos || []).join(', ')}`)
      setDispLookup(null)
      setDispQty('1')
      await refreshSummary()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const saveBulkAll = async () => {
    if (!bulkData?.purchase_groups?.length && !bulkData?.writeoff_lines?.length) {
      setAlert({
        title: 'Bulk return',
        message: 'Load near/expiry stock first.',
        kind: 'warning',
      })
      return
    }
    if (
      !window.confirm(
        'Save all purchase returns and write-offs in this batch?',
      )
    ) {
      return
    }
    setSaving(true)
    setError('')
    try {
      const res = await saveBulkPurchaseReturn({
        purchase_groups: bulkData?.purchase_groups || [],
        writeoff_lines: bulkData?.writeoff_lines || [],
        writeoff_reason: 'No purchase record',
      })
      if (!res.ok) {
        setAlert({
          title: 'Bulk save',
          message: res.error || 'Save failed.',
          kind: 'warning',
        })
        return
      }
      const saved = (res.saved || [])
        .map((s) => s.return_no)
        .filter(Boolean)
        .join(', ')
      setNote(saved ? `Saved: ${saved}` : 'Bulk return saved.')
      // Some bills saved and some refused (the engine checks each bill again at
      // save): say which, or the shop takes the refused ones as sent back.
      const refused = res.errors || []
      if (refused.length) {
        setAlert({
          title: 'Bulk save',
          message: `Not saved:\n${refused.join('\n')}\n\nLoad the bulk list again for these.`,
          kind: 'warning',
        })
      }
      setBulkData(null)
      await refreshSummary()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const clearSalesForm = () => {
    setSalesBill(null)
    setSalesReturnItems([])
    setSalesDisc('0')
    setSalesReason('')
    setSalesQuery('')
    setSalesMedQuery('')
    setSalesBills([])
    setSalesBillMedPick('')
    setSalesReturnQtyInput('')
    setSalesBillInfo('')
    setSalesPickInfo('')
    setSalesSettle('ledger')
    setNote('')
    salesSearchRef.current?.focus()
  }

  const clearPurchaseForm = () => {
    setPurchaseBill(null)
    setEditReturn(null)
    setPurchaseReturnItems([])
    setPurchaseDisc('0')
    setPurchaseReason('')
    setPurchaseQuery('')
    setPurchaseBills([])
    setPurchaseBillMedPick('')
    setPurchaseReturnQtyInput('')
    setPurchaseBillInfo('')
    setPurchasePickInfo('')
    setNote('')
  }

  const clearDisposalForm = () => {
    setDispMed('')
    setDispBatch('')
    setDispQty('1')
    setDispReason('Expired / damaged')
    setDispMode('writeoff')
    setDispLookup(null)
    setMedSuggestions([])
    setNote('')
  }

  const clearBulkForm = () => {
    setBulkData(null)
    setBulkIdx(0)
    setNote('')
  }

  const focusSalesF2 = () => {
    if (salesBill) {
      salesQtyTableRef.current?.focus()
      return
    }
    focusTableSection(salesBillListRef, { preferInput: true })
  }

  const focusSalesF3 = () => {
    focusTableSection(salesHistoryRef, { preferInput: false })
  }

  const focusPurchaseF2 = () => {
    if (purchaseBill) {
      focusTableSection(purchaseQtyTableRef)
      return
    }
    focusTableSection(purchaseBillListRef, { preferInput: true })
  }

  const focusPurchaseF3 = () => {
    focusTableSection(purchaseHistoryRef, { preferInput: false })
  }

  usePageHotkeys({
    // The hook has always taken this; no page passed it, so F5 and the
    // rest fired on every page that had ever been opened.
    enabled: active,
    onSave: () => {
      if (tab === 'sales') void saveSales()
      else if (tab === 'purchase') void savePurchase()
      else if (tab === 'disposal') void saveDisposal()
      else if (tab === 'bulk') void saveBulkAll()
    },
    onClear: () => {
      if (tab === 'sales') clearSalesForm()
      else if (tab === 'purchase') clearPurchaseForm()
      else if (tab === 'disposal') clearDisposalForm()
      else if (tab === 'bulk') clearBulkForm()
    },
    onF2: () => {
      if (tab === 'sales') focusSalesF2()
      else if (tab === 'purchase') focusPurchaseF2()
      else if (tab === 'bulk') focusTableSection(bulkItemsRef)
    },
    onF3: () => {
      if (tab === 'sales') focusSalesF3()
      else if (tab === 'purchase') focusPurchaseF3()
      else if (tab === 'disposal') focusTableSection(disposalHistoryRef, { preferInput: false })
      else if (tab === 'bulk') focusTableSection(bulkWriteoffRef, { preferInput: false })
    },
    onLetter: (k) => {
      if (k === 's') setTab('sales')
      else if (k === 'p') setTab('purchase')
      else if (k === 'w') setTab('disposal')
    },
  })

  useEffect(() => {
    if (!active) return
    if (tab !== 'bulk') return
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.altKey || e.metaKey) return
      const groups = bulkData?.purchase_groups || []
      if (e.key === '[') {
        e.preventDefault()
        setBulkIdx((i) => Math.max(0, i - 1))
      }
      if (e.key === ']') {
        e.preventDefault()
        setBulkIdx((i) => Math.min(Math.max(groups.length - 1, 0), i + 1))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tab, bulkData, active])

  const onDispMedPick = async (name: string) => {
    setDispMed(name)
    try {
      const res = await searchMedicines(name.trim(), 30)
      setMedSuggestions(
        (res.medicines || []).map((m) => ({
          name: m.name,
          batch: m.batch,
        })),
      )
    } catch {
      /* ignore */
    }
  }

  const salesCount = data?.sales_returns.count ?? 0
  const purchaseCount = data?.purchase_returns.count ?? 0
  const writeCount = data?.writeoffs.count ?? 0
  const bulkGroups = bulkData?.purchase_groups || []
  const activeBulkGroup = bulkGroups[bulkIdx] || null
  const bulkWriteoffs = bulkData?.writeoff_lines || []

  // ── Voice (test build): "write-off tab", "purchase return", "cash parat" ──
  // Sets the same tab / select state the buttons and dropdowns set.
  const voiceHandlerRef = useRef<VoiceHandler>(async () => null)
  voiceHandlerRef.current = async (cmd) => {
    const a = cmd.args || {}
    const TAB_NAMES: Record<Tab, string> = {
      sales: 'Sales Return',
      purchase: 'Purchase Return',
      disposal: 'Write-off',
      bulk: 'Bulk Purchase Return',
    }
    const TAB_ALIASES: Record<string, Tab> = {
      sales: 'sales', salesreturn: 'sales', grahak: 'sales',
      purchase: 'purchase', purchasereturn: 'purchase', supplier: 'purchase',
      disposal: 'disposal', writeoff: 'disposal', write: 'disposal', nash: 'disposal',
      bulk: 'bulk', bulkreturn: 'bulk', bulkpurchasereturn: 'bulk',
    }
    if (cmd.intent === 'page_filter') {
      const f = String(a.filter || '')
      if (f === 'tab') {
        const t = TAB_ALIASES[norm(a.value)]
        if (!t) return { ok: false, say: `Returns madhe "${a.value}" tab nahi` }
        setTab(t)
        return { ok: true, say: `Returns: ${TAB_NAMES[t]}` }
      }
      if (f === 'disposal_mode') {
        const v = pickOption(a.value, ['writeoff', 'return'])
        if (!v) return { ok: false, say: `Mode "${a.value}" nahi` }
        setTab('disposal')
        setDispMode(v as 'writeoff' | 'return')
        return { ok: true, say: `Write-off mode: ${v === 'writeoff' ? 'Write-off' : 'Purchase return (disposal)'}` }
      }
      if (f === 'refund') {
        const v = pickOption(a.value, ['ledger', 'cash', 'online'])
        if (!v) return { ok: false, say: `Parat kasa "${a.value}" samajla nahi` }
        setTab('sales')
        setSalesSettle(v as RefundSettle)
        const words: Record<string, string> = {
          ledger: 'grahakachya khatyat jama (cash nahi)',
          cash: 'cash parat',
          online: 'online / UPI parat',
        }
        return { ok: true, say: `Sales return paise: ${words[v]}` }
      }
      return { ok: false, say: `Returns var "${f}" filter nahi` }
    }
    if (cmd.intent === 'page_action') {
      const act = String(a.action || '')
      if (act === 'refresh') {
        await refreshSummary()
        return { ok: true, say: 'Returns refresh kela' }
      }
      if (act === 'load_bulk') {
        // The "Load candidates" button: reads near-expiry stock, saves nothing.
        setTab('bulk')
        void loadBulkPrefill()
        return { ok: true, say: 'Bulk return: near-expiry yaadi aanli' }
      }
      return { ok: false, say: `Returns var "${act}" he kaam voice var nahi` }
    }

    // ── The return being written up, on the Sales Return / Purchase Return tab:
    // "bill S-102 load kar", "Dolo 650 don patte", "dusri line kadh", "save kar".
    // The same checks as the buttons: quantity left to return, whole tablets, no twice.
    const FORM_INTENTS = ['load_bill', 'add_medicine', 'remove_last', 'remove_line', 'remove_medicine', 'save_bill']
    if (!FORM_INTENTS.includes(cmd.intent)) return null
    if (tab !== 'sales' && tab !== 'purchase') {
      return { ok: false, say: `${TAB_NAMES[tab]}: he ya tab var voice ne hot nahi — Sales Return kinva Purchase Return tab ughada` }
    }
    const sales = tab === 'sales'
    const boxLine = () => {
      const box = lastAlertRef.current
      return box ? ` — ${box.title}: ${(box.message || '').split('\n')[0]}` : ' — screen var sandesh baga'
    }

    if (cmd.intent === 'load_bill') {
      const no = String(a.bill_no ?? '').trim()
      if (!no) return { ok: false, say: 'Konta bill? Bill number sanga' }
      const want = no.toUpperCase()
      try {
        lastAlertRef.current = null
        if (sales) {
          const bills = (await searchSalesReturnBills(no, '')).bills || []
          const hit =
            bills.find((b) => String(b.bill_no).toUpperCase() === want) ||
            (bills.length === 1 ? bills[0] : undefined) ||
            bills.find((b) => String(b.bill_no).toUpperCase().endsWith(want))
          if (!hit) {
            return { ok: false, say: bills.length ? `Bill ${no}: ${bills.length} bill sapadle — purna number sanga` : `Bill ${no}: yaadit bill nahi` }
          }
          setSalesQuery(hit.label)
          const loaded = await loadSales(hit.sale_id)
          if (!loaded) return { ok: false, say: `Bill ${no} load zala nahi${boxLine()}` }
          return {
            ok: true,
            say: `Bill ${loaded.bill_no || no} load kela — ${loaded.customer || 'grahak'} · ${loaded.items?.length ?? 0} aushadha`,
          }
        }
        const bills = (await searchPurchaseReturnBills(no)).purchases || []
        const hit =
          bills.find((b) => String(b.bill_label).toUpperCase() === want) ||
          (bills.length === 1 ? bills[0] : undefined) ||
          bills.find((b) => String(b.bill_label).toUpperCase().endsWith(want))
        if (!hit) {
          return { ok: false, say: bills.length ? `Bill ${no}: ${bills.length} bill sapadle — purna number sanga` : `Bill ${no}: yaadit bill nahi` }
        }
        setPurchaseQuery(hit.label)
        const loaded = await loadPurchase(hit.purchase_id)
        if (!loaded) return { ok: false, say: `Purchase bill ${no} load zala nahi${boxLine()}` }
        return {
          ok: true,
          say: `Purchase bill ${loaded.bill_label || no} load kela — ${loaded.supplier || 'supplier'} · ${loaded.items?.length ?? 0} aushadha`,
        }
      } catch (e) {
        return { ok: false, say: `Bill ${no} load zala nahi — ${e instanceof Error ? e.message : e}` }
      }
    }

    if (cmd.intent === 'add_medicine') {
      const bill = sales ? salesBillRef.current : purchaseBillRef.current
      const items = bill?.items || []
      if (!items.length) return { ok: false, say: 'Aadhi bill load kara — "bill number … load kar" mhana' }
      const want = String(a.medicine ?? '').trim().toLowerCase()
      const brand = want.split(/\s+/)[0] || want
      const lower = (s: string) => s.toLowerCase()
      const item =
        items.find((it) => lower(it.name) === want) ||
        items.find((it) => lower(it.name).startsWith(want)) ||
        items.find((it) => lower(it.name).includes(want)) ||
        items.find((it) => lower(it.name).split(/\s+/)[0] === brand)
      if (!item) return { ok: false, say: `${a.medicine}: ya bill madhe nahi` }
      const said = Number(a.qty) || 1
      const tps = Number(item.tablets_per_stripe) || 1
      // A sale counts a strip medicine in tablets: "don patte" is its tablets. A purchase counts strips.
      const qty = sales && a.unit === 'strip' && item.is_tablet && tps > 1 ? said * tps : said
      if (sales) {
        const r = checkSalesReturnAdd(salesBill, salesReturnItems, item.medicine_id, item.name, String(qty))
        if (!r.ok) return { ok: false, say: `${item.name}: ${r.alert.message}` }
        setSalesReturnItems((rows) => [...rows, r.line])
        setSalesPickInfo(`Added ${r.line.name} × ${r.line.qty}. Add more or save.`)
      } else {
        const remaining = purchaseRemainingQty(item.medicine_id, item.remaining_qty)
        if (qty > remaining) return { ok: false, say: `${item.name}: Cannot return more than ${remaining} for ${item.name}.` }
        const part = partTabletProblem(item.name, qty, item.is_tablet, item.tablets_per_stripe)
        if (part) return { ok: false, say: `${item.name}: ${part}` }
        if (purchaseReturnItems.some((r) => r.medicine_id === item.medicine_id)) {
          return { ok: false, say: `${item.name}: ${item.name} is already in the return list. Remove it first to change qty.` }
        }
        setPurchaseReturnItems((rows) => [
          ...rows,
          {
            medicine_id: item.medicine_id,
            name: item.name,
            batch: item.batch,
            qty,
            rate: item.rate || 0,
            amount: Math.round(qty * (item.rate || 0) * 100) / 100,
            type: item.type,
            is_tablet: item.is_tablet,
            tablets_per_stripe: item.tablets_per_stripe,
          },
        ])
        setPurchasePickInfo(`Added ${item.name} × ${qty}. Add more or save.`)
      }
      return { ok: true, say: `${item.name} × ${qty}${qty !== said ? ` (${said} patte)` : ''} return madhe jodla` }
    }

    if (cmd.intent === 'remove_last' || cmd.intent === 'remove_line' || cmd.intent === 'remove_medicine') {
      const lines: { name: string }[] = sales ? salesReturnItems : purchaseReturnItems
      if (!lines.length) return { ok: false, say: 'Kadhayla kahich nahi — return yaadi rikami aahe' }
      let at = lines.length - 1
      if (cmd.intent === 'remove_line') {
        const n = Number(a.n)
        if (!Number.isInteger(n) || n < 1 || n > lines.length) return { ok: false, say: `Return madhe ${a.n ?? '?'} line nahit` }
        at = n - 1
      } else if (cmd.intent === 'remove_medicine') {
        const want = String(a.medicine ?? '').trim().toLowerCase()
        at = lines.findIndex((r) => r.name.toLowerCase().startsWith(want) || r.name.toLowerCase().includes(want))
        if (!want || at < 0) return { ok: false, say: `${a.medicine} return yaadit nahi` }
      }
      const gone = lines[at]
      if (sales) setSalesReturnItems((rows) => rows.filter((_, i) => i !== at))
      else setPurchaseReturnItems((rows) => rows.filter((_, i) => i !== at))
      return { ok: true, say: cmd.intent === 'remove_line' ? `${at + 1} ri line kadhli: ${gone.name}` : `${gone.name} return madhun kadhla` }
    }

    // save_bill: the voice bar has already asked "Yes / No".
    lastAlertRef.current = null
    const saved = sales ? await saveSales() : await savePurchase()
    if (saved) return { ok: true, say: `${TAB_NAMES[tab]} save zala ✓` }
    return { ok: false, say: `Return save zala nahi${boxLine()}` }
  }
  useEffect(() => registerVoicePage('returns', () => voiceHandlerRef.current), [])
  // The voice bar tells the service which tab is showing.
  useEffect(() => setVoicePlace('returns', tab), [tab])

  return (
    <PageRoot className="returns-page">
      <div className="page-toggle-bar settings-inline-actions">
        <ActionBtn
          label="Sales Return"
          variant={tab === 'sales' ? 'primary' : 'neutral'}
          onClick={() => setTab('sales')}
        />
        <ActionBtn
          label="Purchase Return"
          variant={tab === 'purchase' ? 'primary' : 'neutral'}
          onClick={() => setTab('purchase')}
        />
        <ActionBtn
          label="Write-off"
          variant={tab === 'disposal' ? 'primary' : 'neutral'}
          onClick={() => setTab('disposal')}
        />
        <ActionBtn
          label="Bulk Purchase Return"
          variant={tab === 'bulk' ? 'primary' : 'neutral'}
          onClick={() => setTab('bulk')}
        />
      </div>

      <StatusLine
        error={error}
        loading={loading && !data}
      />
      {note ? <Note>{note}</Note> : null}

      <SummaryBar
        items={[
          { label: 'Sales returns', value: salesCount },
          { label: 'Purchase returns', value: purchaseCount },
          { label: 'Write-offs', value: writeCount },
        ]}
      />

      {tab === 'sales' && (
        <>
          <SectionFrame title="Step 1 — Find original bill">
            <div ref={salesBillListRef}>
            <FilterBar>
              <Field label="Bill No / Customer" className="field-bill-search">
                <ModernCombo
                  value={salesQuery}
                  inputRef={salesSearchRef}
                  placeholder="Search bill…"
                  voiceField="search"
                  minChars={0}
                  filterLocal
                  openOnFocus
                  listLabel="Recent bills"
                  keepOrder
                  items={salesBills.map((b) => ({
                    id: String(b.sale_id),
                    label: b.label,
                  }))}
                  onFocus={() => {
                    if (!salesBill) void searchSales()
                  }}
                  onChange={setSalesQuery}
                  onPick={(it) => {
                    setSalesQuery(it.label)
                    void loadSales(Number(it.id))
                  }}
                  onEnter={(picked) => {
                    if (picked) return
                    const match = matchLabeledBill(salesBills, salesQuery)
                    if (match) void loadSales(match.sale_id)
                    else void searchSales()
                  }}
                />
              </Field>
              <Field label="or Medicine">
                <ModernCombo
                  value={salesMedQuery}
                  placeholder="Search medicine…"
                  voiceField="medicine"
                  minChars={0}
                  filterLocal
                  openOnFocus
                  listLabel="Medicines"
                  items={salesMedNames.map((n) => ({ id: n, label: n }))}
                  onFocus={() => void refreshSalesMedicineNames()}
                  onChange={setSalesMedQuery}
                  onPick={(it) => {
                    setSalesMedQuery(it.label)
                    void searchSales({ autoLoadSingle: true })
                  }}
                  onEnter={() => void searchSales({ autoLoadSingle: true })}
                />
              </Field>
              <ActionBar>
                <ActionBtn
                  label="Load Bill"
                  onClick={() => {
                    const match = matchLabeledBill(salesBills, salesQuery)
                    if (match) void loadSales(match.sale_id)
                    else void searchSales()
                  }}
                />
              </ActionBar>
            </FilterBar>
            </div>
            {salesBillInfo ? <Note>{salesBillInfo}</Note> : null}
          </SectionFrame>

          <SectionFrame title="Step 2 — Add return (pick medicine → qty → Enter)">
            {salesBill ? (
              <>
                <FilterBar>
                  <Field label="Medicine in bill">
                    <ModernCombo
                      value={salesBillMedPick}
                      placeholder="Pick medicine from bill…"
                      minChars={0}
                      filterLocal
                      openOnFocus
                      listLabel="Bill medicines"
                      items={(salesBill.items || []).map((it) => ({
                        id: String(it.medicine_id),
                        label: it.name,
                        meta: `${it.batch} · rem ${salesRemainingQty(it.medicine_id, it.remaining_qty)}`,
                      }))}
                      onChange={(v) => {
                        setSalesBillMedPick(v)
                        setSalesBillMedId(null)
                      }}
                      onPick={(it) => {
                        setSalesBillMedPick(it.label)
                        setSalesBillMedId(Number(it.id))
                        const row = salesBill.items?.find(
                          (x) => Number(x.medicine_id) === Number(it.id),
                        )
                        if (row) {
                          setSalesReturnQtyInput(
                            String(
                              salesRemainingQty(
                                row.medicine_id,
                                row.remaining_qty,
                              ),
                            ),
                          )
                        }
                      }}
                      onEnter={addSalesReturnItem}
                    />
                  </Field>
                  <Field label="Return Qty">
                    <input
                      className="settings-input"
                      type="number"
                      min={0}
                      value={salesReturnQtyInput}
                      onChange={(e) => setSalesReturnQtyInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          addSalesReturnItem()
                        }
                      }}
                    />
                    <span className="mono" data-tablet-note="1">
                      {(() => {
                        const row = salesBill?.items?.find(
                          (x) => Number(x.medicine_id) === salesBillMedId,
                        )
                        return row
                          ? tabletCountNote(salesReturnQtyInput, row.is_tablet, row.tablets_per_stripe, 'tablet')
                          : ''
                      })()}
                    </span>
                  </Field>
                  <ActionBar>
                    <ActionBtn
                      label="Add to Return"
                      variant="primary"
                      onClick={addSalesReturnItem}
                    />
                  </ActionBar>
                </FilterBar>
                {salesPickInfo ? <Note>{salesPickInfo}</Note> : null}
                <div
                  ref={salesQtyTableRef}
                  className="settings-table-wrap"
                  tabIndex={-1}
                  data-return-focus="qty"
                >
                  <table className="settings-table">
                    <thead>
                      <tr>
                        <th>Medicine</th>
                        <th>Batch</th>
                        <th>Qty Sold</th>
                        <th>Returnable</th>
                        <th>Rate</th>
                        <th>Type</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(salesBill.items || []).map((it) => (
                        <tr
                          key={it.medicine_id}
                          className={
                            salesBillMedPick === it.name ? 'row-active' : ''
                          }
                          onClick={() => {
                            setSalesBillMedPick(it.name)
                            setSalesBillMedId(Number(it.medicine_id))
                            setSalesReturnQtyInput(
                              String(
                                salesRemainingQty(
                                  it.medicine_id,
                                  it.remaining_qty,
                                ),
                              ),
                            )
                          }}
                        >
                          <td>{it.name}</td>
                          <td>{it.batch}</td>
                          <td>{it.orig_qty}</td>
                          <td>
                            {salesRemainingQty(
                              it.medicine_id,
                              it.remaining_qty,
                            )}
                          </td>
                          <td>{money(it.rate || 0)}</td>
                          <td>{it.type || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <Note>Load a bill, then pick a medicine from the loaded bill.</Note>
            )}
          </SectionFrame>

          <SectionFrame title="Step 3 — Items to return">
            {salesReturnItems.length ? (
              <div className="settings-table-wrap">
                <table className="settings-table">
                  <thead>
                    <tr>
                      <th>Medicine</th>
                      <th>Batch</th>
                      <th>Return Qty</th>
                      <th>Rate</th>
                      <th>Amount</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {salesReturnItems.map((it) => (
                      <tr key={it.medicine_id}>
                        <td>{it.name}</td>
                        <td>{it.batch}</td>
                        <td>{tabletCountNote(it.qty, it.is_tablet, it.tablets_per_stripe, 'tablet') || it.qty}</td>
                        <td>{money(it.rate)}</td>
                        <td>{money(it.amount)}</td>
                        <td>
                          <ActionBtn
                            label="Remove"
                            variant="neutral"
                            onClick={() => removeSalesReturnItem(it.medicine_id)}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Note>No items added yet.</Note>
            )}
          </SectionFrame>

          <SectionFrame title="Step 4 — Confirm & save">
            <FilterBar>
              <Field label="Discount %">
                <input
                  className="settings-input"
                  type="number"
                  value={salesDisc}
                  onChange={(e) => setSalesDisc(e.target.value)}
                />
              </Field>
              <Field label="Reason">
                <input
                  className="settings-input"
                  value={salesReason}
                  onChange={(e) => setSalesReason(e.target.value)}
                />
              </Field>
            </FilterBar>
            {/* Out of the filter row and up against the Save button, in the
                words the counter uses. The choice was always here and always
                defaulted to credit -- but it sat fourth in a row of filters
                labelled "Give refund to customer / Keep on ledger (reduce due
                / add credit)", which is an accountant's sentence, so the shop
                reported the option as missing. */}
            <div className="settings-inline-row">
              <Field label="How is the money settled?" className="field-refund-settle">
                <select
                  className="settings-input"
                  value={salesSettle}
                  onChange={(e) =>
                    setSalesSettle(e.target.value as RefundSettle)
                  }
                >
                  <option value="ledger">
                    Keep as this customer&apos;s credit — no cash given
                  </option>
                  <option value="cash">Give cash back now</option>
                  <option value="online">Send online / UPI now</option>
                </select>
              </Field>
            </div>
            <Note>Estimated refund: {money(salesRefundPreview)}</Note>
            {salesSettleHint ? <Note>{salesSettleHint}</Note> : null}
            <ActionBar>
              {/* The key was always bound and never shown. The one button on
                  this row that named its key was Clear, so a shop that could
                  not reach this button had no idea F5 already did the job. */}
              <ActionBtn
                label={saving ? 'Saving…' : 'Save Sales Return'}
                kbd="F5"
                variant="primary"
                disabled={saving}
                onClick={() => void saveSales()}
              />
              <ActionBtn
                label="Clear [F6]"
                variant="neutral"
                disabled={saving}
                onClick={clearSalesForm}
              />
              <ActionBtn
                label="Delete Return"
                variant="warning"
                disabled={saving || salesHistSel == null}
                onClick={() => void deleteSelectedSalesReturn()}
              />
            </ActionBar>
            <div ref={salesHistoryRef} tabIndex={-1} data-return-focus="history">
              <DataTable
                columns={
                  data?.sales_returns.columns?.length
                    ? data.sales_returns.columns
                    : ['Return No', 'Date', 'Bill No', 'Customer', 'Refund', 'Reason']
                }
                rows={data?.sales_returns.rows || []}
                empty="No recent sales returns"
                selectedRowIndex={salesHistSel}
                onRowClick={setSalesHistSel}
              />
            </div>
          </SectionFrame>
        </>
      )}

      {tab === 'purchase' && (
        <>
          <SectionFrame title="Step 1 — Find original purchase">
            <div ref={purchaseBillListRef}>
            <FilterBar>
              <Field label="Purchase No / Supplier">
                <ModernCombo
                  value={purchaseQuery}
                  placeholder="Search purchase…"
                  voiceField="search"
                  minChars={0}
                  filterLocal
                  openOnFocus
                  listLabel="Recent purchases"
                  keepOrder
                  items={purchaseBills.map((b) => ({
                    id: String(b.purchase_id),
                    label: b.label,
                  }))}
                  onFocus={() => {
                    if (!purchaseBill) void searchPurchase()
                  }}
                  onChange={setPurchaseQuery}
                  onPick={(it) => {
                    setPurchaseQuery(it.label)
                    void loadPurchase(Number(it.id))
                  }}
                  onEnter={(picked) => {
                    if (picked) return
                    const match = matchLabeledBill(purchaseBills, purchaseQuery)
                    if (match) void loadPurchase(match.purchase_id)
                    else void searchPurchase()
                  }}
                />
              </Field>
              <ActionBar>
                <ActionBtn
                  label="Load Purchase"
                  onClick={() => {
                    const match = matchLabeledBill(purchaseBills, purchaseQuery)
                    if (match) void loadPurchase(match.purchase_id)
                    else void searchPurchase()
                  }}
                />
              </ActionBar>
            </FilterBar>
            </div>
            {purchaseBillInfo ? <Note>{purchaseBillInfo}</Note> : null}
          </SectionFrame>

          <SectionFrame title="Step 2 — Add return (pick medicine → qty → Enter)">
            {purchaseBill ? (
              <>
                <FilterBar>
                  <Field label="Medicine in purchase">
                    <ModernCombo
                      value={purchaseBillMedPick}
                      placeholder="Pick medicine from purchase…"
                      minChars={0}
                      filterLocal
                      openOnFocus
                      listLabel="Purchase medicines"
                      items={(purchaseBill.items || []).map((it) => ({
                        id: String(it.medicine_id),
                        label: it.name,
                        meta: `${it.batch} · rem ${purchaseRemainingQty(it.medicine_id, it.remaining_qty)}`,
                      }))}
                      onChange={(v) => {
                        setPurchaseBillMedPick(v)
                        setPurchaseBillMedId(null)
                      }}
                      onPick={(it) => {
                        setPurchaseBillMedPick(it.label)
                        setPurchaseBillMedId(Number(it.id))
                        const row = purchaseBill.items?.find(
                          (x) => Number(x.medicine_id) === Number(it.id),
                        )
                        if (row) {
                          setPurchaseReturnQtyInput(
                            String(
                              purchaseRemainingQty(
                                row.medicine_id,
                                row.remaining_qty,
                              ),
                            ),
                          )
                        }
                      }}
                      onEnter={addPurchaseReturnItem}
                    />
                  </Field>
                  <Field label="Return Qty">
                    <input
                      className="settings-input"
                      type="number"
                      min={0}
                      value={purchaseReturnQtyInput}
                      onChange={(e) =>
                        setPurchaseReturnQtyInput(e.target.value)
                      }
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          addPurchaseReturnItem()
                        }
                      }}
                    />
                    <span className="mono" data-tablet-note="1">
                      {(() => {
                        const row = purchaseBill?.items?.find(
                          (x) => Number(x.medicine_id) === purchaseBillMedId,
                        )
                        return row
                          ? tabletCountNote(purchaseReturnQtyInput, row.is_tablet, row.tablets_per_stripe)
                          : ''
                      })()}
                    </span>
                  </Field>
                  <ActionBar>
                    <ActionBtn
                      label="Add to Return"
                      variant="primary"
                      onClick={addPurchaseReturnItem}
                    />
                  </ActionBar>
                </FilterBar>
                {purchasePickInfo ? <Note>{purchasePickInfo}</Note> : null}
                <div
                  ref={purchaseQtyTableRef}
                  className="settings-table-wrap"
                  tabIndex={-1}
                  data-return-focus="qty"
                >
                  <table className="settings-table">
                    <thead>
                      <tr>
                        <th>Medicine</th>
                        <th>Batch</th>
                        <th>Qty Purchased</th>
                        <th>Returnable</th>
                        <th>Rate</th>
                        <th>Type</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(purchaseBill.items || []).map((it) => (
                        <tr
                          key={it.medicine_id}
                          className={
                            purchaseBillMedPick === it.name ? 'row-active' : ''
                          }
                          onClick={() => {
                            setPurchaseBillMedPick(it.name)
                            setPurchaseReturnQtyInput(
                              String(
                                purchaseRemainingQty(
                                  it.medicine_id,
                                  it.remaining_qty,
                                ),
                              ),
                            )
                          }}
                        >
                          <td>{it.name}</td>
                          <td>{it.batch}</td>
                          <td>{it.orig_qty}</td>
                          <td>
                            {purchaseRemainingQty(
                              it.medicine_id,
                              it.remaining_qty,
                            )}
                          </td>
                          <td>{money(it.rate || 0)}</td>
                          <td>{it.type || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <Note>
                Load a purchase, then pick a medicine from the loaded bill.
              </Note>
            )}
          </SectionFrame>

          <SectionFrame title="Step 3 — Items to return">
            {purchaseReturnItems.length ? (
              <div className="settings-table-wrap">
                <table className="settings-table">
                  <thead>
                    <tr>
                      <th>Medicine</th>
                      <th>Batch</th>
                      <th>Return Qty</th>
                      <th>Rate</th>
                      <th>Amount</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {purchaseReturnItems.map((it) => (
                      <tr key={it.medicine_id}>
                        <td>{it.name}</td>
                        <td>{it.batch}</td>
                        <td>{tabletCountNote(it.qty, it.is_tablet, it.tablets_per_stripe) || it.qty}</td>
                        <td>{money(it.rate)}</td>
                        <td>{money(it.amount)}</td>
                        <td>
                          <ActionBtn
                            label="Remove"
                            variant="neutral"
                            onClick={() =>
                              removePurchaseReturnItem(it.medicine_id)
                            }
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Note>No items added yet.</Note>
            )}
          </SectionFrame>

          <SectionFrame title="Step 4 — Confirm & save">
            <FilterBar>
              <Field label="Discount">
                <input
                  className="settings-input"
                  type="number"
                  value={purchaseDisc}
                  onChange={(e) => setPurchaseDisc(e.target.value)}
                />
              </Field>
              <Field label="Reason">
                <input
                  className="settings-input"
                  value={purchaseReason}
                  onChange={(e) => setPurchaseReason(e.target.value)}
                />
              </Field>
            </FilterBar>
            <Note>Estimated credit to supplier: {money(purchaseRefundPreview)}</Note>
            <ActionBar>
              <ActionBtn
                label={
                  saving
                    ? 'Saving…'
                    : editReturn
                      ? `Save changes to ${editReturn.returnNo}`
                      : 'Save Purchase Return'
                }
                kbd="F5"
                variant="primary"
                disabled={saving}
                onClick={() => void savePurchase()}
              />
              <ActionBtn
                label="Clear [F6]"
                variant="neutral"
                disabled={saving}
                onClick={clearPurchaseForm}
              />
              <ActionBtn
                label="View Details"
                variant="neutral"
                disabled={saving || purchaseHistSel == null}
                onClick={() => void viewPurchaseReturnDetails()}
              />
              <ActionBtn
                label="Save PDF"
                variant="neutral"
                disabled={saving || purchaseHistSel == null}
                onClick={() => void saveSelectedPurchaseReturnPdf()}
              />
              <ActionBtn
                label="Load Purchase"
                variant="neutral"
                disabled={saving || purchaseHistSel == null}
                onClick={() => void loadPurchaseReturnFromHistory()}
              />
              <ActionBtn
                label="Edit Return"
                variant="neutral"
                disabled={saving || purchaseHistSel == null}
                onClick={() => {
                  const id = selectedPurchaseReturnId()
                  if (id) void editPurchaseReturnById(id)
                }}
              />
              <ActionBtn
                label="Delete Return"
                variant="warning"
                disabled={saving || purchaseHistSel == null}
                onClick={() => void deleteSelectedPurchaseReturn()}
              />
            </ActionBar>
            <div ref={purchaseHistoryRef} tabIndex={-1} data-return-focus="history">
              <DataTable
                columns={
                  data?.purchase_returns.columns?.length
                    ? data.purchase_returns.columns
                    : [
                        'Return No',
                        'Date',
                        'Purchase No',
                        'Supplier',
                        'Credit',
                        'Reason',
                      ]
                }
                rows={data?.purchase_returns.rows || []}
                empty="No recent purchase returns"
                selectedRowIndex={purchaseHistSel}
                onRowClick={setPurchaseHistSel}
              />
            </div>
          </SectionFrame>
        </>
      )}

      {tab === 'disposal' && (
        <>
          <SectionFrame title="Return / Write-off">
            <FilterBar>
              <Field label="Medicine">
                <ModernCombo
                  value={dispMed}
                  filterLocal
                  listLabel="Medicines"
                  items={medSuggestions.map((m, i) => ({
                    id: String(i),
                    label: m.name,
                    meta: m.batch || undefined,
                  }))}
                  onChange={(v) => void onDispMedPick(v)}
                  onPick={(item) => {
                    // Carry the batch the row was showing straight into the
                    // Batch box. Splitting it back out of the label lost it,
                    // and lost any medicine whose own name holds a bracket.
                    const row = medSuggestions[Number(item.id)]
                    setDispMed(item.label)
                    if (row?.batch) setDispBatch(row.batch)
                  }}
                  placeholder="Select medicine…"
                  minChars={0}
                  openOnFocus
                />
              </Field>
              <Field label="Batch">
                <input
                  className="settings-input"
                  value={dispBatch}
                  onChange={(e) => setDispBatch(e.target.value)}
                />
              </Field>
              <Field label="Qty">
                <input
                  className="settings-input"
                  type="number"
                  min={0}
                  step="0.01"
                  value={dispQty}
                  onChange={(e) => setDispQty(e.target.value)}
                />
              </Field>
              <Field label="Mode">
                <select
                  className="settings-input"
                  value={dispMode}
                  onChange={(e) =>
                    setDispMode(e.target.value as 'writeoff' | 'return')
                  }
                >
                  <option value="writeoff">Write-off</option>
                  <option value="return">Purchase return (disposal)</option>
                </select>
              </Field>
              <Field label="Reason">
                <input
                  className="settings-input"
                  value={dispReason}
                  onChange={(e) => setDispReason(e.target.value)}
                />
              </Field>
            </FilterBar>
            <ActionBar>
              <ActionBtn label="Lookup" onClick={() => void lookupDisposal()} />
              <ActionBtn
                label="Submit"
                variant="primary"
                disabled={saving}
                onClick={() => void saveDisposal()}
              />
              <ActionBtn
                label="Clear [F6]"
                variant="neutral"
                disabled={saving}
                onClick={clearDisposalForm}
              />
            </ActionBar>
            <div ref={disposalHistoryRef} tabIndex={-1} data-return-focus="history">
              <DataTable
                columns={
                  data?.writeoffs.columns?.length
                    ? data.writeoffs.columns
                    : ['Disposal No', 'Date', 'Qty', 'Reason']
                }
                rows={data?.writeoffs.rows || []}
                empty="No recent write-offs"
              />
            </div>
          </SectionFrame>
        </>
      )}

      {tab === 'bulk' && (
        <>
          <SectionFrame title="Near / Expiry — grouped by purchase bill">
            <Note>
              Same flow as classic Bulk Purchase Return: one tab per purchase
              bill, then write-offs for stock with no purchase record.
            </Note>
            <ActionBar>
              <ActionBtn
                label={bulkLoading ? 'Loading…' : 'Load candidates'}
                disabled={bulkLoading || saving}
                onClick={() => void loadBulkPrefill()}
              />
              <ActionBtn
                label="Save all"
                variant="primary"
                disabled={saving || !bulkData}
                onClick={() => void saveBulkAll()}
              />
              <ActionBtn
                label="Clear [F6]"
                variant="neutral"
                disabled={saving}
                onClick={clearBulkForm}
              />
            </ActionBar>
            {bulkGroups.length ? (
              <>
                <FilterBar>
                  <Field label="Purchase bill">
                    <select
                      className="settings-input"
                      value={bulkIdx}
                      onChange={(e) => setBulkIdx(Number(e.target.value))}
                    >
                      {bulkGroups.map((g, i) => (
                        <option key={g.purchase_id} value={i}>
                          {g.bill_number || `#${g.purchase_id}`} —{' '}
                          {g.supplier_name || 'Supplier'}
                        </option>
                      ))}
                    </select>
                  </Field>
                </FilterBar>
                {activeBulkGroup ? (
                  <>
                    <Note>
                      {activeBulkGroup.bill_number} —{' '}
                      {activeBulkGroup.supplier_name} · Reason:{' '}
                      {activeBulkGroup.reason || 'Near expiry / Expired stock'}
                    </Note>
                    <div
                      ref={bulkItemsRef}
                      className="settings-table-wrap"
                      tabIndex={-1}
                      data-return-focus="bulk-items"
                    >
                      <table className="settings-table">
                        <thead>
                          <tr>
                            <th>Medicine</th>
                            <th>Batch</th>
                            <th>Return qty</th>
                            <th>Rate</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(activeBulkGroup.items || []).map((it) => (
                            <tr key={`${it.medicine_id}-${it.batch}`}>
                              <td>{it.name}</td>
                              <td>{it.batch}</td>
                              <td>{it.qty ?? it.remaining_qty}</td>
                              <td>{money(it.rate || 0)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                ) : null}
              </>
            ) : (
              <Note>No bulk return data loaded.</Note>
            )}
            {bulkWriteoffs.length ? (
              <SectionFrame title="Write-off (no purchase record)">
                <div
                  ref={bulkWriteoffRef}
                  className="settings-table-wrap"
                  tabIndex={-1}
                  data-return-focus="bulk-writeoff"
                >
                  <table className="settings-table">
                    <thead>
                      <tr>
                        <th>Medicine</th>
                        <th>Batch</th>
                        <th>Qty</th>
                        <th>Tag</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bulkWriteoffs.map((line, i) => (
                        <tr key={`${line.medicine_id}-${i}`}>
                          <td>{line.medicine_name}</td>
                          <td>{line.batch_no}</td>
                          <td>{line.quantity}</td>
                          <td>{line.reason_tag}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </SectionFrame>
            ) : null}
          </SectionFrame>
        </>
      )}

      <AlertDialog alert={alert} onClose={() => setAlert(null)} />
      {purchaseDetail ? (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-card" role="dialog" aria-modal="true">
            <h3>Return {purchaseDetail.return_no}</h3>
            <p className="muted">
              Date: {purchaseDetail.return_date} | Purchase:{' '}
              {purchaseDetail.purchase_no} | Supplier: {purchaseDetail.supplier}
              <br />
              Credit: ₹{money(purchaseDetail.refund_amount)} | Reason:{' '}
              {purchaseDetail.reason || '—'}
            </p>
            <div className="settings-table-wrap">
              <table className="settings-table">
                <thead>
                  <tr>
                    <th>Medicine</th>
                    <th>Batch</th>
                    <th>Qty</th>
                    <th>Rate</th>
                    <th>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {purchaseDetail.items.map((it, i) => (
                    <tr key={`${it.name}-${i}`}>
                      <td>{it.name}</td>
                      <td>{it.batch || '—'}</td>
                      <td>{it.qty.toFixed(2)}</td>
                      <td>{it.rate.toFixed(2)}</td>
                      <td>{it.amount.toFixed(2)}</td>
                    </tr>
                  ))}
                  {!purchaseDetail.items.length ? (
                    <tr>
                      <td colSpan={5}>No lines</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            <ActionBar>
              <ActionBtn
                label="Close"
                variant="neutral"
                onClick={() => setPurchaseDetail(null)}
              />
            </ActionBar>
          </div>
        </div>
      ) : null}
    </PageRoot>
  )
}
