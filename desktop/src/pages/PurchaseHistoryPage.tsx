import { useCallback, useEffect, useState, useRef } from 'react'
import { useDebouncedFilterEffect, useFilterEffect } from '../hooks/useLiveFilters'
import { DayPresetRow } from '../components/DayPresets'
import { isAllValue, pickOption, presetRange, RANGE_WORDS, registerVoicePage, type VoiceHandler } from '../voice/voiceBus'
import type { AppNavigate } from '../App'
import { ensureLocalEngine } from '../backend'
import { usePageHotkeys, focusPageFilter } from '../hooks/usePageHotkeys'
import { fetchPurchaseHistory, fetchPurchaseForm, deletePurchaseBill, type TablePayload } from '../pagesApi'
import { useLayoutRowCount } from '../layoutRows'
import { useDashboardSection } from '../dashboardSections'
import { ExportDialog } from './ExportDialog'
import { RowContextMenu } from './MedicineEditDialog'
import { AlertDialog, type AlertState } from './SalesDialogs'
import { ModernCombo } from './ModernCombo'
import {
  ActionBar,
  ActionBtn,
  DataTable,
  PURCHASE_HISTORY_COLUMN_WIDTHS,
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

export function PurchaseHistoryPage({
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
  const [supplier, setSupplier] = useState('')
  const [medicine, setMedicine] = useState('')
  const [batch, setBatch] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [due, setDue] = useState('')
  const [schedule, setSchedule] = useState('')
  const [sort, setSort] = useState(DEFAULT_SORT[0])
  const [data, setData] = useState<TablePayload | null>(null)
  const [choiceSuppliers, setChoiceSuppliers] = useState<string[]>([])
  const [choiceMedicines, setChoiceMedicines] = useState<string[]>([])
  const historyRows = useLayoutRowCount('purchase_history_rows')
  const showSummary = useDashboardSection('purchase_summary')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const [selectedRow, setSelectedRow] = useState<number | null>(null)
  const [alert, setAlert] = useState<AlertState | null>(null)
  const [ctx, setCtx] = useState<{
    x: number
    y: number
    purchaseId: number
  } | null>(null)

  /** True while the engine's default FY window is being written back into the
   *  date boxes — not the shop changing a filter, so it must not reload. */
  const seedingDatesRef = useRef(false)

  const filters = () => ({
    q,
    supplier,
    medicine,
    batch,
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
      const payload = await fetchPurchaseHistory(active)
      if (seq !== loadSeqRef.current) return
      setData((prev) => ({
        ...payload,
        filter_choices: payload.filter_choices || prev?.filter_choices,
      }))
      // An Online read that failed is not an empty day. Say so instead of
      // drawing a healthy-looking screen with no bills in it.
      if (payload.server_error) setError(payload.server_error)
      if (payload.filter_choices?.suppliers?.length) {
        setChoiceSuppliers(payload.filter_choices.suppliers)
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
    void fetchPurchaseForm()
      .then((form) => {
        setChoiceSuppliers((cur) =>
          cur.length
            ? cur
            : (form.suppliers || []).map((s) => s.name).filter(Boolean),
        )
        setChoiceMedicines((cur) =>
          cur.length
            ? cur
            : (form.medicines || [])
                .map((m) => m.name)
                .filter(Boolean),
        )
      })
      .catch(() => {
        /* history payload may still fill the lists */
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!syncRefreshNonce) return
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
    // supplier is NOT in here: it is a free-text combo whose onChange fires on
    // every character, so instant meant one full history query per keystroke --
    // and online that is four store-server calls each time. Its sibling Medicine
    // box was debounced all along; this one was mis-classified as a picker.
  }, [due, schedule, sort, from, to])
  useDebouncedFilterEffect(() => void load(), [q, medicine, batch, supplier])

  const idAt = useCallback(
    (rowIndex: number) => {
      const id = data?.row_ids?.[rowIndex]
      return id && id > 0 ? id : null
    },
    [data?.row_ids],
  )

  const openPurchaseEdit = (rowIndex: number) => {
    const id = idAt(rowIndex)
    if (!id) return
    onNavigate('purchase', { purchaseId: id })
  }

  const confirmDelete = (purchaseId: number) => {
    setAlert({
      title: 'Delete Purchase',
      message: 'Delete this purchase? Stock quantities will be reduced.',
      kind: 'confirm',
      confirmLabel: 'Delete',
      onConfirm: () => {
        void (async () => {
          try {
            const res = await deletePurchaseBill(purchaseId)
            if (!res.ok) {
              setAlert({
                title: 'Delete Failed',
                message: res.error || 'Could not delete purchase.',
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
      if (e.key !== 'Delete') return
      const id =
        ctx?.purchaseId ??
        (selectedRow != null ? idAt(selectedRow) : null)
      if (id) {
        e.preventDefault()
        confirmDelete(id)
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

  usePageHotkeys({
    // The hook has always taken this; no page passed it, so F5 and the
    // rest fired on every page that had ever been opened.
    enabled: active,
    onExport: () => setExportOpen(true),
    onFocusFilter: () => focusPageFilter(),
    onApplyFilter: () => void load(),
    onClearFilter: () => {
      setQ('')
      setSupplier('')
      void load()
    },
  })

  const clear = () => {
    setQ('')
    setSupplier('')
    setMedicine('')
    setBatch('')
    setFrom('')
    setTo('')
    setDue('')
    setSchedule('')
    setSort(DEFAULT_SORT[0])
    void load({
      q: '',
      supplier: '',
      medicine: '',
      batch: '',
      from: '',
      to: '',
      due: '',
      schedule: '',
      sort: DEFAULT_SORT[0],
    })
  }

  // ── Voice (test build): "aajche bill", "due only", "export ughad" ──
  // Sets the same state the preset buttons, dropdowns and buttons above set.
  const voiceHandlerRef = useRef<VoiceHandler>(async () => null)
  voiceHandlerRef.current = async (cmd) => {
    const a = cmd.args || {}
    const PAGE_SAY = 'Purchase History'
    if (cmd.intent === 'page_filter') {
      const f = String(a.filter || '')
      const all = isAllValue(a.value)
      if (f === 'clear') {
        clear()
        return { ok: true, say: `${PAGE_SAY}: sagle filter kadhle` }
      }
      if (f === 'range') {
        const r = presetRange(a.value)
        if (!r) return { ok: false, say: `"${a.value}" he divas samajle nahi` }
        // Exactly what the Today / Yesterday / … buttons do.
        seedingDatesRef.current = false
        setFrom(r.from)
        setTo(r.to)
        return { ok: true, say: `${PAGE_SAY}: ${RANGE_WORDS[r.key]}` }
      }
      if (f === 'due') {
        const v = all ? '' : pickOption(a.value, DUE_OPTIONS.map((o) => o.value))
        if (v == null) return { ok: false, say: `Due filter "${a.value}" nahi` }
        setDue(v)
        return { ok: true, say: `${PAGE_SAY} due: ${v || 'All'}` }
      }
      if (f === 'schedule') {
        const v = all ? '' : pickOption(a.value, schedules)
        if (v == null) return { ok: false, say: `Schedule "${a.value}" yaadit nahi` }
        setSchedule(v)
        return { ok: true, say: `${PAGE_SAY} schedule: ${v || 'All'}` }
      }
      if (f === 'sort') {
        const v = pickOption(a.value, sortOptions)
        if (v == null) return { ok: false, say: `Sort "${a.value}" nahi` }
        setSort(v)
        return { ok: true, say: `${PAGE_SAY} kram: ${v}` }
      }
      return { ok: false, say: `${PAGE_SAY} var "${f}" filter nahi` }
    }
    if (cmd.intent === 'search') {
      setQ(String(a.query || ''))
      return { ok: true, say: `${PAGE_SAY} shodh: ${a.query}` }
    }
    if (cmd.intent === 'page_action') {
      const act = String(a.action || '')
      if (act === 'export') {
        setExportOpen(true)
        return { ok: true, say: 'Purchase History export ughadla' }
      }
      if (act === 'refresh') {
        void load()
        return { ok: true, say: 'Purchase History refresh kela' }
      }
      return { ok: false, say: `Purchase History var "${act}" he kaam voice var nahi` }
    }
    return null
  }
  useEffect(() => registerVoicePage('purchase_history', () => voiceHandlerRef.current), [])

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
              placeholder="Purchase no / bill no / supplier"
              data-voice-field="search"
            />
          </Field>
          <Field label="Supplier">
            <ModernCombo
              value={supplier}
              onChange={setSupplier}
              // Just set it. The debounced effect above is the one loader for
              // this box -- picking used to fire onPick's explicit load, then
              // the effect, then onEnter: three identical history queries for
              // one Enter.
              onPick={(it) => setSupplier(it.label)}
              items={choiceSuppliers.map((n) => ({
                id: n,
                label: n,
              }))}
              minChars={0}
              filterLocal
              openOnFocus
              maxVisible={80}
              listLabel="Suppliers"
              placeholder="Supplier name"
              pageFilter
              voiceField="supplier"
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
              placeholder="Medicine on purchase"
              voiceField="medicine"
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
              placeholder="Batch on purchase"
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
              label="Clear Filter"
              variant="neutral"
              onClick={clear}
              disabled={loading}
            />
          </ActionBar>
        </FilterBar>
        <Note>
          Double-click or right-click → Edit opens the full Purchase page with
          the bill loaded — same add/edit/remove medicines and Update flow as
          Purchase entry.
        </Note>
      </SectionFrame>

      <StatusLine error={error} loading={loading && !data} />

      <SectionFrame title="Purchase History — Bills List" className="list-table-panel">
        {/* Same line as Sales History: a capped range, or a financial year that
            held nothing and was widened to every date. */}
        {data?.rows_note ? <Note>{data.rows_note}</Note> : null}
        <DataTable
          columns={data?.columns || []}
          rows={data?.rows || []}
          rowStyles={data?.row_styles}
          columnWidths={PURCHASE_HISTORY_COLUMN_WIDTHS}
          visibleRows={historyRows}
          empty={loading ? 'Loading…' : 'No purchase bills'}
          onRowDoubleClick={openPurchaseEdit}
          onRowClick={(i) => setSelectedRow(i)}
          selectedRowIndex={selectedRow}
          onRowContextMenu={(rowIndex, e) => {
            const id = idAt(rowIndex)
            if (!id) return
            setSelectedRow(rowIndex)
            setCtx({ x: e.clientX, y: e.clientY, purchaseId: id })
          }}
        />
      </SectionFrame>

      {showSummary ? (
        <SectionFrame title="Purchase Summary" className="list-summary-panel">
          <SummaryBar
            items={[
              { label: 'Bills', value: summary.bills ?? 0 },
              { label: 'Final Amount', value: money(summary.total) },
              { label: 'Paid at Entry', value: money(summary.paid) },
              { label: 'Entry Due', value: money(summary.due) },
              { label: 'Supplier Due', value: money(summary.supplier_due) },
              {
                label: 'Supplier Credit',
                value: money(summary.supplier_credit),
              },
            ]}
          />
        </SectionFrame>
      ) : null}

      <ExportDialog
        page="purchase_history"
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
            label: 'Edit on Purchase Page',
            onClick: () => {
              if (ctx) onNavigate('purchase', { purchaseId: ctx.purchaseId })
            },
          },
          {
            label: 'Delete Purchase',
            onClick: () => {
              if (ctx) confirmDelete(ctx.purchaseId)
            },
          },
        ]}
      />

      <AlertDialog alert={alert} onClose={() => setAlert(null)} />
    </PageRoot>
  )
}
