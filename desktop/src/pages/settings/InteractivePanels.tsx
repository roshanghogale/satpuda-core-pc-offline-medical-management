import { saveFileAndShow, saveTextAndShow } from '../../savedFiles'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { AppNavigate } from '../../App'
import type { ReorderPrefill } from '../../pagesApi'
import { CustomerGstRow } from './CustomerGstRow'
import { useBusinessMode } from '../../wholesale/mode'
import {
  EMPTY_PARTY,
  GST_STATES,
  PartyFields,
  partyFromRow,
  partyPayload,
  type PartyForm,
} from '../../wholesale/PartyFields'
import {
  alertAction,
  applyAlertNavigation,
  deletePayment,
  exportAlerts,
  exportPaymentReport,
  fetchLedger,
  fetchOpenBills,
  fetchPaymentDetails,
  fetchRefundReturns,
  mutateContact,
  mutateShelf,
  reorderAction,
  savePayment,
  saveRefund,
  saveSettingsSection,
  systemAction,
  type LedgerBundle,
  type OpenBill,
  type PaymentAllocation,
  type PaymentBundle,
  type PaymentCleared,
  type PaymentDetails,
  type RefundableReturn,
  type SettingsBundle,
} from '../../settingsApi'
import { focusTableSection, usePageHotkeys } from '../../hooks/usePageHotkeys'
import { SHORTCUT_SECTIONS } from './shortcutSections'
import { presetRange, RANGE_WORDS, registerVoicePage, type VoiceHandler } from '../../voice/voiceBus'
import { dispatchPaymentsChanged, dispatchVillagesChanged } from '../../syncRefresh'
import { useLayoutRowCount } from '../../layoutRows'
import { resolveParty } from '../../partyNamePick'
import { ModernCombo } from '../ModernCombo'
import {
  VoiceNoBar,
  VoiceNoSelect,
  onlyNumbered,
  useContactShortcuts,
} from '../../voice/ContactVoiceNo'
import {
  Check,
  Field,
  Frame,
  Help,
  Note,
  PanelTitle,
  SaveBar,
  SaveBtn,
} from './SettingsChrome'
import { CappedTableWrap } from '../pageChrome'
import '../../styles/interactive-settings.css'
import '../../styles/payments-ledger.css'
import {
  ReorderMultiTabEditor,
  fetchReorderBulkTabs,
  fetchReorderGroupTab,
  type ReorderTabState,
} from './ReorderMultiTabEditor'
import { todayLocal } from '../../localDate'

type Contacts = NonNullable<SettingsBundle['contacts']>
type Shelf = NonNullable<SettingsBundle['shelf']>
type Reorder = NonNullable<SettingsBundle['reorder']>
type Thresholds = NonNullable<SettingsBundle['thresholds']>
type Alerts = NonNullable<SettingsBundle['alerts']>

function money(n: unknown) {
  const v = Number(n || 0)
  return v.toFixed(2)
}

