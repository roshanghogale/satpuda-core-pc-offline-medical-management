/** A list the shop keeps (medicine types, schedules): one card per entry, in order, with
 *  rename in place, move up / down and remove; an Add box on top (Enter adds) that refuses a
 *  name already there. Replaces the one-per-line textareas (owner, 18 Oct 2026: "schedules
 *  and medicine types still look the same"). */
import { useState } from 'react'
import '../../styles/list-editor.css'

export function ListEditor({
  items,
  onChange,
  noun,
  upper = false,
  find = true,
}: {
  items: string[]
  onChange: (items: string[]) => void
  /** "type" / "schedule" -- used in the messages */
  noun: string
  upper?: boolean
  find?: boolean
}) {
  const [add, setAdd] = useState('')
  const [filter, setFilter] = useState('')
  const [msg, setMsg] = useState('')
  const norm = (v: string) => (upper ? v.trim().toUpperCase() : v.trim())
  const has = (v: string, skip = -1) =>
    items.some((x, i) => i !== skip && x.trim().toLowerCase() === v.trim().toLowerCase())

  function addOne() {
    const v = norm(add)
    if (!v) return
    if (has(v)) {
      setMsg(`"${v}" is already in the list.`)
      return
    }
    onChange([...items, v])
    setAdd('')
    setMsg(`Added "${v}". Save to keep it.`)
  }
  function move(i: number, d: number) {
    const j = i + d
    if (j < 0 || j >= items.length) return
    const next = [...items]
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }
  function rename(i: number, v: string) {
    const next = [...items]
    next[i] = upper ? v.toUpperCase() : v
    onChange(next)
  }
  function remove(i: number) {
    const gone = items[i]
    onChange(items.filter((_, k) => k !== i))
    setMsg(`Removed "${gone}". Save to keep the change.`)
  }

  const q = filter.trim().toLowerCase()
  return (
    <div className="le">
      <div className="le-bar">
        <input
          className="settings-input le-add"
          placeholder={`New ${noun}…`}
          value={add}
          onChange={(e) => setAdd(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              addOne()
            }
          }}
        />
        <button type="button" className="settings-action-btn" onClick={addOne} disabled={!add.trim()}>
          + Add
        </button>
        {find && items.length > 12 ? (
          <input
            className="settings-input le-find"
            placeholder={`Find a ${noun}…`}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        ) : null}
        <span className="le-count">
          {items.length} {noun}
          {items.length === 1 ? '' : 's'}
        </span>
      </div>
      {msg ? <div className="le-msg">{msg}</div> : null}
      <div className="le-grid">
        {items.map((it, i) =>
          q && !it.toLowerCase().includes(q) ? null : (
            <div key={i} className={`le-card${has(it, i) ? ' le-dup' : ''}`}>
              <span className="le-no">{i + 1}</span>
              <input
                className="le-name"
                value={it}
                aria-label={`${noun} ${i + 1}`}
                onChange={(e) => rename(i, e.target.value)}
                onBlur={(e) => {
                  if (!e.target.value.trim()) remove(i)
                }}
              />
              <button type="button" className="le-btn" title="Move up" disabled={i === 0} onClick={() => move(i, -1)}>
                ↑
              </button>
              <button
                type="button"
                className="le-btn"
                title="Move down"
                disabled={i === items.length - 1}
                onClick={() => move(i, 1)}
              >
                ↓
              </button>
              <button type="button" className="le-btn le-del" title="Remove" onClick={() => remove(i)}>
                ×
              </button>
            </div>
          ),
        )}
        {!items.length ? <div className="le-empty">No {noun}s yet. Type one above and press Enter.</div> : null}
      </div>
    </div>
  )
}
