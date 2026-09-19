import { useCallback, useEffect, useRef, useState } from 'react'
import { useDebouncedFilterEffect, useFilterEffect } from '../hooks/useLiveFilters'
import { DayPresetRow } from '../components/DayPresets'
import type { AppNavigate } from '../App'
import { ensureLocalEngine } from '../backend'
import { usePageHotkeys, focusPageFilter } from '../hooks/usePageHotkeys'
import {
  deleteSalesBill,
  fetchBillDetails,
  fetchBillPreview,
  fetchPrintAllCandidates,
  type PrintAllCandidate,
  fetchSalesForm,
  fetchSalesHistory,
  printAllSalesBills,
  printSalesBill,
  saveBillPdf,
  type BillDetails,
  type TablePayload,
} from '../pagesApi'
import { ExportDialog } from './ExportDialog'
import { BillPreviewDialog } from './BillPreviewDialog'
import { RowContextMenu } from './MedicineEditDialog'
import { AlertDialog, type AlertState } from './SalesDialogs'
import { ModernCombo } from './ModernCombo'
import { useLayoutRowCount } from '../layoutRows'
import { useDashboardSection } from '../dashboardSections'
import {
  ActionBar,
  ActionBtn,
  DataTable,
  SALES_HISTORY_COLUMN_WIDTHS,
  Field,
  FilterBar,
  Note,
  PageRoot,
  SectionFrame,
  StatusLine,
  SummaryBar,
} from './pageChrome'

const DUE_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'Due Only', label: 'Due Only' },
  { value: 'Credit Only', label: 'Credit Only' },
  { value: 'Paid / Cleared', label: 'Paid / Cleared' },
]

// Mirrors SORT_OPTIONS in core/list_sort.py. Only used until the engine's own
// list arrives, but a short fallback hid the bill-number options on first paint.
const DEFAULT_SORT = [
  'Recent (newest first)',
  'Oldest first',
  'Bill No (high to low)',
  'Bill No (low to high)',
  'Alphabetic (A-Z)',
  'Alphabetic (Z-A)',
]

