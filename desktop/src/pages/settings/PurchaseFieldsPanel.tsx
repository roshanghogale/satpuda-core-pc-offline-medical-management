/** Settings → Layout & Lists → Purchase Form Fields: a tick per input box of the Purchase screen
 *  (owner, 30 Oct 2026). Kept for the whole shop in the page-summaries row as page
 *  "purchase_form" (pageSummaries.ts PURCHASE_FORM_FIELDS). */

import { useEffect, useState } from 'react'
import {
  PURCHASE_FORM_FIELDS,
  PURCHASE_FORM_PAGE,
  isFieldShown,
  loadPageSummaries,
  savePageSummaries,
  usePageSummaries,
  type PageSummaryPrefs,
} from '../../pageSummaries'
import { Check, Frame, Note, PanelTitle, SaveBtn } from './SettingsChrome'

type Draft = Record<string, boolean>

function draftOf(prefs: PageSummaryPrefs | null): Draft {
  const out: Draft = {}
  for (const f of PURCHASE_FORM_FIELDS) out[f.key] = isFieldShown(prefs, PURCHASE_FORM_PAGE, f.key)
  return out
}

export function PurchaseFieldsPanel() {
  const prefs = usePageSummaries()
  const [draft, setDraft] = useState<Draft>(() => draftOf(prefs))
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    void loadPageSummaries(true)
  }, [])
  useEffect(() => {
    if (!dirty) setDraft(draftOf(prefs))
  }, [prefs, dirty])

  const set = (key: string, on: boolean) => {
    setDraft((d) => ({ ...d, [key]: on }))
    setDirty(true)
    setMsg('')
  }

  const save = async () => {
    setSaving(true)
    setErr('')
    setMsg('')
    try {
      await savePageSummaries({ [PURCHASE_FORM_PAGE]: { hidden: false, on: draft } })
      setDirty(false)
      setMsg('Saved. Every computer of the shop shows the same Purchase screen.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save. Try again.')
    } finally {
      setSaving(false)
    }
  }

  const group = (g: 'supplier' | 'medicine') =>
    PURCHASE_FORM_FIELDS.filter((f) => f.group === g).map((f) => (
      <Check
        key={f.key}
        label={f.fromMaster ? `${f.label} (master)` : f.label}
        checked={draft[f.key] !== false}
        onChange={(v) => set(f.key, v)}
      />
    ))

  return (
    <>
      <PanelTitle>Purchase Form Fields</PanelTitle>
      <Note>
        Untick a box to hide it on the Purchase screen. Supplier name, medicine name and quantity
        always show. On a new medicine line a hidden box counts as empty and is never asked for;
        the ones marked (master) are filled from the master medicine list when the line is added.
        A hidden Batch is saved as "NA" (all such stock of a medicine on one row) and a hidden
        Expiry stays empty, so that stock gets no expiry alert. A line opened
        again from the list keeps its own values. Supplier details that are hidden keep what the
        supplier already has. The same on every computer of the shop.
      </Note>
      <Frame title="Supplier Information">
        <div className="settings-check-grid-3">{group('supplier')}</div>
      </Frame>
      <Frame title="Medicine Details">
        <div className="settings-check-grid-3">{group('medicine')}</div>
      </Frame>
      {err ? <p className="settings-error">{err}</p> : null}
      {msg ? <Note>{msg}</Note> : null}
      <div className="settings-inline-actions">
        <SaveBtn label="Save Purchase Fields" saving={saving} onClick={() => void save()} disabled={!dirty} />
        <button
          type="button"
          className="settings-action-btn"
          disabled={saving}
          onClick={() => {
            const all: Draft = {}
            for (const f of PURCHASE_FORM_FIELDS) all[f.key] = true
            setDraft(all)
            setDirty(true)
            setMsg('')
          }}
        >
          Show every box
        </button>
      </div>
    </>
  )
}
