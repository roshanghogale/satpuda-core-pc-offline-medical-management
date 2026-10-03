/** A customer's GSTIN, for GSTR-1 B2B (core/customer_gst.py). Shown while a customer is open for
 *  Edit, saved on its own: the customer's ordinary Save is not touched. "Kadhi pasun" is the first
 *  bill date it applies to -- bills before it went out without it and stay B2C.
 */
import { useEffect, useState } from 'react'
import { getApiBase } from '../../api'

type Gst = { gstin: string; legal_name: string; since: string; state_name: string }

export function CustomerGstRow({ customerId, customerName }: { customerId: number; customerName: string }) {
  const [gst, setGst] = useState<Gst>({ gstin: '', legal_name: '', since: '', state_name: '' })
  const [saved, setSaved] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    setErr('')
    setSaved('')
    fetch(`${getApiBase()}/api/customers/gst?customer_id=${customerId}`)
      .then((r) => r.json())
      .then((d) => {
        if (!live) return
        if (d && d.ok !== false) {
          setGst({ gstin: d.gstin || '', legal_name: d.legal_name || '', since: d.since || '', state_name: d.state_name || '' })
          setSaved(d.gstin || '')
        }
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [customerId])

  async function save() {
    setBusy(true)
    setErr('')
    try {
      const res = await fetch(`${getApiBase()}/api/customers/gst`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          customer_id: customerId,
          gstin: gst.gstin,
          legal_name: gst.legal_name || customerName,
          since: gst.since,
        }),
      })
      const d = await res.json().catch(() => ({}))
      if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
      setGst({ gstin: d.gstin || '', legal_name: d.legal_name || '', since: d.since || '', state_name: d.state_name || '' })
      setSaved(d.gstin || '')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="settings-form-row" style={{ alignItems: 'flex-end' }}>
      <label className="settings-field">
        <span>GSTIN (B2B bill sathi)</span>
        <input
          className="settings-input"
          value={gst.gstin}
          maxLength={15}
          placeholder="27ABCDE1234F1Z5"
          onChange={(e) => setGst((g) => ({ ...g, gstin: e.target.value.toUpperCase() }))}
        />
      </label>
      <label className="settings-field">
        <span>Kadhi pasun (bill tarikh)</span>
        <input
          className="settings-input"
          type="date"
          value={gst.since}
          onChange={(e) => setGst((g) => ({ ...g, since: e.target.value }))}
        />
      </label>
      <button type="button" className="settings-action-btn" disabled={busy} onClick={() => void save()}>
        {busy ? 'Save hot aahe…' : gst.gstin ? 'GSTIN save kara' : saved ? 'GSTIN kadha' : 'GSTIN save kara'}
      </button>
      {err ? <span className="vb-warn">{err}</span> : null}
      {!err && saved ? (
        <span className="settings-hint">
          {saved} · {gst.state_name} · {gst.since} pasunchi bills GSTR-1 B2B madhe
        </span>
      ) : null}
    </div>
  )
}
