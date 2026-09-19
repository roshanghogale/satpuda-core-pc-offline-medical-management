import { useEffect, useState } from 'react'
import { fetchStartupAlerts } from '../../pagesApi'
import {
  fetchAlertPrefs,
  saveAlertPrefs,
  saveSettingsSection,
  type AlertCategoryKey,
  type AlertPopupPrefs,
  type SettingsBundle,
} from '../../settingsApi'
import { Check, Field, Frame, Note, PanelTitle, SaveBtn } from './SettingsChrome'

type Thresholds = NonNullable<SettingsBundle['thresholds']>

/**
 * Settings -> Alert & Monitoring -> Popup & Thresholds.
 *
 * Every control here is a pref the popup logic reads
 * (core/startup_alerts_prefs.py, core/alert_thresholds.py via the settings
 * table) -- nothing decorative.
 */
const CATEGORIES: { key: AlertCategoryKey; label: string; hint: string }[] = [
  {
    key: 'low_stock',
    label: 'Low stock',
    hint: 'In stock, but the total of all batches is below the minimum set for its type.',
  },
  {
    key: 'out_of_stock',
    label: 'Out of stock',
    hint: 'Every batch of that medicine and pack size is at zero.',
  },
  {
    key: 'near_expiry',
    label: 'Near expiry',
    hint: 'In stock and expiring within the months set for its type. A month-only expiry (MM/YY) counts to the month end.',
  },
  {
    key: 'expired',
    label: 'Expired',
    hint: 'In stock and past its expiry. Sold-out batches are not listed.',
  },
  {
    key: 'customer_due',
    label: 'Customer dues',
    hint: 'Customers still owing at least the minimum amount below (and, if set, with a bill at least that many days old).',
  },
]

