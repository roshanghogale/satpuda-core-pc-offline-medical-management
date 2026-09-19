/**
 * Demo mode — the app with no engine behind it.
 *
 * Sales staff need a URL they can open on any laptop and walk a prospect
 * through the real screens. There is no store, no database and nothing is
 * saved: this installs a fetch interceptor that answers every /api/ call from
 * a recorded snapshot, so the app boots and populates exactly as it does at a
 * counter, and every write is politely declined.
 *
 * The snapshot was recorded from a test store and then redacted by field name
 * (see scrub_demo_fixture.py) — no real shop, no server address, no keys.
 */

import { demoSalesCalc } from './demoCalc'
import { REQUIRED_API_REVISION } from './api'

/** Always current: the demo must never fail the engine-version check. */
const DEMO_HEALTH_REVISION = REQUIRED_API_REVISION + 1000

let fixtures: Record<string, unknown> = {}
let installed = false

/** True when the build was made for the demo site. */
export const IS_DEMO = import.meta.env.VITE_DEMO === '1'

/** Writes answer with this, so the UI shows a real message rather than failing. */
const DEMO_REFUSAL = {
  ok: false,
  code: 'demo_mode',
  error:
    'This is a demonstration copy — nothing is saved. ' +
    'Everything else on screen works exactly as it does in the shop.',
}

/** Endpoints whose recorded answer is fine to reuse for a POST. */
const POST_FIXTURES: Record<string, string> = {
  '/api/purchase/calc': 'POST /api/purchase/calc',
}

/**
 * A POST whose answer depends on WHICH medicine and HOW MANY.
 *
 * One canned reply per path was the reason nothing could be put into a bill:
 * every medicine added the same line, so the recording was useless and the
 * refusal was the only honest answer. These pick the recording out by the
 * body, the way the engine picks the row out of the database.
 */
function postKeyFor(apiPath: string, body: unknown): string | null {
  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>
  if (apiPath === '/api/sales/build-line') {
    const id = Number(b.medicine_id || 0)
    const qty = Number(b.qty || 0)
    if (id > 0 && qty > 0) return `POST /api/sales/build-line#${id}|${qty}`
    return null
  }
  if (apiPath === '/api/purchase/lookup-medicine') {
    const name = String(b.name || '').trim().toLowerCase()
    return name ? `POST /api/purchase/lookup-medicine#${name}` : null
  }
  return null
}

/**
 * A sales line for a quantity that was not recorded.
 *
 * Checked against the recordings before it was written: across all 45 recorded
 * lines the engine's answer for quantity N is the answer for quantity 1 with
 * the amount multiplied by N and the rate unchanged. So the demo answers any
 * quantity from the quantity-1 recording rather than refusing, or -- worse --
 * showing the amount for a different quantity.
 */
function scaledLine(fixture: unknown, qty: number): unknown {
  if (!fixture || typeof fixture !== 'object') return null
  const src = fixture as { ok?: boolean; line?: Record<string, unknown> }
  if (!src.ok || !src.line) return null
  const rate = Number(src.line.rate || 0)
  const line = { ...src.line, qty, amount: round2(rate * qty), original_amount: round2(rate * qty) }
  return { ...src, line }
}

function round2(x: number): number {
  return Math.round((x + Number.EPSILON) * 100) / 100
}

function pathOf(input: RequestInfo | URL): string {
  const raw =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url
  try {
    return new URL(raw, window.location.origin).pathname
  } catch {
    return raw.split('?')[0]
  }
}

function search(input: RequestInfo | URL): string {
  const raw =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url
  const i = raw.indexOf('?')
  return i < 0 ? '' : raw.slice(i + 1)
}

/**
 * The recorded answer for a path.
 *
 * A recording cannot hold every filter combination, so a query-specific miss
 * falls back to the same path without its query. A demo that shows the whole
 * list when someone types a filter is far better than one that shows an error.
 */