function money(n: number | string | undefined) {
  const v = Number(n) || 0
  return `₹${v.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function SalesHistoryPage({
  onNavigate,
  syncRefreshNonce = 0,
  active,
}: {
  onNavigate: AppNavigate
  syncRefreshNonce?: number
  /** Required on purpose. Every page stays mounted once visited, so a page
   *  that does not know whether it is on screen keeps answering the
   *  keyboard from behind another one. An optional prop defaulting to true
   *  let exactly that omission through the compiler. */
  active: boolean
}) {
  const [q, setQ] = useState('')
  const [medicine, setMedicine] = useState('')
  const [batch, setBatch] = useState('')
  const [customer, setCustomer] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [due, setDue] = useState('')
  const [schedule, setSchedule] = useState('')
  const [sort, setSort] = useState(DEFAULT_SORT[0])
  const [data, setData] = useState<TablePayload | null>(null)
  const [choiceCustomers, setChoiceCustomers] = useState<string[]>([])
  const [choiceMedicines, setChoiceMedicines] = useState<string[]>([])
  const historyRows = useLayoutRowCount('sales_history_rows')
  const showSummary = useDashboardSection('sales_summary')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const [printAllOpen, setPrintAllOpen] = useState(false)
  const [printAllBusy, setPrintAllBusy] = useState(false)
  // The bills themselves, not just the count. The count and the set that
  // actually printed used to be two independent derivations: the page threw the
  // list away and the engine went and queried again.
  const [printAllBills, setPrintAllBills] = useState<PrintAllCandidate[]>([])
  const [printAllPicked, setPrintAllPicked] = useState<Set<number>>(new Set())
  // The Print All dialog's own Paper select drives this; A6 is only the
  // opening value. (It used to be hardcoded all the way to the engine.)
  const [printAllPaper, setPrintAllPaper] = useState('A6')
  const [printAllSchedule, setPrintAllSchedule] = useState('')
  const [printAllUnapplied, setPrintAllUnapplied] = useState<string[]>([])
  const [selectedRow, setSelectedRow] = useState<number | null>(null)
  const [alert, setAlert] = useState<AlertState | null>(null)
  const [ctx, setCtx] = useState<{
    x: number
    y: number
    saleId: number
  } | null>(null)
  const [viewBill, setViewBill] = useState<BillDetails | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewSaleId, setPreviewSaleId] = useState<number | null>(null)
  const [previewHtml, setPreviewHtml] = useState('')
  const [previewBillNo, setPreviewBillNo] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [previewPrinting, setPreviewPrinting] = useState(false)

  /** True while the engine's default FY window is being written back into the
   *  date boxes. That is not the shop changing a filter, so it must not fire
   *  another load. */
  const seedingDatesRef = useRef(false)

  const filters = () => ({
    q,
    medicine,
    batch,
    customer,
    from,
    to,
    due,
    schedule,
    sort,
  })

  /** Only the newest filter change may paint. Now that the filters apply
   *  themselves, two loads can be in flight at once and the slower one used to
   *  be free to overwrite the newer answer. */
  const loadSeqRef = useRef(0)

  /** The history scope the date boxes were seeded from, or null once the shop
   *  has chosen its own dates. */
  const seededScopeRef = useRef<string | null>(null)

  const load = async (opts?: ReturnType<typeof filters>) => {
    const seq = ++loadSeqRef.current
    setLoading(true)
    setError('')
    try {
      const engine = await ensureLocalEngine()
      if (!engine.ok) {
        setError(engine.error)
        return
      }
      const active = opts ?? filters()
      const payload = await fetchSalesHistory(active)
      if (seq !== loadSeqRef.current) return
      setData((prev) => ({
        ...payload,
        filter_choices: payload.filter_choices || prev?.filter_choices,
      }))
      // An Online read that failed is not an empty day. Say so instead of
      // drawing a healthy-looking screen with no bills in it.
      if (payload.server_error) setError(payload.server_error)
      if (payload.filter_choices?.customers?.length) {
        setChoiceCustomers(payload.filter_choices.customers)
      }
      if (payload.filter_choices?.medicines?.length) {
        setChoiceMedicines(payload.filter_choices.medicines)
      }
      if (payload.filter_from && !(active.from || '').trim()) {
        seedingDatesRef.current = true
        setFrom(String(payload.filter_from))
      }
      if (payload.filter_to && !(active.to || '').trim()) {
        seedingDatesRef.current = true
        setTo(String(payload.filter_to))
      }
      // Remember which scope produced the dates sitting in the boxes. Changing
      // "Default history scope" in Settings did nothing until the app was
      // restarted, because the boxes still held the OLD scope's range and a
      // filled box is never re-seeded.
      if (seedingDatesRef.current) {
        seededScopeRef.current = String(payload.history_scope || '')
      }
    } catch (e) {
      if (seq !== loadSeqRef.current) return
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (seq === loadSeqRef.current) setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    void fetchSalesForm()
      .then((form) => {
        const fromDetails = (form.customer_details || [])
          .map((c) => c.name)
          .filter(Boolean)
        setChoiceCustomers((cur) =>
          cur.length ? cur : fromDetails.length ? fromDetails : form.customers || [],
        )
        setChoiceMedicines((cur) =>
          cur.length
            ? cur
            : (form.medicines || []).map((m) => m.name).filter(Boolean),
        )
      })
      .catch(() => {
        /* history payload may still fill the lists */
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!syncRefreshNonce) return
    // Always reload on server sync hints — do not skip while a prior load is running.
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncRefreshNonce])

  // Coming back to the page after the scope was changed in Settings: re-seed
  // from the new scope rather than showing the old one's range for ever.
  useEffect(() => {
    if (!active) return
    if (seededScopeRef.current === null) return
    if (String(data?.history_scope || '') === seededScopeRef.current) return
    seededScopeRef.current = null
    // Let the explicit load below be the only request: without this the from/to
    // change also trips the live-filter effect, and online that is a second
    // full batch of server calls for one navigation.
    seedingDatesRef.current = true
    setFrom('')
    setTo('')
    void loadRef.current({ ...filters(), from: '', to: '' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active])

  // This listener used to be registered once with the FIRST render's load in
  // its closure, so saving anything in Settings replayed the mount-time
  // filters -- and, because loadSeqRef is shared, that stale answer won the
  // race and silently threw away the dates the shop had just picked.
  const loadRef = useRef(load)
  loadRef.current = load
  useEffect(() => {
    const onLayout = () => void loadRef.current()
    window.addEventListener('satpuda:layout-config-changed', onLayout)
    return () =>
      window.removeEventListener('satpuda:layout-config-changed', onLayout)
  }, [])

  // Pickers and dates apply at once; the typed boxes wait for a pause.
  useFilterEffect(() => {
    if (seedingDatesRef.current) {
      seedingDatesRef.current = false
      return
    }
    void load()
    // customer is NOT in here: it is a free-text combo whose onChange fires on
    // every character, so instant meant one full history query per keystroke --
    // and online that is four store-server calls each time. Its sibling Medicine
    // box was debounced all along; this one was mis-classified as a picker.
  }, [due, schedule, sort, from, to])
  useDebouncedFilterEffect(() => void load(), [q, medicine, batch, customer])

  const idAt = useCallback(
    (rowIndex: number) => {
      const id = data?.row_ids?.[rowIndex]
      return id && id > 0 ? id : null
    },
    [data?.row_ids],
  )

  const selectedSaleId = useCallback(() => {
    if (ctx?.saleId) return ctx.saleId
    if (selectedRow != null) return idAt(selectedRow)
    return null
  }, [ctx, selectedRow, idAt])

  const printBill = async (slot: 1 | 2, mode: 'slot' | 'silent' = 'silent') => {
    const saleId = selectedSaleId()
    if (!saleId) {
      setAlert({
        title: 'Print',
        message: 'Select a bill row first (click a row).',
        kind: 'warning',
      })
      return
    }
    try {
      const res = await printSalesBill({ sale_id: saleId, slot, mode })
      if (!res.ok) {
        setAlert({
          title: 'Print Failed',
          message: res.error || 'Could not print bill.',
          kind: 'error',
        })
        return
      }
      setAlert({
        title: 'Print',
        message: `Bill sent to printer (slot ${slot}).`,
        kind: 'info',
      })
    } catch (e) {
      setAlert({
        title: 'Print Failed',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    }
  }

  const viewBillDetails = async (saleId: number) => {
    try {
      const res = await fetchBillDetails(saleId)
      if (!res.ok) {
        setAlert({
          title: 'View Bill',
          message: res.error || 'Could not load bill.',
          kind: 'warning',
        })
        return
      }
      setViewBill(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const openBillPreview = async (saleId: number) => {
    setPreviewOpen(true)
    setPreviewSaleId(saleId)
    setPreviewLoading(true)
    setPreviewError('')
    setPreviewHtml('')
    try {
      const res = await fetchBillPreview(saleId)
      if (!res.ok || !res.html) {
        setPreviewError(res.error || 'Preview unavailable.')
        return
      }
      setPreviewBillNo(res.bill_no || '')
      setPreviewHtml(res.html)
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : String(e))
    } finally {
      setPreviewLoading(false)
    }
  }

  const printFromPreview = async (slot: 1 | 2, mode: 'slot' | 'silent') => {
    if (!previewSaleId) return
    setPreviewPrinting(true)
    try {
      const res = await printSalesBill({ sale_id: previewSaleId, slot, mode })
      if (!res.ok) {
        setAlert({
          title: 'Print Failed',
          message: res.error || 'Could not print bill.',
          kind: 'error',
        })
        return
      }
      setAlert({
        title: 'Print',
        message: `Bill sent to printer (slot ${slot}).`,
        kind: 'info',
      })
    } catch (e) {
      setAlert({
        title: 'Print Failed',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setPreviewPrinting(false)
    }
  }

  const saveBillPdfAction = async (saleId: number) => {
    try {
      const res = await saveBillPdf(saleId)
      if (!res.ok) {
        setAlert({
          title: 'Save PDF',
          message: res.error || 'Could not save PDF.',
          kind: 'error',
        })
        return
      }
      setAlert({
        title: 'Save PDF',
        message: res.pdf_path ? `Saved to:\n${res.pdf_path}` : 'PDF saved.',
        kind: 'info',
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  usePageHotkeys({
    // The hook has always taken this; no page passed it, so F5 and the
    // rest fired on every page that had ever been opened.
    enabled: active,
    onExport: () => setExportOpen(true),
    onPrintLast: () => void printBill(2, 'silent'),
    onFocusFilter: () => focusPageFilter(),
    onApplyFilter: () => void load(),
    onClearFilter: () => {
      setQ('')
      setMedicine('')
      setBatch('')
      setCustomer('')
      void load()
    },
  })

  const confirmDelete = (saleId: number) => {
    const row = data?.rows?.find((_, i) => data?.row_ids?.[i] === saleId)
    const billNo = row ? String(row[0] ?? saleId) : String(saleId)
    setAlert({
      title: 'Delete Bill',
      message: `Delete bill ${billNo}? Stock will be restored.`,
      kind: 'confirm',
      confirmLabel: 'Delete',
      onConfirm: () => {
        void (async () => {
          try {
            const res = await deleteSalesBill(saleId)
            if (!res.ok) {
              setAlert({
                title: 'Delete Failed',
                message: res.error || 'Could not delete bill.',
                kind: 'error',
              })
              return
            }
            void load()
          } catch (e) {
            setAlert({
              title: 'Delete Failed',
              message: e instanceof Error ? e.message : String(e),
              kind: 'error',
            })
          }
        })()
      },
    })
  }

  const openPrintAll = async (sched = '', revertTo?: string) => {
    if (!from || !to) {
      setAlert({
        title: 'Print All Bills',
        message: 'Set From Date and To Date in filters first.',
        kind: 'warning',
      })
      return
    }
    setPrintAllBusy(true)
    try {
      // The whole filter bar: the picker used to offer every bill in the date
      // range while the list behind it showed the few the shop had filtered to.
      const res = await fetchPrintAllCandidates({
        from,
        to,
        schedule: sched,
        q,
        customer,
        medicine,
        batch,
        due,
      })
      if (!res.ok) {
        // Put the Schedule select back: leaving it changed over a list that did
        // not change is the screen telling the shop something untrue.
        if (revertTo !== undefined) setPrintAllSchedule(revertTo)
        setAlert({
          title: 'Print All',
          message: res.error || 'Could not load bills.',
          kind: 'error',
        })
        return
      }
      const bills = res.bills || []
      setPrintAllBills(bills)
      // Only what can actually be printed starts ticked.
      setPrintAllPicked(
        new Set(bills.filter((b) => b.printable !== false).map((b) => b.sale_id)),
      )
      setPrintAllUnapplied(res.unapplied_filters || [])
      setPrintAllOpen(true)
    } catch (e) {
      if (revertTo !== undefined) setPrintAllSchedule(revertTo)
      setAlert({
        title: 'Print All',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setPrintAllBusy(false)
    }
  }

  const runPrintAll = async () => {
    // Send the ids that were on screen. Re-querying by date is how the number
    // in the dialog and the paper coming out of the printer stopped agreeing.
    const ids = printAllBills
      .map((b) => b.sale_id)
      .filter((id) => printAllPicked.has(id))
    if (!ids.length) return
    setPrintAllBusy(true)
    try {
      const res = await printAllSalesBills({
        from,
        to,
        sale_ids: ids,
        paper: printAllPaper,
        slot: 2,
      })
      // Close either way: leaving all twenty still ticked behind the alert
      // makes "press Print again" the obvious move, and that reprints the ones
      // that already came out.
      setPrintAllOpen(false)
      setAlert({
        title: res.ok ? 'Print All' : 'Print All — errors',
        // Which bills failed, not just how many. A bare count leaves the shop
        // reprinting the whole range to find the gap.
        message: [res.message || res.error || 'Done.', ...(res.failures || [])]
          .filter(Boolean)
          .join('\n'),
        kind: res.ok ? 'info' : 'warning',
      })
    } catch (e) {
      setAlert({
        title: 'Print All Failed',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setPrintAllBusy(false)
    }
  }

  useEffect(() => {
    // Every page stays mounted once visited and is only hidden with
    // display:none, so a window listener on a hidden page still fires. Without
    // this, one F5 saved the sale AND the purchase, F3 opened a bill on a page
    // nobody was looking at, and F12 left a dialog waiting on the other screen.
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return
      }
      if (e.key === 'F7') {
        e.preventDefault()
        void printBill(1, 'slot')
      } else if (e.key === 'F8') {
        e.preventDefault()
        void printBill(2, 'slot')
      } else if (e.key === 'F9') {
        e.preventDefault()
        void printBill(2, 'silent')
      } else if (e.key === 'Delete') {
        const id = selectedSaleId()
        if (id) {
          e.preventDefault()
          confirmDelete(id)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const summary = data?.summary || {}
  const schedules = data?.schedules || []
  const sortOptions = data?.sort_options?.length
    ? data.sort_options
    : DEFAULT_SORT

  const clear = () => {
    setQ('')
    setMedicine('')
    setBatch('')
    setCustomer('')
    setFrom('')
    setTo('')
    setDue('')
    setSchedule('')
    setSort(DEFAULT_SORT[0])
    void load({
      q: '',
      medicine: '',
      batch: '',
      customer: '',
      from: '',
      to: '',
      due: '',
      schedule: '',
      sort: DEFAULT_SORT[0],
    })
  }

  return (
    <PageRoot className="list-page">
      <SectionFrame title="Filter Options">
        <DayPresetRow
          from={from}
          to={to}
          fyLabel={data?.fy_label}
          isDefault={Boolean(data?.default_fy_applied)}
          onPick={(f, t2) => {
            // Both fields, always: Export and Print All both refuse a half
            // filled range, and a single-day mode with its own third field
            // would break them silently.
            //
            // No explicit load() here: from/to are in the live-filter deps, so
            // the state change already fetches. Doing both sent two identical
            // queries -- and online that is ten Postgres round trips for one
            // click on "Today".
            seedingDatesRef.current = false
            setFrom(f)
            setTo(t2)
          }}
        />
        <FilterBar>
          <Field label="Search">
            <input
              className="settings-input"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void load()
              }}
              placeholder="Bill no / customer / phone / doctor"
            />
          </Field>
          <Field label="Medicine Name">
            <ModernCombo
              value={medicine}
              onChange={setMedicine}
              onPick={(it) => {
                setMedicine(it.label)
                void load({ ...filters(), medicine: it.label })
              }}
              onEnter={() => void load()}
              items={choiceMedicines.map((n) => ({
                id: n,
                label: n,
              }))}
              minChars={0}
              filterLocal
              openOnFocus
              maxVisible={80}
              listLabel="Medicines"
              placeholder="Search by medicine on bill"
            />
          </Field>
          <Field label="Batch Number">
            <input
              className="settings-input"
              value={batch}
              onChange={(e) => setBatch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void load()
              }}
              placeholder="Search by batch on bill"
            />
          </Field>
          <Field label="Customer">
            <ModernCombo
              value={customer}
              onChange={setCustomer}
              // Just set it. The debounced effect above is the one loader for
              // this box -- picking used to fire onPick's explicit load, then
              // the effect, then onEnter: three identical history queries for
              // one Enter.
              onPick={(it) => setCustomer(it.label)}
              items={choiceCustomers.map((n) => ({
                id: n,
                label: n,
              }))}
              minChars={0}
              filterLocal
              openOnFocus
              maxVisible={80}
              listLabel="Customers"
              placeholder="Customer name"
              pageFilter
            />
          </Field>
          <Field label="From Date">
            <input
              className="settings-input"
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </Field>
          <Field label="To Date">
            <input
              className="settings-input"
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </Field>
          <Field label="Due Status">
            <select
              className="settings-input"
              value={due}
              onChange={(e) => setDue(e.target.value)}
            >
              {DUE_OPTIONS.map((o) => (
                <option key={o.value || 'all'} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Schedule">
            <select
              className="settings-input"
              value={schedule}
              onChange={(e) => setSchedule(e.target.value)}
            >
              <option value="">All</option>
              {schedules.filter(Boolean).map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Sort">
            <select
              className="settings-input"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              {sortOptions.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>
          <ActionBar>
            <ActionBtn
              label="Apply Filter"
              onClick={() => void load()}
              disabled={loading}
            />
            <ActionBtn
              label="Export"
              variant="secondary"
              onClick={() => setExportOpen(true)}
              disabled={loading}
            />
            <ActionBtn
              label="Print All Bills"
              variant="secondary"
              onClick={() => {
                setPrintAllSchedule('')
                void openPrintAll()
              }}
              disabled={loading || printAllBusy}
            />
            <ActionBtn
              label="Clear Filter"
              variant="neutral"
              onClick={clear}
              disabled={loading}
            />
          </ActionBar>
        </FilterBar>
        <Note>
          Click a row, then F7/F8/F9 to print or Delete to remove. Double-click or
          right-click → Edit opens Sales with the bill loaded.
        </Note>
      </SectionFrame>

      <StatusLine error={error} loading={loading && !data} />

      <SectionFrame title="Sales History — Bills List" className="list-table-panel">
        {/* More bills in the range than the list holds: say so, never total a slice quietly. */}
        {data?.rows_note ? <Note>{data.rows_note}</Note> : null}
        <DataTable
          columns={data?.columns || []}
          rows={data?.rows || []}
          rowStyles={data?.row_styles}
          columnWidths={SALES_HISTORY_COLUMN_WIDTHS}
          visibleRows={historyRows}
          empty={loading ? 'Loading…' : 'No sales bills'}
          onRowDoubleClick={(rowIndex) => {
            const id = idAt(rowIndex)
            if (id) void viewBillDetails(id)
          }}
          onRowClick={(i) => setSelectedRow(i)}
          selectedRowIndex={selectedRow}
          onRowContextMenu={(rowIndex, e) => {
            const id = idAt(rowIndex)
            if (!id) return
            setSelectedRow(rowIndex)
            setCtx({ x: e.clientX, y: e.clientY, saleId: id })
          }}
        />
      </SectionFrame>

      {showSummary ? (
        <SectionFrame title="Sales Summary" className="list-summary-panel">
          <SummaryBar
            items={[
              { label: 'Bills', value: summary.bills ?? 0 },
              { label: 'Total Sales', value: money(summary.total) },
              { label: 'Discount', value: money(summary.discount) },
              { label: 'Amount Paid', value: money(summary.paid) },
              { label: 'Bill Due', value: money(summary.due) },
              { label: 'Customer Due', value: money(summary.customer_due) },
              { label: 'Profit', value: money(summary.profit) },
              { label: 'Returns', value: money(summary.returns) },
              { label: 'Today Revenue', value: money(summary.today_revenue) },
              { label: 'Today Cash', value: money(summary.today_cash) },
              { label: 'Today Online', value: money(summary.today_online) },
              { label: 'Month Revenue', value: money(summary.month_revenue) },
            ]}
          />
        </SectionFrame>
      ) : null}

      <ExportDialog
        page="sales_history"
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        from={from}
        to={to}
        currentColumns={data?.columns || []}
        currentRows={data?.rows || []}
      />

      <RowContextMenu
        menu={ctx}
        onClose={() => setCtx(null)}
        items={[
          {
            label: 'View Bill Details',
            onClick: () => {
              if (ctx) void viewBillDetails(ctx.saleId)
            },
          },
          {
            label: 'Bill Preview',
            onClick: () => {
              if (ctx) void openBillPreview(ctx.saleId)
            },
          },
          {
            label: 'Save PDF (A6)',
            onClick: () => {
              if (ctx) void saveBillPdfAction(ctx.saleId)
            },
          },
          {
            label: 'Edit on Sales Page',
            onClick: () => {
              if (ctx) onNavigate('sales', { saleId: ctx.saleId })
            },
          },
          {
            label: 'Print Sales 1 (F7)',
            onClick: () => {
              if (ctx) void printSalesBill({ sale_id: ctx.saleId, slot: 1, mode: 'slot' })
            },
          },
          {
            label: 'Print Sales 2 (F8)',
            onClick: () => {
              if (ctx) void printSalesBill({ sale_id: ctx.saleId, slot: 2, mode: 'slot' })
            },
          },
          {
            label: 'Silent Reprint (F9)',
            onClick: () => {
              if (ctx) void printSalesBill({ sale_id: ctx.saleId, slot: 2, mode: 'silent' })
            },
          },
          {
            label: 'Delete Bill',
            onClick: () => {
              if (ctx) confirmDelete(ctx.saleId)
            },
          },
        ]}
      />

      {printAllOpen ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setPrintAllOpen(false)}>
          <div
            className="modal-card modal-card-wide"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h2>Print All Bills</h2>
              <button type="button" className="icon-btn" onClick={() => setPrintAllOpen(false)}>
                ✕
              </button>
            </div>
            <div className="modal-body">
              <p className="muted">
                {from} to {to} · slot 2 · {printAllPicked.size} of{' '}
                {printAllBills.length} bill(s) selected
                {(() => {
                  // Mirrors the engine's own bills-per-sheet rule.
                  const per =
                    printAllPaper === 'A4' ? 4 : printAllPaper === 'A5' ? 2 : 1
                  const sheets = Math.ceil(printAllPicked.size / per)
                  return printAllPicked.size
                    ? ` · ≈ ${sheets} sheet(s) (${per} per ${printAllPaper})`
                    : ''
                })()}
              </p>
              {printAllBills.some((b) => b.printable === false) ? (
                <p className="muted printall-warn">
                  {printAllBills.filter((b) => b.printable === false).length}{' '}
                  bill(s) are waiting to sync and cannot be printed yet.
                </p>
              ) : null}
              {printAllUnapplied.length ? (
                <p className="muted printall-warn">
                  {printAllUnapplied.join(', ')} not applied here — this list
                  filters by bill and customer only.
                </p>
              ) : null}
              <div className="printall-controls">
                <label>
                  Paper
                  <select
                    className="settings-input"
                    value={printAllPaper}
                    onChange={(e) => setPrintAllPaper(e.target.value)}
                  >
                    <option value="A6">A6 — one bill per sheet</option>
                    <option value="A5">A5 — two per sheet</option>
                    <option value="A4">A4 — four per sheet</option>
                  </select>
                </label>
                <label>
                  Schedule
                  <select
                    className="settings-input"
                    value={printAllSchedule}
                    disabled={printAllBusy}
                    onChange={(e) => {
                      const s = e.target.value
                      setPrintAllSchedule(s)
                      // Ask the ENGINE, in both modes. A server sale row carries
                      // no per-bill schedule aggregate, so matching one here
                      // emptied the whole selection online; and "Non-Scheduled"
                      // is a real choice meaning "no code on any line", which no
                      // token comparison can ever satisfy.
                      void openPrintAll(s, printAllSchedule)
                    }}
                  >
                    <option value="">All schedules</option>
                    {schedules.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="btn btn-neutral btn-sm"
                  onClick={() =>
                    setPrintAllPicked(
                      // Same rule the row's own checkbox uses (it is disabled
                      // for these). Select all used to tick the unprintable
                      // bills too, so Print All was handed bills it could not
                      // produce and the run died partway through the batch.
                      new Set(
                        printAllBills
                          .filter((b) => b.printable !== false)
                          .map((b) => b.sale_id),
                      ),
                    )
                  }
                >
                  Select all
                </button>
                <button
                  type="button"
                  className="btn btn-neutral btn-sm"
                  onClick={() => setPrintAllPicked(new Set())}
                >
                  Clear all
                </button>
              </div>
              <div className="printall-list">
                {printAllBills.length === 0 ? (
                  <p className="muted">No bills in this range.</p>
                ) : (
                  printAllBills.map((b) => (
                    <label
                      key={b.sale_id}
                      className={`printall-row${b.printable === false ? ' is-unprintable' : ''}`}
                      title={b.reason || undefined}
                    >
                      <input
                        type="checkbox"
                        disabled={b.printable === false}
                        checked={printAllPicked.has(b.sale_id)}
                        onChange={(e) => {
                          const next = new Set(printAllPicked)
                          if (e.target.checked) next.add(b.sale_id)
                          else next.delete(b.sale_id)
                          setPrintAllPicked(next)
                        }}
                      />
                      <span className="printall-no">{b.bill_no}</span>
                      <span className="printall-date">{b.bill_date}</span>
                      <span className="printall-cust">{b.customer || '—'}</span>
                      <span className="printall-total">{money(b.total)}</span>
                      <span
                        className="printall-sched"
                        title={
                          b.schedules_known === false
                            ? 'Schedule not available from the server'
                            : undefined
                        }
                      >
                        {b.reason
                          ? b.reason
                          : b.schedules_known === false
                            ? '—'
                            : b.schedules || 'Non-Scheduled'}
                      </span>
                    </label>
                  ))
                )}
              </div>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn btn-neutral" onClick={() => setPrintAllOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={printAllBusy || printAllPicked.size === 0}
                onClick={() => void runPrintAll()}
              >
                Print {printAllPicked.size || ''}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {viewBill?.ok ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setViewBill(null)}>
          <div
            className="modal-card modal-card-wide"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h2>
                Bill {viewBill.bill_no} — {viewBill.customer}
              </h2>
              <button type="button" className="icon-btn" onClick={() => setViewBill(null)}>
                ✕
              </button>
            </div>
            <div className="modal-body">
              <p className="note">
                {viewBill.bill_date} · Dr {viewBill.doctor || '—'} · Paid{' '}
                {money(viewBill.amount_paid)} · Due {money(viewBill.due_amount)}
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
                    {(viewBill.items || []).map((it, i) => (
                      <tr key={i}>
                        <td>{it.name}</td>
                        <td>{it.batch}</td>
                        <td>{it.qty}</td>
                        <td>{money(it.rate)}</td>
                        <td>{money(it.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="modal-foot">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  if (viewBill.sale_id) void openBillPreview(viewBill.sale_id)
                }}
              >
                Preview
              </button>
              <button type="button" className="btn btn-primary" onClick={() => setViewBill(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <BillPreviewDialog
        open={previewOpen}
        billNo={previewBillNo}
        html={previewHtml}
        loading={previewLoading}
        error={previewError}
        saleId={previewSaleId ?? undefined}
        printing={previewPrinting}
        onPrint={(slot, mode) => void printFromPreview(slot, mode)}
        onClose={() => {
          setPreviewOpen(false)
          setPreviewSaleId(null)
        }}
      />

      <AlertDialog alert={alert} onClose={() => setAlert(null)} />
    </PageRoot>
  )
}
