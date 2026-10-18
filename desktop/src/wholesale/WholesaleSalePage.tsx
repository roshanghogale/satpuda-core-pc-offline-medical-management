/** The Sales screen of a store in wholesale mode: a B2B tax invoice to another medical.
 *
 *  Party must be a customer (its GSTIN, DL numbers, state, rate tier and credit terms are
 *  shown); lines are whole packs + free packs at Rate A / B / C (GST excluded, added on
 *  top), with Disc % and Scheme %. The engine does every sum (core/wholesale_sale.py):
 *  this page only sends what is typed and shows what comes back. Retail billing is the
 *  untouched SalesPage.
 *
 *  The layout is the retail Sales page's (owner, 18 Oct 2026: "purchase and sales are not
 *  like the retail shops"): bill tabs + Tabs & Tools on top, one "Party & Medicine" panel
 *  of compact inputs with a single medicine entry row, the Selected Medicines table taking
 *  the rest of the height, and the Billing Summary pinned at the bottom. Transport / LR /
 *  e-way sit in the summary so they never push the table down. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AppNavigate } from '../App'
import {
  fetchBillPreview,
  fetchSalesBillHint,
  printSalesBill,
  saveBillPdf,
  type MedicineBatch,
} from '../pagesApi'
import { todayLocal } from '../localDate'
import { showSavedFile } from '../savedFiles'
import { useLayoutRowCount } from '../layoutRows'
import './wholesale.css'
import { BillPreviewDialog } from '../pages/BillPreviewDialog'
import { TwoStepMedicinePicker } from '../pages/TwoStepMedicinePicker'
import { ModernCombo } from '../pages/ModernCombo'
import { RowContextMenu } from '../pages/MedicineEditDialog'
import { AlertDialog, type AlertState } from '../pages/SalesDialogs'
import {
  ActionBtn,
  CappedTableWrap,
  DocTabBar,
  DocToolsDialog,
  Field,
  PageRoot,
  Panel,
  SelectWrap,
} from '../pages/pageChrome'
import {
  buildWholesaleLine,
  calcWholesale,
  fetchParty,
  loadWholesale,
  saveWholesale,
  searchParties,
  type PartyInfo,
  type PartyRow,
  type WholesaleCalc,
  type WholesaleLine,
} from './saleApi'

type Line = WholesaleLine & { key: string }

type Form = {
  title: string
  partyText: string
  partyId: number | null
  billDate: string
  transport: string
  lrNo: string
  ewayNo: string
  paymentMode: 'Due' | 'Cash' | 'Online'
  cash: string
  online: string
  items: Line[]
  editingSaleId: number | null
  editingBillNo: string
}

const NAV = 'wholesale-sale'

/* Enter order along the page (data-nav-order), like the retail bill. */
const NO = {
  payment: 1,
  party: 2,
  billDate: 3,
  medicine: 8,
  qty: 9,
  free: 10,
  rate: 11,
  disc: 12,
  sch: 13,
  add: 14,
  transport: 20,
  lr: 21,
  eway: 22,
  cash: 23,
  online: 24,
  save: 30,
} as const

let tabSeq = 0
function blankForm(title?: string): Form {
  return {
    title: title || `Bill ${++tabSeq}`,
    partyText: '',
    partyId: null,
    billDate: todayLocal(),
    transport: '',
    lrNo: '',
    ewayNo: '',
    paymentMode: 'Due',
    cash: '',
    online: '',
    items: [],
    editingSaleId: null,
    editingBillNo: '',
  }
}

function isDirty(f: Form) {
  return f.items.length > 0 || !!f.partyId
}

function money(v: number | undefined | null) {
  return Number(v || 0).toFixed(2)
}

function num(v: string | number) {
  const x = Number(String(v).replace(/,/g, ''))
  return Number.isFinite(x) ? x : 0
}

function shortExpiry(exp: string) {
  return exp ? exp.slice(5, 7) + '/' + exp.slice(2, 4) : ''
}

let keySeq = 0
const nextKey = () => `wl${++keySeq}`

