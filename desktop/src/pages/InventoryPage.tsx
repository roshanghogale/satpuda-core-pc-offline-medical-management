import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useDebouncedFilterEffect, useFilterEffect } from '../hooks/useLiveFilters'
import type { AppNavigate } from '../App'
import { ensureLocalEngine } from '../backend'
import { usePageHotkeys, focusPageFilter } from '../hooks/usePageHotkeys'
import {
  deleteExpiredMedicines,
  deleteInventoryMedicine,
  deleteZeroStockMedicines,
  fetchInventory,
  fetchInventoryReorderPrefill,
  type TablePayload,
} from '../pagesApi'
import { ExportDialog } from './ExportDialog'
import { ExpiredReturnDialog, isExpiredReturnKey } from './ExpiredReturnDialog'
import {
  MedicineEditDialog,
  RowContextMenu,
} from './MedicineEditDialog'
import { AlertDialog, type AlertState } from './SalesDialogs'
import { ModernCombo } from './ModernCombo'
import { useLayoutRowCount } from '../layoutRows'
import { isAllValue, pickOption, registerVoicePage, type VoiceHandler } from '../voice/voiceBus'
import { useDashboardSection } from '../dashboardSections'
import {
  ActionBar,
  ActionBtn,
  DataTable,
  INVENTORY_COLUMN_WIDTHS,
  Field,
  FilterBar,
  Note,
  PageRoot,
  SectionFrame,
  StatusLine,
  SummaryBar,
} from './pageChrome'

/** Fallback for the Sort dropdown. The engine sends the real list as
 *  payload.sort_options (core/list_sort.py INVENTORY_SORT_OPTIONS); this is
 *  only what to show before the first answer arrives. The shelf is ordered by
 *  the ENGINE now -- the browser used to re-sort the rows here and leave
 *  row_ids and row_styles behind, so a right-click opened another medicine. */
const INVENTORY_SORTS = [
  'Best match',
  'Alphabetic (A-Z)',
  'Alphabetic (Z-A)',
  'Stock (low to high)',
  'Stock (high to low)',
  'Expiry (soonest first)',
  'Expiry (latest first)',
] as const

function colIndex(columns: string[] | undefined, ...names: string[]): number {
  const cols = (columns || []).map((c) => String(c).toLowerCase())
  // Exact first: "Supplier Name" also contains "name", and the shop can hide
  // the Name column, which would otherwise hand the delete prompt a supplier.
  for (const n of names) {
    const i = cols.indexOf(n)
    if (i >= 0) return i
  }
  for (const n of names) {
    const i = cols.findIndex((c) => c.includes(n))
    if (i >= 0) return i
  }
  return -1
}

