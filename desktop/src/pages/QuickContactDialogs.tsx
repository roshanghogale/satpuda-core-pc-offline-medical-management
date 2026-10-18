/** The popups the Sales (wholesale) and Purchase screens open to add or edit a party /
 *  supplier without leaving the bill (owner, 18 Oct 2026).
 *
 *  Typing a name that is not saved opens NOTHING; only Enter on such a name in the Party /
 *  Supplier box opens the popup, and the ✎ beside a picked one opens it to edit. Save goes
 *  through the same engine call Settings -> Contacts uses (PUT /api/settings/contacts,
 *  settingsApi.mutateContact); Esc closes it and leaves the typed text alone.
 *
 *  Keys: Enter walks the boxes (the page's own Enter chain, chain `quick-contact`), Enter on
 *  the last box or on Save saves, Esc cancels. While the popup is open the page behind it
 *  does not get F-keys (F5 would otherwise save the bill under it). */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { mutateContact } from '../settingsApi'
import { EMPTY_PARTY, PartyFields, partyPayload, type PartyForm } from '../wholesale/PartyFields'
import '../styles/interactive-settings.css'

const CHAIN = 'quick-contact'
const SAVE_ACTION = 'quick-contact-save'

type Contacts = Awaited<ReturnType<typeof mutateContact>>

function focusFirst(root: HTMLElement | null) {
  const el = root?.querySelector<HTMLElement>(`[data-nav-chain="${CHAIN}"][data-nav-order]`)
  el?.focus()
  if (el instanceof HTMLInputElement) {
    try {
      el.select()
    } catch {
      /* ignore */
    }
  }
}

