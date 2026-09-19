import { useCallback, useContext, useEffect, useState } from 'react'
import { PageActiveContext } from '../../hooks/usePageHotkeys'
import { reorderAction } from '../../settingsApi'
import { Field, Frame, Note, SaveBtn } from './SettingsChrome'

export type ReorderLine = {
  id?: number
  medicine_name: string
  pack_size: string
  quantity: number
  unit_price: number
  current_stock?: number
  min_stock?: number
}

export type ReorderTabState = {
  label: string
  supplier_id?: number | null
  supplier_name: string
  phone: string
  offline: boolean
  offline_note?: string
  delivery?: string
  notes: string
  lines: ReorderLine[]
  editing_group_id?: string | null
}

function emptyTab(label = 'Supplier 1'): ReorderTabState {
  return {
    label,
    supplier_id: null,
    supplier_name: '',
    phone: '',
    offline: false,
    notes: '',
    lines: [],
    editing_group_id: null,
  }
}

type Props = {
  initialTabs?: ReorderTabState[]
  busy: boolean
  setBusy: (v: boolean) => void
  setErr: (v: string) => void
  setMsg: (v: string) => void
  onGroupsChanged?: () => void | Promise<void>
}

export function ReorderMultiTabEditor({
  initialTabs,
  busy,
  setBusy,
  setErr,
  setMsg,
  onGroupsChanged,
}: Props) {
  const [tabs, setTabs] = useState<ReorderTabState[]>(
    initialTabs?.length ? initialTabs : [emptyTab()],
  )
  const [activeIdx, setActiveIdx] = useState(0)

  const pageActive = useContext(PageActiveContext)

  useEffect(() => {
    if (initialTabs?.length) {
      setTabs(initialTabs)
      setActiveIdx(0)
    }
  }, [initialTabs])

  const active = tabs[activeIdx] || tabs[0]

  const patchActive = useCallback(
    (partial: Partial<ReorderTabState>) => {
      setTabs((prev) =>
        prev.map((t, i) => (i === activeIdx ? { ...t, ...partial } : t)),
      )
    },
    [activeIdx],
  )

  function updateLine(
    lineIdx: number,
    field: 'quantity' | 'unit_price',
    value: string,
  ) {
    setTabs((prev) =>
      prev.map((t, ti) =>
        ti !== activeIdx
          ? t
          : {
              ...t,
              lines: t.lines.map((ln, li) =>
                li === lineIdx
                  ? { ...ln, [field]: Number(value) || 0 }
                  : ln,
              ),
            },
      ),
    )
  }

  function removeLine(lineIdx: number) {
    setTabs((prev) =>
      prev.map((t, ti) =>
        ti !== activeIdx
          ? t
          : { ...t, lines: t.lines.filter((_, li) => li !== lineIdx) },
      ),
    )
  }

  function addTab() {
    setTabs((prev) => [...prev, emptyTab(`Supplier ${prev.length + 1}`)])
    setActiveIdx(tabs.length)
  }

  function closeTab(idx: number) {
    if (tabs.length <= 1) return
    setTabs((prev) => prev.filter((_, i) => i !== idx))
    setActiveIdx((cur) => (cur >= idx && cur > 0 ? cur - 1 : cur))
  }

  async function saveTab(status: 'draft' | 'ordered') {
    if (!active?.lines?.length) {
      setErr('Add at least one medicine line.')
      return
    }
    setBusy(true)
    setErr('')
    try {
      const res = await reorderAction({
        action: 'save_supplier_draft',
        status,
        group_id: active.editing_group_id || undefined,
        header: {
          supplier_id: active.supplier_id,
          supplier_name: active.supplier_name,
          supplier_phone: active.phone,
          notes: active.notes,
          order_offline: active.offline,
          editing_group_id: active.editing_group_id,
        },
        lines: active.lines,
      })
      if (!res.ok) {
        setErr(String(res.error || 'Save failed'))
        return
      }
      setMsg(
        status === 'ordered'
          ? `Order saved (${active.label}).`
          : `Draft saved (${active.label}).`,
      )
      await onGroupsChanged?.()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.altKey || e.metaKey) return
      if (e.key === '[') {
        e.preventDefault()
        setActiveIdx((i) => Math.max(0, i - 1))
      }
      if (e.key === ']') {
        e.preventDefault()
        setActiveIdx((i) => Math.min(tabs.length - 1, i + 1))
      }
      if (e.shiftKey && e.key.toLowerCase() === 'n') {
        e.preventDefault()
        addTab()
      }
      if (e.shiftKey && e.key.toLowerCase() === 'w') {
        e.preventDefault()
        closeTab(activeIdx)
      }
    }
    if (!pageActive) return
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [activeIdx, tabs.length, pageActive])

  if (!active) return null

  return (
    <Frame title="Multi-supplier order tabs">
      <Note>
        One tab per supplier — bulk load fills tabs from low-stock candidates.
        Ctrl+Shift+N new tab · Ctrl+Shift+W close · Ctrl+[ / Ctrl+] switch tabs.
      </Note>
      <div className="settings-inline-actions reorder-tab-bar">
        {tabs.map((t, i) => (
          <button
            key={`${t.label}-${i}`}
            type="button"
            className={`settings-toggle-btn${i === activeIdx ? ' active' : ''}`}
            onClick={() => setActiveIdx(i)}
          >
            {t.label || `Tab ${i + 1}`}
          </button>
        ))}
        <button type="button" className="settings-action-btn" onClick={addTab}>
          + Tab
        </button>
        {tabs.length > 1 ? (
          <button
            type="button"
            className="settings-action-btn"
            onClick={() => closeTab(activeIdx)}
          >
            Close tab
          </button>
        ) : null}
      </div>
      <Field label="Supplier name">
        <input
          className="settings-input"
          value={active.supplier_name}
          onChange={(e) => patchActive({ supplier_name: e.target.value })}
        />
      </Field>
      <div className="settings-inline-row">
        <Field label="Phone">
          <input
            className="settings-input"
            value={active.phone}
            onChange={(e) => patchActive({ phone: e.target.value })}
          />
        </Field>
        <Field label="Notes">
          <input
            className="settings-input"
            value={active.notes}
            onChange={(e) => patchActive({ notes: e.target.value })}
          />
        </Field>
      </div>
      <label className="settings-check">
        <input
          type="checkbox"
          checked={active.offline}
          onChange={(e) => patchActive({ offline: e.target.checked })}
        />
        Offline order (no supplier record)
      </label>
      <div className="settings-table-wrap">
        <table className="settings-table">
          <thead>
            <tr>
              <th>Medicine</th>
              <th>Pack</th>
              <th>Qty</th>
              <th>Rate</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {active.lines.map((ln, i) => (
              <tr key={`${ln.medicine_name}-${i}`}>
                <td>{ln.medicine_name}</td>
                <td>{ln.pack_size}</td>
                <td>
                  <input
                    className="settings-input"
                    type="number"
                    value={ln.quantity}
                    onChange={(e) => updateLine(i, 'quantity', e.target.value)}
                  />
                </td>
                <td>
                  <input
                    className="settings-input"
                    type="number"
                    value={ln.unit_price}
                    onChange={(e) => updateLine(i, 'unit_price', e.target.value)}
                  />
                </td>
                <td>
                  <SaveBtn label="Remove" onClick={() => removeLine(i)} />
                </td>
              </tr>
            ))}
            {!active.lines.length ? (
              <tr>
                <td colSpan={5}>No lines in this tab</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <div className="settings-inline-actions">
        <SaveBtn
          label="Save draft"
          saving={busy}
          onClick={() => void saveTab('draft')}
        />
        <SaveBtn
          label="Mark ordered"
          saving={busy}
          onClick={() => void saveTab('ordered')}
        />
      </div>
    </Frame>
  )
}

export async function fetchReorderBulkTabs(): Promise<ReorderTabState[]> {
  const res = await reorderAction({ action: 'bulk_tabs' })
  if (!res.ok) throw new Error(String(res.error || 'Bulk load failed'))
  return (res.tabs as ReorderTabState[]) || []
}

export async function fetchReorderGroupTab(
  groupId: string,
): Promise<ReorderTabState[]> {
  const res = await reorderAction({ action: 'load_group', group_id: groupId })
  if (!res.ok) throw new Error(String(res.error || 'Load group failed'))
  return (res.tabs as ReorderTabState[]) || []
}