export function InventoryPage({
  onNavigate,
  syncRefreshNonce = 0,
  active,
}: {
  onNavigate?: AppNavigate
  syncRefreshNonce?: number
  /** Required on purpose. Every page stays mounted once visited, so a page
   *  that does not know whether it is on screen keeps answering the
   *  keyboard from behind another one. An optional prop defaulting to true
   *  let exactly that omission through the compiler. */
  active: boolean
}) {
  const [q, setQ] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [stockStatus, setStockStatus] = useState('')
  const [expiryStatus, setExpiryStatus] = useState('')
  const [schedule, setSchedule] = useState('')
  // Empty means 'Best match' -- the engine's own order, which is plain A-Z
  // with an empty search box and keeps the name ranking while the shop types.
  const [sortBy, setSortBy] = useState('')
  const [types, setTypes] = useState<string[]>([])
  const [schedules, setSchedules] = useState<string[]>([])

  // ── Voice (test build): "out of stock dakhav", "kami stock", "Dolo shodh" ──
  // Sets the same filters the dropdowns set; the page's own filter effects
  // reload the list, so voice and mouse can never disagree.
  const voiceHandlerRef = useRef<VoiceHandler>(async () => null)
  voiceHandlerRef.current = async (cmd) => {
    const a = cmd.args || {}
    if (cmd.intent === 'inventory_filter') {
      if (a.filter === 'clear') {
        setQ('')
        setTypeFilter('')
        setStockStatus('')
        setExpiryStatus('')
        setSchedule('')
        return { ok: true, say: 'Sagla stock dakhavla (filter kadhle)' }
      }
      if (a.filter === 'stock') {
        const value = a.value === 'out' ? 'Out of Stock' : a.value === 'low' ? 'Low Stock' : 'In Stock'
        setExpiryStatus('')
        setStockStatus(value)
        return { ok: true, say: `Inventory: ${value}` }
      }
      if (a.filter === 'expiry') {
        const value = a.value === 'expired' ? 'Expired' : 'Near Expiry'
        setStockStatus('')
        setExpiryStatus(value)
        return { ok: true, say: `Inventory: ${value}` }
      }
    }
    if (cmd.intent === 'page_filter') {
      const f = String(a.filter || '')
      const all = isAllValue(a.value)
      if (f === 'clear') {
        setQ('')
        setTypeFilter('')
        setStockStatus('')
        setExpiryStatus('')
        setSchedule('')
        setSortBy('')
        return { ok: true, say: 'Inventory: sagle filter kadhle' }
      }
      if (f === 'stock') {
        const v = all ? '' : pickOption(a.value, ['In Stock', 'Low Stock', 'Out of Stock'])
        if (v == null) return { ok: false, say: `Stock filter "${a.value}" nahi` }
        setStockStatus(v)
        return { ok: true, say: `Inventory stock: ${v || 'All'}` }
      }
      if (f === 'expiry') {
        const v = all ? '' : pickOption(a.value, ['Near Expiry', 'Expired'])
        if (v == null) return { ok: false, say: `Expiry filter "${a.value}" nahi` }
        setExpiryStatus(v)
        return { ok: true, say: `Inventory expiry: ${v || 'All'}` }
      }
      if (f === 'type') {
        const v = all ? '' : pickOption(a.value, types)
        if (v == null) return { ok: false, say: `"${a.value}" prakar ya dukanat nahi` }
        setTypeFilter(v)
        return { ok: true, say: `Inventory prakar: ${v || 'All'}` }
      }
      if (f === 'schedule') {
        const v = all ? '' : pickOption(a.value, schedules)
        if (v == null) return { ok: false, say: `Schedule "${a.value}" yaadit nahi` }
        setSchedule(v)
        return { ok: true, say: `Inventory schedule: ${v || 'All'}` }
      }
      if (f === 'sort') {
        const opts = data?.sort_options?.length ? data.sort_options : [...INVENTORY_SORTS]
        const v = pickOption(a.value, opts)
        if (v == null) return { ok: false, say: `Sort "${a.value}" nahi` }
        setSortBy(v === 'Best match' ? '' : v)
        return { ok: true, say: `Inventory kram: ${v}` }
      }
      return { ok: false, say: `Inventory var "${f}" filter nahi` }
    }
    if (cmd.intent === 'page_action') {
      const act = String(a.action || '')
      if (act === 'export') {
        setExportOpen(true)
        return { ok: true, say: 'Inventory export ughadla' }
      }
      if (act === 'expired_return') {
        setExpiredReturnOpen(true)
        return { ok: true, say: 'Return expired ughadla' }
      }
      if (act === 'refresh') {
        // The Refresh button: every filter off, list read again.
        setQ('')
        setTypeFilter('')
        setStockStatus('')
        setExpiryStatus('')
        setSchedule('')
        setSortBy('')
        void load('')
        return { ok: true, say: 'Inventory refresh kela' }
      }
      if (act === 'medicine_details') {
        // "Dolo chi mahiti": search for it and open the first match; with no
        // name, the first row of the list on screen.
        const name = String(a.medicine || a.query || '').trim()
        let id = view.ids[0] && view.ids[0] > 0 ? view.ids[0] : null
        let shownName = String(view.rows[0]?.[nameIndex] ?? '')
        if (name) {
          setQ(name)
          try {
            const res = await fetchInventory({ q: name, type: '', stock: '', expiry: '', schedule: '', sort: '' })
            const ids = res.row_ids || []
            id = ids[0] && ids[0] > 0 ? ids[0] : null
            const ni = colIndex(res.columns, 'name')
            shownName = String(res.rows?.[0]?.[ni >= 0 ? ni : 0] ?? name)
          } catch (e) {
            return { ok: false, say: `Shodh zala nahi: ${e instanceof Error ? e.message : e}` }
          }
        }
        if (!id) return { ok: false, say: name ? `${name}: inventory madhe nahi` : 'Yaadit aushadh nahi' }
        setDialog({ id, mode: 'view' })
        return { ok: true, say: `${shownName}: mahiti ughadli` }
      }
      return { ok: false, say: `Inventory var "${act}" he kaam voice var nahi` }
    }
    if (cmd.intent === 'search') {
      setQ(String(a.query || ''))
      return { ok: true, say: `Inventory shodh: ${a.query}` }
    }
    return null
  }
  useEffect(() => registerVoicePage('inventory', () => voiceHandlerRef.current), [])
  const [data, setData] = useState<TablePayload | null>(null)
  const inventoryRows = useLayoutRowCount('inventory_rows')
  const showSummary = useDashboardSection('inventory_summary')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const [expiredReturnOpen, setExpiredReturnOpen] = useState(false)
  const expiredReturnOpenRef = useRef(false)
  expiredReturnOpenRef.current = expiredReturnOpen
  // Every page stays mounted; a hidden one must not keep a popup up over
  // whatever screen the shop moved to.
  useEffect(() => {
    if (!active) setExpiredReturnOpen(false)
  }, [active])
  // Alt+X opens Return expired (why this key: ExpiredReturnDialog.tsx).
  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (!isExpiredReturnKey(e)) return
      // Already open: the popup itself takes Alt+X back to its search box.
      if (expiredReturnOpenRef.current) return
      // Another popup (edit medicine, export, a question) is up first.
      if (document.querySelector('.modal-backdrop')) return
      e.preventDefault()
      setExpiredReturnOpen(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active])
  const [ctx, setCtx] = useState<{
    x: number
    y: number
    medicineId: number
    name: string
  } | null>(null)
  const [dialog, setDialog] = useState<{
    id: number
    mode: 'edit' | 'view'
  } | null>(null)
  const [alert, setAlert] = useState<AlertState | null>(null)

  /** The shelf as ONE thing. rows, ids and styles arrive from the engine in a
   *  single order and are only ever read from here, so no future edit can
   *  reorder one of them and leave the other two behind -- which is exactly
   *  what the browser-side sort used to do. If the engine ever sends a style
   *  list of the wrong length the colours are dropped rather than painted onto
   *  the wrong medicine. */
  const view = useMemo(() => {
    const rows = data?.rows || []
    const ids = data?.row_ids || []
    const styles = data?.row_styles
    return {
      rows,
      ids,
      styles: styles && styles.length === rows.length ? styles : undefined,
    }
  }, [data?.rows, data?.row_ids, data?.row_styles])

  /** Which column holds the name. Name is hideable in Settings -> Layout, so
   *  position 0 is not a safe guess. */
  const nameIndex = useMemo(() => {
    const i = colIndex(data?.columns, 'name')
    return i >= 0 ? i : 0
  }, [data?.columns])

  /** Only the newest filter change may paint. Now that the filters apply
   *  themselves, two loads can be in flight at once and the slower one used to
   *  be free to overwrite the newer answer. */
  const loadSeqRef = useRef(0)

  const load = async (overrideQ?: string) => {
    const term = overrideQ ?? q
    const seq = ++loadSeqRef.current
    setLoading(true)
    setError('')
    try {
      const engine = await ensureLocalEngine()
      if (!engine.ok) {
        setError(engine.error)
        return
      }
      const payload = await fetchInventory({
        q: term,
        type: typeFilter,
        stock: stockStatus,
        expiry: expiryStatus,
        schedule,
        sort: sortBy,
      })
      if (seq !== loadSeqRef.current) return
      setData(payload)
      // An Online read that failed is not an empty shop. Say so instead of
      // drawing a healthy-looking screen with no rows in it.
      if (payload.server_error) setError(payload.server_error)
      if (payload.types?.length) setTypes(payload.types)
      if (Array.isArray(payload.schedules)) setSchedules(payload.schedules)
    } catch (e) {
      if (seq !== loadSeqRef.current) return
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (seq === loadSeqRef.current) setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!syncRefreshNonce) return
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncRefreshNonce])

  // The dropdowns apply themselves; the search box waits for the typing to stop.
  useFilterEffect(() => void load(), [typeFilter, stockStatus, expiryStatus, schedule, sortBy])
  useDebouncedFilterEffect(() => void load(), [q])

  useEffect(() => {
    const onLayout = () => void load()
    window.addEventListener('satpuda:layout-config-changed', onLayout)
    return () =>
      window.removeEventListener('satpuda:layout-config-changed', onLayout)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const idAt = useCallback(
    (rowIndex: number) => {
      const id = view.ids[rowIndex]
      return id && id > 0 ? id : null
    },
    [view],
  )

  const openView = (rowIndex: number) => {
    const id = idAt(rowIndex)
    if (!id) return
    setDialog({ id, mode: 'view' })
  }

  const confirmDelete = (medicineId: number, nameHint?: string) => {
    setAlert({
      title: 'Delete Medicine',
      message: `Hide "${nameHint || 'this medicine'}" from inventory?`,
      kind: 'confirm',
      confirmLabel: 'Delete',
      cancelLabel: 'Cancel',
      onConfirm: () => {
        void (async () => {
          try {
            const res = await deleteInventoryMedicine(medicineId)
            if (!res.ok) {
              setAlert({
                title: 'Delete Failed',
                message: res.error || 'Could not delete.',
                kind: 'error',
              })
              return
            }
            void load()
          } catch (e) {
            setAlert({
              title: 'Error',
              message: e instanceof Error ? e.message : String(e),
              kind: 'error',
            })
          }
        })()
      },
    })
  }

  const summary = data?.summary || {}

  usePageHotkeys({
    // The hook has always taken this; no page passed it, so F5 and the
    // rest fired on every page that had ever been opened.
    // And not while Return expired is open: Ctrl+F would pull the cursor out
    // of the popup into the page search behind it, Ctrl+E open Export under it.
    enabled: active && !expiredReturnOpen,
    onExport: () => setExportOpen(true),
    onFocusFilter: () => focusPageFilter(),
    onApplyFilter: () => void load(),
    onClearFilter: () => {
      setQ('')
      void load()
    },
  })

  const bulkDeleteZero = () => {
    setAlert({
      title: 'Remove out of stock',
      message: 'Hide all fully out-of-stock medicine batches from inventory?',
      kind: 'confirm',
      confirmLabel: 'Remove',
      onConfirm: () => {
        void (async () => {
          try {
            const res = await deleteZeroStockMedicines()
            setAlert({
              title: 'Done',
              message: res.message || `Removed ${res.hidden ?? 0} batch(es).`,
              kind: 'info',
            })
            void load()
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e))
          }
        })()
      },
    })
  }

  const bulkDeleteExpired = () => {
    setAlert({
      title: 'Remove expired',
      message: 'Hide all expired medicine batches from inventory?',
      kind: 'confirm',
      confirmLabel: 'Remove',
      onConfirm: () => {
        void (async () => {
          try {
            const res = await deleteExpiredMedicines()
            setAlert({
              title: 'Done',
              message: res.message || `Removed ${res.hidden ?? 0} batch(es).`,
              kind: 'info',
            })
            void load()
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e))
          }
        })()
      },
    })
  }

  const reorderSelected = (medicineId: number) => {
    void (async () => {
      try {
        const res = await fetchInventoryReorderPrefill(medicineId)
        if (!res.ok || !res.prefill) {
          setAlert({
            title: 'Reorder',
            message: res.error || 'Could not build reorder line.',
            kind: 'warning',
          })
          return
        }
        onNavigate?.('settings', {
          settingsTab: 'reorder',
          settingsToggle: 'by_supplier',
          reorderMedicinePrefill: res.prefill,
        })
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }

  return (
    <PageRoot className="list-page">
      <SectionFrame title="Search & Filter">
        <FilterBar>
          <Field label="Search">
            <ModernCombo
              pageFilter
              value={q}
              placeholder="Name / batch / manufacturer"
              minChars={0}
              filterLocal
              listLabel="Recent results"
              emptyText="Type to filter inventory"
              items={view.rows.slice(0, 40).map((r, i) => ({
                id: `hint-${i}-${String(r[nameIndex] ?? '')}`,
                label: String(r[nameIndex] ?? ''),
              }))}
              onChange={(v) => setQ(v)}
              onPick={(it) => {
                setQ(it.label)
                void load(it.label)
              }}
              onEnter={(picked) => void load(picked?.label)}
            />
          </Field>
          <Field label="Type">
            <select
              className="settings-input"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="">All</option>
              {types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Stock Status">
            <select
              className="settings-input"
              value={stockStatus}
              onChange={(e) => setStockStatus(e.target.value)}
            >
              <option value="">All</option>
              <option value="In Stock">In Stock</option>
              <option value="Low Stock">Low Stock</option>
              <option value="Out of Stock">Out of Stock</option>
            </select>
          </Field>
          <Field label="Expiry Status">
            <select
              className="settings-input"
              value={expiryStatus}
              onChange={(e) => setExpiryStatus(e.target.value)}
            >
              <option value="">All</option>
              <option value="Near Expiry">Near Expiry</option>
              <option value="Expired">Expired</option>
            </select>
          </Field>
          <Field label="Sort by">
            <select
              className="settings-input"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
            >
              {(data?.sort_options?.length
                ? data.sort_options
                : (INVENTORY_SORTS as readonly string[])
              ).map((o) => (
                <option key={o} value={o === 'Best match' ? '' : o}>
                  {o}
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
              label="Refresh"
              variant="neutral"
              onClick={() => {
                setQ('')
                setTypeFilter('')
                setStockStatus('')
                setExpiryStatus('')
                setSchedule('')
                setSortBy('')
                // Its own copy of load() asked for a different ordering, skipped
                // the newest-answer-wins guard and never showed a failed Online
                // read -- so Refresh could quietly repaint an empty shop.
                void load('')
              }}
              disabled={loading}
            />
            <ActionBtn
              label="Remove zero stock"
              variant="neutral"
              onClick={bulkDeleteZero}
              disabled={loading}
            />
            <ActionBtn
              label="Remove expired"
              variant="neutral"
              onClick={bulkDeleteExpired}
              disabled={loading}
            />
            <ActionBtn
              label="Return expired"
              variant="secondary"
              kbd="Alt+X"
              onClick={() => setExpiredReturnOpen(true)}
              disabled={loading}
            />
          </ActionBar>
        </FilterBar>
        <Note>
          Right-click a medicine to edit every field, view details, or delete.
          Double-click opens details. Columns follow Settings → Layout &amp;
          Lists ({summary.total_medicines ?? 0} rows in list, {inventoryRows}{' '}
          visible).
        </Note>
      </SectionFrame>

      <StatusLine error={error} loading={loading && !data} />

      <SectionFrame title="Inventory — Medicine List" className="list-table-panel">
        <DataTable
          columns={data?.columns || []}
          rows={view.rows}
          rowStyles={view.styles}
          columnWidths={INVENTORY_COLUMN_WIDTHS}
          visibleRows={inventoryRows}
          empty={loading ? 'Loading…' : 'No medicines found'}
          onRowDoubleClick={openView}
          onRowContextMenu={(rowIndex, e) => {
            const id = idAt(rowIndex)
            if (!id) return
            // Take the name off the row that was actually clicked. Looking it
            // up again later by searching row_ids is how the delete prompt
            // ended up naming a different medicine.
            const name = String(view.rows[rowIndex]?.[nameIndex] ?? '')
            setCtx({ x: e.clientX, y: e.clientY, medicineId: id, name })
          }}
        />
      </SectionFrame>

      {showSummary ? (
        <SectionFrame title="Inventory Summary" className="list-summary-panel">
          <SummaryBar
            items={[
              { label: 'Total Medicines', value: summary.total_medicines ?? 0 },
              { label: 'Low Stock', value: summary.low_stock ?? 0 },
              { label: 'Out of Stock', value: summary.out_of_stock ?? 0 },
              { label: 'Near Expiry', value: summary.near_expiry ?? 0 },
              { label: 'Expired', value: summary.expired ?? 0 },
              {
                label: 'Total Value (MRP)',
                value: `₹${Number(summary.total_value || 0).toLocaleString('en-IN', {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}`,
              },
            ]}
          />
        </SectionFrame>
      ) : null}

      <ExpiredReturnDialog
        open={expiredReturnOpen}
        onClose={() => setExpiredReturnOpen(false)}
        onSaved={() => void load()}
        onEditReturn={(id) => {
          setExpiredReturnOpen(false)
          onNavigate?.('returns', { returnsTab: 'purchase', returnsEditReturnId: id })
        }}
      />
      <ExportDialog
        page="inventory"
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        currentColumns={data?.columns || []}
        currentRows={view.rows}
      />

      <RowContextMenu
        menu={ctx}
        onClose={() => setCtx(null)}
        items={[
          {
            label: 'Edit Medicine',
            onClick: () => {
              if (ctx) setDialog({ id: ctx.medicineId, mode: 'edit' })
            },
          },
          {
            label: 'View Details',
            onClick: () => {
              if (ctx) setDialog({ id: ctx.medicineId, mode: 'view' })
            },
          },
          {
            label: 'Reorder',
            onClick: () => {
              if (ctx) reorderSelected(ctx.medicineId)
            },
          },
          { label: '', separator: true },
          {
            label: 'Delete Medicine',
            danger: true,
            onClick: () => {
              if (!ctx) return
              confirmDelete(ctx.medicineId, ctx.name)
            },
          },
        ]}
      />

      {dialog ? (
        <MedicineEditDialog
          medicineId={dialog.id}
          mode={dialog.mode}
          onClose={() => setDialog(null)}
          onSaved={() => void load()}
          onDeleted={() => void load()}
          onRequestEdit={() =>
            setDialog((d) => (d ? { ...d, mode: 'edit' } : d))
          }
        />
      ) : null}

      <AlertDialog alert={alert} onClose={() => setAlert(null)} />
    </PageRoot>
  )
}
