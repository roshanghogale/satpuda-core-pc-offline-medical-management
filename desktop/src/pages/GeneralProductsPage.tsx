import { useCallback, useEffect, useRef, useState } from 'react'
import { registerVoicePage, type VoiceHandler } from '../voice/voiceBus'
import { ensureLocalEngine } from '../backend'
import {
  deleteGeneralProduct,
  fetchGeneralProducts,
  saveGeneralProduct,
  type GeneralProduct,
} from '../pagesApi'
import { AlertDialog, type AlertState } from './SalesDialogs'
import {
  ActionBar,
  ActionBtn,
  DataTable,
  Field,
  FilterBar,
  Note,
  PageRoot,
  SectionFrame,
  StatusLine,
} from './pageChrome'

export function GeneralProductsPage() {
  const [q, setQ] = useState('')
  const [products, setProducts] = useState<GeneralProduct[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [alert, setAlert] = useState<AlertState | null>(null)
  const [editId, setEditId] = useState<number | null>(null)
  const [name, setName] = useState('')
  const [rate, setRate] = useState('0')
  const [mrp, setMrp] = useState('0')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const engine = await ensureLocalEngine()
      if (!engine.ok) {
        setError(engine.error)
        return
      }
      const res = await fetchGeneralProducts(q.trim())
      setProducts(res.products || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [q])

  useEffect(() => {
    void load()
  }, [load])

  const resetForm = () => {
    setEditId(null)
    setName('')
    setRate('0')
    setMrp('0')
  }

  const startEdit = (p: GeneralProduct) => {
    setEditId(p.id)
    setName(p.name)
    setRate(String(p.rate))
    setMrp(String(p.mrp))
  }

  const save = async () => {
    const trimmed = name.trim()
    if (!trimmed) {
      setAlert({
        title: 'Name required',
        message: 'Enter a product name.',
        kind: 'warning',
      })
      return
    }
    setSaving(true)
    setError('')
    try {
      const res = await saveGeneralProduct({
        id: editId ?? undefined,
        name: trimmed,
        rate: Number(rate) || 0,
        mrp: Number(mrp) || 0,
      })
      if (!res.ok) {
        setAlert({
          title: 'Save failed',
          message: res.error || 'Could not save product.',
          kind: 'error',
        })
        return
      }
      setNote(editId ? 'Product updated.' : 'Product added.')
      resetForm()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const remove = async (p: GeneralProduct) => {
    setAlert({
      title: 'Delete product',
      message: `Delete "${p.name}"?`,
      kind: 'confirm',
      onConfirm: async () => {
        try {
          const res = await deleteGeneralProduct(p.id)
          if (!res.ok) {
            setAlert({
              title: 'Delete failed',
              message: res.error || 'Could not delete.',
              kind: 'error',
            })
            return
          }
          if (editId === p.id) resetForm()
          setNote('Product deleted.')
          await load()
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e))
        }
      },
    })
  }

  const rows = products.map((p) => [p.name, p.rate.toFixed(2), p.mrp.toFixed(2)])

  // ── Voice (test build): "general products refresh", "shodh sabun" ──
  const voiceHandlerRef = useRef<VoiceHandler>(async () => null)
  voiceHandlerRef.current = async (cmd) => {
    const a = cmd.args || {}
    if (cmd.intent === 'search') {
      setQ(String(a.query || ''))
      return { ok: true, say: `General Products shodh: ${a.query}` }
    }
    if (cmd.intent === 'page_filter' && a.filter === 'clear') {
      setQ('')
      return { ok: true, say: 'General Products: shodh kadhla' }
    }
    if (cmd.intent === 'page_action' && a.action === 'refresh') {
      await load()
      return { ok: true, say: 'General Products refresh kela' }
    }
    if (cmd.intent === 'page_action' || cmd.intent === 'page_filter') {
      return { ok: false, say: 'General Products var he kaam voice var nahi' }
    }
    return null
  }
  useEffect(() => registerVoicePage('general_products', () => voiceHandlerRef.current), [])

  return (
    <PageRoot>
      <StatusLine error={error} loading={loading && !products.length} />
      {note ? <Note>{note}</Note> : null}

      <SectionFrame title="General products (non-medicine items)">
        <Note>
          Reference rates only — not used in billing, purchase, or stock (same as
          classic General Products).
        </Note>
        <FilterBar>
          <Field label="Search">
            <input
              className="settings-input"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter by name…"
            />
          </Field>
          <ActionBar>
            <ActionBtn label="Refresh" onClick={() => void load()} />
          </ActionBar>
        </FilterBar>

        <DataTable
          columns={['Name', 'Rate', 'MRP']}
          rows={rows}
          empty="No general products"
          onRowClick={(rowIndex) => {
            const p = products[rowIndex]
            if (p) startEdit(p)
          }}
          onRowContextMenu={(rowIndex, e) => {
            e.preventDefault()
            const p = products[rowIndex]
            if (p) void remove(p)
          }}
        />
      </SectionFrame>

      <SectionFrame title={editId ? 'Edit product' : 'Add product'}>
        <FilterBar>
          <Field label="Name">
            <input
              className="settings-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="Rate">
            <input
              className="settings-input"
              type="number"
              step="0.01"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
            />
          </Field>
          <Field label="MRP">
            <input
              className="settings-input"
              type="number"
              step="0.01"
              value={mrp}
              onChange={(e) => setMrp(e.target.value)}
            />
          </Field>
        </FilterBar>
        <ActionBar>
          <ActionBtn
            label={editId ? 'Update' : 'Add'}
            variant="primary"
            disabled={saving}
            onClick={() => void save()}
          />
          {editId ? (
            <ActionBtn label="Cancel edit" onClick={resetForm} />
          ) : null}
        </ActionBar>
      </SectionFrame>

      <AlertDialog alert={alert} onClose={() => setAlert(null)} />
    </PageRoot>
  )
}