function QuickDialog({
  title,
  busy,
  error,
  saveLabel,
  onSave,
  onCancel,
  children,
}: {
  title: string
  busy: boolean
  error: string
  saveLabel: string
  onSave: () => void
  onCancel: () => void
  children: ReactNode
}) {
  const cardRef = useRef<HTMLDivElement | null>(null)
  const saveRef = useRef(onSave)
  saveRef.current = onSave
  const cancelRef = useRef(onCancel)
  cancelRef.current = onCancel

  useEffect(() => {
    const t = window.setTimeout(() => focusFirst(cardRef.current), 30)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        cancelRef.current()
        return
      }
      // F5 / F7 / F9 ... belong to the bill behind the popup: not while it is open.
      if (/^F\d{1,2}$/.test(e.key)) {
        e.preventDefault()
        e.stopPropagation()
      }
    }
    const onNav = (e: Event) => {
      const action = (e as CustomEvent<{ action?: string }>).detail?.action
      if (action === SAVE_ACTION && cardRef.current?.contains(e.target as Node)) saveRef.current()
    }
    window.addEventListener('keydown', onKey, true)
    document.addEventListener('satpuda-nav-action', onNav)
    return () => {
      window.clearTimeout(t)
      window.removeEventListener('keydown', onKey, true)
      document.removeEventListener('satpuda-nav-action', onNav)
    }
  }, [])

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={() => cancelRef.current()}>
      <div
        ref={cardRef}
        className="modal-card modal-card-form quick-contact-card"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>{title}</h2>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onCancel}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          {children}
          {error ? (
            <p className="page-error quick-contact-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="modal-foot">
          <span className="quick-contact-keys">Enter next · Esc cancel</span>
          <button type="button" className="btn btn-neutral" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            data-nav-chain={CHAIN}
            data-nav-order={99}
            data-nav-action={SAVE_ACTION}
            onClick={onSave}
          >
            {busy ? 'Saving…' : saveLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

function Box({
  label,
  value,
  onChange,
  order,
  wide,
  mono,
  last,
  placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  order: number
  wide?: boolean
  mono?: boolean
  last?: boolean
  placeholder?: string
}) {
  return (
    <label className={`iset-field${wide ? ' quick-contact-wide' : ''}`}>
      <span>{label}</span>
      <input
        type="text"
        className={`settings-input${mono ? ' mono' : ''}`}
        value={value}
        placeholder={placeholder}
        data-nav-chain={CHAIN}
        data-nav-order={order}
        data-nav-enter={last ? SAVE_ACTION : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  )
}

/** The id of the contact named `name` in the list the save answered with. */
function idByName(list: { id: number; name: string }[] | undefined, name: string): number | null {
  const want = name.trim().toUpperCase()
  const row = (list || []).find((c) => String(c.name || '').trim().toUpperCase() === want)
  return row ? Number(row.id) || null : null
}

/* ─── Wholesale party (a customer with the Contacts wholesale fields) ─────────────── */

export type PartyDialogState = {
  /** null = a new party; otherwise the customer being edited. */
  id: number | null
  name: string
  phone: string
  address: string
  party: PartyForm
}

export function newPartyState(name: string): PartyDialogState {
  return { id: null, name: name.trim().toUpperCase(), phone: '', address: '', party: { ...EMPTY_PARTY, party_type: 'medical' } }
}

export function PartyQuickDialog({
  state,
  onSaved,
  onCancel,
  findId,
}: {
  state: PartyDialogState
  /** The party is saved: its id (null when it could not be found again) and name. */
  onSaved: (id: number | null, name: string) => void
  onCancel: () => void
  /** Looks the saved name up when the contacts list did not carry it (500-row cap). */
  findId?: (name: string) => Promise<number | null>
}) {
  const [name, setName] = useState(state.name)
  const [phone, setPhone] = useState(state.phone)
  const [address, setAddress] = useState(state.address)
  const [party, setParty] = useState<PartyForm>(state.party)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const busyRef = useRef(false)

  const save = async () => {
    if (busyRef.current) return
    const clean = name.trim()
    if (!clean) {
      setError('Party name is required.')
      return
    }
    busyRef.current = true
    setBusy(true)
    setError('')
    try {
      const fields: Record<string, unknown> = {
        name: clean,
        phone: phone.trim(),
        address: address.trim(),
        ...partyPayload(party),
      }
      const body: Record<string, unknown> = {
        kind: 'customer',
        action: state.id ? 'update' : 'add',
        fields,
        ...fields,
      }
      if (state.id) body.id = state.id
      const next: Contacts = await mutateContact(body)
      let id = state.id ?? idByName(next?.customers, clean)
      if (!id && findId) id = await findId(clean)
      onSaved(id, clean)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  return (
    <QuickDialog
      title={state.id ? 'Edit party' : 'New party'}
      busy={busy}
      error={error}
      saveLabel={state.id ? 'Save Party' : 'Add Party'}
      onSave={() => void save()}
      onCancel={onCancel}
    >
      <div className="quick-contact-grid">
        <Box label="Party name" value={name} onChange={setName} order={1} wide />
        <Box label="Phone" value={phone} onChange={setPhone} order={2} mono />
        <Box label="Address (Village)" value={address} onChange={setAddress} order={3} wide />
      </div>
      <PartyFields value={party} onChange={setParty} navChain={CHAIN} navStart={10} />
      <p className="quick-contact-note">Saved to Contacts → Customers, like a party added there.</p>
    </QuickDialog>
  )
}

/* ─── Purchase supplier ───────────────────────────────────────────────────────────── */

export type SupplierDialogState = {
  id: number | null
  name: string
  phone: string
  address: string
  gstin: string
  dl: string
}

export type SupplierSaved = SupplierDialogState & { id: number | null }

export function SupplierQuickDialog({
  state,
  onSaved,
  onCancel,
}: {
  state: SupplierDialogState
  onSaved: (s: SupplierSaved) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(state.name)
  const [phone, setPhone] = useState(state.phone)
  const [address, setAddress] = useState(state.address)
  const [gstin, setGstin] = useState(state.gstin)
  const [dl, setDl] = useState(state.dl)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const busyRef = useRef(false)

  const save = async () => {
    if (busyRef.current) return
    // Suppliers are stored in capitals (core/name_utils.storage_name_from_entry): the
    // purchase save finds this one again by that name instead of adding a second.
    const clean = name.trim().toUpperCase()
    if (!clean) {
      setError('Supplier name is required.')
      return
    }
    busyRef.current = true
    setBusy(true)
    setError('')
    try {
      const fields: Record<string, unknown> = {
        name: clean,
        phone: phone.trim(),
        gstin: gstin.replace(/\s+/g, '').toUpperCase(),
        address: address.trim(),
      }
      const body: Record<string, unknown> = {
        kind: 'supplier',
        action: state.id ? 'update' : 'add',
        fields,
        ...fields,
      }
      if (state.id) body.id = state.id
      const next: Contacts = await mutateContact(body)
      // DL numbers are not a Contacts field: they go with the purchase save
      // (core/purchase_service.get_or_create_supplier), as they always have.
      onSaved({
        id: state.id ?? idByName(next?.suppliers, clean),
        name: clean,
        phone: String(fields.phone),
        address: String(fields.address),
        gstin: String(fields.gstin),
        dl: dl.trim(),
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  return (
    <QuickDialog
      title={state.id ? 'Edit supplier' : 'New supplier'}
      busy={busy}
      error={error}
      saveLabel={state.id ? 'Save Supplier' : 'Add Supplier'}
      onSave={() => void save()}
      onCancel={onCancel}
    >
      <div className="quick-contact-grid">
        <Box label="Supplier name" value={name} onChange={setName} order={1} wide />
        <Box label="Phone" value={phone} onChange={setPhone} order={2} mono />
        <Box label="Address" value={address} onChange={setAddress} order={3} wide />
        <Box label="GSTIN" value={gstin} onChange={(v) => setGstin(v.toUpperCase())} order={4} mono placeholder="27ABCDE1234F1Z5" />
        <Box label="DL numbers" value={dl} onChange={setDl} order={5} mono last placeholder="20B: ..., 21B: ..." />
      </div>
      <p className="quick-contact-note">
        Saved to Contacts → Suppliers; the DL numbers are kept with the purchase.
      </p>
    </QuickDialog>
  )
}

/* ─── Wholesale bill: Transport / LR / E-way ──────────────────────────────────────── */

export type TransportValue = { transport: string; lrNo: string; ewayNo: string }

export function TransportDialog({
  value,
  onDone,
  onCancel,
}: {
  value: TransportValue
  onDone: (v: TransportValue) => void
  onCancel: () => void
}) {
  const [v, setV] = useState<TransportValue>(value)
  return (
    <QuickDialog
      title="Transport"
      busy={false}
      error=""
      saveLabel="OK"
      onSave={() => onDone({ transport: v.transport.trim(), lrNo: v.lrNo.trim(), ewayNo: v.ewayNo.trim() })}
      onCancel={onCancel}
    >
      <div className="quick-contact-grid">
        <Box label="Transport (optional)" value={v.transport} onChange={(x) => setV({ ...v, transport: x })} order={1} wide />
        <Box label="LR No. (optional)" value={v.lrNo} onChange={(x) => setV({ ...v, lrNo: x })} order={2} mono />
        <Box
          label="E-way Bill No. (optional)"
          value={v.ewayNo}
          onChange={(x) => setV({ ...v, ewayNo: x })}
          order={3}
          mono
          last
        />
      </div>
    </QuickDialog>
  )
}