function lookup(path: string, qs: string): unknown | undefined {
  const wanted = new URLSearchParams(qs)
  if (qs) {
    const sorted = [...wanted.entries()].sort(([a], [b]) => a.localeCompare(b))
    const keyed = path + "?" + new URLSearchParams(sorted).toString()
    if (fixtures[keyed] != null) return fixtures[keyed]
  }

  // Score every recording of this path by how much of the asked-for query it
  // actually answers, and take the best.
  //
  // An exact match cannot be relied on: the page asks for a medicine's batches
  // with a bill_date and a reserved-stock map alongside the name, and it
  // encodes a space as "+" where the recorder wrote "%20". Taking the FIRST
  // recording instead -- which is what the old prefix scan did -- answered
  // "which batches does this medicine have?" with a different medicine's
  // batches. Matching on the parameters that ARE present is the difference
  // between the right answer and a confident wrong one.
  const prefix = path + "?"
  let best: unknown
  let bestScore = 0
  let bestExtra = Number.MAX_SAFE_INTEGER
  for (const key of Object.keys(fixtures)) {
    if (!key.startsWith(prefix) || fixtures[key] == null) continue
    const have = new URLSearchParams(key.slice(prefix.length))
    let score = 0
    for (const [k, v] of have.entries()) {
      if (wanted.get(k) === v) score += 1
    }
    const extra = [...have.keys()].length - score
    if (score > bestScore || (score === bestScore && score > 0 && extra < bestExtra)) {
      best = fixtures[key]
      bestScore = score
      bestExtra = extra
    }
  }
  if (bestScore > 0) return best

  // `!= null`, not `in`: the redaction pass blanks a recording by writing null
  // over it, and a recorded null was handed to the app as a real answer. One of
  // them -- /api/license/status -- made the boot sequence throw on
  // `lic.needs_activation`, which set meta to null and cost the demo its Online
  // chip, the shop name, the saved theme and font size, and the whole sync
  // poller, in total silence. A blanked recording is a MISS.
  if (fixtures[path] != null) return fixtures[path]

  // Last resort: any recording of this path at all. A history list was recorded
  // for one date range and the page opens asking for another, so an
  // exact-match-only lookup left the busiest screens empty.
  for (const key of Object.keys(fixtures)) {
    if (key.startsWith(prefix) && fixtures[key] != null) return fixtures[key]
  }
  return undefined
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Enough of an empty answer that a screen renders instead of throwing. */
function emptyShapeFor(path: string): unknown {
  if (path.includes('/history') || path.includes('/inventory')) {
    return { columns: [], rows: [], row_ids: [], row_styles: [], summary: {} }
  }
  return { ok: true }
}

export async function installDemoMode(): Promise<void> {
  if (installed || !IS_DEMO) return
  installed = true

  // Relative to the page, so the demo works under any path Caddy serves it on.
  const res = await fetch('./demo-fixture.json', { credentials: 'same-origin' })
  if (!res.ok) {
    // The recorded data is behind the same sign-in as the page. A browser
    // holding a CACHED copy of this app loads it without asking the server
    // anything, so the session had already expired by the time we got here --
    // and the app then booted with a refusal in place of its data and showed
    // "Stale local engine. Restart with npm run tauri:dev" on a sales
    // demonstration. Send them to sign in instead of rendering that.
    if (res.status === 401 || res.status === 403) {
      window.location.replace(
        window.location.pathname.indexOf('/demo') === 0 ? '/demo/' : '/',
      )
      await new Promise(() => {})
    }
    throw new Error('Demo data could not be loaded.')
  }
  const loaded = (await res.json()) as unknown
  if (!loaded || typeof loaded !== 'object' || Array.isArray(loaded)) {
    throw new Error('Demo data could not be loaded.')
  }
  fixtures = loaded as Record<string, unknown>

  const realFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = pathOf(input)
    if (!path.includes('/api/')) return realFetch(input, init)

    const apiPath = path.slice(path.indexOf('/api/'))
    const method = (init?.method || 'GET').toUpperCase()

    // The banner is a real image on the server, not JSON.
    if (apiPath.startsWith('/api/home/banner') || apiPath.startsWith('/api/brand/')) {
      return realFetch(`.${apiPath.replace('/api', '/demo-assets')}`, init)
    }

    // Answered from here, never from the recording. The snapshot carries the
    // API revision of the day it was taken, and the app refuses to start
    // against an engine older than it expects -- so every bump of that number
    // broke the demo with a developer's error message. There is no engine
    // here; say so in the shape the check wants.
    if (apiPath === '/api/health') {
      return json({
        ok: true,
        service: 'desktop-api',
        revision: DEMO_HEALTH_REVISION,
        python: '3.13',
        python_minor: 13,
        db: true,
        online_mode: true,
        server_only: true,
        needs_migrate: false,
      })
    }

    if (method === 'GET') {
      const hit = lookup(apiPath, search(input))
      return json(hit === undefined ? emptyShapeFor(apiPath) : hit)
    }

    let body: unknown = null
    try {
      body = init?.body ? JSON.parse(String(init.body)) : null
    } catch {
      body = null
    }

    // The billing arithmetic runs here rather than being replayed. A recording
    // cannot answer a bill the prospect is still typing, which is why the
    // BILLING SUMMARY used to sit at ₹0.00 through the whole demonstration.
    // demoCalc is checked against 360 answers from the real engine.
    if (apiPath === '/api/sales/calc') {
      return json(demoSalesCalc((body || {}) as Record<string, unknown>))
    }

    const keyed = postKeyFor(apiPath, body)
    if (keyed) {
      if (fixtures[keyed] != null) return json(fixtures[keyed])
      if (apiPath === '/api/sales/build-line') {
        const b = (body || {}) as Record<string, unknown>
        const base = fixtures[`POST /api/sales/build-line#${Number(b.medicine_id)}|1`]
        const scaled = scaledLine(base, Number(b.qty || 1))
        if (scaled) return json(scaled)
      }
    }

    const canned = POST_FIXTURES[apiPath]
    if (canned && fixtures[canned] != null) return json(fixtures[canned])

    // Everything that would write. 400 is what the engine returns for a
    // refusal, so the pages' existing error handling shows the message.
    return json(DEMO_REFUSAL, 400)
  }
}