export function AlertPrefsPanel({
  counts,
  onThresholdsChange,
}: {
  counts?: Record<string, number>
  onThresholdsChange?: (t: Thresholds) => void
}) {
  const [prefs, setPrefs] = useState<AlertPopupPrefs | null>(null)
  const [thr, setThr] = useState<Thresholds | null>(null)
  const [recheck, setRecheck] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  function adopt(p: AlertPopupPrefs) {
    setPrefs(p)
    setRecheck(String(p.recheck_minutes))
    if (p.thresholds) setThr(p.thresholds)
    window.dispatchEvent(
      new CustomEvent('satpuda:alert-prefs-changed', {
        detail: { recheck_minutes: p.recheck_minutes },
      }),
    )
  }

  useEffect(() => {
    let cancelled = false
    fetchAlertPrefs()
      .then((p) => {
        if (!cancelled) adopt(p)
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function savePrefs(body: Record<string, unknown>, note: string) {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      adopt(await saveAlertPrefs(body))
      setMsg(note)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function saveThresholds() {
    if (!thr) return
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const next = (await saveSettingsSection(
        'thresholds',
        thr as unknown as Record<string, unknown>,
      )) as unknown as Thresholds
      setThr(next)
      onThresholdsChange?.(next)
      setMsg('Thresholds saved. Inventory, the alert lists and the popup use them now.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function testNow() {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await fetchStartupAlerts({ force: true })
      if (!res.ok) {
        setErr(res.error || 'Could not read the alerts.')
      } else if (!res.tabs?.length) {
        setMsg('Nothing to show right now for the categories ticked below.')
      } else {
        window.dispatchEvent(
          new CustomEvent('satpuda:show-startup-alerts', {
            detail: { tabs: res.tabs, title: 'Startup Alerts (test)' },
          }),
        )
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!prefs) {
    return (
      <>
        <PanelTitle>Popup &amp; Thresholds</PanelTitle>
        {err ? <p className="settings-error">{err}</p> : <p className="muted">Loading…</p>}
      </>
    )
  }

  const setType = (field: 'low_stock' | 'near_expiry', key: string, v: string) =>
    setThr((t) => (t ? { ...t, [field]: { ...t[field], [key]: v } } : t))

  return (
    <>
      <PanelTitle>Popup &amp; Thresholds</PanelTitle>
      <Note>
        What the alert popup shows, when it shows, and the limits every alert is
        judged by. The same numbers drive Inventory&apos;s status column.
      </Note>
      {err ? <p className="settings-error">{err}</p> : null}
      {msg ? <p className="settings-msg">{msg}</p> : null}

      <Frame title="Startup popup">
        <Check
          label="Show alerts when the app opens"
          checked={prefs.enabled}
          onChange={(v) =>
            void savePrefs(
              { enabled: v },
              v ? 'Startup popup turned on.' : 'Startup popup turned off.',
            )
          }
        />
        <Note>
          One window with a tab per category ticked below. &quot;Skip Today&quot;
          in the popup silences it until tomorrow.
        </Note>
        {prefs.snoozed_today ? (
          <div className="settings-inline-actions">
            <Note>Skipped for today.</Note>
            <SaveBtn
              label="Show again today"
              saving={busy}
              onClick={() =>
                void savePrefs({ clear_snooze: true }, 'Skip Today cleared.')
              }
            />
          </div>
        ) : null}
        <div className="settings-inline-actions">
          <SaveBtn label="Test the popup now" saving={busy} onClick={() => void testNow()} />
        </div>
        <Note>
          Opens the popup with today&apos;s data right now, even when it is
          turned off or skipped.
        </Note>
      </Frame>

      <Frame title="What the popup includes">
        {CATEGORIES.map((c) => (
          <div key={c.key}>
            <Check
              label={
                counts && typeof counts[c.key] === 'number'
                  ? `${c.label} (${counts[c.key]} now)`
                  : c.label
              }
              checked={prefs.categories[c.key] !== false}
              onChange={(v) =>
                void savePrefs(
                  { categories: { [c.key]: v } },
                  `${c.label} ${v ? 'included in' : 'left out of'} the popup.`,
                )
              }
            />
            <Note>{c.hint}</Note>
          </div>
        ))}
      </Frame>

      <Frame title="While the app is open">
        <Field label="Check again every (minutes)">
          <input
            className="settings-input"
            type="number"
            min={0}
            max={1440}
            step={1}
            value={recheck}
            onChange={(e) => setRecheck(e.target.value)}
          />
        </Field>
        <Note>
          0 = only when the app opens. Otherwise the popup comes back only for
          rows not already shown today, such as a batch that just went low or
          crossed into near expiry. It never repeats what you have seen.
        </Note>
        <SaveBtn
          label="Save interval"
          saving={busy}
          onClick={() =>
            void savePrefs(
              { recheck_minutes: recheck === '' ? 0 : Number(recheck) },
              'Re-check interval saved.',
            )
          }
        />
      </Frame>

      {thr ? (
        <Frame title="Thresholds (per medicine type)">
          <Note>
            Low stock: alert when the total stock of a medicine (all batches, in
            the same units as Inventory) is below this number. Near expiry:
            alert this many months before expiry.
          </Note>
          <div className="settings-table-wrap">
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Medicine type</th>
                  <th>Low stock below (units)</th>
                  <th>Near expiry within (months)</th>
                </tr>
              </thead>
              <tbody>
                {thr.med_types.map((mt) => {
                  const key = mt.toLowerCase()
                  return (
                    <tr key={mt}>
                      <td>{mt}</td>
                      <td>
                        <input
                          className="settings-input"
                          type="number"
                          min={0}
                          step={1}
                          value={thr.low_stock[key] ?? '10'}
                          onChange={(e) => setType('low_stock', key, e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          className="settings-input"
                          type="number"
                          min={0}
                          step={1}
                          value={thr.near_expiry[key] ?? '3'}
                          onChange={(e) => setType('near_expiry', key, e.target.value)}
                        />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <Field label="Customer due: minimum amount (Rs)">
            <input
              className="settings-input"
              type="number"
              min={0}
              value={thr.customer_due_min_amount}
              onChange={(e) =>
                setThr((t) => (t ? { ...t, customer_due_min_amount: e.target.value } : t))
              }
            />
          </Field>
          <Note>Balances smaller than this are not listed. 0 = any amount owed.</Note>
          <Field label="Customer due: minimum age (days)">
            <input
              className="settings-input"
              type="number"
              min={0}
              step={1}
              value={thr.customer_due_min_days}
              onChange={(e) =>
                setThr((t) => (t ? { ...t, customer_due_min_days: e.target.value } : t))
              }
            />
          </Field>
          <Note>Only customers with an unpaid bill at least this old. 0 = any age.</Note>
          <SaveBtn label="Save thresholds" saving={busy} onClick={() => void saveThresholds()} />
          <Note>Also editable in Layout &amp; Lists → Thresholds; both edit the same values.</Note>
        </Frame>
      ) : null}
    </>
  )
}