function downloadCsv(filename: string, headers: string[], rows: unknown[][]) {
  const esc = (v: unknown) => {
    const s = String(v ?? '')
    if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`
    return s
  }
  const lines = [
    headers.map(esc).join(','),
    ...rows.map((r) => r.map(esc).join(',')),
  ]
  // Into the reports folder with Open / Show in folder, not a silent download.
  void saveTextAndShow(filename, 'text/csv;charset=utf-8', '﻿' + lines.join('\r\n'))
}

function filterText(q: string, parts: unknown[]) {
  if (!q.trim()) return true
  const needle = q.trim().toLowerCase()
  return parts.some((p) => String(p ?? '').toLowerCase().includes(needle))
}

/** Build body for mutateContact; flattens fields for current backend. */
function contactPayload(
  kind: string,
  action: string,
  opts: { id?: number; fields?: Record<string, unknown>; name?: string } = {},
) {
  let act = action
  if (kind === 'village') {
    if (action === 'add_village') act = 'add'
    else if (action === 'set_default_village') act = 'set_default'
  }
  const fields = { ...(opts.fields || {}) }
  if (opts.name != null) fields.name = opts.name
  const body: Record<string, unknown> = {
    kind,
    action: act,
    fields,
    ...fields,
  }
  if (opts.id != null) body.id = opts.id
  return body
}

function locKey(rack: string, section?: string, box?: string) {
  const num = (n: string) => {
    const m = String(n).match(/\d+/)
    return m ? m[0] : String(n)
  }
  if (box != null && section != null) {
    return `rack${num(rack)}section${num(section)}box${num(box)}`
  }
  if (section != null) return `rack${num(rack)}section${num(section)}`
  return `rack${num(rack)}`
}

function pathLabel(rack: string, section?: string, box?: string) {
  if (box != null && section != null) return `${rack} / ${section} / ${box}`
  if (section != null) return `${rack} / ${section}`
  return rack
}

/* ─── Layout pieces (styles/interactive-settings.css) ──────────────────── */

/** A box in a filter / tool bar: the label sits above it. */
function BarField({
  label,
  className = '',
  children,
}: {
  label: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <label className={`iset-field ${className}`.trim()}>
      <span>{label}</span>
      {children}
    </label>
  )
}

/** A table's empty state, in words. */
function EmptyRow({ cols, children }: { cols: number; children: React.ReactNode }) {
  return (
    <tr>
      <td className="iset-empty" colSpan={cols}>
        {children}
      </td>
    </tr>
  )
}

/** What the last action said, kept in sight at the bottom of a panel without a Save. */
function StatusBar({ msg, err }: { msg?: string; err?: string }) {
  if (!msg && !err) return null
  return <div className={`iset-statusbar${err ? ' err' : ''}`}>{err || msg}</div>
}

/** "No customers yet." / "No customer matches "ram"." / "No customer has a voice number." */
function noneText(one: string, many: string, search: string, numbered: boolean) {
  if (numbered) return `No ${one} has a voice number yet.`
  if (search.trim()) return `No ${one} matches “${search.trim()}”.`
  return `No ${many} yet. Add one above.`
}

/** Payment history search: receipt no., party, reference, payment no. or note. */
function historyMatches(r: Record<string, unknown>, q: string): boolean {
  const s = q.trim().toLowerCase()
  if (!s) return true
  return ['receipt_no', 'party', 'reference', 'payment_no', 'note'].some((k) =>
    String(r[k] ?? '').toLowerCase().includes(s),
  )
}

/* ─── Contacts ─────────────────────────────────────────────────────────── */

const CONTACTS_NAV = 'contacts'

function focusContactsNav(order: number) {
  const el = document.querySelector<HTMLElement>(
    `[data-nav-chain="${CONTACTS_NAV}"][data-nav-order="${order}"]`,
  )
  if (!el) return false
  el.focus()
  if (el instanceof HTMLInputElement) {
    try {
      el.select()
    } catch {
      /* ignore */
    }
  }
  return true
}

export function ContactsPanel({
  sectionId,
  contacts,
  onChange,
}: {
  sectionId: string
  contacts: Contacts
  onChange: (c: Contacts) => void
}) {
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [search, setSearch] = useState('')
  const customerRows = useLayoutRowCount('customers_rows')
  const doctorRows = useLayoutRowCount('doctors_rows')
  const supplierRows = useLayoutRowCount('suppliers_rows')
  const [editId, setEditId] = useState<number | null>(null)
  const [doctor, setDoctor] = useState({ name: '', reg_no: '', phone: '' })
  const [customer, setCustomer] = useState({
    name: '',
    phone: '',
    address: '',
  })
  // Wholesale: the customer as a party (GSTIN, DL, state, tier, credit terms).
  const { isWholesale } = useBusinessMode()
  const [party, setParty] = useState<PartyForm>(EMPTY_PARTY)
  const partyRef = useRef(party)
  partyRef.current = party
  const wholesaleRef = useRef(isWholesale)
  wholesaleRef.current = isWholesale
  const [supplier, setSupplier] = useState({
    name: '',
    phone: '',
    gstin: '',
    address: '',
  })
  const [village, setVillage] = useState('')
  // Voice shortcut numbers ("Voice no." column); hidden when the voice service is down.
  const voiceNo = useContactShortcuts()
  const showVoiceNo = !!voiceNo.map
  const [voiceOnly, setVoiceOnly] = useState(false)
  const numbered = voiceOnly && showVoiceNo
  const customerRef = useRef(customer)
  customerRef.current = customer
  const doctorRef = useRef(doctor)
  doctorRef.current = doctor
  const supplierRef = useRef(supplier)
  supplierRef.current = supplier
  const villageRef = useRef(village)
  villageRef.current = village
  const editIdRef = useRef(editId)
  editIdRef.current = editId
  const tableWrapRef = useRef<HTMLDivElement | null>(null)
  const saveDoctorRef = useRef<() => Promise<boolean>>(async () => false)
  const saveCustomerRef = useRef<() => Promise<boolean>>(async () => false)
  const saveSupplierRef = useRef<() => Promise<boolean>>(async () => false)
  const saveVillageRef = useRef<() => Promise<boolean>>(async () => false)

  useEffect(() => {
    setSearch('')
    setEditId(null)
    setMsg('')
    setErr('')
    // Enter form: Name (order 2) — search stays optional via Ctrl+F / order 1.
    const t = window.setTimeout(() => {
      focusContactsNav(2) || focusContactsNav(1)
    }, 40)
    return () => window.clearTimeout(t)
  }, [sectionId])

  async function run(body: Record<string, unknown>) {
    setErr('')
    setMsg('')
    try {
      const next = await mutateContact(body)
      if (next) onChange(next)
      const kind = String(body.kind || '')
      const action = String(body.action || '')
      if (
        kind === 'village' ||
        (kind === 'customer' && action !== 'delete' && action !== 'recalculate')
      ) {
        dispatchVillagesChanged()
      }
      setMsg(
        body.action === 'recalculate'
          ? 'Recalculated all dues.'
          : 'Saved.',
      )
      return true
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      return false
    }
  }

  async function rememberVillage(raw: string) {
    const text = raw.trim()
    if (!text) return
    const current = contacts.villages || []
    if (current.some((v) => v.toUpperCase() === text.toUpperCase())) return
    onChange({ ...contacts, villages: [...current, text] })
    try {
      const next = await mutateContact(
        contactPayload('village', 'add_village', { fields: { name: text } }),
      )
      if (next) onChange(next)
      dispatchVillagesChanged()
    } catch {
      /* keep the local dropdown row even if the save fails */
    }
  }

  function clearEdit() {
    setEditId(null)
    setDoctor({ name: '', reg_no: '', phone: '' })
    setCustomer({ name: '', phone: '', address: '' })
    setParty(EMPTY_PARTY)
    setSupplier({ name: '', phone: '', gstin: '', address: '' })
  }

  async function saveDoctor() {
    const d = doctorRef.current
    if (!String(d.name || '').trim()) {
      setErr('Doctor name is required.')
      focusContactsNav(2)
      return false
    }
    const ok = await run(
      contactPayload('doctor', editIdRef.current != null ? 'update' : 'add', {
        id: editIdRef.current ?? undefined,
        fields: { ...d },
      }),
    )
    if (ok) {
      clearEdit()
      focusContactsNav(2)
    }
    return ok
  }

  async function saveCustomer() {
    const c = customerRef.current
    if (!String(c.name || '').trim()) {
      setErr('Customer name is required.')
      focusContactsNav(2)
      return false
    }
    await rememberVillage(c.address)
    // The party fields only go with a wholesale save: a retail save never touches them.
    const fields: Record<string, unknown> = wholesaleRef.current
      ? { ...c, ...partyPayload(partyRef.current) }
      : { ...c }
    const ok = await run(
      contactPayload(
        'customer',
        editIdRef.current != null ? 'update' : 'add',
        { id: editIdRef.current ?? undefined, fields },
      ),
    )
    if (ok) {
      clearEdit()
      focusContactsNav(2)
    }
    return ok
  }

  async function saveSupplier() {
    const s = supplierRef.current
    if (!String(s.name || '').trim()) {
      setErr('Supplier name is required.')
      focusContactsNav(2)
      return false
    }
    const ok = await run(
      contactPayload(
        'supplier',
        editIdRef.current != null ? 'update' : 'add',
        { id: editIdRef.current ?? undefined, fields: { ...s } },
      ),
    )
    if (ok) {
      clearEdit()
      focusContactsNav(2)
    }
    return ok
  }

  async function saveVillage() {
    const name = villageRef.current.trim()
    if (!name) {
      setErr('Village name is required.')
      focusContactsNav(2)
      return false
    }
    const ok = await run(
      contactPayload('village', 'add_village', { fields: { name } }),
    )
    if (ok) {
      setVillage('')
      focusContactsNav(2)
    }
    return ok
  }

  saveDoctorRef.current = saveDoctor
  saveCustomerRef.current = saveCustomer
  saveSupplierRef.current = saveSupplier
  saveVillageRef.current = saveVillage

  useEffect(() => {
    const onNav = (ev: Event) => {
      const action = String(
        (ev as CustomEvent<{ action?: string }>).detail?.action || '',
      )
      if (action === 'contacts-save-doctor') void saveDoctorRef.current()
      else if (action === 'contacts-save-customer') void saveCustomerRef.current()
      else if (action === 'contacts-save-supplier') void saveSupplierRef.current()
      else if (action === 'contacts-save-village') void saveVillageRef.current()
    }
    document.addEventListener('satpuda-nav-action', onNav)
    return () => document.removeEventListener('satpuda-nav-action', onNav)
  }, [])

  usePageHotkeys({
    onFocusFilter: () => focusContactsNav(1),
    onF2: () => focusTableSection(tableWrapRef),
    onSave: () => {
      if (sectionId === 'doctors') void saveDoctorRef.current()
      else if (sectionId === 'customers') void saveCustomerRef.current()
      else if (sectionId === 'suppliers') void saveSupplierRef.current()
      else if (sectionId === 'villages') void saveVillageRef.current()
    },
    onClear: () => {
      clearEdit()
      setVillage('')
      setSearch('')
      setErr('')
      setMsg('')
      focusContactsNav(2)
    },
  })

  const doctors = useMemo(() => {
    const rows = contacts.doctors.filter((d) =>
      filterText(search, [d.name, d.reg_no, d.phone]),
    )
    return numbered ? onlyNumbered(rows, voiceNo, 'doctor', (d) => d.name) : rows
  }, [contacts.doctors, search, numbered, voiceNo])
  const customers = useMemo(() => {
    const rows = contacts.customers.filter((d) =>
      filterText(search, [d.name, d.phone, d.address, d.total_due]),
    )
    return numbered ? onlyNumbered(rows, voiceNo, 'customer', (d) => d.name) : rows
  }, [contacts.customers, search, numbered, voiceNo])
  const suppliers = useMemo(() => {
    const rows = contacts.suppliers.filter((d) =>
      filterText(search, [d.name, d.phone, d.gstin, d.address, d.total_due]),
    )
    return numbered ? onlyNumbered(rows, voiceNo, 'supplier', (d) => d.name) : rows
  }, [contacts.suppliers, search, numbered, voiceNo])
  const villages = useMemo(() => {
    const rows = contacts.villages.filter((v) => filterText(search, [v]))
    return numbered ? onlyNumbered(rows, voiceNo, 'village', (v) => v) : rows
  }, [contacts.villages, search, numbered, voiceNo])

  const cancelBtn =
    editId != null ? (
      <button
        type="button"
        className="settings-action-btn iset-secondary"
        onClick={clearEdit}
      >
        Cancel
      </button>
    ) : null

  const searchBox = (
    <BarField label="Search" className="grow">
      <input
        className="settings-input"
        data-nav-order={1}
        data-nav-chain={CONTACTS_NAV}
        data-page-filter="primary"
        value={search}
        placeholder="Filter…"
        onChange={(e) => setSearch(e.target.value)}
      />
    </BarField>
  )

  return (
    <>
      {sectionId === 'doctors' && (
        <Frame title={`Doctors (${contacts.doctors.length})`}>
          <div className={`iset-form-card${editId != null ? ' editing' : ''}`}>
            <h4>{editId != null ? 'Edit doctor' : 'Add a doctor'}</h4>
            <div className="settings-form-row">
              <Field label="Name">
                <input
                  className="settings-input"
                  data-nav-order={2}
                  data-nav-chain={CONTACTS_NAV}
                  value={doctor.name}
                  onChange={(e) =>
                    setDoctor((d) => ({ ...d, name: e.target.value }))
                  }
                />
              </Field>
              <Field label="Reg No">
                <input
                  className="settings-input"
                  data-nav-order={3}
                  data-nav-chain={CONTACTS_NAV}
                  placeholder="e.g. MMC/2021/67890"
                  value={doctor.reg_no}
                  onChange={(e) =>
                    setDoctor((d) => ({ ...d, reg_no: e.target.value }))
                  }
                />
              </Field>
              <Field label="Phone">
                <input
                  className="settings-input"
                  data-nav-order={4}
                  data-nav-chain={CONTACTS_NAV}
                  data-nav-enter="contacts-save-doctor"
                  value={doctor.phone}
                  onChange={(e) =>
                    setDoctor((d) => ({ ...d, phone: e.target.value }))
                  }
                />
              </Field>
            </div>
            <div className="iset-row">
              <SaveBtn
                label={editId != null ? 'Save Doctor' : 'Add Doctor'}
                navOrder={5}
                navChain={CONTACTS_NAV}
                navAction="contacts-save-doctor"
                onClick={() => {
                  void saveDoctor()
                }}
              />
              {cancelBtn}
            </div>
          </div>
          <div className="iset-bar">{searchBox}</div>
          <VoiceNoBar sc={voiceNo} kind="doctor" only={voiceOnly} onOnly={setVoiceOnly} />
          <div ref={tableWrapRef}>
          <CappedTableWrap
            visibleRows={doctorRows}
            className="settings-table-wrap"
          >
            <table className="settings-table">
              <thead>
                <tr>
                  {showVoiceNo ? <th style={{ width: 70 }}>Voice no.</th> : null}
                  <th>Name</th>
                  <th>Reg No</th>
                  <th>Phone</th>
                  <th className="act">Actions</th>
                </tr>
              </thead>
              <tbody>
                {doctors.map((d) => (
                  <tr
                    key={d.id}
                    className={editId === d.id ? 'iset-active-row' : undefined}
                  >
                    {showVoiceNo ? (
                      <td>
                        <VoiceNoSelect sc={voiceNo} kind="doctor" name={d.name} />
                      </td>
                    ) : null}
                    <td>{d.name}</td>
                    <td>{d.reg_no}</td>
                    <td>{d.phone}</td>
                    <td className="act">
                      <button
                        type="button"
                        className="iset-btn"
                        onClick={() => {
                          setEditId(d.id)
                          setDoctor({
                            name: d.name,
                            reg_no: d.reg_no,
                            phone: d.phone,
                          })
                        }}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="iset-btn danger"
                        onClick={() =>
                          run(
                            contactPayload('doctor', 'delete', { id: d.id }),
                          )
                        }
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
                {!doctors.length ? (
                  <EmptyRow cols={showVoiceNo ? 5 : 4}>
                    {noneText('doctor', 'doctors', search, numbered)}
                  </EmptyRow>
                ) : null}
              </tbody>
            </table>
          </CappedTableWrap>
          </div>
        </Frame>
      )}

      {sectionId === 'customers' && (
        <Frame title={`Customers (${contacts.customers.length})`}>
          <div className={`iset-form-card${editId != null ? ' editing' : ''}`}>
            <h4>{editId != null ? 'Edit customer' : 'Add a customer'}</h4>
            <div className="settings-form-row">
              <Field label="Name">
                <input
                  className="settings-input"
                  data-nav-order={2}
                  data-nav-chain={CONTACTS_NAV}
                  value={customer.name}
                  onChange={(e) =>
                    setCustomer((c) => ({ ...c, name: e.target.value }))
                  }
                />
              </Field>
              <Field label="Phone">
                <input
                  className="settings-input"
                  data-nav-order={3}
                  data-nav-chain={CONTACTS_NAV}
                  value={customer.phone}
                  onChange={(e) =>
                    setCustomer((c) => ({ ...c, phone: e.target.value }))
                  }
                />
              </Field>
              <Field label="Address (Village)">
                <ModernCombo
                  className="settings-input"
                  value={customer.address}
                  placeholder="Village / address"
                  minChars={0}
                  filterLocal
                  listLabel="Villages"
                  emptyText="New village — Enter to save"
                  enterPicksHighlight={false}
                  navOrder={4}
                  navChain={CONTACTS_NAV}
                  items={(contacts.villages || []).map((v) => ({
                    id: v,
                    label: v,
                  }))}
                  onChange={(v) =>
                    setCustomer((c) => ({ ...c, address: v }))
                  }
                  onPick={(it) => {
                    setCustomer((c) => ({ ...c, address: it.label }))
                    void rememberVillage(it.label)
                  }}
                  onEnter={(picked) => {
                    const addr = (
                      picked?.label || customerRef.current.address
                    ).trim()
                    if (addr) {
                      setCustomer((c) => ({ ...c, address: addr }))
                      void rememberVillage(addr)
                    }
                    void saveCustomer()
                  }}
                  onBlur={() => {
                    void rememberVillage(customerRef.current.address)
                  }}
                />
              </Field>
            </div>
            {isWholesale ? (
              <PartyFields value={party} onChange={setParty} />
            ) : editId != null ? (
              <CustomerGstRow customerId={editId} customerName={customer.name} />
            ) : null}
            <div className="iset-row">
              <SaveBtn
                label={editId != null ? 'Save Customer' : 'Add Customer'}
                navOrder={5}
                navChain={CONTACTS_NAV}
                navAction="contacts-save-customer"
                onClick={() => {
                  void saveCustomer()
                }}
              />
              {cancelBtn}
            </div>
          </div>
          <div className="iset-bar">
            {searchBox}
            <div className="iset-bar-actions">
              <button
                type="button"
                className="settings-action-btn iset-secondary"
                onClick={() =>
                  void systemAction({
                    action: 'export_contacts',
                    kind: 'current_view',
                    format: 'csv',
                    headers: ['Name', 'Phone', 'Address', 'Due'],
                    rows: customers.map((c) => [
                      c.name,
                      c.phone,
                      c.address || '',
                      String(c.total_due ?? 0),
                    ]),
                  }).then((res) => {
                    if (res.ok === false && res.error) setErr(String(res.error))
                    else setMsg(String(res.message || res.path || 'Exported.'))
                  })
                }
              >
                Export current view
              </button>
              <button
                type="button"
                className="settings-action-btn iset-secondary"
                onClick={() =>
                  void systemAction({
                    action: 'export_contacts',
                    kind: 'customer_list',
                    format: 'csv',
                  }).then((res) => {
                    if (res.ok === false && res.error) setErr(String(res.error))
                    else setMsg(String(res.message || res.path || 'Exported.'))
                  })
                }
              >
                Customer list (all)
              </button>
              <button
                type="button"
                className="settings-action-btn iset-secondary"
                onClick={() =>
                  void systemAction({
                    action: 'export_contacts',
                    kind: 'customer_due',
                    format: 'csv',
                  }).then((res) => {
                    if (res.ok === false && res.error) setErr(String(res.error))
                    else setMsg(String(res.message || res.path || 'Exported.'))
                  })
                }
              >
                Customer due list
              </button>
              <button
                type="button"
                className="settings-action-btn iset-secondary"
                onClick={() => {
                  if (
                    !window.confirm(
                      'Recalculate dues for all customers? This may take a moment.',
                    )
                  ) {
                    return
                  }
                  void run(contactPayload('customer', 'recalculate'))
                }}
              >
                Recalculate All Dues
              </button>
            </div>
          </div>
          <VoiceNoBar sc={voiceNo} kind="customer" only={voiceOnly} onOnly={setVoiceOnly} />
          <div ref={tableWrapRef}>
          <CappedTableWrap
            visibleRows={customerRows}
            className="settings-table-wrap"
          >
            <table className="settings-table">
              <thead>
                <tr>
                  {showVoiceNo ? <th style={{ width: 70 }}>Voice no.</th> : null}
                  <th>Name</th>
                  <th>Phone</th>
                  <th>Address</th>
                  {isWholesale ? (
                    <>
                      <th>Type</th>
                      <th>GSTIN</th>
                      <th>State</th>
                      <th>Tier</th>
                      <th className="num">Credit</th>
                    </>
                  ) : null}
                  <th className="num">Due</th>
                  <th className="act">Actions</th>
                </tr>
              </thead>
              <tbody>
                {customers.map((d) => (
                  <tr
                    key={d.id}
                    className={editId === d.id ? 'iset-active-row' : undefined}
                  >
                    {showVoiceNo ? (
                      <td>
                        <VoiceNoSelect sc={voiceNo} kind="customer" name={d.name} />
                      </td>
                    ) : null}
                    <td>{d.name}</td>
                    <td>{d.phone}</td>
                    <td>{d.address || ''}</td>
                    {isWholesale ? (
                      <>
                        <td>{d.party_type === 'medical' ? 'Medical' : 'Retail'}</td>
                        <td>{d.gstin || ''}</td>
                        <td title={GST_STATES[d.state_code || ''] || ''}>{d.state_code || ''}</td>
                        <td>
                          <span className="ws-tier">{d.rate_tier || 'A'}</span>
                        </td>
                        <td className="num">
                          {Number(d.credit_limit || 0) > 0 ? money(Number(d.credit_limit)) : ''}
                          {Number(d.credit_days || 0) > 0 ? ` · ${d.credit_days}d` : ''}
                        </td>
                      </>
                    ) : null}
                    <td className="num">{money(d.total_due)}</td>
                    <td className="act">
                      <button
                        type="button"
                        className="iset-btn"
                        onClick={() => {
                          setEditId(d.id)
                          setCustomer({
                            name: d.name,
                            phone: d.phone,
                            address: d.address || d.village || '',
                          })
                          setParty(partyFromRow(d))
                        }}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="iset-btn danger"
                        onClick={() =>
                          run(
                            contactPayload('customer', 'delete', { id: d.id }),
                          )
                        }
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
                {!customers.length ? (
                  <EmptyRow cols={(showVoiceNo ? 6 : 5) + (isWholesale ? 5 : 0)}>
                    {noneText('customer', 'customers', search, numbered)}
                  </EmptyRow>
                ) : null}
              </tbody>
            </table>
          </CappedTableWrap>
          </div>
        </Frame>
      )}

      {sectionId === 'villages' && (
        <Frame title={`Villages (${contacts.villages.length})`}>
          <div className="iset-form-card">
            <h4>Add a village</h4>
            <div className="iset-bar" style={{ margin: 0 }}>
              <BarField label="New village" className="grow">
                <input
                  className="settings-input"
                  data-nav-order={2}
                  data-nav-chain={CONTACTS_NAV}
                  data-nav-enter="contacts-save-village"
                  value={village}
                  onChange={(e) => setVillage(e.target.value)}
                />
              </BarField>
              <SaveBtn
                label="Add Village"
                navOrder={3}
                navChain={CONTACTS_NAV}
                navAction="contacts-save-village"
                onClick={() => {
                  void saveVillage()
                }}
              />
            </div>
          </div>
          <div className="iset-bar">
            {searchBox}
            <div className="iset-bar-actions">
              <span className="iset-hint">
                Default: <strong>{contacts.default_village || '(none)'}</strong>
              </span>
            </div>
          </div>
          <VoiceNoBar sc={voiceNo} kind="village" only={voiceOnly} onOnly={setVoiceOnly} />
          <div className="iset-table-wrap">
            <table className="settings-table">
              <thead>
                <tr>
                  {showVoiceNo ? <th style={{ width: 70 }}>Voice no.</th> : null}
                  <th>Village</th>
                  <th className="act">Actions</th>
                </tr>
              </thead>
              <tbody>
                {villages.map((v) => (
                  <tr key={v}>
                    {showVoiceNo ? (
                      <td>
                        <VoiceNoSelect sc={voiceNo} kind="village" name={v} />
                      </td>
                    ) : null}
                    <td>
                      {v}
                      {contacts.default_village === v ? (
                        <span className="iset-badge">default</span>
                      ) : null}
                    </td>
                    <td className="act">
                      <button
                        type="button"
                        className="iset-btn"
                        onClick={() =>
                          run(
                            contactPayload('village', 'set_default_village', {
                              fields: { name: v },
                            }),
                          )
                        }
                      >
                        Set default
                      </button>
                      <button
                        type="button"
                        className="iset-btn danger"
                        onClick={() =>
                          run(
                            contactPayload('village', 'remove', {
                              fields: { name: v },
                            }),
                          )
                        }
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
                {!villages.length ? (
                  <EmptyRow cols={showVoiceNo ? 3 : 2}>
                    {noneText('village', 'villages', search, numbered)}
                  </EmptyRow>
                ) : null}
              </tbody>
            </table>
          </div>
        </Frame>
      )}

      {sectionId === 'suppliers' && (
        <Frame title={`Suppliers (${contacts.suppliers.length})`}>
          <div className={`iset-form-card${editId != null ? ' editing' : ''}`}>
            <h4>{editId != null ? 'Edit supplier' : 'Add a supplier'}</h4>
            <div className="settings-form-row">
              <Field label="Name">
                <input
                  className="settings-input"
                  data-nav-order={2}
                  data-nav-chain={CONTACTS_NAV}
                  value={supplier.name}
                  onChange={(e) =>
                    setSupplier((s) => ({ ...s, name: e.target.value }))
                  }
                />
              </Field>
              <Field label="Phone">
                <input
                  className="settings-input"
                  data-nav-order={3}
                  data-nav-chain={CONTACTS_NAV}
                  value={supplier.phone}
                  onChange={(e) =>
                    setSupplier((s) => ({ ...s, phone: e.target.value }))
                  }
                />
              </Field>
              <Field label="GSTIN">
                <input
                  className="settings-input"
                  data-nav-order={4}
                  data-nav-chain={CONTACTS_NAV}
                  value={supplier.gstin}
                  onChange={(e) =>
                    setSupplier((s) => ({ ...s, gstin: e.target.value }))
                  }
                />
              </Field>
              <Field label="Address">
                <input
                  className="settings-input"
                  data-nav-order={5}
                  data-nav-chain={CONTACTS_NAV}
                  data-nav-enter="contacts-save-supplier"
                  value={supplier.address}
                  onChange={(e) =>
                    setSupplier((s) => ({ ...s, address: e.target.value }))
                  }
                />
              </Field>
            </div>
            <div className="iset-row">
              <SaveBtn
                label={editId != null ? 'Save Supplier' : 'Add Supplier'}
                navOrder={6}
                navChain={CONTACTS_NAV}
                navAction="contacts-save-supplier"
                onClick={() => {
                  void saveSupplier()
                }}
              />
              {cancelBtn}
            </div>
          </div>
          <div className="iset-bar">
            {searchBox}
            <div className="iset-bar-actions">
              <button
                type="button"
                className="settings-action-btn iset-secondary"
                onClick={() =>
                  run(contactPayload('supplier', 'recalculate'))
                }
              >
                Recalculate All Dues
              </button>
            </div>
          </div>
          <VoiceNoBar sc={voiceNo} kind="supplier" only={voiceOnly} onOnly={setVoiceOnly} />
          <div ref={tableWrapRef}>
          <CappedTableWrap
            visibleRows={supplierRows}
            className="settings-table-wrap"
          >
            <table className="settings-table">
              <thead>
                <tr>
                  {showVoiceNo ? <th style={{ width: 70 }}>Voice no.</th> : null}
                  <th>Name</th>
                  <th>Phone</th>
                  <th>GSTIN</th>
                  <th>Address</th>
                  <th className="num">Due</th>
                  <th className="act">Actions</th>
                </tr>
              </thead>
              <tbody>
                {suppliers.map((d) => (
                  <tr
                    key={d.id}
                    className={editId === d.id ? 'iset-active-row' : undefined}
                  >
                    {showVoiceNo ? (
                      <td>
                        <VoiceNoSelect sc={voiceNo} kind="supplier" name={d.name} />
                      </td>
                    ) : null}
                    <td>{d.name}</td>
                    <td>{d.phone}</td>
                    <td>{d.gstin}</td>
                    <td>{d.address || ''}</td>
                    <td className="num">{money(d.total_due)}</td>
                    <td className="act">
                      <button
                        type="button"
                        className="iset-btn"
                        onClick={() => {
                          setEditId(d.id)
                          setSupplier({
                            name: d.name,
                            phone: d.phone,
                            gstin: d.gstin,
                            address: d.address || '',
                          })
                        }}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="iset-btn danger"
                        onClick={() =>
                          run(
                            contactPayload('supplier', 'delete', { id: d.id }),
                          )
                        }
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
                {!suppliers.length ? (
                  <EmptyRow cols={showVoiceNo ? 7 : 6}>
                    {noneText('supplier', 'suppliers', search, numbered)}
                  </EmptyRow>
                ) : null}
              </tbody>
            </table>
          </CappedTableWrap>
          </div>
        </Frame>
      )}
      <StatusBar msg={msg} err={err} />
    </>
  )
}

/* ─── Alerts ───────────────────────────────────────────────────────────── */

const ALERT_SPECS: Record<
  string,
  {
    key: keyof Alerts
    title: string
    headers: string[]
    file: string
    actionLabel?: string
    bulkReorder?: boolean
    bulkReturn?: boolean
    dismissRow?: boolean
    dismissAllExpired?: boolean
    dismissAllOos?: boolean
    /** What the Month / Year filter looks at; the date is the row's last (hidden) cell. */
    periodOf?: 'purchase' | 'expiry'
  }
> = {
  low: {
    key: 'low_stock',
    title: 'Low Stock',
    headers: ['Medicine Name', 'Current Stock', 'Unit', 'Supplier', 'Last Batch', 'Expiry', 'Last Bill No', 'Last Purchase'],
    periodOf: 'purchase',
    file: 'alerts-low',
    actionLabel: 'Reorder',
    bulkReorder: true,
    dismissRow: true,
  },
  out: {
    key: 'out_of_stock',
    title: 'Out of Stock',
    headers: [
      'Medicine Name',
      'Pack Size',
      'Selling Rate',
      'Purchase Rate',
      'Medicine Type',
      'Supplier',
      'Last Batch',
      'Last Bill No',
      'Last Purchase',
    ],
    periodOf: 'purchase',
    file: 'alerts-out',
    actionLabel: 'Reorder',
    bulkReorder: true,
    dismissRow: true,
    dismissAllOos: true,
  },
  expired: {
    key: 'expired',
    title: 'Expired',
    headers: [
      'Medicine Name',
      'Batch Number',
      'Expiry Date',
      'Quantity Expired',
      'Supplier Name',
      'Bill Number',
      'Purchase Date',
    ],
    periodOf: 'expiry',
    file: 'alerts-expired',
    actionLabel: 'Return',
    bulkReturn: true,
    dismissRow: true,
    dismissAllExpired: true,
  },
  near: {
    key: 'near_expiry',
    title: 'Near Expiry',
    headers: [
      'Medicine Name',
      'Batch Number',
      'Expiry Date',
      'Remaining Days',
      'Available Qty',
      'Supplier Name',
      'Bill Number',
      'Purchase Date',
    ],
    periodOf: 'expiry',
    file: 'alerts-near',
    actionLabel: 'Return',
    bulkReturn: true,
    dismissRow: true,
  },
  dues: {
    key: 'customer_due',
    title: 'Customer Dues',
    headers: [
      'Customer Name',
      'Mobile Number',
      'Bill Number',
      'Bill Date',
      'Total Amount',
      'Paid Amount',
      'Due Amount',
      'Due Days',
    ],
    file: 'alerts-dues',
  },
}

const ALERT_ROWS_SHOWN = 1000

/** Each list's empty state, in words. */
const ALERT_EMPTY: Record<string, string> = {
  low: 'No medicine is below its low-stock level.',
  out: 'Nothing is out of stock.',
  expired: 'No expired batch is in stock.',
  near: 'No batch is close to its expiry.',
  dues: 'No customer owes more than the minimum due.',
}

/** Columns that hold numbers: right-aligned, tabular figures. */
const ALERT_NUM_HEADERS = new Set([
  'Current Stock',
  'Selling Rate',
  'Purchase Rate',
  'Quantity Expired',
  'Remaining Days',
  'Available Qty',
  'Total Amount',
  'Paid Amount',
  'Due Amount',
  'Due Days',
])

type ExportHow = 'csv' | 'xlsx' | 'pdf' | 'dm_a4' | 'dm_a5' | 'printer'
const EXPORT_HOW: { key: ExportHow; label: string }[] = [
  { key: 'xlsx', label: 'Excel file' },
  { key: 'pdf', label: 'PDF file' },
  { key: 'csv', label: 'CSV file' },
  { key: 'dm_a4', label: 'Print — dot matrix, A4' },
  { key: 'dm_a5', label: 'Print — dot matrix, A5' },
  { key: 'printer', label: 'Print — normal printer (laser / inkjet)' },
]

/** Export or print the list on screen, or all five lists at once, as the screen filters them
 *  (search and Month / Year). */
function AlertsExportDialog({
  alerts,
  current,
  period,
  search,
  onClose,
  onDone,
}: {
  alerts: Alerts
  current: string
  period: string
  search: string
  onClose: () => void
  onDone: (msg: string) => void
}) {
  const [which, setWhich] = useState<'current' | 'all'>('current')
  const [how, setHow] = useState<ExportHow>('xlsx')
  const [pageLayout, setPageLayout] = useState<'portrait' | 'landscape'>('portrait')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const keys = which === 'all' ? Object.keys(ALERT_SPECS) : [current]
  const sections = keys.map((k) => {
    const sp = ALERT_SPECS[k]
    const rows = alertRows(alerts, sp.key as string, sp.periodOf, sp.periodOf ? period : '')
      .filter((r) => filterText(k === current ? search : '', r.slice(0, sp.headers.length)))
      .map((r) => r.slice(0, sp.headers.length))
    const t = sp.periodOf && period ? `${sp.title} — ${periodLabel(period)}` : sp.title
    return { title: t, columns: sp.headers, rows }
  })
  const total = sections.reduce((n, s) => n + s.rows.length, 0)

  async function go() {
    setBusy(true)
    setError('')
    try {
      const printTo = how === 'printer' ? 'printer' : how.startsWith('dm_') ? 'dot_matrix' : ''
      const res = await exportAlerts({
        title: which === 'all' ? 'Alert & Monitoring' : sections[0]?.title || 'Alerts',
        sections,
        format: printTo ? undefined : (how as 'csv' | 'xlsx' | 'pdf'),
        print_to: printTo as '' | 'dot_matrix' | 'printer',
        paper: how === 'dm_a5' ? 'A5' : 'A4',
        page_layout: pageLayout,
      })
      if (!res.ok) {
        setError(res.error || 'Export zala nahi.')
        return
      }
      if (res.content_base64 && res.filename) {
        await saveFileAndShow(res.filename, res.mime || 'application/octet-stream', res.content_base64)
        onDone(`Saved ${res.filename}`)
      } else {
        onDone(res.message || 'Printed.')
      }
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal-card" role="dialog" aria-modal="true" aria-label="Export / Print"
        onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Export / Print — Alert &amp; Monitoring</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          <Field label="What">
            <select className="settings-input" value={which}
              onChange={(e) => setWhich(e.target.value as 'current' | 'all')}>
              <option value="current">This list ({ALERT_SPECS[current]?.title})</option>
              <option value="all">All lists (Low, Out of Stock, Expired, Near Expiry, Dues)</option>
            </select>
          </Field>
          <Field label="How">
            <select className="settings-input" value={how} onChange={(e) => setHow(e.target.value as ExportHow)}>
              {EXPORT_HOW.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          {how !== 'csv' && how !== 'xlsx' && how !== 'dm_a5' ? (
            <Field label="Page">
              <select className="settings-input" value={pageLayout}
                onChange={(e) => setPageLayout(e.target.value as 'portrait' | 'landscape')}>
                <option value="portrait">A4 vertical (portrait)</option>
                <option value="landscape">A4 horizontal (landscape)</option>
              </select>
            </Field>
          ) : null}
          <Note>
            {total} rows{period ? ` · ${periodLabel(period)}` : ''}
            {which === 'all' ? ` · ${sections.map((s) => `${s.title}: ${s.rows.length}`).join(' · ')}` : ''}
          </Note>
          {error ? <p className="settings-error">{error}</p> : null}
          <div className="iset-row">
            <SaveBtn label={how.startsWith('dm_') || how === 'printer' ? 'Print' : 'Save file'}
              saving={busy} onClick={() => void go()} />
            <button type="button" className="settings-action-btn iset-secondary" onClick={onClose}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

/** A spoken month / year ("this", "last", "next", "2027-03", "03", "2027") -> "2027-03" / "2027". */
function resolvePeriod(kind: string, value: string): string {
  const now = new Date()
  const v = value.trim().toLowerCase()
  if (kind === 'year') {
    if (/^\d{4}$/.test(v)) return v
    const y = now.getFullYear() + (v === 'last' ? -1 : v === 'next' ? 1 : v === 'this' || !v ? 0 : NaN)
    return Number.isFinite(y) ? String(y) : ''
  }
  if (/^\d{4}-\d{2}$/.test(v)) return v
  if (/^\d{1,2}$/.test(v) && Number(v) >= 1 && Number(v) <= 12) {
    return `${now.getFullYear()}-${String(Number(v)).padStart(2, '0')}`
  }
  const shift = v === 'last' ? -1 : v === 'next' ? 1 : v === 'this' || !v ? 0 : NaN
  if (!Number.isFinite(shift)) return ''
  const d = new Date(now.getFullYear(), now.getMonth() + shift, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function periodLabel(p: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(p)
  return m ? `${MONTHS[Number(m[2]) - 1] || m[2]} ${m[1]}` : p
}

/** A row's date for the Month / Year filter: its last cell, YYYY-MM-DD ('' when none). */
function rowIso(r: unknown[]): string {
  const v = String(r[r.length - 1] ?? '')
  return /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : ''
}

/** The rows of one tab. With a month or year, Expired / Near Expiry list every batch whose
 *  expiry falls in it (not only those inside the near-expiry days), and Low / Out of Stock
 *  the medicines last bought in it. */
function alertRows(alerts: Alerts, key: string, periodOf: string | undefined, period: string): unknown[][] {
  const base = ((alerts as Record<string, unknown>)[key] as unknown[][]) || []
  if (!period || !periodOf) return base
  if (periodOf === 'expiry') {
    const all = (alerts.expiry_by_batch as unknown[][]) || []
    // all: name, batch, expiry, days, qty, supplier, bill, purchase date, expiry ISO
    const picked = all.filter((r) => rowIso(r).startsWith(period))
    if (key === 'expired') {
      return picked
        .filter((r) => Number(r[3]) < 0)
        .map((r) => [r[0], r[1], r[2], r[4], r[5], r[6], r[7], r[8]])
    }
    return picked.filter((r) => Number(r[3]) >= 0)
  }
  return base.filter((r) => rowIso(r).startsWith(period))
}

/** Years that have something to show, newest first, plus this year. */
function alertYears(alerts: Alerts): string[] {
  const ys = new Set<string>([String(new Date().getFullYear())])
  for (const key of ['low_stock', 'out_of_stock', 'expiry_by_batch']) {
    for (const r of ((alerts as Record<string, unknown>)[key] as unknown[][]) || []) {
      const iso = rowIso(r)
      if (iso) ys.add(iso.slice(0, 4))
    }
  }
  return [...ys].sort().reverse()
}

export function AlertsPanel({
  nestedId,
  alerts,
  onRefresh,
  onAlertsChange,
  onNavigate,
}: {
  nestedId: string
  alerts: Alerts
  onRefresh: () => void
  onAlertsChange?: (alerts: Alerts) => void
  onNavigate?: AppNavigate
}) {
  const [search, setSearch] = useState('')
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  // Month ("2026-10") or year ("2026"); empty = the alert rules as before.
  const [month, setMonth] = useState('')
  const [year, setYear] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const spec = ALERT_SPECS[nestedId] || ALERT_SPECS.low
  const period = month || year
  const rows = alertRows(alerts, spec.key as string, spec.periodOf, period).filter((r) =>
    filterText(search, r.slice(0, spec.headers.length)),
  )
  const count = period ? rows.length : (alerts.counts?.[spec.key as string] ?? rows.length)
  const years = alertYears(alerts)
  const selectedRow = selectedIdx != null ? rows[selectedIdx] : null

  usePageHotkeys({ onSave: () => onRefresh() })

  function exportCsv() {
    // This also fired a whole-inventory export that nobody saw; it did nothing
    // while that export was broken, and would now write a stray file.
    downloadCsv(
      `${spec.file}.csv`,
      spec.headers,
      rows.map((r) => r.slice(0, spec.headers.length)),
    )
  }

  async function runAction(action: string, extra?: Record<string, unknown>) {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await alertAction({
        action,
        section: spec.key,
        ...extra,
      })
      if (!res.ok) {
        setErr(res.error || 'Action failed.')
        return
      }
      if (res.alerts) onAlertsChange?.(res.alerts as Alerts)
      if (res.message) setMsg(res.message)
      if (res.navigate) {
        applyAlertNavigation(res.navigate, onNavigate as never)
      } else onRefresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function rowAction(row?: unknown[]) {
    const values = row || selectedRow
    if (!values) {
      setErr('Select a row first.')
      return
    }
    await runAction('row_action', { values })
  }

  async function dismissRow() {
    if (!selectedRow) {
      setErr('Select a row first.')
      return
    }
    const label =
      spec.key === 'low_stock'
        ? String(selectedRow[0] || '')
        : spec.key === 'out_of_stock'
          ? `${selectedRow[0]} (${selectedRow[1] || 'no pack'})`
          : `${selectedRow[0]} batch ${selectedRow[1] || '—'}`
    if (
      !window.confirm(
        `Remove ${label} from inventory lists and alerts?\n\nSales and purchase history are kept.`,
      )
    ) {
      return
    }
    await runAction('dismiss_row', { values: selectedRow })
    setSelectedIdx(null)
  }

  async function dismissAllExpired() {
    if (
      !window.confirm(
        'Hide every expired medicine batch from inventory lists and alerts?\n\nThis does not delete sales or purchase history.',
      )
    ) {
      return
    }
    await runAction('dismiss_all_expired')
    setSelectedIdx(null)
  }

  async function dismissAllOos() {
    if (
      !window.confirm(
        'Hide every out-of-stock medicine from inventory lists and alerts?\n\nThis does not delete sales or purchase history.',
      )
    ) {
      return
    }
    await runAction('dismiss_all_out_of_stock')
    setSelectedIdx(null)
  }

  async function bulkReorder() {
    await runAction('bulk_reorder')
  }

  async function bulkReturn() {
    await runAction('bulk_return')
  }

  useEffect(() => {
    setSearch('')
    setSelectedIdx(null)
    setErr('')
    setMsg('')
  }, [nestedId])

  useEffect(() => setSelectedIdx(null), [month, year])

  // ── Voice: "ya mahinyache", "march 2027", "2027 che", "filter kadh", "refresh", "export" ──
  const voiceHandlerRef = useRef<VoiceHandler>(async () => null)
  voiceHandlerRef.current = async (cmd) => {
    const a = cmd.args || {}
    if (cmd.intent === 'page_filter' && (a.filter === 'month' || a.filter === 'year')) {
      if (!spec.periodOf) return { ok: false, say: `${spec.title}: mahina / varsh filter nahi` }
      const v = resolvePeriod(String(a.filter), String(a.value ?? ''))
      if (!v) return { ok: false, say: `"${a.value ?? ''}" mahina / varsh samajla nahi` }
      if (a.filter === 'month') {
        setMonth(v)
        setYear('')
      } else {
        setYear(v)
        setMonth('')
      }
      const n = alertRows(alerts, spec.key as string, spec.periodOf, v).length
      return { ok: true, say: `${spec.title}: ${periodLabel(v)} — ${n} ol` }
    }
    if (cmd.intent === 'page_filter' && a.filter === 'clear') {
      setMonth('')
      setYear('')
      setSearch('')
      return { ok: true, say: `${spec.title}: sagle dakhavle` }
    }
    if (cmd.intent === 'page_action' && a.action === 'refresh') {
      onRefresh()
      return { ok: true, say: 'Alerts parat vachle' }
    }
    if (cmd.intent === 'page_action' && a.action === 'export') {
      exportCsv()
      return { ok: true, say: `${spec.title}: CSV export kela` }
    }
    if (cmd.intent === 'search') {
      setSearch(String(a.query || ''))
      return { ok: true, say: `${spec.title}: shodh ${a.query || ''}` }
    }
    if (cmd.intent === 'page_filter' || cmd.intent === 'page_action') {
      return { ok: false, say: `${spec.title} var he kaam voice var nahi` }
    }
    return null
  }
  useEffect(() => registerVoicePage('alerts', () => voiceHandlerRef.current), [])

  const emptyText =
    search.trim() || period
      ? `Nothing in ${spec.title} matches${search.trim() ? ` “${search.trim()}”` : ''}${
          period ? ` in ${periodLabel(period)}` : ''
        }.`
      : ALERT_EMPTY[nestedId] || 'Nothing to show.'
  const hasRowActions = Boolean(spec.actionLabel || spec.dismissRow)
  const hasBulkRemove = Boolean(spec.dismissAllExpired || spec.dismissAllOos)

  return (
    <>
      <PanelTitle>
        {spec.title} ({count}){period ? ` — ${periodLabel(period)}` : ''}
      </PanelTitle>
      <Note>
        Central dashboard for stock, expiry, and customer due alerts. Select a
        row for Reorder/Return actions. F5 refreshes.
      </Note>
      {spec.periodOf ? (
        <Help title="What Month / Year shows">
          {spec.periodOf === 'expiry'
            ? 'Month / Year shows every batch whose expiry falls in it, batch-wise.'
            : 'Month / Year shows the medicines last bought in it.'}
        </Help>
      ) : null}
      <div className="iset-bar">
        <BarField label="Search" className="grow">
          <input
            className="settings-input"
            value={search}
            placeholder="Filter table…"
            onChange={(e) => setSearch(e.target.value)}
          />
        </BarField>
        {spec.periodOf ? (
          <>
            <BarField label="Month" className="mid">
              <input
                type="month"
                className="settings-input"
                value={month}
                onChange={(e) => {
                  setMonth(e.target.value)
                  if (e.target.value) setYear('')
                }}
              />
            </BarField>
            <BarField label="Year" className="short">
              <select
                className="settings-input"
                value={year}
                onChange={(e) => {
                  setYear(e.target.value)
                  if (e.target.value) setMonth('')
                }}
              >
                <option value="">All</option>
                {years.map((y) => (
                  <option key={y} value={y}>
                    {y}
                  </option>
                ))}
              </select>
            </BarField>
            {period ? (
              <button
                type="button"
                className="settings-action-btn iset-secondary"
                onClick={() => {
                  setMonth('')
                  setYear('')
                }}
              >
                Clear Month / Year
              </button>
            ) : null}
          </>
        ) : null}
        <div className="iset-bar-actions">
          <SaveBtn label="Refresh (F5)" onClick={() => onRefresh()} />
          <button
            type="button"
            className="settings-action-btn iset-secondary"
            onClick={() => setExportOpen(true)}
          >
            Export / Print
          </button>
          <button
            type="button"
            className="settings-action-btn iset-secondary"
            disabled={busy}
            onClick={() => void bulkReorder()}
          >
            Reorder by Supplier
          </button>
          <button
            type="button"
            className="settings-action-btn iset-secondary"
            disabled={busy}
            onClick={() => void bulkReturn()}
          >
            Return by Purchase
          </button>
        </div>
      </div>
      {hasRowActions || hasBulkRemove ? (
        <div className="iset-alert-actions">
          {hasRowActions ? (
            <span className="iset-row-label">
              {selectedRow ? `Selected: ${String(selectedRow[0] ?? '')}` : 'Select a row:'}
            </span>
          ) : null}
          {spec.actionLabel ? (
            <SaveBtn
              label={spec.actionLabel}
              saving={busy}
              onClick={() => void rowAction()}
            />
          ) : null}
          {spec.dismissRow ? (
            <button
              type="button"
              className="settings-action-btn iset-secondary"
              disabled={busy}
              onClick={() => void dismissRow()}
            >
              Remove from List
            </button>
          ) : null}
          {hasBulkRemove ? (
            <span className="iset-push">
              {spec.dismissAllExpired ? (
                <button
                  type="button"
                  className="settings-action-btn iset-danger"
                  disabled={busy}
                  onClick={() => void dismissAllExpired()}
                >
                  Remove All Expired
                </button>
              ) : null}
              {spec.dismissAllOos ? (
                <button
                  type="button"
                  className="settings-action-btn iset-danger"
                  disabled={busy}
                  onClick={() => void dismissAllOos()}
                >
                  Remove All Out of Stock
                </button>
              ) : null}
            </span>
          ) : null}
        </div>
      ) : null}
      {err ? <p className="iset-callout warn">{err}</p> : null}
      {msg ? <p className="iset-callout">{msg}</p> : null}
      {exportOpen ? (
        <AlertsExportDialog
          alerts={alerts}
          current={nestedId}
          period={period}
          search={search}
          onClose={() => setExportOpen(false)}
          onDone={(m) => setMsg(m)}
        />
      ) : null}
      <div className="iset-table-wrap capped">
        <table className="settings-table">
          <thead>
            <tr>
              {spec.headers.map((h) => (
                <th key={h} className={ALERT_NUM_HEADERS.has(h) ? 'num' : undefined}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, ALERT_ROWS_SHOWN).map((r, i) => (
              <tr
                key={i}
                className={`iset-clickable${selectedIdx === i ? ' row-selected' : ''}`}
                onClick={() => setSelectedIdx(i)}
                onDoubleClick={() => {
                  setSelectedIdx(i)
                  void rowAction(r)
                }}
              >
                {spec.headers.map((h, j) => (
                  <td key={j} className={ALERT_NUM_HEADERS.has(h) ? 'num' : undefined}>
                    {String(r[j] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
            {!rows.length ? (
              <EmptyRow cols={spec.headers.length}>{emptyText}</EmptyRow>
            ) : null}
            {rows.length > ALERT_ROWS_SHOWN ? (
              <tr>
                <td className="iset-more" colSpan={spec.headers.length}>
                  First {ALERT_ROWS_SHOWN} of {rows.length} shown — Export CSV has every row.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </>
  )
}

/* ─── Payment ──────────────────────────────────────────────────────────── */

/** SCB12/FY2026-27 -> SCB12, as bills are shown everywhere else. */
function shortNo(raw: unknown) {
  return String(raw ?? '').split('/FY')[0]
}

/** "Bills cleared" for a history row: S2-55 ₹500 ✓ · S2-61 ₹200 · ₹50 left as credit
 *  (✓ = the bill was chosen for the payment; the rest went to the oldest bills). */
function clearedText(r: Record<string, unknown>) {
  const ref = refundOf(r)
  if (ref) return `Refund of ${ref.return_no}${ref.bill_no ? ` (bill ${shortNo(ref.bill_no)})` : ''}`
  const cleared = Array.isArray(r.cleared) ? (r.cleared as PaymentCleared[]) : []
  const parts = cleared.map(
    (c) => `${shortNo(c.bill_no) || `#${c.bill_id}`} ₹${money(c.amount)}${c.how === 'chosen' ? ' ✓' : ''}`,
  )
  const left = Number(r.credit_left || 0)
  if (left > 0.009) parts.push(`₹${money(left)} left as credit`)
  return parts.length ? parts.join(' · ') : '—'
}

type RefundOf = { return_id: number; return_no: string; return_date?: string; bill_no?: string }

/** The return a history row hands money back for ("Refund a return"), when it is one. */
function refundOf(r: Record<string, unknown>): RefundOf | null {
  if (!r.refund || !r.refund_of || typeof r.refund_of !== 'object') return null
  return r.refund_of as RefundOf
}

/** "COUGH SYRUP ×2 · PAIN TAB ×1" under a return's number. */
function returnItemsText(items: RefundableReturn['items']) {
  return (items || []).map((it) => `${it.name} ×${it.qty}`).join(' · ')
}

/** The View box of a payment row: what it was, the bills it cleared, or the return it refunded. */
function PaymentDetailsDialog({
  kind,
  row,
  onClose,
}: {
  kind: 'supplier' | 'customer'
  row: Record<string, unknown>
  onClose: () => void
}) {
  const [d, setD] = useState<PaymentDetails | null>(null)
  const [supported, setSupported] = useState(true)
  const [error, setError] = useState('')
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    closeRef.current?.focus()
    let alive = true
    fetchPaymentDetails(kind, Number(row.id || 0))
      .then((res) => {
        if (!alive) return
        setSupported(res.supported !== false)
        setD(res.payment || null)
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
  }, [kind, row.id])

  const amount = d ? d.amount : Number(row.amount || 0)
  const isRefund = d ? d.refund : !!refundOf(row)
  const no = d?.payment_no || String(row.payment_no || '') || `#${String(row.id ?? '')}`
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card pl-details-card"
        role="dialog"
        aria-modal="true"
        aria-label="Payment details"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            e.stopPropagation()
            onClose()
          }
        }}
      >
        <div className="modal-head">
          <h2>
            {isRefund ? 'Refund' : 'Payment'} {no}
          </h2>
          <button type="button" className="icon-btn" ref={closeRef} onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body">
          <dl className="pl-facts">
            <div>
              <dt>Date</dt>
              <dd>{d?.date || String(row.date ?? '')}</dd>
            </div>
            <div>
              <dt>No</dt>
              <dd>{no}</dd>
            </div>
            <div>
              <dt>{kind === 'supplier' ? 'Supplier' : 'Customer'}</dt>
              <dd>{d?.party || String(row.party ?? '')}</dd>
            </div>
            <div>
              <dt>Mode</dt>
              <dd>{d?.mode || String(row.mode ?? '')}</dd>
            </div>
            <div>
              <dt>{isRefund ? (kind === 'supplier' ? 'Paid back to us' : 'Paid to customer') : 'Amount'}</dt>
              <dd className="num">₹{money(isRefund ? Math.abs(amount) : amount)}</dd>
            </div>
            {kind === 'customer' && d && !isRefund && d.cash && d.online ? (
              <div>
                <dt>Cash / Online</dt>
                <dd className="num">
                  ₹{money(d.cash)} / ₹{money(d.online)}
                </dd>
              </div>
            ) : null}
            {(d?.reference || row.reference) && !isRefund ? (
              <div>
                <dt>Reference</dt>
                <dd>{d?.reference || String(row.reference ?? '')}</dd>
              </div>
            ) : null}
            {d?.receipt_no || row.receipt_no ? (
              <div>
                <dt>Receipt no.</dt>
                <dd>{d?.receipt_no || String(row.receipt_no ?? '')}</dd>
              </div>
            ) : null}
            {d?.note ? (
              <div>
                <dt>Note</dt>
                <dd>{d.note}</dd>
              </div>
            ) : null}
          </dl>
          {error ? <p className="iset-callout warn">{error}</p> : null}
          {!supported ? (
            <Note>Online, which bills a payment cleared is worked out on the server; see Bills cleared in the list.</Note>
          ) : null}
          {d && isRefund ? (
            <>
              <div className="iset-subhead">Return refunded</div>
              <div className="iset-table-wrap plain">
                <table className="settings-table">
                  <thead>
                    <tr>
                      <th>Return No</th>
                      <th>Date</th>
                      <th>{kind === 'supplier' ? 'Purchase' : 'Bill'}</th>
                      <th className="num">Return total</th>
                      <th className="num">Refunded</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.refunded.map((x) => (
                      <tr key={x.return_id}>
                        <td>{x.return_no}</td>
                        <td>{x.return_date}</td>
                        <td>{shortNo(x.bill_no) || '—'}</td>
                        <td className="num">{money(x.refund)}</td>
                        <td className="num">{money(x.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
          {d && !isRefund ? (
            <>
              <div className="iset-subhead">
                {kind === 'supplier' ? 'Purchases' : 'Bills'} this payment cleared ({d.cleared.length})
              </div>
              <div className="iset-table-wrap plain">
                <table className="settings-table">
                  <thead>
                    <tr>
                      <th>{kind === 'supplier' ? 'Purchase / Bill No' : 'Bill No'}</th>
                      <th>Date</th>
                      <th className="num">Bill total</th>
                      <th className="num">Cleared</th>
                      <th>How</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.cleared.map((c, i) => (
                      <tr key={`${c.bill_id}-${i}`}>
                        <td>{shortNo(c.bill_no) || `#${c.bill_id}`}</td>
                        <td>{c.date}</td>
                        <td className="num">{money(c.bill_total)}</td>
                        <td className="num">{money(c.amount)}</td>
                        <td>{c.how === 'chosen' ? 'Chosen ✓' : 'Oldest first'}</td>
                      </tr>
                    ))}
                    {!d.cleared.length ? <EmptyRow cols={5}>It has not cleared any bill.</EmptyRow> : null}
                  </tbody>
                </table>
              </div>
              <div className="pl-left">
                {d.credit_left > 0.009
                  ? `₹${money(d.credit_left)} of it is left as credit.`
                  : 'Nothing of it is left as credit.'}
              </div>
            </>
          ) : null}
          <div className="iset-row">
            <button type="button" className="settings-action-btn iset-secondary" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export function PaymentPanel({
  kind,
  initial,
  paymentModes,
  onChange,
}: {
  kind: 'supplier' | 'customer'
  initial: PaymentBundle
  paymentModes: string[]
  onChange: (p: PaymentBundle) => void
}) {
  const [data, setData] = useState(initial)
  const [party, setParty] = useState('')
  /** The row the shop clicked, when it clicked one. Only this tells two
   *  same-named parties apart -- the typed name cannot. */
  const [partyId, setPartyId] = useState<number | null>(null)
  const [amount, setAmount] = useState('')
  const [mode, setMode] = useState('Cash')
  const [cash, setCash] = useState('0')
  const [online, setOnline] = useState('0')
  const [date, setDate] = useState(todayLocal())
  const [reference, setReference] = useState('')
  // The paper receipt's number (both modes): saved on the payment, searchable below.
  const [receiptNo, setReceiptNo] = useState('')
  const [histSearch, setHistSearch] = useState('')
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [editHint, setEditHint] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  // "Which bills does this pay?" -- oldest first (as always) or the bills ticked here.
  const [billMode, setBillMode] = useState<'oldest' | 'choose'>('oldest')
  const [openBills, setOpenBills] = useState<OpenBill[]>([])
  const [billsSupported, setBillsSupported] = useState(true)
  const [billsErr, setBillsErr] = useState('')
  /** bill id -> amount typed for it; a ticked bill is a key here. */
  const [ticks, setTicks] = useState<Record<number, string>>({})
  /** The amount follows the ticked total until the shop types it. */
  const [amountTyped, setAmountTyped] = useState(false)
  // "Refund a return" (core/return_refunds.py): money going out for a return's open credit.
  const [payKind, setPayKind] = useState<'payment' | 'refund'>('payment')
  const [refundRows, setRefundRows] = useState<RefundableReturn[]>([])
  const [refundSupported, setRefundSupported] = useState(true)
  const [refundErr, setRefundErr] = useState('')
  /** return id -> amount typed for it; a ticked return is a key here. */
  const [refundTicks, setRefundTicks] = useState<Record<number, string>>({})
  const [refundMode, setRefundMode] = useState<'cash' | 'online'>('cash')
  /** The return of the refund being edited (an edit pays back that one return). */
  const [editingReturn, setEditingReturn] = useState<number | null>(null)
  const [viewRow, setViewRow] = useState<Record<string, unknown> | null>(null)
  const today = todayLocal()
  const [repFrom, setRepFrom] = useState(today.slice(0, 8) + '01')
  const [repTo, setRepTo] = useState(today)
  const [repParty, setRepParty] = useState<'all' | 'this'>('all')
  const [repBusy, setRepBusy] = useState(false)

  useEffect(() => {
    setData(initial)
  }, [initial])

  // Built once per party list, not once per keystroke: this is 1778 rows on the
  // owner's store, and ModernCombo keys its own work off the array it is handed.
  const partyItems = useMemo(
    () =>
      data.parties.map((p) => ({
        id: String(p.id),
        label: p.name,
        meta: `due ₹${money(p.due)}`,
      })),
    [data.parties],
  )

  const selectedParty = useMemo(
    () => resolveParty(data.parties, party, partyId),
    [data.parties, party, partyId],
  )
  const selectedDue = selectedParty?.due ?? 0
  const selectedPartyId = selectedParty?.id ?? null

  // The party's open bills, read when "Choose bills" is on (owes = without the payment
  // being edited, so its own bills show what they owed before it).
  useEffect(() => {
    if (billMode !== 'choose' || !party.trim()) {
      setOpenBills([])
      return
    }
    let alive = true
    setBillsErr('')
    fetchOpenBills({ kind, party, partyId: selectedPartyId, paymentId: editingId })
      .then((d) => {
        if (!alive) return
        setBillsSupported(d.supported !== false)
        const bills = Array.isArray(d.bills) ? d.bills : []
        setOpenBills(bills)
        const open = new Set(bills.map((b) => b.bill_id))
        setTicks((t) => {
          const kept: Record<number, string> = {}
          for (const [id, v] of Object.entries(t)) if (open.has(Number(id))) kept[Number(id)] = v
          return kept
        })
      })
      .catch((e) => alive && setBillsErr(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
  }, [billMode, kind, party, selectedPartyId, editingId])

  // The party's returns with credit still open, read when "Refund a return" is on (the
  // refund being edited left out, so its return shows what it had before it).
  useEffect(() => {
    if (payKind !== 'refund' || !party.trim()) {
      setRefundRows([])
      return
    }
    let alive = true
    setRefundErr('')
    fetchRefundReturns({ kind, party, partyId: selectedPartyId, paymentId: editingId })
      .then((d) => {
        if (!alive) return
        setRefundSupported(d.supported !== false)
        const rows = Array.isArray(d.returns) ? d.returns : []
        setRefundRows(rows)
        const open = new Set(rows.map((r) => r.return_id))
        setRefundTicks((t) => {
          const kept: Record<number, string> = {}
          for (const [id, v] of Object.entries(t)) if (open.has(Number(id))) kept[Number(id)] = v
          return kept
        })
      })
      .catch((e) => alive && setRefundErr(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
  }, [payKind, kind, party, selectedPartyId, editingId])

  const refundShown = editingReturn != null ? refundRows.filter((r) => r.return_id === editingReturn) : refundRows
  const refundOpenTotal = refundRows.reduce((s, r) => s + (Number(r.left) || 0), 0)
  const refundTotal = Object.values(refundTicks).reduce((s, v) => s + (Number(v) || 0), 0)

  function tickReturn(r: RefundableReturn, on: boolean) {
    const next = { ...refundTicks }
    if (on) next[r.return_id] = r.left.toFixed(2)
    else delete next[r.return_id]
    setRefundTicks(next)
  }

  function setRefundAmount(r: RefundableReturn, raw: string) {
    // never more than the return's open credit
    const v = Number(raw) > r.left ? r.left.toFixed(2) : raw
    setRefundTicks({ ...refundTicks, [r.return_id]: v })
  }

  function switchKind(next: 'payment' | 'refund') {
    if (next === payKind) return
    // a payment being edited is not a refund, nor the other way round
    clearForm()
    setPayKind(next)
    setErr('')
    setMsg('')
  }

  const tickedTotal = Object.values(ticks).reduce((s, v) => s + (Number(v) || 0), 0)
  const payAmount = kind === 'supplier' ? Number(amount) || 0 : (Number(cash) || 0) + (Number(online) || 0)
  const choosing = billMode === 'choose' && billsSupported

  /** Put the ticked total in the amount box, unless the shop typed the amount. */
  function followTicked(next: Record<number, string>) {
    setTicks(next)
    if (amountTyped) return
    const total = Object.values(next).reduce((s, v) => s + (Number(v) || 0), 0)
    const shown = total > 0 ? total.toFixed(2) : ''
    if (kind === 'supplier') {
      setAmount(shown)
    } else {
      setCash(shown || '0')
      setOnline('0')
    }
  }

  function tickBill(b: OpenBill, on: boolean) {
    const next = { ...ticks }
    if (on) next[b.bill_id] = b.owes.toFixed(2)
    else delete next[b.bill_id]
    followTicked(next)
  }

  function setTickAmount(b: OpenBill, raw: string) {
    // never more than the bill owes
    const v = Number(raw) > b.owes ? b.owes.toFixed(2) : raw
    followTicked({ ...ticks, [b.bill_id]: v })
  }

  function chosenAllocations(): PaymentAllocation[] {
    if (!choosing) return []
    return openBills
      .filter((b) => ticks[b.bill_id] != null && Number(ticks[b.bill_id]) > 0)
      .map((b) => ({ bill_id: b.bill_id, bill_no: b.bill_no, amount: Number(Number(ticks[b.bill_id]).toFixed(2)) }))
  }

  function clearForm() {
    setAmount('')
    setCash('0')
    setOnline('0')
    setReference('')
    setReceiptNo('')
    setNote('')
    setEditingId(null)
    setEditHint('')
    setBillMode('oldest')
    setTicks({})
    setAmountTyped(false)
    setRefundTicks({})
    setEditingReturn(null)
  }

  /** "Refund a return" saved: one negative payment per ticked return. Null when saved. */
  async function saveRefundNow(): Promise<string | null> {
    const refunds = refundShown
      .filter((r) => refundTicks[r.return_id] != null && Number(refundTicks[r.return_id]) > 0)
      .map((r) => ({ return_id: r.return_id, amount: Number(Number(refundTicks[r.return_id]).toFixed(2)) }))
    if (!party.trim()) {
      const why = kind === 'supplier' ? 'Pick the supplier first.' : 'Pick the customer first.'
      setErr(why)
      return why
    }
    if (!refunds.length) {
      const why = 'Tick a return and type the amount to refund.'
      setErr(why)
      return why
    }
    try {
      const next = await saveRefund({
        kind,
        party,
        party_id: selectedPartyId,
        date,
        mode: refundMode,
        note,
        refunds,
        ...(editingId ? { id: editingId } : {}),
      })
      const merged = {
        ...next,
        history: Array.isArray(next.history) && next.history.length ? next.history : data.history,
        parties: Array.isArray(next.parties) && next.parties.length ? next.parties : data.parties,
      }
      setData(merged)
      onChange(merged)
      dispatchPaymentsChanged(kind)
      const total = refunds.reduce((s, r) => s + r.amount, 0)
      setMsg(
        `${editingId ? 'Refund updated' : 'Refund saved'}: ₹${money(total)} ${
          kind === 'supplier' ? 'paid back by the supplier' : 'paid to the customer'
        }.`,
      )
      clearForm()
      return null
    } catch (e) {
      const why = e instanceof Error ? e.message : String(e)
      setErr(why)
      return why
    }
  }

  async function exportReport(format: 'csv' | 'pdf') {
    setErr('')
    setMsg('')
    if (repParty === 'this' && !party.trim()) {
      setErr(kind === 'supplier' ? 'Pick the supplier first.' : 'Pick the customer first.')
      return
    }
    setRepBusy(true)
    try {
      const res = await exportPaymentReport({
        kind,
        from: repFrom,
        to: repTo,
        party: repParty === 'this' ? party : '',
        format,
      })
      if (res.content_base64 && res.filename) {
        await saveFileAndShow(res.filename, res.mime || 'application/octet-stream', res.content_base64)
        setMsg(`Saved ${res.filename}`)
      } else {
        setErr(res.error || 'Report not saved.')
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRepBusy(false)
    }
  }

  /** Null when saved, else why not (voice shows it). */
  async function save(): Promise<string | null> {
    setErr('')
    setMsg('')
    if (payKind === 'refund') return saveRefundNow()
    const allocations = chosenAllocations()
    const ticked = allocations.reduce((s, a) => s + a.amount, 0)
    if (choosing && ticked > payAmount + 0.009) {
      const why = `The bills you ticked need ₹${money(ticked)}; the amount is ₹${money(payAmount)}.`
      setErr(why)
      return why
    }
    try {
      // Always sent ([] = oldest bills first), so an edit back to "Oldest bills first"
      // drops the saved choice. Online the engine ignores it (the server clears oldest first).
      const body =
        kind === 'supplier'
          ? {
              kind,
              party,
              amount: Number(amount),
              mode,
              date,
              reference,
              receipt_no: receiptNo.trim(),
              allocations,
              ...(editingId ? { id: editingId } : {}),
            }
          : {
              kind,
              party,
              cash: Number(cash),
              online: Number(online),
              date,
              reference,
              receipt_no: receiptNo.trim(),
              note,
              allocations,
              ...(editingId ? { id: editingId } : {}),
            }
      const next = await savePayment(body)
      const merged = {
        ...next,
        history:
          Array.isArray(next.history) && next.history.length
            ? next.history
            : data.history,
        parties:
          Array.isArray(next.parties) && next.parties.length
            ? next.parties
            : data.parties,
      }
      setData(merged)
      onChange(merged)
      dispatchPaymentsChanged(kind)
      setMsg(
        (editingId ? 'Payment updated.' : 'Payment saved.') +
          // Saved regardless; only pointed out.
          (next.warnings?.length ? ` Please check: ${next.warnings.join(' ')}` : ''),
      )
      clearForm()
      return null
    } catch (e) {
      const why = e instanceof Error ? e.message : String(e)
      setErr(why)
      return why
    }
  }

  usePageHotkeys({ onSave: () => void save() })

  // ── Voice (test build): "Ramesh Patil 500 cash", "grahak Ramesh", "save kar" ──
  // Fills the same boxes the shop fills by hand. Nothing is saved until "save kar",
  // and the voice bar asks "Yes / No" before that reaches here.
  const voiceHandlerRef = useRef<VoiceHandler>(async () => null)
  voiceHandlerRef.current = async (cmd) => {
    const a = cmd.args || {}
    const who = kind === 'supplier' ? 'Supplier' : 'Grahak'
    if (cmd.intent === 'save_bill') {
      if (!party.trim()) return { ok: false, say: `Payment save zala nahi — ${who} nivdala nahi` }
      const why = await save()
      return why ? { ok: false, say: `Payment save zala nahi — ${why}` } : { ok: true, say: `Payment save zala ✓ — ${party}` }
    }
    if (cmd.intent !== 'payment' && cmd.intent !== 'set_customer') return null
    if (a.kind && a.kind !== kind) {
      return { ok: false, say: `He ${who} payment aahe — "${a.kind} payment ughad" mhana` }
    }
    // The party said, else the one already in the box.
    const name = String(a.name ?? '').trim()
    const want = name.toLowerCase()
    const lower = (s: string) => s.toLowerCase()
    const p = want
      ? data.parties.find((x) => lower(x.name) === want) ||
        data.parties.find((x) => lower(x.name).startsWith(want)) ||
        data.parties.find((x) => lower(x.name).includes(want))
      : selectedParty
    if (!p) return { ok: false, say: name ? `"${name}": ${who} yaadit nahi` : `${who}che naav sanga` }
    setParty(p.name)
    setPartyId(p.id)
    const amt = Number(a.amount)
    const hasAmt = a.amount != null && a.amount !== '' && Number.isFinite(amt) && amt > 0
    // As a click on the supplier does: its due goes in when no amount was said.
    if (kind === 'supplier' && !hasAmt && p.due > 0) setAmount(p.due.toFixed(2))
    if (cmd.intent === 'set_customer' || !hasAmt) {
      return { ok: true, say: `${who}: ${p.name} — baki ₹${money(p.due)}${cmd.intent === 'payment' ? ' · kiti rakkam? "500 cash" mhana' : ''}` }
    }
    const online = String(a.mode || '').toLowerCase() === 'online'
    let modeWord = online ? 'online' : 'cash'
    if (kind === 'supplier') {
      setAmount(String(amt))
      if (a.mode) {
        const pick = paymentModes.find((m) => (online ? /online|upi/i : /cash/i).test(m))
        if (pick) setMode(pick)
      } else {
        modeWord = mode
      }
    } else {
      setCash(online ? '0' : String(amt))
      setOnline(online ? String(amt) : '0')
    }
    return { ok: true, say: `${p.name}: ₹${amt} ${modeWord} — 'save kar' mhana` }
  }
  useEffect(() => registerVoicePage('payment', () => voiceHandlerRef.current), [])

  async function remove(row: Record<string, unknown>) {
    setErr('')
    try {
      const next = await deletePayment({
        kind,
        id: row.id,
        payment_no: row.payment_no,
      })
      const merged = {
        ...next,
        history:
          Array.isArray(next.history) && next.history.length
            ? next.history
            : data.history.filter((h) => Number(h.id) !== Number(row.id)),
        parties:
          Array.isArray(next.parties) && next.parties.length
            ? next.parties
            : data.parties,
      }
      setData(merged)
      onChange(merged)
      dispatchPaymentsChanged(kind)
      setMsg(refundOf(row) ? 'Refund deleted; the return has its credit back.' : 'Payment deleted.')
      if (editingId != null && Number(row.id) === editingId) clearForm()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  function loadHistoryRow(r: Record<string, unknown>) {
    const id = Number(r.id || 0)
    const ref = refundOf(r)
    if (ref && id > 0) {
      // A refund opens in "Refund a return" with its one return ticked.
      clearForm()
      setPayKind('refund')
      setParty(String(r.party ?? ''))
      setPartyId(null)
      setDate(String(r.date ?? todayLocal()))
      setRefundMode(/online/i.test(String(r.mode ?? '')) ? 'online' : 'cash')
      // customer refunds keep "Refund SR1-3 · note"; only the shop's own words go back in the box
      const n = String(r.note ?? '')
      const lead = `Refund ${ref.return_no}`
      setNote(n.startsWith(lead) ? n.slice(lead.length).replace(/^\s*·\s*/, '') : n)
      setRefundTicks({ [ref.return_id]: Math.abs(Number(r.amount || 0)).toFixed(2) })
      setEditingReturn(ref.return_id)
      setEditingId(id)
      setEditHint(`Editing refund #${id} (${ref.return_no}). Save updates this row.`)
      return
    }
    if (payKind === 'refund') {
      setRefundTicks({})
      setEditingReturn(null)
      setPayKind('payment')
    }
    setParty(String(r.party ?? ''))
    // A history row keeps the party's NAME, never its id, so there is no click
    // to honour here -- let the name resolve on its own.
    setPartyId(null)
    setDate(String(r.date ?? todayLocal()))
    setReference(String(r.reference ?? ''))
    setReceiptNo(String(r.receipt_no ?? ''))
    if (kind === 'supplier') {
      setAmount(String(r.amount ?? ''))
      setMode(String(r.mode || 'Cash'))
    } else {
      setCash(String(r.cash ?? 0))
      setOnline(String(r.online ?? 0))
      setNote(String(r.note ?? ''))
    }
    // The saved choice of bills, as it was saved; the amount stays as saved too.
    const saved = Array.isArray(r.allocations) ? (r.allocations as PaymentAllocation[]) : []
    const t: Record<number, string> = {}
    for (const a of saved) if (Number(a.amount) > 0) t[Number(a.bill_id)] = Number(a.amount).toFixed(2)
    setTicks(t)
    setBillMode(saved.length ? 'choose' : 'oldest')
    setAmountTyped(true)
    if (id > 0) {
      setEditingId(id)
      setEditHint(`Editing payment #${id}. Save updates this row.`)
    } else {
      setEditingId(null)
      setEditHint('')
    }
  }

  const histCols = kind === 'customer' ? 11 : 9

  return (
    <>
      <PanelTitle>
        {kind === 'supplier' ? 'Supplier Payment' : 'Customer Payment'}
      </PanelTitle>
      <div className="iset-form-frame">
        <Frame
          title={
            payKind === 'refund'
              ? editingId
                ? `Edit refund #${editingId}`
                : 'Refund a return'
              : editingId
                ? `Edit payment #${editingId}`
                : 'New payment'
          }
        >
          <Note>F5 / Ctrl+G saves payment (button below)</Note>
          <div>
            <div className="pl-kind" role="radiogroup" aria-label="Payment kind">
              <button
                type="button"
                role="radio"
                aria-checked={payKind === 'payment'}
                className={payKind === 'payment' ? 'on' : undefined}
                onClick={() => switchKind('payment')}
              >
                Payment
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={payKind === 'refund'}
                className={payKind === 'refund' ? 'on' : undefined}
                onClick={() => switchKind('refund')}
              >
                Refund a return
              </button>
            </div>
            {payKind === 'refund' ? (
              <span className="pl-kind-note">
                {kind === 'supplier'
                  ? 'The supplier pays us back for a return.'
                  : 'We pay the customer back for a return.'}
              </span>
            ) : null}
          </div>
          {editHint ? <p className="iset-callout">{editHint}</p> : null}
          <div className="iset-pay-top">
            <BarField label={kind === 'supplier' ? 'Supplier' : 'Customer'}>
              <ModernCombo
                className="settings-input"
                value={party}
                placeholder={
                  kind === 'supplier' ? 'Search supplier…' : 'Search customer…'
                }
                minChars={0}
                filterLocal
                maxVisible={30}
                listLabel={kind === 'supplier' ? 'Suppliers' : 'Customers'}
                emptyText="No matching party"
                items={partyItems}
                voiceField={kind}
                onChange={(v) => {
                  setParty(v)
                  // Typing has moved off whatever was clicked, so the id that told two
                  // same-named parties apart no longer describes this text.
                  setPartyId(null)
                  setTicks({})
                  setRefundTicks({})
                }}
                onPick={(it) => {
                  setParty(it.label)
                  const id = Number(it.id)
                  setPartyId(id)
                  // another party's bills are not this one's
                  setTicks({})
                  setRefundTicks({})
                  setAmountTyped(false)
                  const p = data.parties.find((x) => x.id === id)
                  if (p && kind === 'supplier' && p.due > 0 && payKind === 'payment') {
                    setAmount(p.due.toFixed(2))
                  }
                }}
              />
            </BarField>
            <div className={`iset-pay-due${selectedDue > 0.009 ? ' owes' : ''}`}>
              <span>Outstanding due</span>
              <strong>₹{money(selectedDue)}</strong>
            </div>
            {payKind === 'refund' ? (
              party.trim() ? (
                <div className="iset-pay-due pl-credit-box">
                  <span>Return credit open</span>
                  <strong>₹{money(refundOpenTotal)}</strong>
                </div>
              ) : (
                <span className="iset-hint">
                  Pick the {kind === 'supplier' ? 'supplier' : 'customer'} to see its returns.
                </span>
              )
            ) : party.trim() ? (
              <BarField label="Which bills does this pay?">
                <select
                  className="settings-input"
                  value={billMode}
                  onChange={(e) => setBillMode(e.target.value === 'choose' ? 'choose' : 'oldest')}
                >
                  <option value="oldest">Oldest bills first</option>
                  <option value="choose">Choose bills</option>
                </select>
              </BarField>
            ) : (
              <span className="iset-hint">
                Pick the {kind === 'supplier' ? 'supplier' : 'customer'} to see what is owed.
              </span>
            )}
          </div>
          {payKind === 'refund' && party.trim() ? (
            !refundSupported ? (
              <Note>Online, refund a return from the Returns page; refunds here work in Offline / offline-first.</Note>
            ) : (
              <>
                {refundErr ? <p className="iset-callout warn">{refundErr}</p> : null}
                <div className="iset-table-wrap">
                  <table className="settings-table">
                    <thead>
                      <tr>
                        <th style={{ width: 28 }} />
                        <th>Return No</th>
                        <th>Date</th>
                        <th>{kind === 'supplier' ? 'Purchase' : 'Bill'}</th>
                        <th>Medicines</th>
                        <th className="num">Credit left</th>
                        <th className="num">Refund</th>
                      </tr>
                    </thead>
                    <tbody>
                      {refundShown.map((r) => {
                        const on = refundTicks[r.return_id] != null
                        return (
                          <tr key={r.return_id} className={on ? 'row-selected' : undefined}>
                            <td>
                              <input
                                type="checkbox"
                                checked={on}
                                aria-label={`Refund ${r.return_no}`}
                                onChange={(e) => tickReturn(r, e.target.checked)}
                              />
                            </td>
                            <td>{r.return_no}</td>
                            <td>{r.return_date}</td>
                            <td>{shortNo(r.bill_no) || '—'}</td>
                            <td className="wrap">
                              <span className="pl-items">{returnItemsText(r.items) || '—'}</span>
                            </td>
                            <td className="num">{money(r.left)}</td>
                            <td className="num">
                              <input
                                className="settings-input iset-cell-input"
                                type="number"
                                min="0"
                                step="0.01"
                                disabled={!on}
                                value={on ? refundTicks[r.return_id] : ''}
                                onChange={(e) => setRefundAmount(r, e.target.value)}
                              />
                            </td>
                          </tr>
                        )
                      })}
                      {!refundShown.length ? (
                        <EmptyRow cols={7}>
                          {editingReturn != null
                            ? 'This refund was made with the return itself (settled in cash / online); change it on the Returns page, or delete it here.'
                            : 'No return of this party has credit left to refund.'}
                        </EmptyRow>
                      ) : null}
                    </tbody>
                  </table>
                </div>
                {refundTotal > 0.009 ? (
                  <Note>
                    Refund ₹{money(refundTotal)} {kind === 'supplier' ? 'comes back from the supplier' : 'goes to the customer'}
                  </Note>
                ) : null}
              </>
            )
          ) : null}
          {payKind === 'payment' && billMode === 'choose' && party.trim() ? (
            !billsSupported ? (
              <Note>Online, a payment clears the oldest bills first; bills can be chosen in Offline / offline-first.</Note>
            ) : (
              <>
                {billsErr ? <p className="iset-callout warn">{billsErr}</p> : null}
                <div className="iset-table-wrap">
                  <table className="settings-table">
                    <thead>
                      <tr>
                        <th style={{ width: 28 }} />
                        <th>{kind === 'supplier' ? 'Purchase / Bill No' : 'Bill No'}</th>
                        <th>Date</th>
                        <th className="num">Bill total</th>
                        <th className="num">Owes now</th>
                        <th className="num">Pay on it</th>
                      </tr>
                    </thead>
                    <tbody>
                      {openBills.map((b) => {
                        const on = ticks[b.bill_id] != null
                        return (
                          <tr key={b.bill_id} className={on ? 'row-selected' : undefined}>
                            <td>
                              <input
                                type="checkbox"
                                checked={on}
                                aria-label={`Pay ${shortNo(b.bill_no)}`}
                                onChange={(e) => tickBill(b, e.target.checked)}
                              />
                            </td>
                            <td>{shortNo(b.bill_no) || `#${b.bill_id}`}</td>
                            <td>{b.date}</td>
                            <td className="num">{money(b.total)}</td>
                            <td className="num">{money(b.owes)}</td>
                            <td className="num">
                              <input
                                className="settings-input iset-cell-input"
                                type="number"
                                min="0"
                                step="0.01"
                                disabled={!on}
                                value={on ? ticks[b.bill_id] : ''}
                                onChange={(e) => setTickAmount(b, e.target.value)}
                              />
                            </td>
                          </tr>
                        )
                      })}
                      {!openBills.length ? (
                        <EmptyRow cols={6}>No bill of this party owes anything.</EmptyRow>
                      ) : null}
                    </tbody>
                  </table>
                </div>
                {tickedTotal > 0.009 && payAmount > tickedTotal + 0.009 ? (
                  <Note>
                    Ticked ₹{money(tickedTotal)} · ₹{money(payAmount - tickedTotal)} more goes to the oldest bills
                  </Note>
                ) : null}
                {tickedTotal > 0.009 && payAmount < tickedTotal - 0.009 ? (
                  <p className="iset-callout warn">
                    The bills you ticked need ₹{money(tickedTotal)}; the amount is ₹{money(payAmount)}.
                  </p>
                ) : null}
              </>
            )
          ) : null}
          {payKind === 'refund' ? (
            <div className="iset-pay-fields">
              <BarField label="Mode">
                <select
                  className="settings-input"
                  value={refundMode}
                  onChange={(e) => setRefundMode(e.target.value === 'online' ? 'online' : 'cash')}
                >
                  <option value="cash">Cash</option>
                  <option value="online">Online</option>
                </select>
              </BarField>
              <BarField label="Date">
                <input
                  className="settings-input"
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                />
              </BarField>
              {kind === 'customer' ? (
                <BarField label="Note">
                  <input
                    className="settings-input"
                    value={note}
                    placeholder="Saved after “Refund SR…”"
                    onChange={(e) => setNote(e.target.value)}
                  />
                </BarField>
              ) : null}
            </div>
          ) : null}
          <div
            className="iset-pay-fields"
            style={payKind === 'refund' ? { display: 'none' } : undefined}
          >
            {kind === 'supplier' ? (
              <>
                <BarField label="Amount" className="amount">
                  <input
                    className="settings-input"
                    inputMode="decimal"
                    value={amount}
                    onChange={(e) => {
                      setAmount(e.target.value)
                      setAmountTyped(true)
                    }}
                  />
                </BarField>
                <BarField label="Mode">
                  <select
                    className="settings-input"
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                  >
                    {paymentModes.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </BarField>
              </>
            ) : (
              <>
                <BarField label="Cash" className="amount">
                  <input
                    className="settings-input"
                    inputMode="decimal"
                    value={cash}
                    onChange={(e) => {
                      setCash(e.target.value)
                      setAmountTyped(true)
                    }}
                  />
                </BarField>
                <BarField label="Online" className="amount">
                  <input
                    className="settings-input"
                    inputMode="decimal"
                    value={online}
                    onChange={(e) => {
                      setOnline(e.target.value)
                      setAmountTyped(true)
                    }}
                  />
                </BarField>
              </>
            )}
            <BarField label="Date">
              <input
                className="settings-input"
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </BarField>
            <BarField label="Reference">
              <input
                className="settings-input"
                value={reference}
                onChange={(e) => setReference(e.target.value)}
              />
            </BarField>
            {payKind !== 'refund' ? (
              <BarField label="Receipt no.">
                <input
                  className="settings-input"
                  value={receiptNo}
                  maxLength={40}
                  placeholder="optional"
                  title="The number on the paper receipt given or taken with this payment"
                  onChange={(e) => setReceiptNo(e.target.value)}
                />
              </BarField>
            ) : null}
            {kind === 'customer' ? (
              <BarField label="Note">
                <input
                  className="settings-input"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </BarField>
            ) : null}
          </div>
          <div className="settings-savebar">
            <SaveBtn
              label={
                payKind === 'refund'
                  ? editingId
                    ? 'Update Refund'
                    : 'Save Refund'
                  : editingId
                    ? 'Update Payment'
                    : 'Save Payment'
              }
              onClick={save}
            />
            {editingId ? (
              <button
                type="button"
                className="settings-action-btn iset-secondary"
                onClick={clearForm}
              >
                Cancel edit
              </button>
            ) : null}
            {err ? (
              <span className="settings-savebar-msg err">{err}</span>
            ) : msg ? (
              <span className="settings-savebar-msg ok">{msg}</span>
            ) : null}
          </div>
        </Frame>
      </div>
      <Frame title={`Payment history (${data.history.length})`}>
        <div className="iset-bar">
          <input
            className="settings-input"
            style={{ maxWidth: 320 }}
            value={histSearch}
            placeholder="Search receipt no., party, reference…"
            onChange={(e) => setHistSearch(e.target.value)}
          />
        </div>
        <div className="iset-table-wrap capped">
          <table className="settings-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Party</th>
                <th className="num">Amount</th>
                {kind === 'customer' ? (
                  <>
                    <th className="num">Cash</th>
                    <th className="num">Online</th>
                  </>
                ) : null}
                <th>Mode</th>
                <th>Ref</th>
                <th>Receipt</th>
                <th className="num">Due Before</th>
                <th className="num">Due After</th>
                <th title="✓ = chosen for this payment; the rest went to the oldest bills">Bills cleared</th>
                <th className="act">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.history.filter((r) => historyMatches(r, histSearch)).map((r) => (
                <tr
                  key={String(r.id)}
                  className={
                    editingId != null && Number(r.id) === editingId
                      ? 'row-selected'
                      : undefined
                  }
                >
                  <td>{String(r.date ?? '')}</td>
                  <td>
                    {refundOf(r) ? (
                      <span className="pl-party-cell">
                        {String(r.party ?? '')}
                        <span
                          className="iset-badge pl-refund"
                          title={kind === 'supplier' ? 'The supplier paid us back for this return' : 'We paid the customer back for this return'}
                        >
                          Refund {refundOf(r)?.return_no}
                        </span>
                      </span>
                    ) : (
                      String(r.party ?? '')
                    )}
                  </td>
                  <td className={`num${Number(r.amount || 0) < 0 ? ' pl-out' : ''}`}>{money(r.amount)}</td>
                  {kind === 'customer' ? (
                    <>
                      <td className="num">{money(r.cash)}</td>
                      <td className="num">{money(r.online)}</td>
                    </>
                  ) : null}
                  <td>{String(r.mode ?? '')}</td>
                  <td>{String(r.reference ?? '')}</td>
                  <td>{String(r.receipt_no ?? '')}</td>
                  <td className="num">
                    {r.due_before != null ? money(r.due_before) : '—'}
                  </td>
                  <td className="num">{r.due_after != null ? money(r.due_after) : '—'}</td>
                  <td className="wrap" style={{ minWidth: 160 }}>{clearedText(r)}</td>
                  <td className="act">
                    <button
                      type="button"
                      className="iset-btn"
                      onClick={() => setViewRow(r)}
                    >
                      View
                    </button>
                    <button
                      type="button"
                      className="iset-btn"
                      onClick={() => loadHistoryRow(r)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="iset-btn danger"
                      onClick={() => remove(r)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
              {!data.history.length ? (
                <EmptyRow cols={histCols}>
                  No {kind === 'supplier' ? 'supplier' : 'customer'} payments yet.
                  Saved payments show here.
                </EmptyRow>
              ) : null}
            </tbody>
          </table>
        </div>
      </Frame>
      {viewRow ? <PaymentDetailsDialog kind={kind} row={viewRow} onClose={() => setViewRow(null)} /> : null}
      <Frame title="Payment report — which bills each payment cleared">
        <div className="iset-bar" style={{ margin: 0 }}>
          <BarField label="From" className="date">
            <input className="settings-input" type="date" value={repFrom} onChange={(e) => setRepFrom(e.target.value)} />
          </BarField>
          <BarField label="To" className="date">
            <input className="settings-input" type="date" value={repTo} onChange={(e) => setRepTo(e.target.value)} />
          </BarField>
          <BarField label={kind === 'supplier' ? 'Suppliers' : 'Customers'} className="grow">
            <select
              className="settings-input"
              value={repParty}
              onChange={(e) => setRepParty(e.target.value === 'this' ? 'this' : 'all')}
            >
              <option value="all">All</option>
              <option value="this">Only {party.trim() || (kind === 'supplier' ? 'the supplier above' : 'the customer above')}</option>
            </select>
          </BarField>
          <div className="iset-bar-actions">
            <button
              type="button"
              className="settings-action-btn iset-secondary"
              disabled={repBusy}
              onClick={() => void exportReport('csv')}
            >
              Export CSV
            </button>
            <button
              type="button"
              className="settings-action-btn iset-secondary"
              disabled={repBusy}
              onClick={() => void exportReport('pdf')}
            >
              Save PDF
            </button>
          </div>
        </div>
      </Frame>
    </>
  )
}

/* ─── Ledger ───────────────────────────────────────────────────────────── */

export function LedgerPanel({
  kind,
  refreshNonce = 0,
}: {
  kind: 'supplier' | 'customer'
  refreshNonce?: number
}) {
  const [party, setParty] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState(todayLocal())
  const [data, setData] = useState<LedgerBundle | null>(null)
  const [err, setErr] = useState('')
  const ledgerTableRef = useRef<HTMLDivElement>(null)

  async function load(nextParty = party, range?: { from: string; to: string }) {
    setErr('')
    try {
      const res = await fetchLedger({
        kind,
        party: nextParty,
        from: range ? range.from : from,
        to: range ? range.to : to,
      })
      setData(res)
      if (!from) setFrom(res.from)
      if (!to) setTo(res.to)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    void load('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind])

  useEffect(() => {
    if (!refreshNonce) return
    void load(party)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshNonce])

  usePageHotkeys({
    onF2: () => focusTableSection(ledgerTableRef, { preferInput: false }),
    onApplyFilter: () => void load(party),
  })

  // ── Voice (test build): "ledger aajche", "ledger ha mahina", "ledger reset" ──
  // The same From / To the date boxes hold, then the same statement read.
  const voiceHandlerRef = useRef<VoiceHandler>(async () => null)
  voiceHandlerRef.current = async (cmd) => {
    const a = cmd.args || {}
    const who = kind === 'supplier' ? 'Supplier ledger' : 'Customer ledger'
    if (cmd.intent === 'page_filter' && a.filter === 'range') {
      const r = presetRange(a.value)
      if (!r) return { ok: false, say: `"${a.value}" he divas samajle nahi` }
      // 'all' is the Reset button's range: engine default From, To = today.
      const next = r.key === 'all' ? { from: '', to: todayLocal() } : r
      setFrom(next.from)
      setTo(next.to)
      await load(party, next)
      return { ok: true, say: `${who}: ${RANGE_WORDS[r.key]}` }
    }
    if (cmd.intent === 'page_filter' && a.filter === 'clear') {
      setParty('')
      setFrom('')
      setTo(todayLocal())
      setData(null)
      await load('', { from: '', to: todayLocal() })
      return { ok: true, say: `${who}: reset kela` }
    }
    if (cmd.intent === 'page_action' && a.action === 'refresh') {
      await load(party)
      return { ok: true, say: `${who}: statement parat vachla` }
    }
    if (cmd.intent === 'search' || (cmd.intent === 'page_filter' && a.filter === 'party')) {
      const name = String(a.query || a.value || '').trim()
      if (!name) return { ok: false, say: 'Konacha ledger? naav sanga' }
      setParty(name)
      await load(name)
      return { ok: true, say: `${who}: ${name}` }
    }
    if (cmd.intent === 'page_filter' || cmd.intent === 'page_action') {
      return { ok: false, say: `${who} var he kaam voice var nahi` }
    }
    return null
  }
  useEffect(() => registerVoicePage('ledger', () => voiceHandlerRef.current), [])

  const onFilterEnter = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      void load(party)
    }
  }

  // get_ledger lists every customers.name with no DISTINCT and this shop has
  // same-named rows, so the raw list repeats them. The statement is fetched by
  // name and collapses those rows anyway, which makes a second identical entry
  // pure noise here -- and, as a React key, a duplicate.
  const partyItems = useMemo(() => {
    const seen = new Set<string>()
    const out: { id: string; label: string }[] = []
    for (const name of data?.parties || []) {
      const key = name.trim().toLowerCase()
      if (!key || seen.has(key)) continue
      seen.add(key)
      out.push({ id: name, label: name })
    }
    return out
  }, [data?.parties])

  function exportCsv() {
    if (!data) return
    downloadCsv(
      `ledger-${kind}.csv`,
      ['Date', 'Particulars', 'Debit', 'Credit', 'Balance', 'Cleared / Paid by'],
      data.rows.map((r) => [
        r.date,
        r.particulars,
        money(r.debit),
        money(r.credit),
        money(r.balance),
        r.links || '',
      ]),
    )
  }

  return (
    <>
      <PanelTitle>
        {kind === 'supplier' ? 'Supplier Ledger' : 'Customer Ledger'}
      </PanelTitle>
      {err ? <p className="iset-callout warn">{err}</p> : null}
      <div className="iset-bar">
        <BarField label="Party" className="grow">
          <ModernCombo
            className="settings-input"
            value={party}
            placeholder={
              kind === 'supplier' ? 'Search supplier…' : 'Search customer…'
            }
            minChars={0}
            filterLocal
            maxVisible={30}
            listLabel={kind === 'supplier' ? 'Suppliers' : 'Customers'}
            emptyText="No matching party"
            items={partyItems}
            onChange={setParty}
            onPick={(it) => setParty(it.label)}
            // Enter still runs the statement, as it did on the <select>. The name
            // has to come from the pick rather than from `party`: the pick has not
            // reached state yet when this fires, so reading it would search for
            // whatever the field held one keystroke ago.
            onEnter={(picked) => void load(picked?.label ?? party)}
          />
        </BarField>
        <BarField label="From" className="date">
          <input
            className="settings-input"
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            onKeyDown={onFilterEnter}
          />
        </BarField>
        <BarField label="To" className="date">
          <input
            className="settings-input"
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            onKeyDown={onFilterEnter}
          />
        </BarField>
        <div className="iset-bar-actions">
          <SaveBtn label="View Statement" onClick={() => load(party)} />
          <button
            type="button"
            className="settings-action-btn iset-secondary"
            onClick={() => {
              setParty('')
              setFrom('')
              setTo(todayLocal())
              setData(null)
              setErr('')
              void load('')
            }}
          >
            Reset
          </button>
          <button
            type="button"
            className="settings-action-btn iset-secondary"
            onClick={exportCsv}
            disabled={!data?.rows?.length}
          >
            Export CSV
          </button>
        </div>
      </div>
      {data ? (
        <>
          {/* Optional chaining is not decoration here: an answer without a
              summary threw while React was rendering, which unmounts the whole
              tree -- nav bar included -- and leaves a white window that only a
              restart recovers. A ledger with no totals should show zeroes. */}
          <div className="iset-summary">
            <div className="iset-stat">
              <span>Opening</span>
              <strong>₹{money(data.summary?.opening)}</strong>
            </div>
            <div className="iset-stat">
              <span>Debits</span>
              <strong>₹{money(data.summary?.debits)}</strong>
            </div>
            <div className="iset-stat">
              <span>Credits</span>
              <strong>₹{money(data.summary?.credits)}</strong>
            </div>
            <div className={`iset-stat${Number(data.summary?.closing || 0) > 0.009 ? ' due' : ''}`}>
              <span>Closing</span>
              <strong>₹{money(data.summary?.closing)}</strong>
            </div>
            <div className="iset-stat">
              <span>Entries</span>
              <strong>{data.rows.length}</strong>
            </div>
          </div>
          <div
            ref={ledgerTableRef}
            className="iset-table-wrap capped"
            tabIndex={-1}
            data-ledger-focus="table"
          >
            <table className="settings-table">
              <thead>
                <tr>
                  <th style={{ width: 110 }}>Date</th>
                  <th>Particulars</th>
                  <th className="num">Debit</th>
                  <th className="num">Credit</th>
                  <th className="num">Balance</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.date}</td>
                    <td className="wrap">
                      {r.particulars}
                      {r.links ? (
                        <div className={`pl-ledger-links${r.links.startsWith('Paid by') ? ' paid-by' : ''}`}>
                          {r.links}
                        </div>
                      ) : null}
                    </td>
                    <td className="num">{money(r.debit)}</td>
                    <td className="num">{money(r.credit)}</td>
                    <td className="num">{money(r.balance)}</td>
                  </tr>
                ))}
                {data.rows.length ? (
                  <tr className="iset-total">
                    <td colSpan={2}>Summary</td>
                    <td className="num">{money(data.summary?.debits)}</td>
                    <td className="num">{money(data.summary?.credits)}</td>
                    <td className="num">{money(data.summary?.closing)}</td>
                  </tr>
                ) : (
                  <EmptyRow cols={5}>
                    {party.trim()
                      ? `No entries for ${party.trim()} in these dates.`
                      : 'No entries in these dates. Pick a party and press View Statement.'}
                  </EmptyRow>
                )}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </>
  )
}

/* ─── Reorder ──────────────────────────────────────────────────────────── */

type ReorderLine = {
  id?: number
  medicine_name: string
  pack_size: string
  quantity: number
  unit_price: number
  current_stock?: number
  min_stock?: number
}

function ReorderBySupplierEditor({
  reorder,
  busy,
  setBusy,
  setErr,
  setMsg,
  onChange,
  medicinePrefill,
}: {
  reorder: Reorder
  busy: boolean
  setBusy: (v: boolean) => void
  setErr: (v: string) => void
  setMsg: (v: string) => void
  onChange: (r: Reorder) => void
  medicinePrefill?: {
    medicine_name: string
    pack_size: string
    quantity: number
    unit_price: number
  }
}) {
  const [supplierId, setSupplierId] = useState('')
  const [medicines, setMedicines] = useState<string[]>([])
  const [pickMed, setPickMed] = useState('')
  const [pickPack, setPickPack] = useState('')
  const [pickQty, setPickQty] = useState('0')
  const [lines, setLines] = useState<ReorderLine[]>([])
  const [phone, setPhone] = useState('')
  const [notes, setNotes] = useState('')
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    if (!medicinePrefill?.medicine_name) return
    setLines((prev) => {
      if (prev.some((ln) => ln.medicine_name === medicinePrefill.medicine_name)) {
        return prev
      }
      return [...prev, { ...medicinePrefill }]
    })
    setMsg(`Prefilled ${medicinePrefill.medicine_name} from inventory.`)
  }, [medicinePrefill, setMsg])

  async function onSupplierPick(sid: string) {
    setSupplierId(sid)
    setLines([])
    setMedicines([])
    setErr('')
    if (!sid) return
    try {
      const res = await reorderAction({
        action: 'supplier_medicines',
        supplier_id: Number(sid),
      })
      setMedicines((res.medicines as string[]) || [])
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  async function addLine() {
    if (!supplierId) {
      setErr('Select a supplier first.')
      return
    }
    if (!pickMed.trim()) {
      setErr('Pick a medicine to add.')
      return
    }
    setBusy(true)
    setErr('')
    try {
      const res = await reorderAction({
        action: 'build_line',
        supplier_id: Number(supplierId),
        medicine_name: pickMed.trim(),
        pack_size: pickPack.trim(),
        quantity: Number(pickQty) || 0,
      })
      const line = res.line as ReorderLine | undefined
      if (!line?.medicine_name) {
        setErr('Could not build line.')
        return
      }
      setLines((prev) => [...prev, line])
      setPickMed('')
      setPickPack('')
      setPickQty('0')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function updateLine(idx: number, field: 'quantity' | 'unit_price', value: string) {
    setLines((prev) =>
      prev.map((ln, i) =>
        i === idx
          ? {
              ...ln,
              [field]: Number(value) || 0,
            }
          : ln,
      ),
    )
  }

  function removeLine(idx: number) {
    setLines((prev) => prev.filter((_, i) => i !== idx))
  }

  // ── Voice (test build): "Dolo 650 das" adds a line for the chosen supplier ──
  // The same engine call as the Add button; nothing is saved by voice.
  const voiceHandlerRef = useRef<VoiceHandler>(async () => null)
  voiceHandlerRef.current = async (cmd) => {
    if (cmd.intent !== 'add_medicine') return null
    const a = cmd.args || {}
    const said = String(a.medicine ?? '').trim()
    if (!supplierId) return { ok: false, say: `${said || 'Aushadh'}: aadhi supplier nivda (Load by Supplier)` }
    if (!said) return { ok: false, say: 'Konta aushadh? naav sanga' }
    const want = said.toLowerCase()
    // The supplier's own spelling when it has this medicine.
    const name =
      medicines.find((m) => m.toLowerCase() === want) ||
      medicines.find((m) => m.toLowerCase().startsWith(want)) ||
      medicines.find((m) => m.toLowerCase().includes(want)) ||
      said
    try {
      const res = await reorderAction({
        action: 'build_line',
        supplier_id: Number(supplierId),
        medicine_name: name,
        pack_size: '',
        quantity: Number(a.qty) || 0,
      })
      const line = res.line as ReorderLine | undefined
      if (!line?.medicine_name) return { ok: false, say: `${said}: ${String(res.error || 'line banli nahi')}` }
      setLines((prev) => [...prev, line])
      return { ok: true, say: `${line.medicine_name} × ${line.quantity} reorder yaadit jodla` }
    } catch (e) {
      return { ok: false, say: `${said}: ${e instanceof Error ? e.message : e}` }
    }
  }
  useEffect(() => registerVoicePage('reorder', () => voiceHandlerRef.current), [])

  async function saveSupplierOrder(status: 'draft' | 'ordered') {
    if (!lines.length) {
      setErr('Add at least one medicine line.')
      return
    }
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await reorderAction({
        action: 'save_supplier_draft',
        status,
        header: {
          supplier_id: supplierId ? Number(supplierId) : null,
          supplier_phone: phone,
          notes,
          order_offline: offline,
        },
        lines,
      })
      if (!res.ok) {
        setErr(String(res.error || 'Save failed'))
        return
      }
      onChange({
        ...reorder,
        groups: (res.groups as Reorder['groups']) || reorder.groups,
      })
      setLines([])
      setMsg(status === 'ordered' ? 'Supplier order saved.' : 'Draft saved.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Frame title="Load by Supplier">
      <Note>
        Select a supplier, add medicines with suggested qty/rate, then save as
        draft or mark ordered (same as classic Reorder supplier tab).
      </Note>
      <Field label="Supplier">
        <select
          className="settings-input"
          value={supplierId}
          onChange={(e) => void onSupplierPick(e.target.value)}
        >
          <option value="">— Select —</option>
          {(reorder.suppliers || []).map((s) => {
            const sid = String(s.supplier_id ?? s.id ?? '')
            const label = String(
              s.name || s.supplier_name || s.label || sid,
            )
            return (
              <option key={sid} value={sid}>
                {label}
              </option>
            )
          })}
        </select>
      </Field>
      {supplierId ? (
        <>
          <div className="iset-form-card">
            <h4>Add a medicine</h4>
            <div className="iset-bar" style={{ margin: 0 }}>
              <BarField label="Medicine" className="grow">
                <select
                  className="settings-input"
                  value={pickMed}
                  onChange={(e) => setPickMed(e.target.value)}
                >
                  <option value="">— Select —</option>
                  {medicines.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </BarField>
              <BarField label="Pack" className="short">
                <input
                  className="settings-input"
                  value={pickPack}
                  onChange={(e) => setPickPack(e.target.value)}
                />
              </BarField>
              <BarField label="Qty" className="short">
                <input
                  className="settings-input"
                  type="number"
                  style={{ textAlign: 'right' }}
                  value={pickQty}
                  onChange={(e) => setPickQty(e.target.value)}
                />
              </BarField>
              <SaveBtn
                label="Add line"
                disabled={busy}
                onClick={() => void addLine()}
              />
            </div>
          </div>
          <div className="iset-table-wrap">
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Medicine</th>
                  <th>Pack</th>
                  <th className="num">Qty</th>
                  <th className="num">Rate</th>
                  <th className="num">Stock</th>
                  <th className="act" />
                </tr>
              </thead>
              <tbody>
                {lines.map((ln, i) => (
                  <tr key={`${ln.medicine_name}-${i}`}>
                    <td>{ln.medicine_name}</td>
                    <td>{ln.pack_size}</td>
                    <td className="num">
                      <input
                        className="settings-input iset-cell-input"
                        type="number"
                        aria-label={`${ln.medicine_name} qty`}
                        value={ln.quantity}
                        onChange={(e) =>
                          updateLine(i, 'quantity', e.target.value)
                        }
                      />
                    </td>
                    <td className="num">
                      <input
                        className="settings-input iset-cell-input"
                        type="number"
                        aria-label={`${ln.medicine_name} rate`}
                        value={ln.unit_price}
                        onChange={(e) =>
                          updateLine(i, 'unit_price', e.target.value)
                        }
                      />
                    </td>
                    <td className="num">{ln.current_stock ?? '—'}</td>
                    <td className="act">
                      <button
                        type="button"
                        className="iset-btn danger"
                        onClick={() => removeLine(i)}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
                {!lines.length ? (
                  <EmptyRow cols={6}>No medicines in this order yet. Add one above.</EmptyRow>
                ) : null}
              </tbody>
            </table>
          </div>
          <div className="settings-form-row">
            <Field label="Phone">
              <input
                className="settings-input"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </Field>
            <Field label="Notes">
              <input
                className="settings-input"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </Field>
          </div>
          <Check
            label="Offline order (no supplier record)"
            checked={offline}
            onChange={setOffline}
          />
          <div className="iset-row">
            <SaveBtn
              label="Save draft"
              saving={busy}
              onClick={() => void saveSupplierOrder('draft')}
            />
            <SaveBtn
              label="Mark ordered"
              saving={busy}
              onClick={() => void saveSupplierOrder('ordered')}
            />
          </div>
        </>
      ) : null}
    </Frame>
  )
}

export function ReorderPanel({
  reorder,
  mode,
  onChange,
  onOpenDefaults,
  onOpenPurchase,
  medicinePrefill,
  bulkReorderLoad,
}: {
  reorder: Reorder
  mode: 'pending' | 'new' | 'by_supplier' | 'defaults' | string
  onChange: (r: Reorder) => void
  onOpenDefaults?: () => void
  onOpenPurchase?: (prefill: ReorderPrefill) => void
  medicinePrefill?: {
    medicine_name: string
    pack_size: string
    quantity: number
    unit_price: number
  }
  bulkReorderLoad?: boolean
}) {
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [defaultQty, setDefaultQty] = useState(reorder.reorder_default_qty)
  const [multiTabs, setMultiTabs] = useState<ReorderTabState[] | null>(null)

  useEffect(() => {
    setDefaultQty(reorder.reorder_default_qty)
  }, [reorder.reorder_default_qty])

  async function editGroup(groupId: string) {
    if (!groupId) return
    setBusy(true)
    setErr('')
    try {
      const tabs = await fetchReorderGroupTab(groupId)
      setMultiTabs(tabs)
      setMsg('Loaded pending order for editing.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function bulkLoadTabs() {
    setBusy(true)
    setErr('')
    try {
      const tabs = await fetchReorderBulkTabs()
      setMultiTabs(tabs)
      setMsg(`Loaded ${tabs.length} supplier tab(s) from stock candidates.`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!bulkReorderLoad || mode !== 'new') return
    void bulkLoadTabs()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once when navigated from alerts
  }, [bulkReorderLoad, mode])

  async function refreshList() {
    setErr('')
    try {
      const res = await reorderAction({ action: 'list' })
      const next = {
        groups: (res.groups as Reorder['groups']) || reorder.groups,
        suppliers:
          (res.suppliers as Reorder['suppliers']) || reorder.suppliers,
        reorder_default_qty: String(
          res.reorder_default_qty ?? reorder.reorder_default_qty,
        ),
      }
      onChange(next)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  async function createGrouped() {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await reorderAction({ action: 'create_grouped' })
      const next: Reorder = {
        ...reorder,
        groups: (res.groups as Reorder['groups']) || reorder.groups,
      }
      onChange(next)
      setMsg('Supplier-grouped drafts created.')
      await refreshList()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function saveDefaults() {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      await saveSettingsSection('thresholds', {
        reorder_default_qty: defaultQty,
      })
      onChange({ ...reorder, reorder_default_qty: defaultQty })
      setMsg('Reorder default qty saved.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function openPurchaseForGroup(group: Record<string, unknown>) {
    const firstId = Number(group.first_id || 0)
    if (firstId <= 0) {
      setErr('No order id for this group.')
      return
    }
    setBusy(true)
    setErr('')
    try {
      const res = await reorderAction({
        action: 'purchase_prefill',
        order_id: firstId,
      })
      const prefill = res.prefill as ReorderPrefill | undefined
      if (!prefill?.order_id) {
        setErr('Could not build purchase prefill.')
        return
      }
      onOpenPurchase?.(prefill)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function markGroupReceived(groupId: string) {
    if (!groupId) return
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await reorderAction({ action: 'mark_received', group_id: groupId })
      onChange({
        ...reorder,
        groups: (res.groups as Reorder['groups']) || reorder.groups,
      })
      setMsg('Order marked received.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function cancelGroup(groupId: string) {
    if (!groupId) return
    if (
      !window.confirm(
        'Cancel this supplier order and all its medicines?',
      )
    ) {
      return
    }
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await reorderAction({ action: 'cancel', group_id: groupId })
      onChange({
        ...reorder,
        groups: (res.groups as Reorder['groups']) || reorder.groups,
      })
      setMsg('Order cancelled.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PanelTitle>Reorder</PanelTitle>
      {err ? <p className="iset-callout warn">{err}</p> : null}
      {msg ? <p className="iset-callout">{msg}</p> : null}

      {mode === 'pending' && (
        <Frame title={`Pending Orders (${(reorder.groups || []).length})`}>
          <div className="iset-bar">
            <div className="iset-bar-actions">
              <button
                type="button"
                className="settings-action-btn iset-secondary"
                onClick={() => void refreshList()}
              >
                Refresh
              </button>
            </div>
          </div>
          <div className="iset-table-wrap">
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Group / Supplier</th>
                  <th>Status</th>
                  <th>Items</th>
                  <th className="act">Actions</th>
                </tr>
              </thead>
              <tbody>
                {(reorder.groups || []).map((g, i) => {
                  const groupId = String(g.group_id || '')
                  const status = String(g.status || '').toLowerCase()
                  const received = status === 'received'
                  return (
                  <tr key={i}>
                    <td>
                      {String(
                        g.supplier_name ||
                          g.label ||
                          g.group_id ||
                          `Group ${i + 1}`,
                      )}
                    </td>
                    <td>
                      <span className={`iset-badge${received ? ' ok' : ''}`}>
                        {String(g.status || '—')}
                      </span>
                    </td>
                    <td className="wrap">
                      {String(
                        g.medicines_preview ||
                          g.item_count ||
                          g.count ||
                          '',
                      )}
                    </td>
                    <td className="act">
                      <button
                        type="button"
                        className="iset-btn"
                        disabled={busy || received}
                        onClick={() => void editGroup(groupId)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="iset-btn primary"
                        disabled={busy || received}
                        onClick={() => void openPurchaseForGroup(g)}
                      >
                        Open Purchase
                      </button>
                      <button
                        type="button"
                        className="iset-btn"
                        disabled={busy || received}
                        onClick={() => void markGroupReceived(groupId)}
                      >
                        Mark Received
                      </button>
                      <button
                        type="button"
                        className="iset-btn danger"
                        disabled={busy || received}
                        onClick={() => void cancelGroup(groupId)}
                      >
                        Cancel
                      </button>
                    </td>
                  </tr>
                  )
                })}
                {!reorder.groups?.length ? (
                  <EmptyRow cols={4}>
                    No pending orders. Make one under New Order or Load by Supplier.
                  </EmptyRow>
                ) : null}
              </tbody>
            </table>
          </div>
        </Frame>
      )}

      {mode === 'new' && (
        <>
          <Frame title="New Order">
            <Note>
              Create draft reorder groups grouped by preferred supplier, or use
              multi-supplier tabs with bulk load (same as classic Reorder page).
            </Note>
            <div className="iset-row">
              <SaveBtn
                label="Create supplier-grouped drafts"
                saving={busy}
                onClick={() => void createGrouped()}
              />
              <button
                type="button"
                className="settings-action-btn iset-secondary"
                disabled={busy}
                onClick={() => void bulkLoadTabs()}
              >
                Bulk load tabs
              </button>
            </div>
          </Frame>
          {multiTabs ? (
            <ReorderMultiTabEditor
              initialTabs={multiTabs}
              busy={busy}
              setBusy={setBusy}
              setErr={setErr}
              setMsg={setMsg}
              onGroupsChanged={async () => {
                const res = await reorderAction({ action: 'list' })
                onChange({
                  ...reorder,
                  groups: (res.groups as Reorder['groups']) || reorder.groups,
                })
                setMultiTabs(null)
              }}
            />
          ) : null}
        </>
      )}

      {mode === 'by_supplier' && (
        <>
          <ReorderBySupplierEditor
            reorder={reorder}
            busy={busy}
            setBusy={setBusy}
            setErr={setErr}
            setMsg={setMsg}
            onChange={onChange}
            medicinePrefill={medicinePrefill}
          />
          {multiTabs ? (
            <ReorderMultiTabEditor
              initialTabs={multiTabs}
              busy={busy}
              setBusy={setBusy}
              setErr={setErr}
              setMsg={setMsg}
              onGroupsChanged={() => void refreshList()}
            />
          ) : null}
        </>
      )}

      {mode === 'defaults' && (
        <>
          <Frame title="Reorder Defaults">
            <Note>
              Default order quantity used when creating reorder drafts. You can
              also edit under Layout &amp; Lists → Thresholds.
            </Note>
            <Field label="Default reorder qty">
              <input
                className="settings-input"
                inputMode="numeric"
                style={{ maxWidth: 110, textAlign: 'right' }}
                value={defaultQty}
                onChange={(e) => setDefaultQty(e.target.value)}
              />
            </Field>
            {onOpenDefaults ? (
              <div className="iset-row">
                <button
                  type="button"
                  className="settings-action-btn iset-secondary"
                  onClick={onOpenDefaults}
                >
                  Open Layout → Thresholds
                </button>
              </div>
            ) : null}
          </Frame>
          <SaveBar
            label="Save Default Qty"
            saving={busy}
            onClick={() => void saveDefaults()}
          />
        </>
      )}

      {multiTabs && mode === 'pending' ? (
        <ReorderMultiTabEditor
          initialTabs={multiTabs}
          busy={busy}
          setBusy={setBusy}
          setErr={setErr}
          setMsg={setMsg}
          onGroupsChanged={async () => {
            const res = await reorderAction({ action: 'list' })
            onChange({
              ...reorder,
              groups: (res.groups as Reorder['groups']) || reorder.groups,
            })
            setMultiTabs(null)
          }}
        />
      ) : null}
    </>
  )
}

/* ─── Shelf ────────────────────────────────────────────────────────────── */

type SelLoc = {
  type: 'rack' | 'section' | 'box'
  id: number
  location: string
  path: string
  rackId: number
  sectionId?: number
}

export function ShelfPanel({
  shelf,
  onChange,
  sectionId,
}: {
  shelf: Shelf
  onChange: (s: Shelf) => void
  sectionId?: string
}) {
  const [name, setName] = useState('')
  const [rackId, setRackId] = useState<number | ''>('')
  const [secId, setSecId] = useState<number | ''>('')
  const [err, setErr] = useState('')
  const [selected, setSelected] = useState<SelLoc | null>(null)
  const [renameBuf, setRenameBuf] = useState('')

  async function run(body: Record<string, unknown>) {
    setErr('')
    try {
      const next = await mutateShelf(body)
      if (next) onChange(next)
      setName('')
      setRenameBuf('')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const sections =
    shelf.racks.find((r) => r.id === rackId)?.sections || []

  const assignedAt = useMemo(() => {
    if (!selected) return []
    const loc = selected.location
    return (shelf.assigned || []).filter((m) => {
      if (selected.type === 'box') return m.location === loc
      return m.location === loc || m.location.startsWith(loc)
    })
  }, [shelf.assigned, selected])

  // Tk uses a persistent split: tree | medicines. Sidebar section is navigational only.
  void sectionId

  const isSel = (type: SelLoc['type'], id: number) =>
    selected?.type === type && selected.id === id

  return (
    <>
      <PanelTitle>Shelf Management</PanelTitle>
      {err ? <p className="iset-callout warn">{err}</p> : null}
      {shelf.error ? <p className="iset-callout warn">{shelf.error}</p> : null}
      <div className="iset-bar">
        <Check
          label="Show location on medicine lists"
          checked={Boolean(shelf.show_location)}
          onChange={(v) =>
            void run({ action: 'set_show_location', show_location: v })
          }
        />
      </div>

      <div className="iset-split">
        <div>
            <Frame title="Shelf Structure">
              <div className="iset-shelf-add">
                <BarField label="Name">
                  <input
                    className="settings-input"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </BarField>
                <SaveBtn
                  label="+ Rack"
                  onClick={() => run({ action: 'add_rack', name })}
                />
                <BarField label="Under rack">
                  <select
                    className="settings-input"
                    value={rackId}
                    onChange={(e) =>
                      setRackId(e.target.value ? Number(e.target.value) : '')
                    }
                  >
                    <option value="">Select rack…</option>
                    {shelf.racks.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name}
                      </option>
                    ))}
                  </select>
                </BarField>
                <SaveBtn
                  label="+ Section"
                  disabled={!rackId}
                  onClick={() =>
                    run({ action: 'add_section', rack_id: rackId, name })
                  }
                />
                <BarField label="Under section">
                  <select
                    className="settings-input"
                    value={secId}
                    onChange={(e) =>
                      setSecId(e.target.value ? Number(e.target.value) : '')
                    }
                  >
                    <option value="">Select section…</option>
                    {sections.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </BarField>
                <SaveBtn
                  label="+ Box"
                  disabled={!secId}
                  onClick={() =>
                    run({ action: 'add_box', section_id: secId, name })
                  }
                />
              </div>

              <h4 className="iset-subhead">Racks</h4>
              <ul className="iset-tree">
                {shelf.racks.map((r) => (
                  <li key={r.id}>
                    <div className={`iset-tree-node${isSel('rack', r.id) ? ' selected' : ''}`}>
                      <button
                        type="button"
                        className="iset-tree-name"
                        onClick={() => {
                          const location = locKey(r.name)
                          setSelected({
                            type: 'rack',
                            id: r.id,
                            location,
                            path: pathLabel(r.name),
                            rackId: r.id,
                          })
                          setRenameBuf(r.name)
                          setRackId(r.id)
                        }}
                      >
                        <strong>Rack {r.name}</strong>
                      </button>
                      <button
                        type="button"
                        className="iset-btn"
                        onClick={() => {
                          const n = window.prompt('Rename rack', r.name)
                          if (n)
                            void run({
                              action: 'rename_rack',
                              id: r.id,
                              name: n,
                            })
                        }}
                      >
                        Rename
                      </button>
                      <button
                        type="button"
                        className="iset-btn danger"
                        onClick={() => {
                          if (window.confirm(`Delete rack ${r.name}?`))
                            void run({ action: 'delete_rack', id: r.id })
                        }}
                      >
                        Delete
                      </button>
                    </div>
                    <ul>
                      {r.sections.map((s) => (
                        <li key={s.id}>
                          <div className={`iset-tree-node${isSel('section', s.id) ? ' selected' : ''}`}>
                            <button
                              type="button"
                              className="iset-tree-name"
                              onClick={() => {
                                const location = locKey(r.name, s.name)
                                setSelected({
                                  type: 'section',
                                  id: s.id,
                                  location,
                                  path: pathLabel(r.name, s.name),
                                  rackId: r.id,
                                  sectionId: s.id,
                                })
                                setRenameBuf(s.name)
                                setRackId(r.id)
                                setSecId(s.id)
                              }}
                            >
                              Section {s.name}
                            </button>
                            <button
                              type="button"
                              className="iset-btn"
                              onClick={() => {
                                const n = window.prompt('Rename section', s.name)
                                if (n)
                                  void run({
                                    action: 'rename_section',
                                    id: s.id,
                                    name: n,
                                  })
                              }}
                            >
                              Rename
                            </button>
                            <button
                              type="button"
                              className="iset-btn danger"
                              onClick={() => {
                                if (window.confirm(`Delete section ${s.name}?`))
                                  void run({
                                    action: 'delete_section',
                                    id: s.id,
                                  })
                              }}
                            >
                              Delete
                            </button>
                          </div>
                          <ul>
                            {s.boxes.map((b) => (
                              <li key={b.id}>
                                <div className={`iset-tree-node${isSel('box', b.id) ? ' selected' : ''}`}>
                                  <button
                                    type="button"
                                    className="iset-tree-name"
                                    onClick={() => {
                                      const location = locKey(
                                        r.name,
                                        s.name,
                                        b.name,
                                      )
                                      setSelected({
                                        type: 'box',
                                        id: b.id,
                                        location,
                                        path: pathLabel(r.name, s.name, b.name),
                                        rackId: r.id,
                                        sectionId: s.id,
                                      })
                                      setRenameBuf(b.name)
                                      setRackId(r.id)
                                      setSecId(s.id)
                                    }}
                                  >
                                    Box {b.name}
                                  </button>
                                  <button
                                    type="button"
                                    className="iset-btn"
                                    onClick={() => {
                                      const n = window.prompt(
                                        'Rename box',
                                        b.name,
                                      )
                                      if (n)
                                        void run({
                                          action: 'rename_box',
                                          id: b.id,
                                          name: n,
                                        })
                                    }}
                                  >
                                    Rename
                                  </button>
                                  <button
                                    type="button"
                                    className="iset-btn danger"
                                    onClick={() => {
                                      if (
                                        window.confirm(`Delete box ${b.name}?`)
                                      )
                                        void run({
                                          action: 'delete_box',
                                          id: b.id,
                                        })
                                    }}
                                  >
                                    Delete
                                  </button>
                                </div>
                              </li>
                            ))}
                          </ul>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
                {!shelf.racks.length ? (
                  <li className="iset-tree-empty">No racks yet. Type a name and press + Rack.</li>
                ) : null}
              </ul>
              {selected ? (
                <Note>
                  Selected: {selected.path}
                  {renameBuf ? ` (${selected.type})` : ''}
                </Note>
              ) : (
                <Note>Select a rack / section / box to assign medicines.</Note>
              )}
            </Frame>
          </div>

          <div>
            <Frame
              title={
                selected
                  ? `Medicines at location — ${selected.path}`
                  : 'Medicines at location'
              }
            >
              {!selected ? (
                <Note>Select a location on the left.</Note>
              ) : (
                <>
                  <h4 className="iset-subhead">Assigned ({assignedAt.length})</h4>
                  <div className="iset-table-wrap">
                    <table className="settings-table">
                      <thead>
                        <tr>
                          <th>Medicine</th>
                          <th>Batch</th>
                          <th>Location</th>
                          <th className="act" />
                        </tr>
                      </thead>
                      <tbody>
                        {assignedAt.map((m) => (
                          <tr key={m.id}>
                            <td>{m.name}</td>
                            <td>{m.batch}</td>
                            <td>{m.location}</td>
                            <td className="act">
                              <button
                                type="button"
                                className="iset-btn"
                                onClick={() =>
                                  void run({
                                    action: 'unassign',
                                    medicine_id: m.id,
                                  })
                                }
                              >
                                Unassign
                              </button>
                            </td>
                          </tr>
                        ))}
                        {!assignedAt.length ? (
                          <EmptyRow cols={4}>
                            No medicine is assigned here yet. Assign one from the list below.
                          </EmptyRow>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                  <h4 className="iset-subhead">
                    Unassigned ({(shelf.unassigned || []).length})
                  </h4>
                  <div className="iset-table-wrap">
                    <table className="settings-table">
                      <thead>
                        <tr>
                          <th>Medicine</th>
                          <th>Batch</th>
                          <th className="num">Stock</th>
                          <th className="act" />
                        </tr>
                      </thead>
                      <tbody>
                        {(shelf.unassigned || []).slice(0, 200).map((m) => (
                          <tr key={m.id}>
                            <td>{m.name}</td>
                            <td>{m.batch}</td>
                            <td className="num">{m.stock}</td>
                            <td className="act">
                              <button
                                type="button"
                                className="iset-btn primary"
                                onClick={() =>
                                  void run({
                                    action: 'assign',
                                    medicine_id: m.id,
                                    location: selected.location,
                                  })
                                }
                              >
                                Assign
                              </button>
                            </td>
                          </tr>
                        ))}
                        {!shelf.unassigned?.length ? (
                          <EmptyRow cols={4}>Every medicine has a location.</EmptyRow>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </Frame>
          </div>
      </div>
    </>
  )
}

/* ─── Thresholds ───────────────────────────────────────────────────────── */

export function ThresholdsPanel({
  thresholds,
  onChange,
}: {
  thresholds: Thresholds
  onChange: (t: Thresholds) => void
}) {
  const [local, setLocal] = useState(thresholds)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setLocal(thresholds)
  }, [thresholds])

  async function save() {
    setSaving(true)
    setErr('')
    setMsg('')
    try {
      const next = (await saveSettingsSection(
        'thresholds',
        local as unknown as Record<string, unknown>,
      )) as unknown as Thresholds
      setLocal(next)
      onChange(next)
      setMsg('Thresholds saved.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const [filter, setFilter] = useState('')
  const types = local.med_types.filter((mt) =>
    mt.toLowerCase().includes(filter.trim().toLowerCase()),
  )

  return (
    <>
      <PanelTitle>Thresholds &amp; Reorder</PanelTitle>
      <Frame title="Low stock and near expiry, per medicine type">
        <Note>
          Low stock: alert when the total stock of a medicine (all batches, in the
          units the inventory shows) is below this number. Near expiry: alert this
          many months before a batch expires.
        </Note>
        {local.med_types.length > 12 ? (
          <input
            className="settings-input settings-type-filter"
            placeholder="Find a medicine type…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        ) : null}
        <table className="settings-type-table">
          <thead>
            <tr>
              <th>Medicine type</th>
              <th>Low stock below</th>
              <th>Near expiry within (months)</th>
            </tr>
          </thead>
          <tbody>
            {types.map((mt) => {
              const key = mt.toLowerCase()
              return (
                <tr key={mt}>
                  <td>{mt}</td>
                  <td>
                    <input
                      className="settings-input"
                      inputMode="numeric"
                      aria-label={`${mt} low stock below`}
                      value={local.low_stock[key] ?? '10'}
                      onChange={(e) =>
                        setLocal((t) => ({
                          ...t,
                          low_stock: { ...t.low_stock, [key]: e.target.value },
                        }))
                      }
                    />
                  </td>
                  <td>
                    <input
                      className="settings-input"
                      inputMode="numeric"
                      aria-label={`${mt} near expiry months`}
                      value={local.near_expiry[key] ?? '3'}
                      onChange={(e) =>
                        setLocal((t) => ({
                          ...t,
                          near_expiry: { ...t.near_expiry, [key]: e.target.value },
                        }))
                      }
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Frame>
      <Frame title="Customer dues and reorder">
        <Field label="Minimum due amount (Rs)">
          <input
            className="settings-input"
            value={local.customer_due_min_amount}
            onChange={(e) =>
              setLocal((t) => ({
                ...t,
                customer_due_min_amount: e.target.value,
              }))
            }
          />
        </Field>
        <Field label="Minimum due days">
          <input
            className="settings-input"
            value={local.customer_due_min_days}
            onChange={(e) =>
              setLocal((t) => ({
                ...t,
                customer_due_min_days: e.target.value,
              }))
            }
          />
        </Field>
        <Field label="Default reorder qty">
          <input
            className="settings-input"
            value={local.reorder_default_qty}
            onChange={(e) =>
              setLocal((t) => ({
                ...t,
                reorder_default_qty: e.target.value,
              }))
            }
          />
        </Field>
      </Frame>
      <SaveBar label="Save Thresholds" saving={saving} onClick={save} msg={msg} err={err} />
    </>
  )
}

/* ─── Shortcuts (the list: ./shortcutSections.ts) ─────────────────────── */

export function ShortcutsPanel() {
  return (
    <>
      <PanelTitle>⌨ Shortcuts</PanelTitle>
      <Note>
        Every key this app answers, page by page. Press Escape to leave a field
        so number and letter keys work.
      </Note>
      <div className="iset-shortcuts">
        {SHORTCUT_SECTIONS.map((sec) => (
          <Frame key={sec.title} title={sec.title}>
            <div className="iset-table-wrap plain">
              <table className="settings-table">
                <tbody>
                  {sec.rows.map(([key, desc], i) => (
                    <tr key={`${sec.title}-${i}`}>
                      <td style={{ width: '34%' }}>
                        {key ? <span className="iset-key">{key}</span> : '—'}
                      </td>
                      <td className="wrap">{desc}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Frame>
        ))}
      </div>
    </>
  )
}