export function WholesaleSalePage({
  active,
  editSaleId = null,
  onEditConsumed,
  focusNonce = 0,
}: {
  active: boolean
  editSaleId?: number | null
  onEditConsumed?: () => void
  onNavigate?: AppNavigate
  syncRefreshNonce?: number
  focusNonce?: number
}) {
  const [tabs, setTabs] = useState<Form[]>(() => [blankForm()])
  const [activeTab, setActiveTabRaw] = useState(0)
  const form = tabs[activeTab] ?? tabs[0]
  const [info, setInfo] = useState<PartyInfo | null>(null)
  const [calc, setCalc] = useState<WholesaleCalc | null>(null)
  const [suggest, setSuggest] = useState<PartyRow[]>([])
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [medText, setMedText] = useState('')
  /** The medicine being entered (or a line being edited) before it goes on the bill. */
  const [pending, setPending] = useState<Line | null>(null)
  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  const [hint, setHint] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [toolsOpen, setToolsOpen] = useState(false)
  const [alert, setAlert] = useState<AlertState | null>(null)
  const [lineMenu, setLineMenu] = useState<{ x: number; y: number; index: number } | null>(null)
  const [lastSaved, setLastSaved] = useState<{ id: number; billNo: string } | null>(null)
  const [preview, setPreview] = useState<{
    open: boolean
    html?: string
    billNo?: string
    loading?: boolean
    error?: string
    saleId?: number
  }>({ open: false })
  const billingRows = useLayoutRowCount('billing_rows')
  const partyRef = useRef<HTMLInputElement | null>(null)
  const medRef = useRef<HTMLInputElement | null>(null)
  const qtyRef = useRef<HTMLInputElement | null>(null)
  const tableRef = useRef<HTMLTableElement | null>(null)
  const activeRef = useRef(0)
  activeRef.current = Math.min(activeTab, tabs.length - 1)
  const tabsRef = useRef(tabs)
  tabsRef.current = tabs

  const setForm = useCallback(
    (fn: (f: Form) => Form) =>
      setTabs((all) => all.map((f, i) => (i === activeRef.current ? fn(f) : f))),
    [],
  )
  const patch = useCallback((p: Partial<Form>) => setForm((f) => ({ ...f, ...p })), [setForm])
  const party = info?.party ?? null
  const tier = party?.rate_tier || 'A'

  const focusNav = useCallback((order: number) => {
    window.setTimeout(() => {
      const el = document.querySelector<HTMLElement>(
        `.desktop-page[data-nav-chain="${NAV}"] [data-nav-order="${order}"]`,
      )
      if (!el) return
      el.focus()
      if (el instanceof HTMLInputElement) {
        try {
          el.select()
        } catch {
          /* date inputs */
        }
      }
    }, 0)
  }, [])

  const clearEntry = () => {
    setPending(null)
    setEditingIdx(null)
    setMedText('')
  }

  const setActiveTab = (i: number) => {
    if (i === activeRef.current) return
    clearEntry()
    setCalc(null)
    setActiveTabRaw(i)
  }

  // Invoice no. hint for the bill date.
  useEffect(() => {
    if (form.editingSaleId) return
    let alive = true
    fetchSalesBillHint(form.billDate)
      .then((r) => alive && setHint(r.next_bill_hint || ''))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [form.billDate, form.editingSaleId, lastSaved])

  // Party search.
  useEffect(() => {
    if (!suggestOpen) return
    let alive = true
    const t = window.setTimeout(() => {
      searchParties(form.partyText.trim())
        .then((r) => {
          if (!alive) return
          setSuggest(r.parties || [])
        })
        .catch(() => undefined)
    }, 120)
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [form.partyText, suggestOpen])

  // The picked party's details.
  useEffect(() => {
    if (!form.partyId) {
      setInfo(null)
      return
    }
    let alive = true
    fetchParty(form.partyId)
      .then((r) => alive && setInfo(r.ok ? r : null))
      .catch((e) => alive && setError(String((e as Error).message || e)))
    return () => {
      alive = false
    }
  }, [form.partyId])

  // Live totals from the engine.
  const calcBody = useMemo(
    () => ({
      customer_id: form.partyId || 0,
      bill_date: form.billDate,
      editing_sale_id: form.editingSaleId || 0,
      payment_mode: form.paymentMode,
      cash_paid: num(form.cash),
      online_paid: num(form.online),
      items: form.items.map((it) => ({
        id: it.id,
        qty: num(it.qty),
        free: num(it.free),
        rate: num(it.rate),
        disc_pct: num(it.disc_pct),
        scheme_pct: num(it.scheme_pct),
        gst_percent: it.gst_percent,
        hsn_code: it.hsn_code,
        rate_tier: it.rate_tier,
      })),
    }),
    [form],
  )
  useEffect(() => {
    let alive = true
    const t = window.setTimeout(() => {
      calcWholesale(calcBody)
        .then((r) => alive && r.ok && setCalc(r))
        .catch(() => undefined)
    }, 120)
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [calcBody])

  // A bill opened for edit from Sales History: into this tab when it is empty, else a new one.
  useEffect(() => {
    if (!editSaleId) return
    let alive = true
    loadWholesale(editSaleId)
      .then((r) => {
        if (!alive) return
        onEditConsumed?.()
        if (!r.ok) {
          setError(r.error || 'Could not open the bill.')
          return
        }
        const loaded: Form = {
          ...blankForm(`Edit ${r.bill_no}`),
          partyText: r.party?.name || '',
          partyId: r.customer_id || null,
          billDate: r.bill_date || todayLocal(),
          transport: r.transport || '',
          lrNo: r.lr_no || '',
          ewayNo: r.eway_bill_no || '',
          paymentMode: r.cash_paid > 0 ? 'Cash' : r.online_paid > 0 ? 'Online' : 'Due',
          cash: r.cash_paid ? String(r.cash_paid) : '',
          online: r.online_paid ? String(r.online_paid) : '',
          items: (r.items || []).map((it) => ({ ...it, key: nextKey() })),
          editingSaleId: r.sale_id,
          editingBillNo: r.bill_no,
        }
        const all = tabsRef.current
        const cur = all[activeRef.current]
        clearEntry()
        setCalc(null)
        if (cur && !isDirty(cur)) {
          setTabs(all.map((f, i) => (i === activeRef.current ? loaded : f)))
        } else {
          setTabs([...all, loaded])
          setActiveTabRaw(all.length)
        }
        setNotice(`Editing bill ${r.bill_no}.`)
        setError('')
      })
      .catch((e) => alive && setError(String((e as Error).message || e)))
    return () => {
      alive = false
    }
  }, [editSaleId, onEditConsumed])

  // Coming to the page puts the cursor in the party box (or the medicine once a party is in).
  useEffect(() => {
    if (!active) return
    const t = window.setTimeout(() => {
      const f = tabsRef.current[activeRef.current]
      if (document.querySelector('.modal-backdrop')) return
      if (f?.partyId) medRef.current?.focus()
      else partyRef.current?.focus()
    }, 60)
    return () => window.clearTimeout(t)
  }, [active, focusNonce, activeTab])

  const pickParty = (row: PartyRow) => {
    patch({ partyText: row.name, partyId: row.id })
    setSuggestOpen(false)
    window.setTimeout(() => medRef.current?.focus(), 0)
  }

  /** Units already on this bill by medicine id, so the picker shows what is left. */
  const reserved = useMemo(() => {
    const out: Record<string, number> = {}
    form.items.forEach((it, i) => {
      if (i === editingIdx) return
      const units = (num(it.qty) + num(it.free)) * (it.units_per_pack || 1)
      out[String(it.id)] = (out[String(it.id)] || 0) + units
    })
    return out
  }, [form.items, editingIdx])

  // After the render that enables the entry boxes (a timer alone can fire before it).
  const [qtyFocusTick, setQtyFocusTick] = useState(0)
  const focusQty = () => setQtyFocusTick((n) => n + 1)
  useEffect(() => {
    if (!qtyFocusTick) return
    qtyRef.current?.focus()
    qtyRef.current?.select()
  }, [qtyFocusTick])

  const onBatchPicked = async (b: MedicineBatch) => {
    setError('')
    try {
      const r = await buildWholesaleLine({ medicine_id: b.id, rate_tier: tier, qty: 1 })
      if (!r.ok || !r.line) {
        setError(r.error || 'Could not add the medicine.')
        return
      }
      setPending({ ...r.line, key: nextKey() })
      setEditingIdx(null)
      setMedText(r.line.name)
      // No Rate A on the batch: a red message, not a quiet notice (owner, 18 Oct 2026).
      if ((r.line as { rate_missing?: boolean }).rate_missing) setError(String(r.line.warning || ''))
      else if (r.line.warning) setNotice(r.line.warning)
      focusQty()
    } catch (e) {
      setError(String((e as Error).message || e))
    }
  }

  const patchPending = (p: Partial<Line>) => setPending((cur) => (cur ? { ...cur, ...p } : cur))

  const setRate = (value: string) => {
    if (!pending) return
    const v = num(value)
    // The chip names the tier only while the rate is that tier's own.
    let t = ''
    for (const k of ['A', 'B', 'C']) {
      if (pending.tier_rates && pending.tier_rates[k] > 0 && Math.abs(pending.tier_rates[k] - v) < 0.005) {
        t = k
        if (k === tier) break
      }
    }
    patchPending({ rate: value as unknown as number, rate_tier: t })
  }

  /** Add Medicine / Update Medicine: the entry row goes onto the bill. */
  const commitEntry = () => {
    if (!pending) {
      if (medText.trim()) setError('Pick the medicine and its batch from the list.')
      focusNav(NO.medicine)
      return
    }
    if (num(pending.qty) + num(pending.free) <= 0) {
      setError('Enter the quantity (strips).')
      focusQty()
      return
    }
    setError('')
    const line = pending
    const idx = editingIdx
    setForm((f) => ({
      ...f,
      items:
        idx !== null && idx < f.items.length
          ? f.items.map((it, i) => (i === idx ? line : it))
          : [...f.items, line],
    }))
    clearEntry()
    focusNav(NO.medicine)
  }

  const startLineEdit = (i: number) => {
    const it = form.items[i]
    if (!it) return
    setPending({ ...it })
    setEditingIdx(i)
    setMedText(it.name)
    focusQty()
  }

  const cancelLineEdit = () => {
    clearEntry()
    focusNav(NO.medicine)
  }

  const removeLine = (i: number) => {
    setForm((f) => ({ ...f, items: f.items.filter((_, j) => j !== i) }))
    if (editingIdx !== null) {
      if (editingIdx === i) clearEntry()
      else if (editingIdx > i) setEditingIdx(editingIdx - 1)
    }
  }

  const clearForm = () => {
    setForm((f) => ({ ...blankForm(f.editingSaleId ? undefined : f.title) }))
    setInfo(null)
    setCalc(null)
    clearEntry()
    setNotice('')
    setError('')
    window.setTimeout(() => partyRef.current?.focus(), 0)
  }

  const addTab = () => {
    const all = tabsRef.current
    setTabs([...all, blankForm()])
    clearEntry()
    setCalc(null)
    setActiveTabRaw(all.length)
    setError('')
  }

  const doCloseTab = (idx: number) => {
    const all = tabsRef.current
    if (all.length <= 1) return
    const next = all.filter((_, i) => i !== idx)
    const cur = activeRef.current
    clearEntry()
    setCalc(null)
    setTabs(next)
    setActiveTabRaw(idx < cur ? cur - 1 : Math.min(cur, next.length - 1))
  }

  const closeTab = (index?: number) => {
    const all = tabsRef.current
    if (all.length <= 1) return
    const idx = index ?? activeRef.current
    if (all[idx] && isDirty(all[idx])) {
      setAlert({
        title: 'Close Bill',
        message: 'This bill is not saved. Close anyway?',
        kind: 'confirm',
        confirmLabel: 'Close',
        onConfirm: () => doCloseTab(idx),
      })
      return
    }
    doCloseTab(idx)
  }

  const save = async (print: boolean) => {
    if (busy) return
    setError('')
    setNotice('')
    if (!form.partyId) {
      setError('Pick the party first.')
      partyRef.current?.focus()
      return
    }
    if (!form.items.length) {
      setError(pending ? 'Press Add Medicine to put the medicine on the bill.' : 'Add medicines to the bill.')
      return
    }
    const savedTab = activeRef.current
    setBusy(true)
    try {
      const body = {
        ...calcBody,
        invoice_mode: 'wholesale',
        transport: form.transport,
        lr_no: form.lrNo,
        eway_bill_no: form.ewayNo,
      }
      const res = await saveWholesale(body)
      if (!res.ok || !res.sale_id) {
        setError(res.error || 'The bill was not saved.')
        return
      }
      const saved = { id: res.sale_id, billNo: res.bill_no || '' }
      setLastSaved(saved)
      const warn = (res.warnings || []).join(' ')
      setTabs((all) =>
        all.map((f, i) => (i === savedTab ? blankForm(f.editingSaleId ? undefined : f.title) : f)),
      )
      if (savedTab === activeRef.current) {
        setInfo(null)
        setCalc(null)
        clearEntry()
        window.setTimeout(() => partyRef.current?.focus(), 0)
      }
      setNotice(`Bill ${saved.billNo} saved (Rs ${money(res.grand_total)}).${warn ? ' ' + warn : ''}`)
      if (print) await printBill(saved.id, saved.billNo)
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setBusy(false)
    }
  }

  const printBill = async (saleId: number, billNo: string, slot: 1 | 2 = 1) => {
    try {
      const r = await printSalesBill({ sale_id: saleId, bill_no: billNo, slot })
      if (!r.ok) setError(r.error || 'Print failed.')
    } catch (e) {
      setError(String((e as Error).message || e))
    }
  }

  const openPreview = async (saleId: number) => {
    setPreview({ open: true, loading: true, saleId })
    try {
      const r = await fetchBillPreview(saleId)
      setPreview({
        open: true,
        saleId,
        html: r.html,
        billNo: r.bill_no,
        error: r.ok ? undefined : r.error,
      })
    } catch (e) {
      setPreview({ open: true, saleId, error: String((e as Error).message || e) })
    }
  }

  const savePdf = async (saleId: number) => {
    try {
      const r = await saveBillPdf(saleId)
      if (r.ok && r.pdf_path) showSavedFile({ path: r.pdf_path })
      else setError(r.error || 'PDF could not be created.')
    } catch (e) {
      setError(String((e as Error).message || e))
    }
  }

  // Enter-field actions (data-nav-enter / data-nav-action) raised inside this page only.
  const navActionRef = useRef<(action: string) => void>(() => undefined)
  navActionRef.current = (action: string) => {
    if (action === 'ws-add') commitEntry()
    else if (action === 'ws-save') focusNav(NO.save)
    else if (action === 'med-next') {
      if (pending) focusQty()
    }
  }
  useEffect(() => {
    const onNav = (e: Event) => {
      const target = e.target as HTMLElement | null
      if (!target?.closest?.(`.desktop-page[data-nav-chain="${NAV}"]`)) return
      const action = (e as CustomEvent<{ action?: string }>).detail?.action
      if (action) navActionRef.current(action)
    }
    document.addEventListener('satpuda-nav-action', onNav)
    return () => document.removeEventListener('satpuda-nav-action', onNav)
  }, [])

  // Keys like the retail bill -- only while this page is on screen. F5 save, F7 save and
  // print, F9 new bill, F3 / F4 open / close a bill tab, F12 Tabs & Tools.
  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      const key = e.key
      const ctrl = e.ctrlKey
      const shift = e.shiftKey
      if (key === 'F12') {
        e.preventDefault()
        setToolsOpen((o) => !o)
        return
      }
      // A popup (tools, preview, a question) answers its own keys.
      if (document.querySelector('.modal-backdrop')) return
      if (key === 'Escape' && editingIdx !== null) {
        e.preventDefault()
        cancelLineEdit()
        return
      }
      if (key === 'F2') {
        e.preventDefault()
        tableRef.current?.querySelector<HTMLElement>('tbody tr[tabindex="0"]')?.focus()
        return
      }
      if (key === 'F5' && shift) {
        e.preventDefault()
        clearForm()
        return
      }
      if (key === 'F5') {
        e.preventDefault()
        void save(false)
      } else if (key === 'F7') {
        e.preventDefault()
        void save(true)
      } else if (key === 'F9') {
        e.preventDefault()
        clearForm()
      } else if (key === 'F3' && !ctrl && !shift) {
        e.preventDefault()
        addTab()
      } else if (key === 'F4' && !ctrl && !shift) {
        e.preventDefault()
        closeTab()
      } else if (ctrl && (key === 'PageUp' || key === '[' || (e.altKey && key === 'ArrowLeft'))) {
        e.preventDefault()
        setActiveTab(Math.max(0, activeRef.current - 1))
      } else if (ctrl && (key === 'PageDown' || key === ']' || (e.altKey && key === 'ArrowRight'))) {
        e.preventDefault()
        setActiveTab(Math.min(tabsRef.current.length - 1, activeRef.current + 1))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const lineCalc = (i: number) => calc?.lines?.[i]
  const intra = calc ? calc.intra_state : (info?.intra_state ?? true)
  const pos = calc?.place_of_supply || info?.place_of_supply || ''
  const posName = calc?.place_of_supply_name || info?.place_of_supply_name || ''
  const dueMode = form.paymentMode === 'Due'
  const supplyText = pos ? `${posName} (${pos}) · ${intra ? 'CGST + SGST' : 'IGST'}` : intra ? 'CGST + SGST' : 'IGST'
  const pendingOver =
    !!pending &&
    !form.editingSaleId &&
    num(pending.qty) + num(pending.free) > (pending.stock_packs || 0) + 1e-9
  const tabTitles = tabs.map((t) => t.title + (isDirty(t) ? ' •' : ''))

  const setPaymentMode = (m: Form['paymentMode']) =>
    patch(m === 'Due' ? { paymentMode: m, cash: '', online: '' } : { paymentMode: m })

  return (
    <PageRoot className="bill-page wholesale-sale-page" navChain={NAV}>
      <AlertDialog alert={alert} onClose={() => setAlert(null)} />
      <DocToolsDialog
        open={toolsOpen}
        title="Wholesale bills & tools"
        onClose={() => setToolsOpen(false)}
        tabs={tabTitles}
        active={activeTab}
        addLabel="New bill"
        onSelect={(i) => {
          setActiveTab(i)
          setToolsOpen(false)
        }}
        onAdd={() => {
          addTab()
          setToolsOpen(false)
        }}
        onCloseTab={(i) => closeTab(i)}
        hint="F5 save · F7 save & print · F9 new bill · F3 new tab · F4 close tab"
        tools={[
          {
            label: 'New bill tab',
            detail: 'Open another wholesale bill',
            kbd: 'F3',
            onClick: () => {
              addTab()
              setToolsOpen(false)
            },
          },
          {
            label: 'Close this bill',
            detail: 'Last tab cannot be closed',
            kbd: 'F4',
            disabled: tabs.length <= 1,
            onClick: () => {
              setToolsOpen(false)
              closeTab()
            },
          },
          {
            label: 'Clear this bill',
            detail: 'Start the bill again',
            kbd: 'F9',
            onClick: () => {
              setToolsOpen(false)
              clearForm()
            },
          },
          {
            label: lastSaved ? `Preview ${lastSaved.billNo}` : 'Preview last bill',
            detail: lastSaved ? 'The bill saved last' : 'No bill saved yet on this screen',
            disabled: !lastSaved,
            onClick: () => {
              setToolsOpen(false)
              if (lastSaved) void openPreview(lastSaved.id)
            },
          },
          {
            label: lastSaved ? `Print ${lastSaved.billNo}` : 'Print last bill',
            detail: 'Print the bill saved last again',
            disabled: !lastSaved,
            onClick: () => {
              setToolsOpen(false)
              if (lastSaved) void printBill(lastSaved.id, lastSaved.billNo)
            },
          },
          {
            label: 'Save PDF of last bill',
            detail: 'A PDF file of the bill saved last',
            disabled: !lastSaved,
            onClick: () => {
              setToolsOpen(false)
              if (lastSaved) void savePdf(lastSaved.id)
            },
          },
        ]}
      />

      <DocTabBar
        tabs={tabTitles}
        active={activeTab}
        onSelect={setActiveTab}
        onAdd={addTab}
        onCloseTab={(i) => closeTab(i)}
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

      <Panel title="Party & Medicine" className="sales-header-panel ws-header-panel">
        <div className="form-grid">
          <Field label="Payment">
            <SelectWrap>
              <select
                data-nav-order={NO.payment}
                data-nav-chain={NAV}
                value={form.paymentMode}
                onChange={(e) => setPaymentMode(e.target.value as Form['paymentMode'])}
                onKeyDown={(e) => {
                  const k = e.key.toLowerCase()
                  if (k === 'd') {
                    e.preventDefault()
                    setPaymentMode('Due')
                  } else if (k === 'c') {
                    e.preventDefault()
                    setPaymentMode('Cash')
                  } else if (k === 'o') {
                    e.preventDefault()
                    setPaymentMode('Online')
                  }
                }}
              >
                <option value="Due">Due (credit)</option>
                <option value="Cash">Cash</option>
                <option value="Online">Online</option>
              </select>
            </SelectWrap>
          </Field>
          <Field label="Party (customer)">
            <ModernCombo
              value={form.partyText}
              inputRef={partyRef}
              navOrder={NO.party}
              navChain={NAV}
              placeholder="Type the medical's name"
              minChars={0}
              keepOrder
              voiceField="customer"
              listLabel="Parties"
              items={suggest.map((p) => ({
                id: String(p.id),
                label: p.name,
                meta: [p.phone, p.party_type === 'medical' ? 'MEDICAL' : ''].filter(Boolean).join(' · '),
              }))}
              onFocus={() => setSuggestOpen(true)}
              onChange={(v) => {
                patch({ partyText: v, partyId: null })
                setSuggestOpen(true)
              }}
              onPick={(item) => {
                const row = suggest.find((p) => String(p.id) === item.id)
                if (row) pickParty(row)
              }}
              onEnter={(picked) => focusNav(picked ? NO.medicine : NO.billDate)}
            />
          </Field>
          <Field label="GSTIN">
            <input
              type="text"
              className="mono"
              readOnly
              tabIndex={-1}
              value={party ? party.gstin || 'Unregistered' : ''}
              placeholder="—"
            />
          </Field>
          <Field label="DL Numbers">
            <input
              type="text"
              className="mono"
              readOnly
              tabIndex={-1}
              value={party ? party.dl_numbers || 'Not on file' : ''}
              title={party?.dl_numbers || ''}
              placeholder="—"
            />
          </Field>
          <Field label="Previous Due">
            <input
              type="text"
              className="due-val mono"
              readOnly
              tabIndex={-1}
              value={money(party?.total_due)}
            />
          </Field>
          <Field label="Bill Date">
            <input
              type="date"
              className="mono"
              data-nav-order={NO.billDate}
              data-nav-chain={NAV}
              value={form.billDate}
              onChange={(e) => patch({ billDate: e.target.value || todayLocal() })}
            />
          </Field>
          <Field label="Due Date">
            <input type="text" className="mono" readOnly tabIndex={-1} value={calc?.due_date || ''} />
          </Field>
          <Field label="Invoice No">
            <input
              type="text"
              className="mono"
              readOnly
              tabIndex={-1}
              value={form.editingSaleId ? form.editingBillNo : hint}
              title="Next sale bill number (automatic)"
            />
          </Field>
          <Field label="Place of Supply">
            <input type="text" readOnly tabIndex={-1} value={supplyText} title={supplyText} />
          </Field>
          <Field label="Rate · Credit">
            <input
              type="text"
              className="mono"
              readOnly
              tabIndex={-1}
              value={
                party
                  ? `Rate ${party.rate_tier} · ${party.credit_limit > 0 ? `Rs ${money(party.credit_limit)}` : 'no limit'} · ${party.credit_days || 0} days`
                  : ''
              }
              placeholder="—"
            />
          </Field>
        </div>
        <div className="medsel-grid ws-medsel">
          <Field label="Medicine">
            <TwoStepMedicinePicker
              billDate={form.billDate}
              reserved={reserved}
              navChain={NAV}
              navOrder={NO.medicine}
              value={medText}
              onValueChange={(v) => {
                setMedText(v)
                if (pending && editingIdx === null) setPending(null)
              }}
              onBatchPicked={(b) => void onBatchPicked(b)}
              onEmptyEnter={() => focusNav(NO.transport)}
              inputRef={medRef}
              hideMrp
            />
          </Field>
          <Field label="Batch">
            <input
              type="text"
              className="mono"
              readOnly
              tabIndex={-1}
              value={pending ? `${pending.batch}${pending.expiry ? ' · ' + shortExpiry(pending.expiry) : ''}` : ''}
              placeholder="Pick from list ↓"
            />
          </Field>
          <Field label="Qty" hint="strips">
            <input
              ref={qtyRef}
              type="text"
              className={`mono${pendingOver ? ' ws-over' : ''}`}
              data-nav-order={NO.qty}
              data-nav-chain={NAV}
              disabled={!pending}
              value={pending ? String(pending.qty) : ''}
              title={pending ? `${pending.stock_packs} pack(s) in stock` : ''}
              onFocus={(e) => e.currentTarget.select()}
              onChange={(e) => patchPending({ qty: e.target.value.replace(/[^0-9]/g, '') as unknown as number })}
            />
          </Field>
          <Field label="Free">
            <input
              type="text"
              className="mono"
              data-nav-order={NO.free}
              data-nav-chain={NAV}
              disabled={!pending}
              value={pending ? String(pending.free || '') : ''}
              onChange={(e) => patchPending({ free: e.target.value.replace(/[^0-9]/g, '') as unknown as number })}
            />
          </Field>
          <Field label="Rate" hint="per strip">
            <div className="ws-rate-box">
              <input
                type="text"
                className="mono"
                data-nav-order={NO.rate}
                data-nav-chain={NAV}
                disabled={!pending}
                value={pending ? String(pending.rate) : ''}
                onChange={(e) => setRate(e.target.value)}
              />
              {pending ? (
                <span
                  className={`chip ${pending.rate_tier ? 'chip-H' : 'chip-none'}`}
                  title={pending.rate_tier ? `Rate ${pending.rate_tier}` : 'Typed / base rate'}
                >
                  {pending.rate_tier || '·'}
                </span>
              ) : null}
            </div>
          </Field>
          <Field label="Disc %">
            <input
              type="text"
              className="mono"
              data-nav-order={NO.disc}
              data-nav-chain={NAV}
              disabled={!pending}
              value={pending ? String(pending.disc_pct || '') : ''}
              onChange={(e) => patchPending({ disc_pct: e.target.value as unknown as number })}
            />
          </Field>
          <Field label="Sch %">
            <input
              type="text"
              className="mono"
              data-nav-order={NO.sch}
              data-nav-chain={NAV}
              data-nav-enter="ws-add"
              disabled={!pending}
              value={pending ? String(pending.scheme_pct || '') : ''}
              onChange={(e) => patchPending({ scheme_pct: e.target.value as unknown as number })}
            />
          </Field>
          <Field label="HSN">
            <input
              type="text"
              className="mono"
              disabled={!pending}
              value={pending ? pending.hsn_code : ''}
              onChange={(e) => patchPending({ hsn_code: e.target.value })}
            />
          </Field>
          <Field label={' '}>
            <ActionBtn
              label={editingIdx !== null ? 'Update Medicine' : 'Add Medicine'}
              onClick={commitEntry}
              className="btn-full"
              navOrder={NO.add}
              navAction="ws-add"
              navChain={NAV}
            />
          </Field>
          {error ? (
            <div className="hint-text ws-msg ws-msg-error" role="alert">{error}</div>
          ) : pendingOver ? (
            <div className="hint-text ws-msg ws-msg-error">
              Only {pending?.stock_packs} strip(s) in stock for this batch.
            </div>
          ) : notice ? (
            <div className="hint-text ws-msg">{notice}</div>
          ) : (
            <div className="hint-text">
              {pending
                ? `Stock ${pending.stock_packs} strip(s) · GST ${pending.gst_percent}% · Enter on Sch % adds the line`
                : '↓ list · Enter pick · Enter / double-click row: edit line · Delete key removes line · qty in strips (packs for syrups etc.)'}
            </div>
          )}
        </div>
      </Panel>

      <Panel
        title="Selected Medicines"
        table
        className="bill-items-panel"
        headRight={
          <span className="count">
            {form.items.length} item{form.items.length === 1 ? '' : 's'} · qty in strips
          </span>
        }
      >
        <CappedTableWrap visibleRows={billingRows}>
          <table className="sat-table ws-sale-table" ref={tableRef}>
            <thead>
              <tr>
                <th className="numsm">Sr</th>
                <th>Medicine</th>
                <th>Batch</th>
                <th>Exp</th>
                <th>HSN</th>
                <th className="numsm">Qty</th>
                <th className="numsm">Free</th>
                <th className="numsm">Rate</th>
                <th className="numsm">Disc %</th>
                <th className="numsm">Sch %</th>
                <th className="num">Taxable</th>
                <th className="numsm">GST %</th>
                <th className="num">GST</th>
                <th className="num">Amount</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {form.items.length === 0 ? (
                <tr>
                  <td colSpan={15} className="muted">
                    {form.partyId ? 'No medicines added' : 'Pick the party, then add medicines.'}
                  </td>
                </tr>
              ) : (
                form.items.map((it, i) => {
                  const c = lineCalc(i)
                  const over = num(it.qty) + num(it.free) > (it.stock_packs || 0) + 1e-9 && !form.editingSaleId
                  return (
                    <tr
                      key={it.key}
                      tabIndex={0}
                      className={editingIdx === i ? 'row-editing' : undefined}
                      onDoubleClick={() => startLineEdit(i)}
                      onContextMenu={(e) => {
                        e.preventDefault()
                        setLineMenu({ x: e.clientX, y: e.clientY, index: i })
                      }}
                      onKeyDown={(e) => {
                        if (editingIdx === i) return
                        if (e.key === 'Delete' || e.key === 'Backspace') {
                          e.preventDefault()
                          removeLine(i)
                        } else if (e.key === 'Enter') {
                          e.preventDefault()
                          e.stopPropagation()
                          startLineEdit(i)
                        } else if (e.key === 'ArrowDown') {
                          e.preventDefault()
                          ;(e.currentTarget.nextElementSibling as HTMLElement | null)?.focus()
                        } else if (e.key === 'ArrowUp') {
                          e.preventDefault()
                          const prev = e.currentTarget.previousElementSibling as HTMLElement | null
                          if (prev?.tagName === 'TR') prev.focus()
                          else focusNav(NO.medicine)
                        } else if (e.key === 'Escape') {
                          e.preventDefault()
                          focusNav(NO.medicine)
                        }
                      }}
                    >
                      <td className="numsm mono">{i + 1}</td>
                      <td>
                        <span className="med-name">{it.name}</span>
                      </td>
                      <td className="mono">{it.batch || '—'}</td>
                      <td className="mono">{shortExpiry(it.expiry) || '—'}</td>
                      <td className="mono">{it.hsn_code || '—'}</td>
                      <td className="numsm mono" title={`${it.stock_packs} pack(s) in stock`}>
                        {String(it.qty)}
                        {over ? <span className="ws-over-note"> stock {it.stock_packs}</span> : null}
                      </td>
                      <td className="numsm mono">{num(it.free) ? String(it.free) : ''}</td>
                      <td className="numsm mono ws-rate-cell">
                        {money(num(it.rate))}
                        <span
                          className={`chip ${it.rate_tier ? 'chip-H' : 'chip-none'}`}
                          title={it.rate_tier ? `Rate ${it.rate_tier}` : 'Typed / base rate'}
                        >
                          {it.rate_tier || '·'}
                        </span>
                      </td>
                      <td className="numsm mono">{num(it.disc_pct) ? String(it.disc_pct) : ''}</td>
                      <td className="numsm mono">{num(it.scheme_pct) ? String(it.scheme_pct) : ''}</td>
                      <td className="num mono">{money(c?.taxable)}</td>
                      <td className="numsm mono">{it.gst_percent}</td>
                      <td
                        className="num mono"
                        title={c ? (intra ? `CGST ${money(c.cgst)} + SGST ${money(c.sgst)}` : `IGST ${money(c.igst)}`) : ''}
                      >
                        {money(c?.gst_amt)}
                      </td>
                      <td className="num mono">{money(c?.amount)}</td>
                      <td>
                        <button
                          type="button"
                          className="icon-btn del"
                          title="Remove line"
                          onClick={(e) => {
                            e.stopPropagation()
                            removeLine(i)
                          }}
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </CappedTableWrap>
      </Panel>

      <Panel title="Billing Summary" className="summary-panel" bare>
        <div className="summary-grid">
          <div className="summary-col">
            <h3>Transport &amp; Payment</h3>
            <div className="sfield-row sfield-row-3">
              <div className="sfield">
                <label>Transport <span className="opt">(optional)</span></label>
                <input
                  type="text"
                  data-nav-order={NO.transport}
                  data-nav-chain={NAV}
                  value={form.transport}
                  onChange={(e) => patch({ transport: e.target.value })}
                />
              </div>
              <div className="sfield">
                <label>LR No. <span className="opt">(optional)</span></label>
                <input
                  type="text"
                  className="mono"
                  data-nav-order={NO.lr}
                  data-nav-chain={NAV}
                  value={form.lrNo}
                  onChange={(e) => patch({ lrNo: e.target.value })}
                />
              </div>
              <div className="sfield">
                <label>E-way Bill No. <span className="opt">(optional)</span></label>
                <input
                  type="text"
                  className="mono"
                  data-nav-order={NO.eway}
                  data-nav-chain={NAV}
                  value={form.ewayNo}
                  onChange={(e) => patch({ ewayNo: e.target.value })}
                />
              </div>
              <div className="sfield">
                <label>GST</label>
                <div className="gst-note-box" title={`Place of supply: ${supplyText}`}>
                  {intra ? 'CGST + SGST (added)' : 'IGST (added)'}
                </div>
              </div>
              <div className="sfield">
                <label>Cash</label>
                <input
                  type="text"
                  className="mono"
                  data-nav-order={NO.cash}
                  data-nav-chain={NAV}
                  disabled={dueMode}
                  value={form.cash}
                  onChange={(e) => patch({ cash: e.target.value })}
                />
              </div>
              <div className="sfield">
                <label>Online</label>
                <input
                  type="text"
                  className="mono"
                  data-nav-order={NO.online}
                  data-nav-chain={NAV}
                  data-nav-enter="ws-save"
                  disabled={dueMode}
                  value={form.online}
                  onChange={(e) => patch({ online: e.target.value })}
                />
              </div>
            </div>
            {calc?.warnings?.length ? (
              <p className="page-error ws-calc-warn">{calc.warnings.join(' ')}</p>
            ) : null}
          </div>

          <div className="summary-col">
            <h3>Totals</h3>
            <div className="totals-list">
              <div className="totals-row">
                <span className="label">Taxable</span>
                <span className="value">{money(calc?.taxable_total)}</span>
              </div>
              {intra ? (
                <>
                  <div className="totals-row">
                    <span className="label">CGST</span>
                    <span className="value">{money(calc?.cgst_total)}</span>
                  </div>
                  <div className="totals-row">
                    <span className="label">SGST</span>
                    <span className="value">{money(calc?.sgst_total)}</span>
                  </div>
                </>
              ) : (
                <div className="totals-row">
                  <span className="label">IGST</span>
                  <span className="value">{money(calc?.igst_total)}</span>
                </div>
              )}
              <div className="totals-row">
                <span className="label">Round off</span>
                <span className="value">{money(calc?.round_off)}</span>
              </div>
              <div className="totals-row divider emph amt">
                <span className="label">Grand Total</span>
                <span className="value">{money(calc?.grand_total)}</span>
              </div>
              <div className="ws-words" title={calc?.amount_in_words || ''}>
                {calc?.amount_in_words || ''}
              </div>
            </div>
          </div>

          <div className="summary-col actions-col">
            <ActionBtn
              label={busy ? 'Saving…' : 'Save'}
              kbd="F5"
              disabled={busy}
              navOrder={NO.save}
              navAction="ws-save-click"
              navChain={NAV}
              onClick={() => void save(false)}
            />
            <div className="print-row">
              <ActionBtn
                label="Save & Print"
                kbd="F7"
                variant="secondary"
                disabled={busy}
                onClick={() => void save(true)}
              />
              <ActionBtn label="New Bill" kbd="F9" variant="secondary" onClick={clearForm} />
            </div>
            <div className="print-row">
              <ActionBtn
                label={lastSaved ? `Preview ${lastSaved.billNo}` : 'Preview last'}
                variant="neutral"
                disabled={!lastSaved}
                onClick={() => lastSaved && void openPreview(lastSaved.id)}
              />
              <ActionBtn
                label={lastSaved ? `Print ${lastSaved.billNo}` : 'Print last'}
                variant="neutral"
                disabled={!lastSaved}
                onClick={() => lastSaved && void printBill(lastSaved.id, lastSaved.billNo)}
              />
              <ActionBtn
                label="Save PDF"
                variant="neutral"
                disabled={!lastSaved}
                onClick={() => lastSaved && void savePdf(lastSaved.id)}
              />
            </div>
          </div>
        </div>
      </Panel>

      <RowContextMenu
        menu={lineMenu ? { x: lineMenu.x, y: lineMenu.y } : null}
        onClose={() => setLineMenu(null)}
        items={[
          {
            label: 'Edit Line',
            onClick: () => {
              if (lineMenu) startLineEdit(lineMenu.index)
            },
          },
          { separator: true, label: '' },
          {
            label: 'Delete Line',
            danger: true,
            onClick: () => {
              if (lineMenu) removeLine(lineMenu.index)
            },
          },
        ]}
      />

      <BillPreviewDialog
        open={preview.open}
        billNo={preview.billNo}
        html={preview.html}
        loading={preview.loading}
        error={preview.error}
        saleId={preview.saleId}
        onClose={() => setPreview({ open: false })}
        onPrint={(slot) => {
          if (preview.saleId) void printBill(preview.saleId, preview.billNo || '', slot)
        }}
        onSavePdf={() => {
          if (preview.saleId) void savePdf(preview.saleId)
        }}
      />
    </PageRoot>
  )
}
