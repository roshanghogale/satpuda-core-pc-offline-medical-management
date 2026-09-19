import { useEffect, useMemo, useRef, useState } from 'react'
import type { AppNavigate } from '../../App'
import type { ReorderPrefill } from '../../pagesApi'
import {
  alertAction,
  applyAlertNavigation,
  deletePayment,
  fetchLedger,
  mutateContact,
  mutateShelf,
  reorderAction,
  savePayment,
  saveSettingsSection,
  systemAction,
  type LedgerBundle,
  type PaymentBundle,
  type SettingsBundle,
} from '../../settingsApi'
import { focusTableSection, usePageHotkeys } from '../../hooks/usePageHotkeys'
import { SHORTCUT_SECTIONS } from './shortcutSections'
import { dispatchPaymentsChanged, dispatchVillagesChanged } from '../../syncRefresh'
import { useLayoutRowCount } from '../../layoutRows'
import { resolveParty } from '../../partyNamePick'
import { ModernCombo } from '../ModernCombo'
import {
  Check,
  Field,
  Frame,
  Note,
  PanelTitle,
  SaveBtn,
} from './SettingsChrome'
import { CappedTableWrap } from '../pageChrome'
import {
  ReorderMultiTabEditor,
  fetchReorderBulkTabs,
  fetchReorderGroupTab,
  type ReorderTabState,
} from './ReorderMultiTabEditor'

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
  const blob = new Blob([lines.join('\r\n')], {
    type: 'text/csv;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
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
  const [supplier, setSupplier] = useState({
    name: '',
    phone: '',
    gstin: '',
    address: '',
  })
  const [village, setVillage] = useState('')
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
    const ok = await run(
      contactPayload(
        'customer',
        editIdRef.current != null ? 'update' : 'add',
        { id: editIdRef.current ?? undefined, fields: { ...c } },
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

  const doctors = useMemo(
    () =>
      contacts.doctors.filter((d) =>
        filterText(search, [d.name, d.reg_no, d.phone]),
      ),
    [contacts.doctors, search],
  )
  const customers = useMemo(
    () =>
      contacts.customers.filter((d) =>
        filterText(search, [d.name, d.phone, d.address, d.total_due]),
      ),
    [contacts.customers, search],
  )
  const suppliers = useMemo(
    () =>
      contacts.suppliers.filter((d) =>
        filterText(search, [
          d.name,
          d.phone,
          d.gstin,
          d.address,
          d.total_due,
        ]),
      ),
    [contacts.suppliers, search],
  )
  const villages = useMemo(
    () => contacts.villages.filter((v) => filterText(search, [v])),
    [contacts.villages, search],
  )

  return (
    <>
      {err ? <p className="error">{err}</p> : null}
      {msg ? <Note>{msg}</Note> : null}

      {sectionId === 'doctors' && (
        <Frame title={`Doctors (${contacts.doctors.length})`}>
          <Field label="Search">
            <input
              className="settings-input"
              data-nav-order={1}
              data-nav-chain={CONTACTS_NAV}
              data-page-filter="primary"
              value={search}
              placeholder="Filter…"
              onChange={(e) => setSearch(e.target.value)}
            />
          </Field>
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
          <div className="settings-inline-actions">
            <SaveBtn
              label={editId != null ? 'Save Doctor' : 'Add Doctor'}
              navOrder={5}
              navChain={CONTACTS_NAV}
              navAction="contacts-save-doctor"
              onClick={() => {
                void saveDoctor()
              }}
            />
            {editId != null ? (
              <button
                type="button"
                className="settings-link-btn"
                onClick={clearEdit}
              >
                Cancel
              </button>
            ) : null}
          </div>
          <div ref={tableWrapRef}>
          <CappedTableWrap
            visibleRows={doctorRows}
            className="settings-table-wrap"
          >
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Reg No</th>
                  <th>Phone</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {doctors.map((d) => (
                  <tr key={d.id}>
                    <td>{d.name}</td>
                    <td>{d.reg_no}</td>
                    <td>{d.phone}</td>
                    <td>
                      <button
                        type="button"
                        className="settings-link-btn"
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
                      </button>{' '}
                      <button
                        type="button"
                        className="settings-link-btn"
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
                  <tr>
                    <td colSpan={4}>No doctors</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </CappedTableWrap>
          </div>
        </Frame>
      )}

      {sectionId === 'customers' && (
        <Frame title={`Customers (${contacts.customers.length})`}>
          <Field label="Search">
            <input
              className="settings-input"
              data-nav-order={1}
              data-nav-chain={CONTACTS_NAV}
              data-page-filter="primary"
              value={search}
              placeholder="Filter…"
              onChange={(e) => setSearch(e.target.value)}
            />
          </Field>
          <div className="settings-inline-actions">
            <SaveBtn
              label="Recalculate All Dues"
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
            />
            <button
              type="button"
              className="settings-action-btn"
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
              className="settings-action-btn"
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
              className="settings-action-btn"
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
          </div>
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
          <div className="settings-inline-actions">
            <SaveBtn
              label={editId != null ? 'Save Customer' : 'Add Customer'}
              navOrder={5}
              navChain={CONTACTS_NAV}
              navAction="contacts-save-customer"
              onClick={() => {
                void saveCustomer()
              }}
            />
            {editId != null ? (
              <button
                type="button"
                className="settings-link-btn"
                onClick={clearEdit}
              >
                Cancel
              </button>
            ) : null}
          </div>
          <div ref={tableWrapRef}>
          <CappedTableWrap
            visibleRows={customerRows}
            className="settings-table-wrap"
          >
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Phone</th>
                  <th>Address</th>
                  <th>Due</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {customers.map((d) => (
                  <tr key={d.id}>
                    <td>{d.name}</td>
                    <td>{d.phone}</td>
                    <td>{d.address || ''}</td>
                    <td>{money(d.total_due)}</td>
                    <td>
                      <button
                        type="button"
                        className="settings-link-btn"
                        onClick={() => {
                          setEditId(d.id)
                          setCustomer({
                            name: d.name,
                            phone: d.phone,
                            address: d.address || d.village || '',
                          })
                        }}
                      >
                        Edit
                      </button>{' '}
                      <button
                        type="button"
                        className="settings-link-btn"
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
                  <tr>
                    <td colSpan={5}>No customers</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </CappedTableWrap>
          </div>
        </Frame>
      )}

      {sectionId === 'villages' && (
        <Frame title="Villages">
          <Field label="Search">
            <input
              className="settings-input"
              data-nav-order={1}
              data-nav-chain={CONTACTS_NAV}
              data-page-filter="primary"
              value={search}
              placeholder="Filter…"
              onChange={(e) => setSearch(e.target.value)}
            />
          </Field>
          <Note>Default: {contacts.default_village || '(none)'}</Note>
          <Field label="New village">
            <input
              className="settings-input"
              data-nav-order={2}
              data-nav-chain={CONTACTS_NAV}
              data-nav-enter="contacts-save-village"
              value={village}
              onChange={(e) => setVillage(e.target.value)}
            />
          </Field>
          <SaveBtn
            label="Add Village"
            navOrder={3}
            navChain={CONTACTS_NAV}
            navAction="contacts-save-village"
            onClick={() => {
              void saveVillage()
            }}
          />
          <ul className="settings-plain-list">
            {villages.map((v) => (
              <li key={v}>
                {v}
                {contacts.default_village === v ? ' (default)' : ''}{' '}
                <button
                  type="button"
                  className="settings-link-btn"
                  onClick={() =>
                    run(
                      contactPayload('village', 'set_default_village', {
                        fields: { name: v },
                      }),
                    )
                  }
                >
                  Set default
                </button>{' '}
                <button
                  type="button"
                  className="settings-link-btn"
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
              </li>
            ))}
            {!villages.length ? <li>No villages</li> : null}
          </ul>
        </Frame>
      )}

      {sectionId === 'suppliers' && (
        <Frame title={`Suppliers (${contacts.suppliers.length})`}>
          <Field label="Search">
            <input
              className="settings-input"
              data-nav-order={1}
              data-nav-chain={CONTACTS_NAV}
              data-page-filter="primary"
              value={search}
              placeholder="Filter…"
              onChange={(e) => setSearch(e.target.value)}
            />
          </Field>
          <SaveBtn
            label="Recalculate All Dues"
            onClick={() =>
              run(contactPayload('supplier', 'recalculate'))
            }
          />
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
          <div className="settings-inline-actions">
            <SaveBtn
              label={editId != null ? 'Save Supplier' : 'Add Supplier'}
              navOrder={6}
              navChain={CONTACTS_NAV}
              navAction="contacts-save-supplier"
              onClick={() => {
                void saveSupplier()
              }}
            />
            {editId != null ? (
              <button
                type="button"
                className="settings-link-btn"
                onClick={clearEdit}
              >
                Cancel
              </button>
            ) : null}
          </div>
          <div ref={tableWrapRef}>
          <CappedTableWrap
            visibleRows={supplierRows}
            className="settings-table-wrap"
          >
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Phone</th>
                  <th>GSTIN</th>
                  <th>Address</th>
                  <th>Due</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {suppliers.map((d) => (
                  <tr key={d.id}>
                    <td>{d.name}</td>
                    <td>{d.phone}</td>
                    <td>{d.gstin}</td>
                    <td>{d.address || ''}</td>
                    <td>{money(d.total_due)}</td>
                    <td>
                      <button
                        type="button"
                        className="settings-link-btn"
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
                      </button>{' '}
                      <button
                        type="button"
                        className="settings-link-btn"
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
                  <tr>
                    <td colSpan={6}>No suppliers</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </CappedTableWrap>
          </div>
        </Frame>
      )}
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
  }
> = {
  low: {
    key: 'low_stock',
    title: 'Low Stock',
    headers: ['Medicine Name', 'Current Stock', 'Unit', 'Supplier'],
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
    ],
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
    ],
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
    ],
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
  const spec = ALERT_SPECS[nestedId] || ALERT_SPECS.low
  const rows = ((alerts[spec.key] as unknown[][]) || []).filter((r) =>
    filterText(search, r),
  )
  const count = alerts.counts?.[spec.key as string] ?? rows.length
  const selectedRow = selectedIdx != null ? rows[selectedIdx] : null

  usePageHotkeys({ onSave: () => onRefresh() })

  function exportCsv() {
    try {
      void systemAction({ action: 'export', kind: 'inventory' })
    } catch {
      /* prefer client CSV */
    }
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

  return (
    <>
      <PanelTitle>
        {spec.title} ({count})
      </PanelTitle>
      <Note>
        Central dashboard for stock, expiry, and customer due alerts. Select a
        row for Reorder/Return actions. F5 refreshes.
      </Note>
      <div className="settings-inline-actions">
        <Field label="Search">
          <input
            className="settings-input"
            value={search}
            placeholder="Filter table…"
            onChange={(e) => setSearch(e.target.value)}
          />
        </Field>
        <SaveBtn label="Refresh (F5)" onClick={() => onRefresh()} />
        <SaveBtn label="Export CSV" onClick={exportCsv} />
        <SaveBtn
          label="Reorder by Supplier"
          saving={busy}
          onClick={() => void bulkReorder()}
        />
        <SaveBtn
          label="Return by Purchase"
          saving={busy}
          onClick={() => void bulkReturn()}
        />
      </div>
      {(spec.actionLabel || spec.dismissRow || spec.dismissAllExpired || spec.dismissAllOos) && (
        <div className="settings-inline-actions">
          {spec.actionLabel ? (
            <SaveBtn
              label={spec.actionLabel}
              saving={busy}
              onClick={() => void rowAction()}
            />
          ) : null}
          {spec.bulkReturn && spec.actionLabel === 'Return' ? (
            <SaveBtn
              label="Return by Purchase"
              saving={busy}
              onClick={() => void bulkReturn()}
            />
          ) : null}
          {spec.bulkReorder && spec.actionLabel === 'Reorder' ? (
            <SaveBtn
              label="Reorder by Supplier"
              saving={busy}
              onClick={() => void bulkReorder()}
            />
          ) : null}
          {spec.dismissRow ? (
            <button
              type="button"
              className="settings-link-btn"
              disabled={busy}
              onClick={() => void dismissRow()}
            >
              Remove from List
            </button>
          ) : null}
          {spec.dismissAllExpired ? (
            <button
              type="button"
              className="settings-link-btn danger-text"
              disabled={busy}
              onClick={() => void dismissAllExpired()}
            >
              Remove All Expired
            </button>
          ) : null}
          {spec.dismissAllOos ? (
            <button
              type="button"
              className="settings-link-btn danger-text"
              disabled={busy}
              onClick={() => void dismissAllOos()}
            >
              Remove All Out of Stock
            </button>
          ) : null}
        </div>
      )}
      {err ? <p className="settings-error">{err}</p> : null}
      {msg ? <p className="settings-msg">{msg}</p> : null}
      <div className="settings-table-wrap">
        <table className="settings-table">
          <thead>
            <tr>
              {spec.headers.map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 400).map((r, i) => (
              <tr
                key={i}
                className={selectedIdx === i ? 'row-selected' : ''}
                onClick={() => setSelectedIdx(i)}
                onDoubleClick={() => {
                  setSelectedIdx(i)
                  void rowAction(r)
                }}
              >
                {spec.headers.map((_, j) => (
                  <td key={j}>{String(r[j] ?? '')}</td>
                ))}
              </tr>
            ))}
            {!rows.length ? (
              <tr>
                <td colSpan={spec.headers.length}>No rows</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </>
  )
}

/* ─── Payment ──────────────────────────────────────────────────────────── */

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
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [editHint, setEditHint] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)

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

  function clearForm() {
    setAmount('')
    setCash('0')
    setOnline('0')
    setReference('')
    setNote('')
    setEditingId(null)
    setEditHint('')
  }

  async function save() {
    setErr('')
    setMsg('')
    try {
      const body =
        kind === 'supplier'
          ? {
              kind,
              party,
              amount: Number(amount),
              mode,
              date,
              reference,
              ...(editingId ? { id: editingId } : {}),
            }
          : {
              kind,
              party,
              cash: Number(cash),
              online: Number(online),
              date,
              reference,
              note,
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
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  usePageHotkeys({ onSave: () => void save() })

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
      setMsg('Payment deleted.')
      if (editingId != null && Number(row.id) === editingId) clearForm()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  function loadHistoryRow(r: Record<string, unknown>) {
    const id = Number(r.id || 0)
    setParty(String(r.party ?? ''))
    // A history row keeps the party's NAME, never its id, so there is no click
    // to honour here -- let the name resolve on its own.
    setPartyId(null)
    setDate(String(r.date ?? new Date().toISOString().slice(0, 10)))
    setReference(String(r.reference ?? ''))
    if (kind === 'supplier') {
      setAmount(String(r.amount ?? ''))
      setMode(String(r.mode || 'Cash'))
    } else {
      setCash(String(r.cash ?? 0))
      setOnline(String(r.online ?? 0))
      setNote(String(r.note ?? ''))
    }
    if (id > 0) {
      setEditingId(id)
      setEditHint(`Editing payment #${id}. Save updates this row.`)
    } else {
      setEditingId(null)
      setEditHint('')
    }
  }

  return (
    <>
      <PanelTitle>
        {kind === 'supplier' ? 'Supplier Payment' : 'Customer Payment'}
      </PanelTitle>
      <Note>F5 / Ctrl+G saves payment (button below)</Note>
      {err ? <p className="error">{err}</p> : null}
      {msg ? <Note>{msg}</Note> : null}
      {editHint ? <Note>{editHint}</Note> : null}
      <Field label={kind === 'supplier' ? 'Supplier' : 'Customer'}>
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
          onChange={(v) => {
            setParty(v)
            // Typing has moved off whatever was clicked, so the id that told two
            // same-named parties apart no longer describes this text.
            setPartyId(null)
          }}
          onPick={(it) => {
            setParty(it.label)
            const id = Number(it.id)
            setPartyId(id)
            const p = data.parties.find((x) => x.id === id)
            if (p && kind === 'supplier' && p.due > 0) {
              setAmount(p.due.toFixed(2))
            }
          }}
        />
      </Field>
      <Note>Outstanding due: ₹{money(selectedDue)}</Note>
      {kind === 'supplier' ? (
        <>
          <Field label="Amount">
            <input
              className="settings-input"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </Field>
          <Field label="Mode">
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
          </Field>
        </>
      ) : (
        <>
          <Field label="Cash">
            <input
              className="settings-input"
              value={cash}
              onChange={(e) => setCash(e.target.value)}
            />
          </Field>
          <Field label="Online">
            <input
              className="settings-input"
              value={online}
              onChange={(e) => setOnline(e.target.value)}
            />
          </Field>
        </>
      )}
      <Field label="Date">
        <input
          className="settings-input"
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
      </Field>
      <Field label="Reference">
        <input
          className="settings-input"
          value={reference}
          onChange={(e) => setReference(e.target.value)}
        />
      </Field>
      {kind === 'customer' ? (
        <Field label="Note">
          <input
            className="settings-input"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>
      ) : null}
      <div className="settings-inline-actions">
        <SaveBtn
          label={editingId ? 'Update Payment' : 'Save Payment'}
          onClick={save}
        />
        {editingId ? (
          <button type="button" className="settings-link-btn" onClick={clearForm}>
            Cancel edit
          </button>
        ) : null}
      </div>
      <div className="settings-table-wrap">
        <table className="settings-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Party</th>
              <th>Amount</th>
              {kind === 'customer' ? (
                <>
                  <th>Cash</th>
                  <th>Online</th>
                </>
              ) : null}
              <th>Mode</th>
              <th>Ref</th>
              <th>Due Before</th>
              <th>Due After</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {data.history.map((r) => (
              <tr
                key={String(r.id)}
                className={
                  editingId != null && Number(r.id) === editingId
                    ? 'row-selected'
                    : undefined
                }
              >
                <td>{String(r.date ?? '')}</td>
                <td>{String(r.party ?? '')}</td>
                <td>{money(r.amount)}</td>
                {kind === 'customer' ? (
                  <>
                    <td>{money(r.cash)}</td>
                    <td>{money(r.online)}</td>
                  </>
                ) : null}
                <td>{String(r.mode ?? '')}</td>
                <td>{String(r.reference ?? '')}</td>
                <td>
                  {r.due_before != null ? money(r.due_before) : '—'}
                </td>
                <td>{r.due_after != null ? money(r.due_after) : '—'}</td>
                <td>
                  <button
                    type="button"
                    className="settings-link-btn"
                    onClick={() => loadHistoryRow(r)}
                  >
                    Edit
                  </button>{' '}
                  <button
                    type="button"
                    className="settings-link-btn"
                    onClick={() => remove(r)}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {!data.history.length ? (
              <tr>
                <td colSpan={kind === 'customer' ? 10 : 8}>No payments yet</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
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
  const [to, setTo] = useState(new Date().toISOString().slice(0, 10))
  const [data, setData] = useState<LedgerBundle | null>(null)
  const [err, setErr] = useState('')
  const ledgerTableRef = useRef<HTMLDivElement>(null)

  async function load(nextParty = party) {
    setErr('')
    try {
      const res = await fetchLedger({ kind, party: nextParty, from, to })
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
      ['Date', 'Particulars', 'Debit', 'Credit', 'Balance'],
      data.rows.map((r) => [
        r.date,
        r.particulars,
        money(r.debit),
        money(r.credit),
        money(r.balance),
      ]),
    )
  }

  return (
    <>
      <PanelTitle>
        {kind === 'supplier' ? 'Supplier Ledger' : 'Customer Ledger'}
      </PanelTitle>
      {err ? <p className="error">{err}</p> : null}
      <Field label="Party">
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
      </Field>
      <Field label="From">
        <input
          className="settings-input"
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          onKeyDown={onFilterEnter}
        />
      </Field>
      <Field label="To">
        <input
          className="settings-input"
          type="date"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          onKeyDown={onFilterEnter}
        />
      </Field>
      <div className="settings-inline-actions">
        <SaveBtn label="View Statement" onClick={() => load(party)} />
        <SaveBtn
          label="Reset"
          onClick={() => {
            setParty('')
            setFrom('')
            setTo(new Date().toISOString().slice(0, 10))
            setData(null)
            setErr('')
            void load('')
          }}
        />
        <SaveBtn
          label="Export CSV"
          onClick={exportCsv}
          disabled={!data?.rows?.length}
        />
      </div>
      {data ? (
        <>
          {/* Optional chaining is not decoration here: an answer without a
              summary threw while React was rendering, which unmounts the whole
              tree -- nav bar included -- and leaves a white window that only a
              restart recovers. A ledger with no totals should show zeroes. */}
          <Note>
            Opening ₹{money(data.summary?.opening)} · Debits ₹
            {money(data.summary?.debits)} · Credits ₹
            {money(data.summary?.credits)} · Closing ₹
            {money(data.summary?.closing)}
          </Note>
          <div
            ref={ledgerTableRef}
            className="settings-table-wrap"
            tabIndex={-1}
            data-ledger-focus="table"
          >
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Particulars</th>
                  <th>Debit</th>
                  <th>Credit</th>
                  <th>Balance</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.date}</td>
                    <td>{r.particulars}</td>
                    <td>{money(r.debit)}</td>
                    <td>{money(r.credit)}</td>
                    <td>{money(r.balance)}</td>
                  </tr>
                ))}
                <tr>
                  <td colSpan={2}>
                    <strong>Summary</strong>
                  </td>
                  <td>
                    <strong>{money(data.summary.debits)}</strong>
                  </td>
                  <td>
                    <strong>{money(data.summary.credits)}</strong>
                  </td>
                  <td>
                    <strong>{money(data.summary.closing)}</strong>
                  </td>
                </tr>
                {!data.rows.length ? (
                  <tr>
                    <td colSpan={5}>No ledger rows</td>
                  </tr>
                ) : null}
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
          <Field label="Medicine">
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
          </Field>
          <div className="settings-inline-actions">
            <Field label="Pack">
              <input
                className="settings-input"
                value={pickPack}
                onChange={(e) => setPickPack(e.target.value)}
              />
            </Field>
            <Field label="Qty">
              <input
                className="settings-input"
                type="number"
                value={pickQty}
                onChange={(e) => setPickQty(e.target.value)}
              />
            </Field>
            <SaveBtn
              label="Add line"
              disabled={busy}
              onClick={() => void addLine()}
            />
          </div>
          <div className="settings-table-wrap">
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Medicine</th>
                  <th>Pack</th>
                  <th>Qty</th>
                  <th>Rate</th>
                  <th>Stock</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {lines.map((ln, i) => (
                  <tr key={`${ln.medicine_name}-${i}`}>
                    <td>{ln.medicine_name}</td>
                    <td>{ln.pack_size}</td>
                    <td>
                      <input
                        className="settings-input"
                        type="number"
                        value={ln.quantity}
                        onChange={(e) =>
                          updateLine(i, 'quantity', e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        className="settings-input"
                        type="number"
                        value={ln.unit_price}
                        onChange={(e) =>
                          updateLine(i, 'unit_price', e.target.value)
                        }
                      />
                    </td>
                    <td>{ln.current_stock ?? '—'}</td>
                    <td>
                      <SaveBtn label="Remove" onClick={() => removeLine(i)} />
                    </td>
                  </tr>
                ))}
                {!lines.length ? (
                  <tr>
                    <td colSpan={6}>No lines yet</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
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
          <label className="settings-check">
            <input
              type="checkbox"
              checked={offline}
              onChange={(e) => setOffline(e.target.checked)}
            />
            Offline order (no supplier record)
          </label>
          <div className="settings-inline-actions">
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
      {err ? <p className="error">{err}</p> : null}
      {msg ? <Note>{msg}</Note> : null}

      {mode === 'pending' && (
        <Frame title="Pending Orders">
          <SaveBtn label="Refresh" onClick={() => void refreshList()} />
          <div className="settings-table-wrap">
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Group / Supplier</th>
                  <th>Status</th>
                  <th>Items</th>
                  <th>Actions</th>
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
                    <td>{String(g.status || '')}</td>
                    <td>
                      {String(
                        g.medicines_preview ||
                          g.item_count ||
                          g.count ||
                          '',
                      )}
                    </td>
                    <td>
                      <div className="settings-inline-actions">
                        <SaveBtn
                          label="Edit"
                          disabled={busy || received}
                          onClick={() => void editGroup(groupId)}
                        />
                        <SaveBtn
                          label="Open Purchase"
                          disabled={busy || received}
                          onClick={() => void openPurchaseForGroup(g)}
                        />
                        <SaveBtn
                          label="Mark Received"
                          disabled={busy || received}
                          onClick={() => void markGroupReceived(groupId)}
                        />
                        <SaveBtn
                          label="Cancel"
                          disabled={busy || received}
                          onClick={() => void cancelGroup(groupId)}
                        />
                      </div>
                    </td>
                  </tr>
                  )
                })}
                {!reorder.groups?.length ? (
                  <tr>
                    <td colSpan={4}>No pending reorder groups</td>
                  </tr>
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
            <div className="settings-inline-actions">
              <SaveBtn
                label="Create supplier-grouped drafts"
                saving={busy}
                onClick={() => void createGrouped()}
              />
              <SaveBtn
                label="Bulk load tabs"
                saving={busy}
                onClick={() => void bulkLoadTabs()}
              />
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
        <Frame title="Reorder Defaults">
          <Note>
            Default order quantity used when creating reorder drafts. You can
            also edit under Layout &amp; Lists → Thresholds.
          </Note>
          <Field label="Default reorder qty">
            <input
              className="settings-input"
              value={defaultQty}
              onChange={(e) => setDefaultQty(e.target.value)}
            />
          </Field>
          <div className="settings-inline-actions">
            <SaveBtn
              label="Save Default Qty"
              saving={busy}
              onClick={() => void saveDefaults()}
            />
            {onOpenDefaults ? (
              <button
                type="button"
                className="settings-link-btn"
                onClick={onOpenDefaults}
              >
                Open Layout → Thresholds
              </button>
            ) : null}
          </div>
        </Frame>
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

  return (
    <>
      <PanelTitle>Shelf Management</PanelTitle>
      {err ? <p className="error">{err}</p> : null}
      {shelf.error ? <p className="error">{shelf.error}</p> : null}
      <Check
        label="Show location on medicine lists"
        checked={Boolean(shelf.show_location)}
        onChange={(v) =>
          void run({ action: 'set_show_location', show_location: v })
        }
      />

      <div className="settings-split">
        <div className="settings-split-pane">
            <Frame title="Shelf Structure">
              <Field label="Name">
                <input
                  className="settings-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <div className="settings-inline-actions">
                <SaveBtn
                  label="+ Rack"
                  onClick={() => run({ action: 'add_rack', name })}
                />
                <Field label="Under rack">
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
                </Field>
                <SaveBtn
                  label="+ Section"
                  disabled={!rackId}
                  onClick={() =>
                    run({ action: 'add_section', rack_id: rackId, name })
                  }
                />
                <Field label="Under section">
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
                </Field>
                <SaveBtn
                  label="+ Box"
                  disabled={!secId}
                  onClick={() =>
                    run({ action: 'add_box', section_id: secId, name })
                  }
                />
              </div>

              <ul className="settings-tree">
                {shelf.racks.map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      className="settings-link-btn"
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
                    </button>{' '}
                    <button
                      type="button"
                      className="settings-link-btn"
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
                    </button>{' '}
                    <button
                      type="button"
                      className="settings-link-btn"
                      onClick={() => {
                        if (window.confirm(`Delete rack ${r.name}?`))
                          void run({ action: 'delete_rack', id: r.id })
                      }}
                    >
                      Delete
                    </button>
                    <ul>
                      {r.sections.map((s) => (
                        <li key={s.id}>
                          <button
                            type="button"
                            className="settings-link-btn"
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
                          </button>{' '}
                          <button
                            type="button"
                            className="settings-link-btn"
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
                          </button>{' '}
                          <button
                            type="button"
                            className="settings-link-btn"
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
                          <ul>
                            {s.boxes.map((b) => (
                              <li key={b.id}>
                                <button
                                  type="button"
                                  className="settings-link-btn"
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
                                </button>{' '}
                                <button
                                  type="button"
                                  className="settings-link-btn"
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
                                </button>{' '}
                                <button
                                  type="button"
                                  className="settings-link-btn"
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
                              </li>
                            ))}
                          </ul>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
                {!shelf.racks.length ? <li>No racks yet</li> : null}
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

          <div className="settings-split-pane">
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
                  <h4>Assigned</h4>
                  <div className="settings-table-wrap">
                    <table className="settings-table">
                      <thead>
                        <tr>
                          <th>Medicine</th>
                          <th>Batch</th>
                          <th>Location</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {assignedAt.map((m) => (
                          <tr key={m.id}>
                            <td>{m.name}</td>
                            <td>{m.batch}</td>
                            <td>{m.location}</td>
                            <td>
                              <button
                                type="button"
                                className="settings-link-btn"
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
                          <tr>
                            <td colSpan={4}>None assigned here</td>
                          </tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                  <h4>Unassigned</h4>
                  <div className="settings-table-wrap">
                    <table className="settings-table">
                      <thead>
                        <tr>
                          <th>Medicine</th>
                          <th>Batch</th>
                          <th>Stock</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {(shelf.unassigned || []).slice(0, 200).map((m) => (
                          <tr key={m.id}>
                            <td>{m.name}</td>
                            <td>{m.batch}</td>
                            <td>{m.stock}</td>
                            <td>
                              <button
                                type="button"
                                className="settings-link-btn"
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
                          <tr>
                            <td colSpan={4}>No unassigned medicines</td>
                          </tr>
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

  return (
    <>
      <PanelTitle>Thresholds &amp; Reorder</PanelTitle>
      {err ? <p className="error">{err}</p> : null}
      {msg ? <Note>{msg}</Note> : null}
      <h4>Low Stock</h4>
      <div className="settings-grid-3">
        {local.med_types.map((mt) => {
          const key = mt.toLowerCase()
          return (
            <Field key={`low-${mt}`} label={mt}>
              <input
                className="settings-input"
                value={local.low_stock[key] ?? '10'}
                onChange={(e) =>
                  setLocal((t) => ({
                    ...t,
                    low_stock: { ...t.low_stock, [key]: e.target.value },
                  }))
                }
              />
            </Field>
          )
        })}
      </div>
      <h4>Near Expiry (months)</h4>
      <div className="settings-grid-3">
        {local.med_types.map((mt) => {
          const key = mt.toLowerCase()
          return (
            <Field key={`near-${mt}`} label={mt}>
              <input
                className="settings-input"
                value={local.near_expiry[key] ?? '3'}
                onChange={(e) =>
                  setLocal((t) => ({
                    ...t,
                    near_expiry: { ...t.near_expiry, [key]: e.target.value },
                  }))
                }
              />
            </Field>
          )
        })}
      </div>
      <Field label="Minimum Due Amount (Rs)">
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
      <Field label="Minimum Due Days">
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
      <SaveBtn label="Save Thresholds" saving={saving} onClick={save} />
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
      {SHORTCUT_SECTIONS.map((sec) => (
        <Frame key={sec.title} title={sec.title}>
          <div className="settings-table-wrap">
            <table className="settings-table">
              <tbody>
                {sec.rows.map(([key, desc], i) => (
                  <tr key={`${sec.title}-${i}`}>
                    <td style={{ fontWeight: 600, width: '22%' }}>
                      {key || '—'}
                    </td>
                    <td>{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Frame>
      ))}
    </>
  )
}
