/** Settings → Layout & Lists → Page Summaries: for every page with a summary, "Show summary"
 *  and a tick per figure. The same choice as the small button beside each summary, kept for
 *  the whole shop (pageSummaries.ts). Hiding only hides on screen. */

import { useEffect, useState } from 'react'
import {
  SUMMARY_PAGES,
  isFieldShown,
  isSummaryShown,
  loadPageSummaries,
  savePageSummaries,
  usePageSummaries,
  type PageSummaryChange,
  type PageSummaryPrefs,
} from '../../pageSummaries'
import { Check, Frame, Note, PanelTitle, SaveBtn } from './SettingsChrome'

type Draft = Record<string, { show: boolean; on: Record<string, boolean> }>

function draftOf(prefs: PageSummaryPrefs | null): Draft {
  const out: Draft = {}
  for (const p of SUMMARY_PAGES) {
    const on: Record<string, boolean> = {}
    for (const f of p.fields) on[f.key] = isFieldShown(prefs, p.page, f.key)
    out[p.page] = { show: isSummaryShown(prefs, p.page), on }
  }
  return out
}

export function PageSummariesPanel() {
  const prefs = usePageSummaries()
  const [draft, setDraft] = useState<Draft>(() => draftOf(prefs))
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  // Read fresh when the panel opens: another PC or the phone may have changed it.
  useEffect(() => {
    void loadPageSummaries(true)
  }, [])
  useEffect(() => {
    if (!dirty) setDraft(draftOf(prefs))
  }, [prefs, dirty])

  const change = (page: string, next: Partial<Draft[string]>) => {
    setDraft((d) => ({ ...d, [page]: { ...d[page], ...next } }))
    setDirty(true)
    setMsg('')
  }

  const save = async () => {
    setSaving(true)
    setErr('')
    setMsg('')
    try {
      const body: PageSummaryChange = {}
      for (const [page, d] of Object.entries(draft)) body[page] = { hidden: !d.show, on: d.on }
      await savePageSummaries(body)
      setDirty(false)
      setMsg('Saved. Every computer and phone of the shop shows the same.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save. Try again.')
    } finally {
      setSaving(false)
    }
  }

  const showAll = () => {
    const d = draftOf(null)
    setDraft(d)
    setDirty(true)
    setMsg('')
  }

  return (
    <>
      <PanelTitle>Page Summaries</PanelTitle>
      <Note>
        Choose what each page's summary shows: untick a figure to hide it, or untick Show summary to
        hide the whole summary. It only hides it on screen — bills, prints and exports stay the same.
        The same on every computer and phone of the shop. The small gear beside each summary does the
        same.
      </Note>
      {SUMMARY_PAGES.map((p) => {
        const d = draft[p.page]
        if (!d) return null
        return (
          <Frame key={p.page} title={p.label}>
            <Check
              label="Show summary"
              checked={d.show}
              onChange={(v) => change(p.page, { show: v })}
            />
            <div className="settings-check-grid-3">
              {p.fields.map((f) => (
                <Check
                  key={f.key}
                  label={f.label}
                  checked={d.on[f.key] !== false}
                  disabled={!d.show}
                  onChange={(v) => change(p.page, { on: { ...d.on, [f.key]: v } })}
                />
              ))}
            </div>
          </Frame>
        )
      })}
      {err ? <p className="settings-error">{err}</p> : null}
      {msg ? <Note>{msg}</Note> : null}
      <div className="settings-inline-actions">
        <SaveBtn label="Save Page Summaries" saving={saving} onClick={() => void save()} disabled={!dirty} />
        <button type="button" className="settings-action-btn" onClick={showAll} disabled={saving}>
          Show everything
        </button>
      </div>
    </>
  )
}
