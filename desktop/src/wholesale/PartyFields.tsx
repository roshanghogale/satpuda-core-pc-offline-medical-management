/** A customer's wholesale party fields (Settings -> Contacts -> Customers, wholesale mode only):
 *  Party type, GSTIN, DL numbers, State (from the GSTIN's first two digits when there is one),
 *  Rate tier A / B / C, Credit limit, Credit days. Saved with the customer (core/wholesale_party.py);
 *  the GSTIN goes where it always lived, core/customer_gst.py.
 */
import './wholesale.css'

export type PartyForm = {
  party_type: 'retail' | 'medical'
  gstin: string
  dl_numbers: string
  state_code: string
  rate_tier: 'A' | 'B' | 'C'
  credit_limit: string
  credit_days: string
}

export const EMPTY_PARTY: PartyForm = {
  party_type: 'retail',
  gstin: '',
  dl_numbers: '',
  state_code: '',
  rate_tier: 'A',
  credit_limit: '',
  credit_days: '',
}

/** GST state codes (core/customer_gst.STATES). */
export const GST_STATES: Record<string, string> = {
  '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab', '04': 'Chandigarh',
  '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
  '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
  '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam', '19': 'West Bengal',
  '20': 'Jharkhand', '21': 'Odisha', '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
  '26': 'Dadra and Nagar Haveli and Daman and Diu', '27': 'Maharashtra', '29': 'Karnataka',
  '30': 'Goa', '31': 'Lakshadweep', '32': 'Kerala', '33': 'Tamil Nadu', '34': 'Puducherry',
  '35': 'Andaman and Nicobar Islands', '36': 'Telangana', '37': 'Andhra Pradesh', '38': 'Ladakh',
  '97': 'Other Territory',
}

/** The state a GSTIN belongs to ('' when it does not start with a known code). */
export function gstinState(gstin: string): string {
  const code = String(gstin || '').trim().slice(0, 2)
  return /^\d\d$/.test(code) && GST_STATES[code] ? code : ''
}

/** A contacts-list row -> the form. */
export function partyFromRow(row: Record<string, unknown> | null | undefined): PartyForm {
  const r = row || {}
  const tier = String(r.rate_tier || 'A').toUpperCase()
  const limit = Number(r.credit_limit || 0)
  const days = Number(r.credit_days || 0)
  return {
    party_type: r.party_type === 'medical' ? 'medical' : 'retail',
    gstin: String(r.gstin || ''),
    dl_numbers: String(r.dl_numbers || ''),
    state_code: String(r.state_code || ''),
    rate_tier: tier === 'B' || tier === 'C' ? tier : 'A',
    credit_limit: limit > 0 ? String(limit) : '',
    credit_days: days > 0 ? String(days) : '',
  }
}

/** The form -> the fields the customer save sends. */
export function partyPayload(p: PartyForm): Record<string, unknown> {
  const gstin = p.gstin.replace(/[^0-9A-Za-z]/g, '').toUpperCase()
  return {
    party_type: p.party_type,
    gstin,
    dl_numbers: p.dl_numbers.trim(),
    state_code: gstinState(gstin) || p.state_code,
    rate_tier: p.rate_tier,
    credit_limit: p.credit_limit.trim() === '' ? 0 : Number(p.credit_limit),
    credit_days: p.credit_days.trim() === '' ? 0 : Number(p.credit_days),
  }
}

export function PartyFields({
  value,
  onChange,
  navChain,
  navStart,
}: {
  value: PartyForm
  onChange: (next: PartyForm) => void
  navChain?: string
  navStart?: number
}) {
  const set = <K extends keyof PartyForm>(k: K, v: PartyForm[K]) => onChange({ ...value, [k]: v })
  const fromGstin = gstinState(value.gstin)
  const state = fromGstin || value.state_code
  let order = navStart ?? 0
  const nav = () => (navChain && navStart != null ? { 'data-nav-chain': navChain, 'data-nav-order': order++ } : {})
  return (
    <div className="ws-party">
      <label className="iset-field">
        <span>Party type</span>
        <select
          className="settings-input"
          value={value.party_type}
          onChange={(e) => set('party_type', e.target.value === 'medical' ? 'medical' : 'retail')}
          {...nav()}
        >
          <option value="retail">Retail</option>
          <option value="medical">Medical</option>
        </select>
      </label>
      <label className="iset-field">
        <span>GSTIN</span>
        <input
          className="settings-input"
          value={value.gstin}
          maxLength={15}
          placeholder="27ABCDE1234F1Z5"
          onChange={(e) => set('gstin', e.target.value.toUpperCase())}
          {...nav()}
        />
      </label>
      <label className="iset-field">
        <span>DL numbers</span>
        <input
          className="settings-input"
          value={value.dl_numbers}
          placeholder="20B: ..., 21B: ..."
          onChange={(e) => set('dl_numbers', e.target.value)}
          {...nav()}
        />
      </label>
      <label className="iset-field">
        <span>State{fromGstin ? ' (from GSTIN)' : ''}</span>
        <select
          className="settings-input"
          value={state}
          disabled={!!fromGstin}
          onChange={(e) => set('state_code', e.target.value)}
          {...nav()}
        >
          <option value="">—</option>
          {Object.entries(GST_STATES).sort(([a], [b]) => a.localeCompare(b)).map(([code, name]) => (
            <option key={code} value={code}>
              {code} {name}
            </option>
          ))}
        </select>
      </label>
      <label className="iset-field">
        <span>Rate tier</span>
        <select
          className="settings-input ws-narrow"
          value={value.rate_tier}
          onChange={(e) => set('rate_tier', (e.target.value as PartyForm['rate_tier']) || 'A')}
          {...nav()}
        >
          <option value="A">A</option>
          <option value="B">B</option>
          <option value="C">C</option>
        </select>
      </label>
      <label className="iset-field">
        <span>Credit limit (₹)</span>
        <input
          className="settings-input ws-narrow"
          inputMode="decimal"
          value={value.credit_limit}
          placeholder="no limit"
          onChange={(e) => set('credit_limit', e.target.value.replace(/[^0-9.]/g, ''))}
          {...nav()}
        />
      </label>
      <label className="iset-field">
        <span>Credit days</span>
        <input
          className="settings-input ws-narrow"
          inputMode="numeric"
          value={value.credit_days}
          placeholder="0"
          onChange={(e) => set('credit_days', e.target.value.replace(/[^0-9]/g, ''))}
          {...nav()}
        />
      </label>
    </div>
  )
}
