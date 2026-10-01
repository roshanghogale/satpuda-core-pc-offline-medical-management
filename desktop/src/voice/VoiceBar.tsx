/** The voice bar: Voice ON (tap F1) and just speak; or hold F1 and speak; or Ctrl+K and type.
 *
 * It owns nothing about billing. It sends the audio to the local voice
 * service, gets back one command, and either does it itself (go to a page,
 * answer a stock question, press a popup's button) or hands it to the page the
 * command belongs to.
 *
 * Anything that saves, prints or sends asks first: "Yes" or Enter does it,
 * "No" or Esc drops it. A command the parser was unsure of asks too, and a
 * medicine that matched several names shows them numbered: "1, 2 ki 3".
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { AppNavigate } from '../App'
import { fetchMedicineBatches, fetchSalesHistory, lookupCustomerByName } from '../pagesApi'
import {
  voiceCancel,
  voiceConfig,
  voiceDevices,
  voiceHealth,
  voiceListenContext,
  voiceListenNext,
  voiceListenPause,
  voiceListenStart,
  voiceListenStop,
  voiceParse,
  voiceRefreshVocab,
  voiceStart,
  voiceStop,
  voiceSetShortcut,
  refreshShortcutCache,
  voiceShortcuts,
  SHORTCUTS_EVENT,
  shortcutKindWord,
  isShortcutKind,
  MAX_SHORTCUTS,
  type FieldContext,
  type ListenItem,
  type VoiceAlt,
  type VoiceCommand,
  type VoiceDevice,
  type VoiceHealth,
  type VoiceReply,
} from './voiceClient'
import {
  INTENT_PAGE,
  LOCAL_INTENT_PAGE,
  PAGE_INTENTS,
  SALE_BILL_INTENTS,
  SETTINGS_HOSTED,
  onVoicePlace,
  pageIsListening,
  presetRange,
  sendToPage,
  voicePlace,
  type VoiceResult,
} from './voiceBus'
import { isListeningHeld, setVoiceHandsFree, voiceBusy } from './voiceMode'
import { engineWord, msText, reloadVoiceLevel, useVoiceLevel } from './voiceLevel'
import { useVoiceEnabled, voiceSourceNote } from './voiceEnabled'
import { VoiceLevelPanel } from './VoiceLevelPanel'
import { VoicePackCard } from './VoicePackPanel'
import { PAGE_NAV, type PageId } from '../keyboard'
import { SETTINGS_TABS } from '../pages/settings/settingsConfig'
import { forSpeech, getVoiceGender, sayCancel, sayKey, setVoiceGender, type Gender } from './sayClip'
import { amountKeys, confirmKey, numberKeys, optionsKey, pageKey, resultSaid, settingsKey, type Said } from './replyKeys'
import {
  clearOptionNumbers,
  closeVoiceField,
  fieldOptions,
  findExactOption,
  findOption,
  findVoiceField,
  numberOptions,
  openVoiceField,
  optionEls,
  optionText,
  setFieldText,
  shortcutNumbered,
  VOICE_PICKED_EVENT,
} from './voiceField'

type Phase = 'offline' | 'loading' | 'ready' | 'listening' | 'thinking'
/** done: green, ask: amber (a question / list waits), bad: red, refuse: red text (security guard said no). */
type Tone = 'done' | 'ask' | 'bad' | 'refuse'
/** voice/guard.py refused the sentence: licence, passwords, server, restore, bulk export, other stores,
 *  switching the wake word / voice check off. Those are done by hand in Settings, never by voice. */
const REFUSED_TEXT = 'Voice ne he karta yet nahi — suraksha'
/** One line of what the bar did: what it HEARD, what it UNDERSTOOD, and the result. */
type Entry = {
  /** Same id on the line above and its copy in the history (the history leaves it out). */
  id: number
  heard: string
  understood: string
  say: string
  ok: boolean
  tone: Tone
  at: string
  secs?: number
  /** "AI 1.2s" / "Rules 0.1s": which understanding answered, and how long it took. */
  engine?: string
  /** Compare mode: "Dusre: Grahak: RAMESH PATIL (Rules)". */
  alt?: string
  /** What the gates said (speaker match, wake word), for the tooltip. */
  gate?: string
}
/** What a newer service says about how an utterance was understood. */
type ReplyMeta = { engine?: string; alt?: string; gate?: string }
/** A dropdown / search box voice opened, and the rows its list shows. */
type VField = { name: string; el: HTMLInputElement | HTMLTextAreaElement }
type FieldView = { name: string; options: string[] }
/** A numbered list: the chosen option goes into `cmd.args[field]` and `cmd` runs. */
type Choice = { cmd: VoiceCommand; options: string[]; field: 'medicine' | 'name' }
type Ear = { listening: boolean; paused: boolean; level: number }

const TALK_KEY = 'F1'  // F9 is Sales' reprint-last; F2-F12 and Insert are all taken
/** F1 released sooner than this was a tap (Voice ON / OFF), not push-to-talk. */
const TAP_MS = 400
const POLL_MS = 100  // the answer shows up to 0.2 s sooner than at 300
const HANDS_FREE_KEY = 'satpuda.voice.handsFree'
const TALK_BACK_KEY = 'satpuda.voice.talkBack'
const PAGE_WORDS: Record<string, string> = {
  home: 'Home', sales: 'Billing', purchase: 'Purchase', inventory: 'Inventory',
  sales_history: 'Sales History', purchase_history: 'Purchase History',
  returns: 'Returns', payment: 'Payments', settings: 'Settings',
  general_products: 'General Products', reorder: 'Reorder',
}
/** Every page the shell can show: the nav bar plus General Products (Home's tile). */
const PAGES = new Set<string>([...PAGE_NAV.map((p) => p.id), 'general_products'])
/** Other names a page is called by. */
const PAGE_ALIASES: Record<string, string> = {
  billing: 'sales', bill: 'sales', sale: 'sales', payments: 'payment',
  history: 'sales_history', purchases: 'purchase_history', general: 'general_products',
}
/** Commands whose unsure names come as a numbered list, and the arg the pick fills. */
const CHOICE_FIELD: Record<string, 'medicine' | 'name'> = {
  add_medicine: 'medicine', remove_medicine: 'medicine', set_doctor: 'name', set_customer: 'name',
}
/** "Show me the doctors / customers / medicines": what a pick from the list runs. */
const LIST_RUN: Record<string, { intent: string; field: 'medicine' | 'name' }> = {
  doctor: { intent: 'set_doctor', field: 'name' },
  customer: { intent: 'set_customer', field: 'name' },
  medicine: { intent: 'add_medicine', field: 'medicine' },
}
const MAX_OPTIONS = 8
/** Field commands: they work on the dropdown voice opened. */
const FIELD_INTENTS = new Set(['focus_field', 'type_text', 'clear_field', 'pick_option', 'close_field'])
const FIELD_WORD: Record<string, string> = {
  customer: 'Grahak', doctor: 'Doctor', medicine: 'Aushadh', supplier: 'Supplier',
  search: 'Search', village: 'Gav', address: 'Patta', customer_phone: 'Grahak number', doctor_phone: 'Doctor number',
}
const FIELD_KEY: Record<string, string> = {
  customer: 'field_customer_opened', doctor: 'field_doctor_opened', medicine: 'field_medicine_opened',
  supplier: 'field_supplier_opened', search: 'field_search_opened', village: 'field_village_opened',
  address: 'field_village_opened', customer_phone: 'ok', doctor_phone: 'ok',
}
const fieldWord = (name: string) => FIELD_WORD[name] || name
/** Pick lists whose box holds the chosen name: opened by voice, they start empty so every name shows. */
const CLEAR_ON_OPEN = new Set(['customer', 'doctor', 'village', 'address'])
const up = (v: unknown) => String(v ?? '').trim().toUpperCase()
/** "Doctor 2" / "Grahak 2" / "Supplier 2" / "Gaon 2": which numbered shortcut a command came from. */
const kindWord = shortcutKindWord
const hasShortcut = (a: Record<string, any>) =>
  a.shortcut != null && a.shortcut !== '' && Number.isFinite(Number(a.shortcut))
/** Confirm lines for commands whose service `say` may be empty. */
const CONFIRM_TEXT: Record<string, string> = { clear_bill: 'Bill clear karu?' }

/** What the bar understood, as one short line: "Grahak: SUNIL JADHAV". */
function understoodLine(cmd: VoiceCommand): string {
  const a = cmd.args || {}
  const amt = (v: unknown) => (v == null || v === '' ? 'purna bill' : `₹${v}`)
  switch (cmd.intent) {
    case 'set_customer': return `Grahak${hasShortcut(a) ? ` ${a.shortcut}` : ''}: ${up(a.name) || '?'}${a.new ? ' (navin)' : ''}`
    case 'set_doctor': return `Doctor${hasShortcut(a) ? ` ${a.shortcut}` : ''}: ${up(a.name) || '?'}${a.new ? ' (navin)' : ''}`
    case 'set_shortcut': return `${kindWord(a.kind)} ${a.n ?? '?'} = ${up(a.name) || '—'}`
    case 'shortcut_missing': return `${kindWord(a.kind)} ${a.n ?? '?'}: naav nahi`
    case 'add_medicine': return `Aushadh: ${up(a.medicine) || '?'} × ${a.qty ?? 1}${a.unit === 'strip' ? ' patta' : ''}${a.more ? ' (ajun)' : ''}`
    case 'remove_medicine': return `Kadha: ${up(a.medicine) || '?'}`
    case 'remove_last': return 'Shevatcha aushadh kadha'
    case 'remove_line': return `Line ${a.n ?? '?'} kadha`
    case 'one_more': return a.qty ? `Ajun ${a.qty}` : 'Ajun ek'
    case 'give_regulars': return 'Regular aushadha'
    case 'new_bill': return 'Navin bill'
    case 'clear_bill': return 'Bill clear'
    case 'save_bill': return 'Bill save'
    case 'print_bill': return 'Bill print'
    case 'discount': return `Discount: ${a.percent ? `${a.amount}%` : `₹${a.amount}`}`
    case 'cash': return `Cash: ${amt(a.amount)}`
    case 'online': return `Online: ${amt(a.amount)}`
    case 'udhari': return 'Udhari (Due)'
    case 'search': return `Search: ${up(a.query)}`
    case 'navigate': return `Page: ${PAGE_WORDS[PAGE_ALIASES[String(a.page)] || String(a.page)] || a.page}`
    case 'open_settings': return `Settings: ${[a.tab, a.section].filter(Boolean).join(' / ')}`
    case 'page_filter': return `Filter: ${a.filter ?? ''}${a.value != null ? ` = ${a.value}` : ''}`
    case 'page_action': return `Kaam: ${a.action ?? ''}`
    case 'today_sales': return 'Aajchi vikri'
    case 'sales_total': return `${SALES_PERIOD[String(a.period || 'today')]?.[a.what === 'profit' ? 'profit' : a.what === 'bills' ? 'bills' : 'sales'] || 'Vikri'}`
    case 'due_query': return `Baki: ${up(a.name) || '?'}`
    case 'payment':
      return `Payment${a.kind ? ` (${a.kind})` : ''}: ${up(a.name) || '?'}${a.amount != null && a.amount !== '' ? ` ₹${a.amount}` : ''}${a.mode ? ` ${a.mode}` : ''}`
    case 'load_bill': return `Bill load: ${a.bill_no ?? '?'}`
    case 'stock_query': return `Stock: ${up(a.medicine)}`
    case 'expiry_query': return `Expiry: ${up(a.medicine)}`
    case 'list_options': return `Yaadi: ${a.kind ?? ''}${a.query ? ` "${a.query}"` : ''}`
    case 'focus_field': return `Field: ${fieldWord(String(a.field || ''))}${a.text ? ` "${up(a.text)}"` : ''}`
    case 'type_text': return `Type: "${up(a.text)}"`
    case 'clear_field': return a.field ? `${fieldWord(String(a.field))} pusa` : 'Field rikama'
    case 'pick_option':
      if (hasShortcut(a)) return `Nivad: ${a.shortcut} = ${up(a.text) || '?'}`
      return a.n != null && a.n !== '' ? `Nivad: ${a.n} number` : `Nivad: ${up(a.text)}`
    case 'set_field':
      if (a.value != null) return `${fieldWord(String(a.field || ''))}: ${a.value}`
      return `${kindWord(a.field)}${hasShortcut(a) ? ` ${a.shortcut}` : ''}: ${up(a.name) || '?'}`
    case 'close_field': return 'Field band'
    case 'choose': return `Number: ${a.n ?? '?'}`
    case 'yes': return 'Ho'
    case 'no': return 'Nahi'
    case 'close': return 'Band kara'
    case 'voice_off': return 'Voice band'
    case 'unknown': case 'none': return 'Samajla nahi'
    case 'unknown_medicine': return `Aushadh sapadla nahi${a.medicine ? `: ${up(a.medicine)}` : ''}`
    case 'ask': return 'Parat vicharla'
    case 'refuse': return 'Suraksha: voice sathi band'
    default: return cmd.intent
  }
}

/** Why the page could not do it, in the shop's words, with the app's own reason:
 *  "Dolo 650 add zala nahi: doctor nivda aadhi — "doctor …" mhana (Please select a doctor…)". */
function failNote(
  cmd: VoiceCommand,
  r: VoiceResult,
): { say: string; said: Said; tone: Tone; need?: 'doctor' | 'customer' } {
  const text = String(r.say || '').trim()
  if (cmd.intent !== 'add_medicine' && cmd.intent !== 'one_more') {
    return { say: text || `${understoodLine(cmd)} — zala nahi`, said: resultSaid(cmd, r), tone: 'bad' }
  }
  const name = String(cmd.args?.medicine ?? '').trim()
  // The page's line starts with the medicine's name; the rest is the reason.
  let why = name && text.toLowerCase().startsWith(`${name.toLowerCase()}:`) ? text.slice(name.length + 1).trim() : text
  const doctor = r.code === 'doctor_required' || /select a doctor|requires? a doctor/i.test(text)
  const customer = r.code === 'customer_required' || /requires? a customer/i.test(text)
  // A box on the page (Margin Warning) waits for "ho" / "nahi": that is a question.
  const popup = r.code === 'popup' || /popup var "ho"/i.test(text)
  // The owner asked for one short line here, nothing more (2026-09-26).
  if (doctor || customer) {
    return {
      say: doctor ? 'Pahile doctor add kara' : 'Pahile grahak add kara',
      said: { key: doctor ? 'doctor_first' : 'customer_first', cue: 'warn' },
      tone: 'ask',
      need: doctor ? 'doctor' : 'customer',
    }
  }
  const said: Said = popup ? { key: ['medicine_not_added', 'popup_opened'], cue: 'warn' } : resultSaid(cmd, r)
  return {
    say: `${name || 'Aushadh'} add zala nahi: ${why || 'karan kalale nahi'}`,
    said,
    tone: popup || doctor || customer ? 'ask' : 'bad',
    need: doctor ? 'doctor' : customer ? 'customer' : undefined,
  }
}

/** A medicine held back until a doctor / customer is chosen; `picked` once one is. */
type Retry = { need: 'doctor' | 'customer'; cmds: VoiceCommand[]; picked: boolean }

/** Commands that go on choosing the doctor / customer a held-back medicine waits for;
 *  anything else ("Crocin de", "bill clear kar", "band kar") drops that medicine. */
function keepsRetry(c: VoiceCommand, need: 'doctor' | 'customer'): boolean {
  switch (c.intent) {
    case 'set_doctor': return need === 'doctor'
    case 'set_customer': return need === 'customer'
    case 'pick_option': case 'choose': case 'type_text': case 'yes':
    case 'unknown': case 'none': case 'ask':
      return true
    case 'focus_field': case 'clear_field': return String(c.args?.field || need).toLowerCase() === need
    default: return false
  }
}

/** "Aajchi vikri kiti?" and the like: the words on screen and the spoken lead-in, per period. */
const SALES_PERIOD: Record<string, { sales: string; profit: string; bills: string; lead: string }> = {
  today: { sales: 'Aajchi vikri', profit: 'Aajcha nafa', bills: 'Aajche bill', lead: 'lead_sales_today' },
  yesterday: { sales: 'Kaalchi vikri', profit: 'Kaalcha nafa', bills: 'Kaalche bill', lead: 'lead_sales_yesterday' },
  week: { sales: 'Gelya 7 divsanchi vikri', profit: 'Gelya 7 divsancha nafa', bills: 'Gelya 7 divsanche bill', lead: 'lead_sales_week' },
  month: { sales: 'Ya mahinyachi vikri', profit: 'Ya mahinyacha nafa', bills: 'Ya mahinyache bill', lead: 'lead_sales_month' },
}
/** ₹ as the shop writes it: 12,340 (paise only when there are some). */
const rupees = (n: number) => n.toLocaleString('en-IN', { maximumFractionDigits: 2 })

/** Where the shop is, as the service and the routing see it. A Settings panel with a form
 *  of its own is its own place ("payment" + its "customer" / "supplier" side, "reorder");
 *  Returns adds its tab. Everything else is the page itself. */
function placeOf(page: string): { page: string; section: string } {
  if (page === 'payment' || page === 'settings') {
    const [tab = '', toggle = ''] = voicePlace('settings').split('/')
    if (tab === 'payment') return { page: 'payment', section: toggle }
    if (tab === 'reorder') return { page: 'reorder', section: toggle }
    // Alert & Monitoring has its own words: "expired ya mahinyache", "2027 che", "filter kadh"
    if (tab === 'alerts') return { page: 'alerts', section: toggle }
    return { page, section: '' }
  }
  if (page === 'returns') return { page, section: voicePlace('returns') }
  return { page, section: '' }
}

/** A command the page on screen cannot take, in the shop's words ('' when it can). A sale-bill
 *  command said on Purchase, Returns or Payments is refused -- never carried to the open Sales
 *  bill behind the shop's back. From Home it still goes to Sales. */
function wrongPlace(page: string, intent: string): string {
  const here = placeOf(page).page
  if (PAGE_INTENTS[here]?.includes(intent)) return ''
  const own = LOCAL_INTENT_PAGE[intent]
  if (own && own !== here) {
    const w = PAGE_WORDS[own] || own
    return `He ya page var chalat nahi — ${w} var ja kinva '${w} ughad' mhana`
  }
  if (SALE_BILL_INTENTS.has(intent) && here !== 'sales' && here !== 'home') {
    return "He ya page var chalat nahi — Sales var ja kinva 'Sales ughad' mhana"
  }
  return ''
}

/** "Save kar" on a page with its own form asks about THAT save. */
const SAVE_ASK: Record<string, string> = { returns: 'Return save karu?', payment: 'Payment save karu?' }

/** Replies that understood nothing: they must not wipe a question or list off the screen. */
const NOT_UNDERSTOOD = new Set(['unknown', 'none', 'ask'])

const wait = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms))
/** Let React commit what the last command changed, so the next one reads the new state. */
const settle = () => wait(40)
async function waitFor<T>(get: () => T | null | undefined | false, ms: number, step = 100): Promise<T | null> {
  const until = Date.now() + ms
  for (;;) {
    const v = get()
    if (v) return v
    if (Date.now() >= until) return null
    await wait(step)
  }
}
const MAX_LIST_OPTIONS = 30
/** An option "+ Bhath E" is a NEW name to create. */
const NEW_MARK = /^\+\s*/

function cleanOptions(v: unknown, max: number): string[] {
  return Array.isArray(v) ? v.map((x) => String(x ?? '').trim()).filter(Boolean).slice(0, max) : []
}

/** How an option is shown: "+ Bhath E" -> "Navin: Bhath E". */
function optionLabel(o: string): string {
  return NEW_MARK.test(o) ? `Navin: ${o.replace(NEW_MARK, '')}` : o
}

// localStorage can throw (private mode, blocked storage): a remembered switch is a convenience only.
function loadFlag(key: string, dflt: boolean): boolean {
  try {
    const v = window.localStorage.getItem(key)
    return v == null ? dflt : v === '1'
  } catch {
    return dflt
  }
}
function saveFlag(key: string, on: boolean) {
  try {
    window.localStorage.setItem(key, on ? '1' : '0')
  } catch {
    /* not remembered: fine */
  }
}

/** A short tone, so a headset user knows the mic opened and closed. */
function beep(freq: number, ms = 90) {
  // With Voice ON the mic is always open and the spoken reply is the feedback;
  // a tone would only be something more for it to hear.
  if (beepMuted) return
  try {
    const AC = (window as any).AudioContext || (window as any).webkitAudioContext
    const ctx = new AC()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.frequency.value = freq
    gain.gain.value = 0.08
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start()
    window.setTimeout(() => {
      osc.stop()
      void ctx.close()
    }, ms)
  } catch {
    /* no audio device: silent is fine */
  }
}
let beepMuted = false
// Entry ids; the random part keeps them apart across a dev hot reload that restarts the count.
let entrySeq = 0

/** Settings' answer to "open this tab / section" as a phrase. */
function settingsSaid(r: VoiceResult | null, tab: unknown, section: unknown): Said {
  if (r?.ok) return { key: settingsKey(tab, section) || 'page_settings' }
  return r ? { key: 'settings_not_found', cue: 'warn' } : { key: 'settings_not_opened', cue: 'warn' }
}

/** A mic that would not open: missing, or there but busy / refused. */
function micSaid(error: string): Said {
  return { key: /device|not found|no mic|no input|sapadla/i.test(error) ? 'mic_not_found' : 'mic_not_opened', cue: 'error' }
}

async function waitForPage(page: string, ms = 3000) {
  const until = Date.now() + ms
  while (!pageIsListening(page) && Date.now() < until) {
    await new Promise((r) => window.setTimeout(r, 80))
  }
  // One more beat so the page has rendered its first state.
  await new Promise((r) => window.setTimeout(r, 120))
}

// ── Popups answered by voice ──
// Every alert, confirm and page dialog in the app is a role=dialog / alertdialog
// or a .modal-card inside a .modal-backdrop, so one generic reader serves them all.

const DIALOG_SEL = '[role="dialog"], [role="alertdialog"], [aria-modal="true"], .modal-card, .modal-backdrop'
const YES_WORDS = [
  'save anyway', 'yes', 'ok', 'okay', 'ho', 'save', 'add', 'update', 'continue',
  'confirm', 'print', 'resume', 'apply', 'proceed', 'done', 'submit',
]
const NO_WORDS = ['cancel', 'no', 'nako', 'close', 'not now', 'dismiss']
const CLOSE_WORDS = ['close', 'cancel', 'dismiss']

function shown(el: HTMLElement): boolean {
  if (!el.getClientRects().length) return false
  const st = window.getComputedStyle(el)
  return st.visibility !== 'hidden' && st.display !== 'none'
}

/** The popup on top: the last one in the page (portals append to body, nested ones come after their parent). */
function topDialog(): HTMLElement | null {
  const all = Array.from(document.querySelectorAll<HTMLElement>(DIALOG_SEL)).filter(
    (el) => !el.closest('.vb-panel, .vb-enroll-backdrop') && shown(el),
  )
  return all.length ? all[all.length - 1] : null
}

const words = (s: string) => s.toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim()

type Btn = { el: HTMLElement; text: string; aria: string }

function buttonsOf(dlg: HTMLElement): Btn[] {
  return Array.from(
    dlg.querySelectorAll<HTMLElement>('button, [role="button"], input[type="button"], input[type="submit"]'),
  )
    .filter((b) => !(b as HTMLButtonElement).disabled && b.getAttribute('aria-disabled') !== 'true' && shown(b))
    .map((el) => ({
      el,
      text: words(el instanceof HTMLInputElement ? el.value : el.textContent || ''),
      aria: words(el.getAttribute('aria-label') || el.title || ''),
    }))
}

/** The first button (in word order) whose label is, or starts with, one of the words. */
function findBtn(list: Btn[], ws: string[], ariaToo = true): Btn | null {
  for (const w of ws) {
    const hit = list.find(
      (b) =>
        b.text === w || b.text.startsWith(`${w} `) ||
        (ariaToo && !b.text.replace(/[^a-z]/g, '') && (b.aria === w || b.aria.startsWith(`${w} `))),
    )
    if (hit) return hit
  }
  return null
}

function btnName(b: Btn): string {
  const t = (b.el instanceof HTMLInputElement ? b.el.value : b.el.textContent || '').replace(/\s+/g, ' ').trim()
  return /[a-z0-9]/i.test(t) ? t : b.el.getAttribute('aria-label') || b.el.title || t
}

function pressEscape(target: HTMLElement) {
  target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }))
}

/** Answer the top popup: yes / no / close. Returns what to show and say, or null when no popup is open. */
// A popup about the licence, activation, a restore, a delete or the server is answered by
// hand only: a spoken "ho" -- or someone else's "ho" -- must never confirm one of those.
const SENSITIVE_DIALOG =
  /licen[cs]e|activat|expir(y|es) (date|on)|validity|trial|restore|delete|wipe|erase|reset|danger|server|sync|admin|password|pin|backup|export all|sagla data|parvana|सक्रिय|परवाना|हटव|पुनर्स्थाप/i

function answerDialog(intent: string): { say: string; ok: boolean; said: Said } | null {
  const dlg = topDialog()
  if (!dlg) return null
  if (intent === 'yes' && SENSITIVE_DIALOG.test(dlg.textContent || '')) {
    return {
      say: 'Ha popup haatane uttar dya — suraksha',
      ok: false,
      said: { key: 'voice_refused', cue: 'warn' },
    }
  }
  const list = buttonsOf(dlg)
  let hit: Btn | null = null
  if (intent === 'yes') {
    hit = findBtn(list, YES_WORDS, false)
    if (!hit) {
      // No known word: the dialog's primary button, or the one it put focus on.
      const primary = list.filter((b) => /primary/.test(b.el.className))
      const focused = list.find((b) => b.el === document.activeElement)
      hit = primary.length ? primary[primary.length - 1] : focused || null
    }
    if (!hit) return { say: 'Popup madhe "Yes" sarkha button sapadla nahi', ok: false, said: { key: 'popup_no_yes_button', cue: 'warn' } }
  } else if (intent === 'no') {
    hit = findBtn(list, NO_WORDS)
  } else {
    // close: Close / Cancel; an alert with only OK is closed by its OK.
    hit = findBtn(list, CLOSE_WORDS, false)
    if (!hit) {
      const texted = list.filter((b) => /[a-z]/.test(b.text))
      if (texted.length === 1 && findBtn(texted, ['ok', 'okay', 'done', 'got it'], false)) hit = texted[0]
    }
    if (!hit) hit = findBtn(list, CLOSE_WORDS)
  }
  if (hit) {
    const name = btnName(hit)
    hit.el.click()
    const key = intent === 'yes' ? 'popup_yes' : intent === 'no' ? 'popup_no' : 'popup_closed'
    return { say: `'${name}' dabla`, ok: true, said: { key } }
  }
  // Nothing to click: Esc is how every popup here closes itself.
  pressEscape(dlg)
  return { say: 'Popup band kela', ok: true, said: { key: 'popup_closed' } }
}

/** A popup as one line: its title and the first line of what it says. */
function dialogText(dlg: HTMLElement): string {
  const title = (dlg.querySelector('h1, h2, h3, [id$="title"]')?.textContent || '').replace(/\s+/g, ' ').trim()
  const body = dlg.querySelector('.modal-body, p')?.textContent || ''
  const first = body.split('\n').map((x) => x.trim()).find(Boolean) || ''
  return [title, first].filter(Boolean).join(': ').slice(0, 160)
}

/** Close any open suggestion list. Both list components (ModernCombo,
 *  TwoStepMedicinePicker) close on a mousedown outside themselves and on blur,
 *  so this is their own way of closing, not a DOM hack. */
function closeDropdowns() {
  // A dropdown voice opened on purpose stays open until voice closes it.
  if (voiceFieldOpen) return
  document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }))
  const a = document.activeElement as HTMLElement | null
  if (a && a !== document.body && !a.closest('.vb-panel') && a.matches('input, textarea, select, [role="combobox"]')) {
    a.blur()
  }
}

/** Set while a voice-opened dropdown is being driven (the sweep below must not shut it). */
let voiceFieldOpen = false

/** Pages focus their next field a beat after a command, so sweep again after that. */
function closeDropdownsSoon() {
  closeDropdowns()
  window.setTimeout(closeDropdowns, 250)
  window.setTimeout(closeDropdowns, 700)
}

export function VoiceBar({ page, navigate }: { page: string; navigate: AppNavigate }) {
  const [phase, setPhase] = useState<Phase>('offline')
  const [health, setHealth] = useState<VoiceHealth | null>(null)
  const [open, setOpen] = useState(true)
  const [showSetup, setShowSetup] = useState(false)
  const [devices, setDevices] = useState<VoiceDevice[]>([])
  const [typed, setTyped] = useState('')
  const [last, setLast] = useState<Entry | null>(null)
  const [history, setHistory] = useState<Entry[]>([])
  const [pending, setPendingState] = useState<VoiceCommand | null>(null)
  const [choice, setChoiceState] = useState<Choice | null>(null)
  const [talkBack, setTalkBackState] = useState(() => loadFlag(TALK_BACK_KEY, true))
  const [gender, setGenderState] = useState<Gender>(getVoiceGender)
  const [handsFree, setHandsFreeState] = useState(() => loadFlag(HANDS_FREE_KEY, false))
  const [ear, setEar] = useState<Ear>({ listening: false, paused: false, level: 0 })
  const [working, setWorking] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  // The gates (newer service): segments dropped since listening began, and "Satpuda" heard alone.
  const [gates, setGates] = useState<{ ignored: number; waiting: boolean }>({ ignored: 0, waiting: false })
  const { cfg: level } = useVoiceLevel()
  const levelRef = useRef(level)
  levelRef.current = level
  const voiceSwitch = useVoiceEnabled()
  const inputRef = useRef<HTMLInputElement>(null)
  const listeningRef = useRef(false)
  const pageRef = useRef(page)
  pageRef.current = page
  // Settings / Returns say which panel or tab shows (voiceBus); the bar follows along.
  const [, setPlaceSeq] = useState(0)
  useEffect(() => onVoicePlace(() => setPlaceSeq((n) => n + 1)), [])
  const here = placeOf(page)
  // The refs are what the handlers read: several spoken commands can arrive
  // back to back, before React has re-rendered with the new state.
  const pendingRef = useRef<VoiceCommand | null>(null)
  const choiceRef = useRef<Choice | null>(null)
  const handsFreeRef = useRef(handsFree)
  // The rest of a many-part sentence, waiting behind a question: "…, bill save kar" asks
  // Yes / No before the save, and whatever came after it runs once it is answered.
  const queueRef = useRef<VoiceCommand[]>([])
  const startCallRef = useRef<Promise<unknown> | null>(null)
  // The utterance being answered: what Whisper heard, and what the bar made of it.
  const rawHeardRef = useRef('')
  const understoodRef = useRef('')
  const metaRef = useRef<ReplyMeta | null>(null)
  // The dropdown / search box voice opened (null: none). Its rows are numbered on screen.
  const fieldRef = useRef<VField | null>(null)
  const [fieldView, setFieldViewState] = useState<FieldView | null>(null)
  const fieldViewRef = useRef<FieldView | null>(null)
  const [fieldSeq, setFieldSeq] = useState(0)
  const setFieldView = useCallback((v: FieldView | null) => {
    const old = fieldViewRef.current
    const same =
      old === v ||
      (!!old && !!v && old.name === v.name && old.options.length === v.options.length &&
        old.options.every((o, i) => o === v.options[i]))
    if (same) return
    fieldViewRef.current = v
    setFieldViewState(v)
  }, [])

  const setPending = useCallback((c: VoiceCommand | null) => {
    pendingRef.current = c
    setPendingState(c)
  }, [])
  const setChoice = useCallback((c: Choice | null) => {
    choiceRef.current = c
    setChoiceState(c)
  }, [])
  const setTalkBack = (on: boolean) => {
    setTalkBackState(on)
    saveFlag(TALK_BACK_KEY, on)
  }

  useEffect(() => {
    // Voice just came on (the store switch answered late): pages that read the shortcut
    // numbers at mount (Sales' badges) got nothing then; hand them the map now.
    voiceShortcuts()
      .then((r) => window.dispatchEvent(new CustomEvent(SHORTCUTS_EVENT, { detail: r.shortcuts })))
      .catch(() => {})
    return () => {
      setVoiceHandsFree(false)
      beepMuted = false
    }
  }, [])

  // Dropdowns read this flag; beeps stay quiet while the mic is always open.
  useEffect(() => {
    handsFreeRef.current = handsFree
    setVoiceHandsFree(handsFree)
    beepMuted = handsFree
    if (handsFree) closeDropdowns()
    // A voice-opened list orders itself by the shortcuts at once: have them ready.
    if (handsFree) void refreshShortcutCache(0)
  }, [handsFree])

  /** What the service should expect next: a number, a yes/no, a popup answer, a dropdown pick, or anything. */
  const pendingWord = useCallback((): string => {
    if (choiceRef.current) return 'choice'
    if (pendingRef.current) return pendingRef.current.intent
    if (topDialog()) return 'dialog'
    return fieldRef.current ? 'dropdown' : ''
  }, [])

  /** The open voice field and its rows, for the service (empty when none is open). */
  const fieldContext = useCallback((): FieldContext => {
    const f = fieldRef.current
    return f ? { field: f.name, options: fieldOptions(f.el) } : { field: '', options: [] }
  }, [])

  // ── voice-opened dropdowns ──
  /** Re-read the open field's list: number its rows and remember what it shows. */
  const refreshField = useCallback(() => {
    const f = fieldRef.current
    if (!f) {
      clearOptionNumbers()
      setFieldView(null)
      return
    }
    numberOptions(f.el)
    setFieldView({ name: f.name, options: fieldOptions(f.el) })
  }, [setFieldView])

  /** Stop driving the field; `close` also closes its list and leaves it (Escape + blur). */
  const endField = useCallback(
    (close: boolean) => {
      const f = fieldRef.current
      fieldRef.current = null
      voiceFieldOpen = false
      if (f && close) closeVoiceField(f.el)
      clearOptionNumbers()
      setFieldView(null)
      setFieldSeq((n) => n + 1)
    },
    [setFieldView],
  )

  /** Focus the field and open its list, even in voice mode: voice asked for it. */
  const startField = useCallback(
    (name: string, el: VField['el']) => {
      const old = fieldRef.current
      if (old && old.el !== el) closeVoiceField(old.el)
      fieldRef.current = { name, el }
      voiceFieldOpen = true
      openVoiceField(el)
      setFieldSeq((n) => n + 1)
      refreshField()
    },
    [refreshField],
  )

  // Voice OFF: the field is the keyboard's again.
  useEffect(() => {
    if (!handsFree && fieldRef.current) endField(false)
  }, [handsFree, endField])

  // While a field is open: follow its list (debounced) and let go when focus leaves it.
  useEffect(() => {
    const f = fieldRef.current
    if (!f) return
    let t = 0
    const later = () => {
      if (!t) t = window.setTimeout(() => {
        t = 0
        if (fieldRef.current !== f) return
        if (!f.el.isConnected || document.activeElement !== f.el) {
          // The page moved on (a pick moved focus, a popup opened, the shop clicked away).
          endField(false)
          return
        }
        refreshField()
      }, 150)
    }
    const target = f.el.closest('[data-voice-wrap]') || f.el.parentElement || f.el
    const mo = new MutationObserver(later)
    mo.observe(target, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['data-voice-shortcut'],
    })
    f.el.addEventListener('focusout', later)
    f.el.addEventListener('input', later)
    later()
    return () => {
      mo.disconnect()
      f.el.removeEventListener('focusout', later)
      f.el.removeEventListener('input', later)
      if (t) window.clearTimeout(t)
    }
  }, [fieldSeq, endField, refreshField])

  // ── service health ──
  const poll = useCallback(async () => {
    try {
      const h = await voiceHealth()
      setHealth(h)
      if (!listeningRef.current) {
        setPhase((p) => (p === 'thinking' ? p : h.ready ? 'ready' : 'loading'))
      }
    } catch {
      setHealth(null)
      if (!listeningRef.current) setPhase('offline')
    }
  }, [])
  useEffect(() => {
    void poll()
    const t = window.setInterval(poll, 4000)
    return () => window.clearInterval(t)
  }, [poll])

  const setSecs = useCallback((secs: number) => {
    setLast((l) => (l ? { ...l, secs } : l))
  }, [])

  const note = useCallback(
    /** `said` is the recorded phrase key (and error / warning sound) for this line; names and
     *  amounts stay on screen only. `spoken` is the system-voice fallback when a clip is missing. */
    (heard: string, say: string, ok: boolean, said: Said | string | null, spoken?: string, tone?: Tone) => {
      // An utterance shows the words Whisper heard and what they were taken to mean.
      const e: Entry = {
        id: ++entrySeq + Math.random(),
        heard: heard ? rawHeardRef.current || heard : '',
        understood: heard ? understoodRef.current : '',
        say,
        ok,
        tone: tone || (ok ? 'done' : 'bad'),
        at: new Date().toLocaleTimeString(),
        ...(heard && metaRef.current ? metaRef.current : {}),
      }
      setLast(e)
      setHistory((h) => [e, ...h].slice(0, 16))
      const s: Said | null = typeof said === 'string' ? { key: said } : said
      if (!s) return
      // A question ("Bill clear karu?", "Konta? number sanga") must hear its answer at once:
      // the owner's earbuds keep the bar's voice out of the mic, so the mic stays open (and
      // opens again now if an earlier line paused it). Other replies still pause it while
      // spoken; with spoken replies off nothing pauses it.
      const asks = e.tone === 'ask'
      if (asks && handsFreeRef.current && !isListeningHeld()) void voiceListenPause(false).catch(() => {})
      sayKey(
        s.key,
        {
          start: () => {
            if (talkBack && !asks && handsFreeRef.current) void voiceListenPause(true).catch(() => {})
          },
          done: () => {
            // The enrollment wizard keeps the mic paused until it closes.
            if (handsFreeRef.current && !isListeningHeld()) void voiceListenPause(false).catch(() => {})
          },
        },
        // The sound plays with spoken replies off too; only the words are dropped.
        { fallback: forSpeech(spoken ?? say), cue: s.cue, silent: !talkBack, skipMissing: s.skipMissing },
      )
    },
    [talkBack],
  )

  const noteRef = useRef(note)
  noteRef.current = note

  /** Say a fixed phrase without a line on screen (the status line shows the state). */
  const sayOnly = useCallback(
    (key: string, fallback: string) => {
      sayKey(
        key,
        {
          start: () => {
            if (talkBack && handsFreeRef.current) void voiceListenPause(true).catch(() => {})
          },
          done: () => {
            if (handsFreeRef.current && !isListeningHeld()) void voiceListenPause(false).catch(() => {})
          },
        },
        { fallback, silent: !talkBack },
      )
    },
    [talkBack],
  )
  const sayOnlyRef = useRef(sayOnly)
  sayOnlyRef.current = sayOnly

  const setHandsFree = useCallback(
    (on: boolean) => {
      handsFreeRef.current = on
      setHandsFreeState(on)
      saveFlag(HANDS_FREE_KEY, on)
      if (!on) note('', 'Voice band', true, 'voice_off')
    },
    [note],
  )

  /** Show Settings and hand it {tab, section}; the page answers what it opened. */
  const openSettings = useCallback(
    async (tab: unknown, section: unknown) => {
      if (pageRef.current !== 'settings') navigate('settings')
      await waitForPage('settings')
      return sendToPage('settings', {
        intent: 'open_settings',
        args: { tab, section },
        heard: '',
        text: '',
        confidence: 1,
        needs_confirm: false,
        say: '',
      })
    },
    [navigate],
  )

  /** Payments: bring up the customer or supplier side the command is for (the panel remounts). */
  const showPaymentSide = useCallback(async (kind: string) => {
    if (kind !== 'customer' && kind !== 'supplier') return
    if (placeOf(pageRef.current).section === kind) return
    await sendToPage('settings', {
      intent: 'open_settings',
      args: { tab: 'payment', section: kind },
      heard: '',
      text: '',
      confidence: 1,
      needs_confirm: false,
      say: '',
    })
    await waitFor(() => placeOf(pageRef.current).section === kind, 1500)
    // The new side's panel registers its voice handler as it mounts.
    await wait(200)
  }, [])

  /** Show a numbered list and wait for its number (or a click). */
  const showChoice = useCallback(
    (heard: string, c: Choice) => {
      setChoice(c)
      const n = c.options.length
      const shownList =
        n > MAX_OPTIONS
          ? `Konta? ${n} paryay — yaadit baga, number sanga`
          : `Konta?  ${c.options.map((o, i) => `${i + 1}. ${optionLabel(o)}`).join('   ')}`
      note(heard, shownList, true, optionsKey(n), 'Konta? number sanga', 'ask')
      beep(660, 60)
    },
    [note, setChoice],
  )

  /** Focus the field `name` and open its list; null when this page has no such field. */
  const openField = useCallback(
    async (name: string, text: string): Promise<VField['el'] | null> => {
      const el = findVoiceField(name)
      if (!el) return null
      // "Doctor dakhav": the name left in the box filtered the list down to nothing.
      // Empty it first so every name shows (the numbered shortcuts on top), or put
      // exactly the words that were said.
      const list = !!el.closest('[data-voice-wrap]')
      if (!text && list && CLEAR_ON_OPEN.has(name) && el.value) setFieldText(el, '')
      startField(name, el)
      if (text) setFieldText(el, text)
      // The list fills a moment later (the medicine list asks the engine).
      // A pick list (and any typed words) waits for its first rows; a plain box a moment.
      await wait(150)
      await waitFor(() => optionEls(el).length > 0, list && (text || CLEAR_ON_OPEN.has(name)) ? 900 : 100)
      refreshField()
      return el
    },
    [refreshField, startField],
  )

  // ── the voice-opened dropdown: focus / type / clear / pick / close ──
  const runField = useCallback(
    async (cmd: VoiceCommand) => {
      const heard = cmd.heard
      const a = cmd.args || {}
      const intent = cmd.intent
      if (intent === 'focus_field') {
        const name = String(a.field || '').trim().toLowerCase()
        const text = String(a.text ?? '').trim()
        const el = await openField(name, text)
        if (!el) {
          note(heard, `${fieldWord(name) || 'He field'}: ya page var nahi`, false, { key: 'field_not_here', cue: 'warn' })
          return
        }
        const n = fieldViewRef.current?.options.length ?? 0
        const byShortcut = shortcutNumbered(el)
        note(
          heard,
          `${fieldWord(name)} ughadla${text ? ` — "${text}"` : ''}${
            byShortcut ? ' · shortcut number sanga, baki naav sanga' : n ? ` · ${n} naav, number sanga` : ''
          }`,
          true,
          FIELD_KEY[name] || 'field_search_opened',
        )
        return
      }
      // "Doctor clear kar" / "customer kadh": THAT field is emptied, its list open or not, and
      // nothing is asked. Sales clears the name with what came with it; elsewhere the box is emptied.
      const clearName = intent === 'clear_field' ? String(a.field || '').trim().toLowerCase() : ''
      if (clearName) {
        if (fieldRef.current && fieldRef.current.el !== findVoiceField(clearName)) endField(true)
        const word = fieldWord(clearName)
        let r: VoiceResult | null = null
        if (pageRef.current === 'sales' && CLEAR_ON_OPEN.has(clearName)) {
          r = await sendToPage('sales', { ...cmd, needs_confirm: false })
        }
        if (!r) {
          const el = findVoiceField(clearName)
          if (!el) {
            note(heard, `${word || 'He field'}: ya page var nahi`, false, { key: 'field_not_here', cue: 'warn' })
            return
          }
          setFieldText(el, '')
        } else if (!r.ok) {
          note(heard, r.say || `${word} pusla nahi`, false, { key: 'work_failed', cue: 'warn' })
          return
        }
        // Its list open: it now shows every name.
        if (fieldRef.current) {
          await wait(150)
          refreshField()
        }
        note(heard, `${word} pusla`, true, 'field_cleared')
        return
      }
      // The others work on the open field; a field the shop focused by hand counts too.
      let f = fieldRef.current
      if (!f) {
        const act = document.activeElement
        const name = act instanceof HTMLElement ? (act.getAttribute('data-voice-field') || '').split(/\s+/)[0] : ''
        if (name && (act instanceof HTMLInputElement || act instanceof HTMLTextAreaElement)) {
          startField(name, act)
          f = fieldRef.current
        }
      }
      if (!f) {
        if (intent === 'close_field') {
          note(heard, 'Band karayla kahi ughadla nahi', false, { key: 'nothing_to_close', cue: 'warn' })
        } else {
          note(heard, 'Aadhi field ughada: "grahak ughad", "doctor ughad"', false, { key: 'field_not_here', cue: 'warn' })
        }
        return
      }
      const word = fieldWord(f.name)
      if (intent === 'close_field') {
        endField(true)
        note(heard, `${word} band kela`, true, 'field_closed')
        return
      }
      if (intent === 'type_text' || intent === 'clear_field') {
        const text = intent === 'clear_field' ? '' : String(a.text ?? '').trim()
        if (document.activeElement !== f.el) openVoiceField(f.el)
        setFieldText(f.el, text)
        await wait(250)
        refreshField()
        if (intent === 'clear_field') note(heard, `${word}: rikama kela`, true, 'field_cleared')
        else note(heard, `${word}: "${text}"`, true, 'typed')
        return
      }
      // pick_option: the exact name first (a spoken shortcut number comes with its name),
      // then by number (the shortcut number or the position), else by the words.
      const hasN = a.n != null && a.n !== '' && Number.isFinite(Number(a.n))
      const pickText = String(a.text ?? '').trim()
      let row = pickText ? findExactOption(f.el, pickText) : null
      if (!row && hasShortcut(a) && pickText) {
        // The shortcut's name is not among the rows shown: look it up in the list.
        const before = f.el.value
        setFieldText(f.el, pickText)
        await wait(250)
        row = findExactOption(f.el, pickText)
        if (!row) {
          setFieldText(f.el, before)
          await wait(150)
          refreshField()
          note(heard, `${kindWord(f.name)} ${a.shortcut}: "${pickText}" yaadit nahi`, false, {
            key: 'shortcut_not_in_list',
            cue: 'warn',
          })
          return
        }
      }
      if (!row) row = findOption(f.el, hasN ? { n: Number(a.n) } : { text: pickText })
      if (!row) {
        const count = optionEls(f.el).length
        if (!count) {
          note(heard, `${word}: yaadit kahi nahi`, false, { key: 'list_empty', cue: 'warn' })
        } else if (hasN && shortcutNumbered(f.el)) {
          note(heard, `${kindWord(f.name)} ${a.n} la naav thevla nahi`, false, { key: 'shortcut_missing', cue: 'warn' })
        } else if (hasN) {
          note(heard, `${a.n} nahi — 1 te ${Math.min(count, 30)} madhla sanga`, false, { key: 'choice_out_of_range', cue: 'warn' })
        } else {
          note(heard, `"${a.text}" yaadit nahi`, false, { key: 'list_empty', cue: 'warn' })
        }
        return
      }
      const label = optionText(row)
      // Exactly what a mouse click on the row does.
      row.click()
      // A medicine goes on to its batches: wait until the list is gone, or shows new rows.
      await wait(120)
      await waitFor(() => {
        if (fieldRef.current !== f || document.activeElement !== f.el) return true
        const wrap = f.el.closest('[data-voice-wrap]')
        const list = wrap?.querySelector<HTMLElement>('[role="listbox"]')
        if (!list || !list.getClientRects().length) return true
        return optionEls(f.el).some((o) => optionText(o) !== label)
      }, 2500)
      const still = fieldRef.current === f && document.activeElement === f.el && optionEls(f.el).length > 0
      if (still) {
        refreshField()
        const n = optionEls(f.el).length
        note(heard, `${word}: ${label} — ata ${n} paryay, number sanga`, true, { key: ['option_picked', optionsKey(n)] }, undefined, 'ask')
        return
      }
      if (fieldRef.current === f) endField(document.activeElement === f.el)
      note(heard, hasShortcut(a) ? `${kindWord(f.name)} ${a.shortcut}: ${label}` : `${word}: ${label}`, true, 'option_picked')
    },
    [endField, note, openField, refreshField, startField],
  )

  /** set_field {field, name, shortcut}: open the page's supplier / village box, pick the row
   *  whose text is `name` (case ignored) and close it -- the pick a mouse would make. */
  const setFieldByName = useCallback(
    async (cmd: VoiceCommand) => {
      const heard = cmd.heard
      const a = cmd.args || {}
      const name = String(a.field || '').trim().toLowerCase()
      const want = String(a.name ?? '').trim()
      const label = `${kindWord(name)}${hasShortcut(a) ? ` ${a.shortcut}` : ''}`
      const el = findVoiceField(name)
      if (!el) {
        note(heard, `${fieldWord(name) || 'He field'}: ya page var nahi`, false, { key: 'field_not_here', cue: 'warn' })
        return
      }
      if (!want) {
        note(heard, `${label} la naav thevla nahi`, false, { key: 'shortcut_missing', cue: 'warn' })
        return
      }
      const before = el.value
      startField(name, el)
      setFieldText(el, want)
      await wait(250)
      refreshField()
      const row = findExactOption(el, want)
      if (!row) {
        setFieldText(el, before)
        endField(true)
        note(heard, `${label}: "${up(want)}" yaadit nahi`, false, { key: 'shortcut_not_in_list', cue: 'warn' })
        return
      }
      const picked = optionText(row)
      row.click()
      await wait(120)
      if (fieldRef.current?.el === el) endField(document.activeElement === el)
      note(heard, `${label}: ${picked.toUpperCase()}`, true, 'field_set')
    },
    [endField, note, refreshField, startField],
  )

  /** set_field {field, value}: "grahak number 98…", "gav Wadshingi" -- the words go into that
   *  box as if typed (a phone as its digits), and a list it opens is shut again. */
  const setFieldValue = useCallback(
    async (cmd: VoiceCommand) => {
      const heard = cmd.heard
      const a = cmd.args || {}
      const name = String(a.field || '').trim().toLowerCase()
      const phone = /phone/.test(name)
      const value = phone ? String(a.value ?? '').replace(/\D/g, '') : String(a.value ?? '').trim()
      const el = findVoiceField(name)
      if (!el) {
        note(heard, `${fieldWord(name) || 'He field'}: ya page var nahi`, false, { key: 'field_not_here', cue: 'warn' })
        return
      }
      if (!value) {
        note(heard, `${fieldWord(name)}: kay lihu te sanga`, false, { key: 'say_again', cue: 'warn' })
        return
      }
      if (fieldRef.current) endField(fieldRef.current.el !== el)
      const list = !!el.closest('[data-voice-wrap]')
      // In and out of a list box, as the keyboard would: leaving it is what keeps a new village.
      if (list) el.focus()
      setFieldText(el, value)
      if (list) {
        // The page reads the new text on its next render; leave after that.
        await settle()
        closeVoiceField(el)
      }
      // Sales has one "Address (Village)" box: an address said goes there, and the line says so.
      const shared = name === 'address' && /\bvillage\b/.test(el.getAttribute('data-voice-field') || '')
      note(
        heard,
        shared ? `Gav: ${value} (vegla address field nahi — Gav madhe lihila)` : `${fieldWord(name)}: ${value}`,
        true,
        'typed',
      )
    },
    [endField, note],
  )

  // ── a medicine that waits for a doctor / customer ──
  const retryRef = useRef<Retry | null>(null)
  // Replies being handled now: a pick made meanwhile waits for the reply to finish.
  const replyingRef = useRef(0)

  /** The medicine could not go on without a doctor / customer: hold it, open that list
   *  (emptied, every name showing) and ask for one. Choosing one adds the medicine. */
  const askNeeded = useCallback(
    async (heard: string, cmd: VoiceCommand, f: ReturnType<typeof failNote>) => {
      const need = f.need!
      const old = retryRef.current
      const args = { ...(cmd.args || {}) }
      delete args.options
      const held = { ...cmd, args, needs_confirm: false }
      retryRef.current = {
        need,
        cmds: old && old.need === need && !old.picked ? [...old.cmds, held] : [held],
        picked: false,
      }
      await openField(need, '')
      note(
        heard,
        f.say,
        false,
        f.said,
        undefined,
        'ask',
      )
    },
    [note, openField],
  )

  /** A doctor / customer was chosen: add the held-back medicine(s), once. */
  const runRetry = useCallback(async () => {
    const r = retryRef.current
    if (!r || !r.picked) return
    retryRef.current = null
    // The pick lands in the page's state on its next render.
    await wait(120)
    for (const c of r.cmds) {
      let res: VoiceResult | null
      try {
        res = await sendToPage('sales', c)
      } catch (e) {
        res = { ok: false, say: e instanceof Error ? e.message : String(e) }
      }
      const name = up(c.args?.medicine) || 'Aushadh'
      if (res?.ok) {
        note(c.heard, `${name} add zala — ${res.say}`, true, 'medicine_added')
        beep(880, 70)
        continue
      }
      const f = failNote(c, res || { ok: false, say: 'Billing tayar nahi' })
      // A doctor chosen, and now it wants a customer too (or the other way round).
      if (f.need && f.need !== r.need) await askNeeded(c.heard, c, f)
      else note(c.heard, f.say, false, f.said, undefined, f.tone)
    }
  }, [askNeeded, note])
  const runRetryRef = useRef(runRetry)
  runRetryRef.current = runRetry

  // The page says a doctor / customer was chosen (mouse, keyboard or voice).
  useEffect(() => {
    const on = (e: Event) => {
      const r = retryRef.current
      const field = (e as CustomEvent<{ field?: string }>).detail?.field
      if (!r || r.picked || field !== r.need) return
      r.picked = true
      // A spoken pick finishes its own reply first; a mouse pick goes on now.
      if (!replyingRef.current) void runRetryRef.current()
    }
    window.addEventListener(VOICE_PICKED_EVENT, on)
    return () => window.removeEventListener(VOICE_PICKED_EVENT, on)
  }, [])

  // Voice OFF: nothing is held back any more.
  useEffect(() => {
    if (!handsFree) retryRef.current = null
  }, [handsFree])

  // ── doing the command ──
  const execute = useCallback(
    async (cmd: VoiceCommand, confirmed = false) => {
      const heard = cmd.heard
      let a = cmd.args || {}
      // A spoken name (medicine, doctor, customer) the service was unsure of: its
      // guesses come numbered, and the shop says or clicks the number.
      const field = CHOICE_FIELD[cmd.intent]
      const options = field ? cleanOptions(a.options, MAX_OPTIONS) : []
      const named = field ? String(a[field] ?? '').trim() : ''
      // Only what the page on screen can take (see wrongPlace): refused before any list or question.
      const refused = wrongPlace(pageRef.current, cmd.intent)
      if (refused) {
        note(heard, refused, false, { key: 'action_not_possible', cue: 'warn' })
        return
      }
      if (field && options.length && !confirmed) {
        if (cmd.needs_confirm || (!named && field === 'medicine')) {
          showChoice(heard, { cmd, options, field })
          return
        }
        // The service is sure: no list, just do it. A doctor / customer it is sure of
        // but did not name is its first guess.
        if (!named) {
          const first = options[0]
          const args: Record<string, any> = { ...a, [field]: first.replace(NEW_MARK, '') }
          if (NEW_MARK.test(first)) args.new = true
          delete args.options
          cmd = { ...cmd, args }
          a = args
        }
      }
      // Ask first for anything that saves, prints or was a guess.
      if (cmd.needs_confirm && !confirmed) {
        setPending(cmd)
        const ownSave = cmd.intent === 'save_bill' ? SAVE_ASK[placeOf(pageRef.current).page] : ''
        const ask = ownSave || cmd.say || CONFIRM_TEXT[cmd.intent] || 'He karu?'
        note(heard, `${ask}  — Yes / No?`, true, ownSave ? 'confirm_generic' : confirmKey(cmd.intent), `${ask}. Yes ki No?`, 'ask')
        beep(660, 60)
        return
      }
      const intent = cmd.intent

      if (FIELD_INTENTS.has(intent)) {
        await runField(cmd)
        return
      }

      if (intent === 'navigate') {
        const want = String(a.page || '').trim().toLowerCase()
        const pg = PAGE_ALIASES[want] || want
        if (PAGES.has(pg)) {
          navigate(pg as PageId)
          note(heard, `${PAGE_WORDS[pg] || pg} ughadla`, true, pageKey(pg))
          return
        }
        // "Ledger ughad", "Reorder ughad": a Settings tab said as a page.
        if (SETTINGS_TABS.some((t) => t.id === pg)) {
          const r = await openSettings(pg, null)
          note(heard, r?.say || 'Settings ughadla', r?.ok ?? false, settingsSaid(r, pg, null))
          return
        }
        note(heard, `"${a.page}" naavacha page nahi`, false, { key: 'page_not_found', cue: 'warn' })
        return
      }
      if (intent === 'open_settings') {
        // The Settings page sets its own tab / section / sub-tab (voice handler
        // in SettingsPage), so a repeat of the same command works and a
        // sub-tab (Payment → Customer, Alerts → Expired) opens too.
        const r = await openSettings(a.tab, a.section)
        note(heard, r?.say || cmd.say || 'Settings ughadla', r?.ok ?? false, settingsSaid(r, a.tab, a.section))
        return
      }
      if (intent === 'page_filter' || intent === 'page_action') {
        const target = String(a.page || '').trim().toLowerCase()
        const host = SETTINGS_HOSTED[target]
        let moved = false
        if (host) {
          const opened = await openSettings(host.tab, a.section ?? host.section ?? null)
          if (!opened?.ok) {
            note(heard, opened?.say || 'Settings ughadla nahi', false, settingsSaid(opened, host.tab, null))
            return
          }
          await waitForPage(target, 6000)
        } else if (PAGES.has(target)) {
          if (pageRef.current !== target) {
            moved = true
            navigate(target as PageId)
            await waitForPage(target)
          }
        } else {
          note(heard, `"${a.page}" naavacha page nahi`, false, { key: 'page_not_found', cue: 'warn' })
          return
        }
        const r = await sendToPage(target, { ...cmd, needs_confirm: false })
        // "Inventory ughadli, out of stock dakhavtoy": say the page too when voice moved there.
        const said = r?.say || `${PAGE_WORDS[target] || target}: he kaam ajun voice var nahi`
        const res = resultSaid(cmd, r)
        note(heard, moved && r?.ok ? `${PAGE_WORDS[target] || target} ughadla — ${said}` : said, r?.ok ?? false, {
          key: moved && r?.ok ? [pageKey(target), ...([] as string[]).concat(res.key)] : res.key,
          cue: res.cue,
        })
        if (r?.ok) beep(880, 70)
        return
      }
      // "Aajchi vikri kiti?", "kaalcha nafa", "ya mahinyache bill": answered on the bar from
      // the same totals Sales History shows; the page on screen stays.
      if (intent === 'sales_total' || intent === 'today_sales') {
        const period = SALES_PERIOD[String(a.period || '')] ? String(a.period) : 'today'
        const what = a.what === 'profit' || a.what === 'bills' ? a.what : 'sales'
        const w = SALES_PERIOD[period]
        const range = presetRange(period)
        try {
          const res = await fetchSalesHistory({ from: range?.from, to: range?.to })
          if (res.server_error) throw new Error(res.server_error)
          const s = res.summary || {}
          const total = Number(s.total) || 0
          const bills = Number(s.bills) || 0
          if (what === 'profit') {
            if (s.profit == null || s.profit === '') {
              note(heard, `${w.profit}: ha aakda milala nahi`, false, { key: 'work_failed', cue: 'warn' })
              return
            }
            const p = Number(s.profit) || 0
            note(heard, `${w.profit}: ₹${rupees(p)} (vikri ₹${rupees(total)})`, true, {
              key: ['lead_profit', ...amountKeys(p)],
              skipMissing: true,
            })
          } else if (what === 'bills') {
            note(heard, `${w.bills}: ${bills} (₹${rupees(total)})`, true, {
              key: [...numberKeys(bills), 'word_bills'],
              skipMissing: true,
            })
          } else {
            note(heard, `${w.sales}: ₹${rupees(total)} (${bills} bill)`, true, {
              key: [w.lead, ...amountKeys(total)],
              skipMissing: true,
            })
          }
        } catch (e) {
          note(heard, `Vikri baghta aali nahi: ${e instanceof Error ? e.message : e}`, false, { key: 'work_failed', cue: 'warn' })
        }
        return
      }
      // "Ramesh Patil cha baki kiti?": the live balance Sales' Previous Due reads.
      if (intent === 'due_query') {
        const name = String(a.name ?? '').trim()
        if (!name) {
          note(heard, 'Konacha baki? Grahakache naav sanga', false, { key: 'say_customer_name', cue: 'warn' })
          return
        }
        try {
          const res = await lookupCustomerByName(name, true)
          const c = res.found ? res.customer : null
          if (!c) {
            note(heard, `Grahak sapadla nahi: ${up(name)}`, false, { key: 'customer_not_found', cue: 'warn' })
            return
          }
          const due = Number(c.due) || 0
          const credit = Number(c.credit) || 0
          if (credit > 0 && due <= 0) {
            note(heard, `${c.name}: jama ₹${rupees(credit)} (baki nahi)`, true, {
              key: ['lead_jama', ...amountKeys(credit)],
              skipMissing: true,
            })
          } else {
            note(heard, `${c.name}: baki ₹${rupees(due)}`, true, { key: ['lead_baki', ...amountKeys(due)], skipMissing: true })
          }
        } catch (e) {
          note(heard, `Baki baghta ala nahi: ${e instanceof Error ? e.message : e}`, false, { key: 'work_failed', cue: 'warn' })
        }
        return
      }
      if (intent === 'stock_query' || intent === 'expiry_query') {
        try {
          const res = await fetchMedicineBatches(a.medicine)
          const batches = res.batches || []
          if (!batches.length) {
            note(heard, `${a.medicine}: stock nahi`, false, { key: 'stock_nahi', cue: 'warn' })
            return
          }
          if (intent === 'stock_query') {
            const total = batches.reduce((s, b) => s + (Number(b.available ?? b.stock) || 0), 0)
            note(heard, `${a.medicine}: ${total} uplabdh (${batches.length} batch)`, true,
              total > 0 ? 'stock_available' : { key: 'stock_nahi', cue: 'warn' })
          } else {
            const soonest = [...batches].sort((x, y) => String(x.expiry).localeCompare(String(y.expiry)))[0]
            note(heard, `${a.medicine}: javalchi expiry ${soonest.expiry} (batch ${soonest.batch})`, true, 'expiry_shown')
          }
        } catch (e) {
          note(heard, `Stock baghta ala nahi: ${e instanceof Error ? e.message : e}`, false, {
            key: 'stock_check_failed',
            cue: 'error',
          })
        }
        return
      }
      if (intent === 'search') {
        // Payments is the Settings page on its Payment tab.
        const here = pageRef.current === 'payment' ? 'settings' : pageRef.current
        const r = await sendToPage(here, cmd)
        note(heard, r?.say || `Ya page var shodh nahi — ${cmd.say}`, r?.ok ?? false,
          r ? resultSaid(cmd, r) : { key: 'search_not_here', cue: 'warn' })
        return
      }

      // "Doctor konte aahet?", "Dolo sarkhi aushadha": a numbered list to pick from.
      if (intent === 'list_options') {
        const run = LIST_RUN[String(a.kind || '').toLowerCase()]
        const opts = cleanOptions(a.options, MAX_LIST_OPTIONS)
        if (!run || !opts.length) {
          note(heard, `${a.query ? `"${a.query}": ` : ''}Yaadit kahi nahi`, false, { key: 'list_empty', cue: 'warn' })
          return
        }
        // The pick runs like the spoken command would, so it moves to Sales (INTENT_PAGE) then.
        const args: Record<string, any> = run.intent === 'add_medicine' ? { qty: 1 } : {}
        showChoice(heard, {
          cmd: { ...cmd, intent: run.intent, args, needs_confirm: false },
          options: opts,
          field: run.field,
        })
        return
      }
      // "Doctor shortcut 2 Joshi": put a name on a number (the service keeps the list).
      if (intent === 'set_shortcut') {
        const k = String(a.kind || '').toLowerCase()
        const kind = isShortcutKind(k) ? k : 'doctor'
        const n = Number(a.n)
        const name = String(a.name ?? '').trim()
        const word = kindWord(kind)
        try {
          if (!Number.isInteger(n) || n < 1 || n > MAX_SHORTCUTS) throw new Error(`number ${a.n ?? '?'}`)
          await voiceSetShortcut(kind, n, name)
          note(heard, name ? `${word} ${n} = ${name.toUpperCase()}` : `${word} ${n} rikama kela`, true, 'shortcut_saved')
          beep(880, 70)
        } catch (e) {
          note(heard, `${word} ${a.n ?? '?'}: shortcut save zala nahi — ${e instanceof Error ? e.message : e}`, false, {
            key: 'shortcut_not_saved',
            cue: 'warn',
          })
        }
        return
      }
      // "Supplier 1" / "Gaon 3": put that name in the page's supplier / village box.
      if (intent === 'set_field') {
        if (a.value != null) await setFieldValue(cmd)
        else await setFieldByName(cmd)
        return
      }
      // "Doctor 5" with nothing saved on 5.
      if (intent === 'shortcut_missing') {
        note(heard, `${kindWord(a.kind)} ${a.n ?? '?'} la naav thevla nahi — Settings › Voice madhe theva`, false, {
          key: 'shortcut_missing',
          cue: 'warn',
        })
        return
      }
      // "Doctor dropdown don": the list voice opened shuts before the number's name goes in.
      if ((intent === 'set_doctor' || intent === 'set_customer') && hasShortcut(a) && fieldRef.current) endField(true)
      // A doctor / customer with no name and no guesses: not in the list.
      if ((intent === 'set_doctor' || intent === 'set_customer') && !String(a.name ?? '').trim()) {
        const who = intent === 'set_doctor' ? 'Doctor' : 'Grahak'
        note(heard, cmd.say || `${who} sapadla nahi`, false, {
          key: intent === 'set_doctor' ? 'doctor_not_found' : 'customer_not_found',
          cue: 'warn',
        })
        return
      }

      // The form on screen takes its own commands (Returns, Payments, Reorder); the rest go
      // to the page they belong to (wrongPlace above has refused a sale command elsewhere).
      const here = placeOf(pageRef.current).page
      const local = !!PAGE_INTENTS[here]?.includes(intent)
      const target = local ? here : INTENT_PAGE[intent]
      if (target) {
        if (!local && pageRef.current !== target) {
          navigate(target as any)
          await waitForPage(target)
        }
        // "Ramesh 500 cash" while Supplier Payment shows: Customer Payment opens first.
        if (intent === 'payment') await showPaymentSide(String(a.kind || ''))
        // The page's handler reads its state through refs that a render updates: let the
        // previous command ("Dolo jod", "cash 100") land before "shevatcha kadh" / "save" reads it.
        await settle()
        let r: VoiceResult | null
        try {
          r = await sendToPage(target, { ...cmd, needs_confirm: false })
        } catch (e) {
          // A refusal the engine sent without a code, or no engine at all: it used to end in silence.
          r = { ok: false, say: e instanceof Error ? e.message : String(e) }
        }
        if (r && !r.ok) {
          const f = failNote(cmd, r)
          // Needs a doctor / customer first: its list opens, and choosing one adds this medicine.
          if (f.need && target === 'sales') await askNeeded(heard, cmd, f)
          else note(heard, f.say, false, f.said, undefined, f.tone)
          return
        }
        // "Doctor 2": the number said is shown with the name it stands for.
        const numbered =
          r?.ok && (intent === 'set_doctor' || intent === 'set_customer') && hasShortcut(a)
            ? `${intent === 'set_doctor' ? 'Doctor' : 'Grahak'} ${a.shortcut}: ${up(a.name)}`
            : ''
        const absent = local
          ? `He ya ${PAGE_WORDS[target] || target} panel var chalat nahi${target === 'reorder' ? " — 'Load by Supplier' ughadun supplier nivda" : ''}`
          : `${PAGE_WORDS[target]} tayar nahi`
        note(heard, numbered || r?.say || absent, r?.ok ?? false, resultSaid(cmd, r))
        if (r?.ok) beep(880, 70)
        return
      }
      // unknown, unknown_medicine, none, ask
      const said: Said =
        intent === 'unknown_medicine' ? { key: 'medicine_not_found', cue: 'error' }
          : intent === 'ask' ? { key: 'say_again' }
            : { key: 'not_understood', cue: 'error' }
      note(heard, cmd.say || 'Samajla nahi', false, said)
    },
    [askNeeded, endField, navigate, note, openSettings, runField, setFieldByName, setFieldValue, setPending, showChoice, showPaymentSide],
  )

  /** Run commands in order; stop at the first that asks (Yes / No or a number) and keep the rest. */
  const runAll = useCallback(
    async (list: VoiceCommand[]) => {
      for (let i = 0; i < list.length; i++) {
        let cmd = list[i]
        // "Cash 100, baki udhari": the cash said in the same breath stays; Udhari
        // alone would have zeroed it.
        if (cmd.intent === 'udhari' && list.slice(0, i).some((c) => c.intent === 'cash' || c.intent === 'online')) {
          cmd = { ...cmd, args: { ...(cmd.args || {}), keep_paid: true } }
        }
        // A save always asks first (Sales, a return, a payment), whatever the service said.
        if (cmd.intent === 'save_bill' && !cmd.needs_confirm) cmd = { ...cmd, needs_confirm: true }
        if (cmd.needs_confirm) {
          queueRef.current = list.slice(i + 1)
          await execute(cmd)
          return
        }
        await execute(cmd)
      }
      queueRef.current = []
    },
    [execute],
  )

  /** Number n of the shown list: run the original command with that medicine. */
  const pickChoice = useCallback(
    async (n: number, heard = '') => {
      const c = choiceRef.current
      if (!c) return
      const picked = c.options[n - 1]
      if (!picked) {
        note(heard, `${n || '?'} nahi — 1 te ${c.options.length} madhla sanga`, false, {
          key: 'choice_out_of_range',
          cue: 'warn',
        })
        return
      }
      setChoice(null)
      const rest = queueRef.current
      queueRef.current = []
      const args: Record<string, any> = { ...(c.cmd.args || {}), [c.field]: picked.replace(NEW_MARK, '') }
      delete args.options
      // "+ Bhath E" is a new doctor / customer; a name from the list is a known one.
      if (c.field === 'name') {
        if (NEW_MARK.test(picked)) args.new = true
        else delete args.new
      }
      await execute({ ...c.cmd, heard: heard || c.cmd.heard, args, needs_confirm: false }, true)
      if (rest.length) await runAll(rest)
    },
    [execute, note, runAll, setChoice],
  )

  /** After a popup button: a popup that comes up next ("Bill Saved", "Payment Required") is read out too. */
  const followDialog = useCallback(
    async (pressed: HTMLElement, pressedText: string) => {
      const next = await waitFor(() => {
        const d = topDialog()
        if (!d) return null
        const t = dialogText(d)
        return (d !== pressed || !pressed.isConnected) && t && t !== pressedText ? d : null
      }, 4000, 150)
      if (!next) return
      const text = dialogText(next)
      const good = /saved|success/i.test(text) && !/not saved|failed|error|could not/i.test(text)
      note(
        '',
        `Popup: ${text}`,
        good,
        good ? (/bill/i.test(text) ? 'bill_saved' : 'popup_opened') : { key: 'popup_opened', cue: 'warn' },
        undefined,
        'ask',
      )
    },
    [note],
  )

  const answer = useCallback(
    async (reply: VoiceReply) => {
      rawHeardRef.current = String(reply.heard || reply.command?.heard || '').trim()
      if (!reply.ok || !reply.command) {
        understoodRef.current = 'Samajla nahi'
        note(reply.heard || '', reply.error || 'Voice service kadun uttar nahi', false, {
          key: 'service_no_reply',
          cue: 'error',
        })
        return
      }
      const cmd = reply.command
      const list = reply.commands && reply.commands.length ? reply.commands : [cmd]
      understoodRef.current = list.map(understoodLine).join(' · ')
      // The security guard refused this sentence: NOTHING of it runs -- no command, and it
      // answers no question or list on screen either (those are dropped, as for any new command).
      const refused = list.find((c) => c.intent === 'refuse')
      if (refused) {
        setChoice(null)
        setPending(null)
        queueRef.current = []
        note(refused.heard || cmd.heard, REFUSED_TEXT, false, { key: 'voice_refused', cue: 'warn' }, undefined, 'refuse')
        return
      }
      if (cmd.intent === 'voice_off') {
        setHandsFree(false)
        return
      }
      // A medicine waiting for a doctor / customer: saying anything but that choice drops it.
      const held = retryRef.current
      if (held && !held.picked && !list.every((c) => keepsRetry(c, held.need))) retryRef.current = null
      // Not understood: a question or list on screen stays, so "ho" misheard does not lose the save.
      const lost = list.length === 1 && NOT_UNDERSTOOD.has(cmd.intent)
      // 1. The bar's own numbered list.
      const shownChoice = choiceRef.current
      if (shownChoice && !lost) {
        if (cmd.intent === 'choose' || (cmd.intent === 'pick_option' && cmd.args?.n != null)) {
          await pickChoice(Number(cmd.args?.n), cmd.heard)
          return
        }
        if (cmd.intent === 'pick_option' && String(cmd.args?.text ?? '').trim()) {
          // A name said for the bar's own list: exact, then starts-with, then contains.
          const want = String(cmd.args?.text).trim().toLowerCase()
          const opts = shownChoice.options.map((o) => o.replace(NEW_MARK, '').toLowerCase())
          let i = opts.findIndex((o) => o === want)
          if (i < 0) i = opts.findIndex((o) => o.startsWith(want))
          if (i < 0) i = opts.findIndex((o) => o.includes(want))
          if (i >= 0) {
            await pickChoice(i + 1, cmd.heard)
            return
          }
        }
        if (cmd.intent === 'yes' && shownChoice.options.length === 1) {
          await pickChoice(1, cmd.heard)
          return
        }
        setChoice(null)
        queueRef.current = []
        if (cmd.intent === 'no') {
          note(cmd.heard, 'Radd kela', true, 'cancelled')
          return
        }
        // Anything else: drop the list and do the new command.
      }
      // 2. The bar's own Yes / No question comes before any popup on the page.
      const waiting = pendingRef.current
      if (waiting && lost) {
        note(cmd.heard, `Samajla nahi — ${waiting.say || CONFIRM_TEXT[waiting.intent] || 'He karu?'} Yes / No?`, false,
          { key: [confirmKey(waiting.intent)], cue: 'error' }, undefined, 'ask')
        return
      }
      if (waiting) {
        if (cmd.intent === 'yes') {
          understoodRef.current = `Ho → ${understoodLine(waiting)}`
          setPending(null)
          await execute(waiting, true)
          // What was said after the question, then what the sentence said after "yes".
          const rest = [...queueRef.current, ...list.slice(1)]
          queueRef.current = []
          if (rest.length) {
            await settle()
            await runAll(rest)
          }
          return
        }
        if (cmd.intent === 'no') {
          setPending(null)
          queueRef.current = []
          note(cmd.heard, 'Radd kela', true, 'cancelled')
          return
        }
        // Anything else: drop the question and do the new command.
        setPending(null)
        queueRef.current = []
      }
      // 3. A popup on the page: press its button. One still being drawn (the save's
      // answer) gets a moment to appear before "nothing was asked".
      if (list.length === 1 && (cmd.intent === 'yes' || cmd.intent === 'no' || cmd.intent === 'close')) {
        const dlg = topDialog() || (fieldRef.current ? null : await waitFor(() => topDialog(), 800))
        if (dlg) {
          const before = dialogText(dlg)
          const d = answerDialog(cmd.intent)
          if (d) {
            note(cmd.heard, before ? `${d.say} — ${before}` : d.say, d.ok, d.said)
            void followDialog(dlg, before)
            return
          }
        }
        // 4. The dropdown voice opened: "ho" picks its only row, "nahi" / "band" closes it.
        const f = fieldRef.current
        if (f) {
          if (cmd.intent === 'yes') {
            const n = optionEls(f.el).length
            if (n === 1) {
              await runField({ ...cmd, intent: 'pick_option', args: { text: optionText(optionEls(f.el)[0]) } })
            } else if (n) {
              note(cmd.heard, `Konta? 1 te ${Math.min(n, 30)} madhla number sanga`, false, 'which_number', undefined, 'ask')
            } else {
              note(cmd.heard, 'Yaadit kahi nahi', false, { key: 'list_empty', cue: 'warn' })
            }
            return
          }
          await runField({ ...cmd, intent: 'close_field', args: {} })
          return
        }
        if (cmd.intent === 'close') {
          note(cmd.heard, 'Band karayla kahi ughadla nahi', false, { key: 'nothing_to_close', cue: 'warn' })
        } else {
          note(cmd.heard, 'Kahi vicharla nahi', false, { key: 'nothing_asked', cue: 'warn' })
        }
        return
      }
      if (cmd.intent === 'choose') {
        if (fieldRef.current) {
          await runField({ ...cmd, intent: 'pick_option', args: { n: cmd.args?.n } })
          return
        }
        note(cmd.heard, 'Nivadayla yaadi nahi', false, { key: 'no_list', cue: 'warn' })
        return
      }
      // Any other command leaves the dropdown first (a field command works on it).
      if (fieldRef.current && !lost && !list.some((c) => FIELD_INTENTS.has(c.intent))) endField(true)
      await runAll(list)
    },
    [endField, execute, followDialog, note, pickChoice, runAll, runField, setChoice, setHandsFree, setPending],
  )

  /** Every reply (hold-F1, typed, hands-free) comes through here; no list is left open after it. */
  const handleReply = useCallback(
    async (reply: VoiceReply) => {
      const done = voiceBusy()
      // A new reply: whatever the bar was still saying is old news.
      sayCancel()
      metaRef.current = replyMeta(reply)
      replyingRef.current++
      try {
        await answer(reply)
        // The doctor / customer this reply chose lets the held-back medicine go on.
        if (retryRef.current?.picked) await runRetry()
      } catch (e) {
        // Whatever broke says so on the bar; a thrown error used to end the command in silence.
        note(reply.heard || reply.command?.heard || '', `Kaam zala nahi: ${e instanceof Error ? e.message : e}`, false, {
          key: 'work_failed',
          cue: 'warn',
        })
      } finally {
        replyingRef.current--
        rawHeardRef.current = ''
        understoodRef.current = ''
        metaRef.current = null
        // A dropdown voice opened stays open; every other list is shut.
        if (!fieldRef.current) closeDropdownsSoon()
        // Pages focus their next field a moment later; keep the lists shut until then.
        window.setTimeout(done, 800)
      }
    },
    [answer, note, runRetry],
  )
  const handleReplyRef = useRef(handleReply)
  handleReplyRef.current = handleReply

  // ── hands-free: the service listens, the bar polls what it heard ──
  const serviceReady = phase !== 'offline' && phase !== 'loading' && health?.ready === true
  useEffect(() => {
    if (!handsFree || !serviceReady) {
      setEar({ listening: false, paused: false, level: 0 })
      setGates({ ignored: 0, waiting: false })
      return
    }
    let alive = true
    let ignoredSaid = false  // "dusra awaj" is said once per listening, not for every customer
    let wasWaiting = false
    let busy = false
    let fresh = true        // first poll after a start: take the service's seq, drop what it held before
    let lastSeq = 0
    let starting = false
    let lastTry = 0
    let failedOnce = false

    const start = async () => {
      if (starting) return
      starting = true
      lastTry = Date.now()
      try {
        const w = placeOf(pageRef.current)
        const r = await voiceListenStart(w.page, pendingWord(), fieldContext(), w.section)
        if (!alive) return
        if (!r.ok) {
          if (!failedOnce) noteRef.current('', `Mic ughadla nahi: ${r.error || 'mic'}`, false, micSaid(r.error || ''))
          failedOnce = true
          return
        }
        fresh = true
        failedOnce = false
        ignoredSaid = false
        // Wake word mode says what to do first (the level may still be on its way).
        const lv = levelRef.current ?? (await reloadVoiceLevel())
        if (!alive) return
        if (lv?.wake_word) {
          noteRef.current('', 'Voice ON — aadhi "Satpuda" mhana', true, { key: ['voice_on', 'wake_say_satpuda'] })
        } else {
          noteRef.current('', 'Voice ON — bola', true, 'voice_on')
        }
      } catch {
        /* service gone: the health poll will stop this loop */
      } finally {
        starting = false
      }
    }

    const tick = async () => {
      if (busy || !alive) return
      busy = true
      try {
        const r = await voiceListenNext(lastSeq)
        if (!alive) return
        setEar({ listening: !!r.listening, paused: !!r.paused, level: Math.max(0, Math.min(1, Number(r.level) || 0)) })
        // The gates (a newer service; an older one sends neither).
        const ignored = Math.max(0, Number(r.ignored) || 0)
        const waiting = r.waiting_for_command === true
        setGates((g) => (g.ignored === ignored && g.waiting === waiting ? g : { ignored, waiting }))
        if (waiting && !wasWaiting && !isListeningHeld()) sayOnlyRef.current('wake_heard_listening', 'Bola')
        wasWaiting = waiting
        if (ignored > 0 && !ignoredSaid && levelRef.current?.speaker_check && !isListeningHeld()) {
          ignoredSaid = true
          sayOnlyRef.current('other_voice_ignored', 'Dusra awaj, sodun dila')
        }
        // The service restarted, or was stopped by someone else: open the mic again.
        if (!r.listening && !starting && Date.now() - lastTry > 3000) {
          void start()
          return
        }
        const seq = Number(r.seq) || 0
        if (fresh || seq < lastSeq) {
          // Its numbering began again (or we just started): only what comes next is ours.
          fresh = false
          lastSeq = seq
          return
        }
        const items = (r.items || []).filter((it) => it.seq > lastSeq).sort((x, y) => x.seq - y.seq)
        for (const it of items) {
          lastSeq = it.seq
          if (!alive || !handsFreeRef.current) break
          // The enrollment wizard has the mic: what was heard is a sample, not a command.
          if (isListeningHeld()) continue
          if (!worthAnswering(it)) continue
          setWorking(true)
          try {
            await handleReplyRef.current({
              ok: !!it.command,
              heard: it.heard,
              command: it.command,
              commands: it.commands,
              timing: it.timing,
              error: it.error,
              engine: it.engine,
              llm_ms: it.llm_ms,
              alt: it.alt,
              gate: it.gate,
            })
            if (it.timing) setSecs(it.timing.asr_s)
          } finally {
            setWorking(false)
          }
        }
      } catch {
        if (alive) setEar((e) => ({ ...e, listening: false, level: 0 }))
      } finally {
        busy = false
      }
    }

    lastTry = 0
    void start()
    const t = window.setInterval(() => void tick(), POLL_MS)
    return () => {
      alive = false
      window.clearInterval(t)
      void voiceListenStop().catch(() => {})
    }
    // note and the reply handler are read through refs; the loop restarts only on ON / OFF.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handsFree, serviceReady])

  // The voice level (wake word, compare...) from a newer service; an older one has none.
  useEffect(() => {
    if (serviceReady) void reloadVoiceLevel()
  }, [serviceReady])

  // Know when a popup opens or closes, so the service expects "yes / no / close".
  useEffect(() => {
    if (!handsFree) {
      setDialogOpen(false)
      return
    }
    let t = 0
    const check = () => {
      t = 0
      setDialogOpen(!!topDialog())
    }
    const mo = new MutationObserver(() => {
      if (!t) t = window.setTimeout(check, 150)
    })
    mo.observe(document.body, { childList: true, subtree: true })
    check()
    return () => {
      mo.disconnect()
      if (t) window.clearTimeout(t)
    }
  }, [handsFree])

  // Tell the listening service where we are and what answer we wait for. With a
  // dropdown open that is "dropdown", its name and the rows it shows, in order;
  // the rows are re-read (debounced) whenever the list changes.
  const pendingNow =
    choice ? 'choice' : pending ? pending.intent : dialogOpen ? 'dialog' : fieldView ? 'dropdown' : ''
  const fieldKey = fieldView ? `${fieldView.name}\u0001${fieldView.options.join('\u0002')}` : ''
  useEffect(() => {
    if (!handsFree || !serviceReady) return
    const v = fieldViewRef.current
    void voiceListenContext(
      here.page,
      pendingNow,
      v ? { field: v.name, options: v.options } : { field: '', options: [] },
      here.section,
    ).catch(() => {})
  }, [handsFree, serviceReady, here.page, here.section, pendingNow, fieldKey])

  // ── talking (hold F1 / the mic button) ──
  const startTalking = useCallback(async () => {
    if (listeningRef.current || phase === 'offline' || phase === 'loading') return
    listeningRef.current = true
    setPhase('listening')
    beep(990, 60)
    try {
      const call = voiceStart()
      startCallRef.current = call
      const r = await call
      if (!r.ok) throw new Error(r.error || 'mic')
    } catch (e) {
      listeningRef.current = false
      setPhase('ready')
      const msg = e instanceof Error ? e.message : String(e)
      note('', `Mic ughadla nahi: ${msg}`, false, micSaid(msg))
    }
  }, [phase, note])

  const stopTalking = useCallback(async () => {
    if (!listeningRef.current) return
    listeningRef.current = false
    setPhase('thinking')
    beep(660, 50)
    try {
      const w = placeOf(pageRef.current)
      const reply = await voiceStop(w.page, pendingWord(), fieldContext(), w.section)
      if (reply.note === 'too short') {
        note('', 'Khup chhota — F1 dharun thevun bola', false, { key: 'too_short', cue: 'warn' })
      } else {
        await handleReply(reply)
        if (reply.timing) setSecs(reply.timing.asr_s)
      }
    } catch (e) {
      note('', `Voice service: ${e instanceof Error ? e.message : e}`, false, { key: 'engine_not_running', cue: 'error' })
    } finally {
      setPhase('ready')
    }
  }, [fieldContext, handleReply, note, pendingWord])

  /** A tap of F1 with Voice OFF: drop the recording it opened and turn Voice ON. */
  const tapOn = useCallback(async () => {
    if (listeningRef.current) {
      listeningRef.current = false
      try {
        await startCallRef.current
      } catch {
        /* it failed to open: nothing to cancel */
      }
      await voiceCancel().catch(() => {})
      setPhase('ready')
    }
    setHandsFree(true)
  }, [setHandsFree])

  // F1: tap = Voice ON / OFF, hold = push-to-talk (Voice OFF). Ctrl+K to type. Enter / Esc answer a question.
  const f1DownAt = useRef(0)
  const f1Toggles = useRef(false)
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === TALK_KEY) {
        e.preventDefault()
        if (e.repeat || isListeningHeld()) return
        f1DownAt.current = Date.now()
        // With Voice ON the service is already listening: F1 only switches it off.
        f1Toggles.current = handsFreeRef.current
        if (!f1Toggles.current) void startTalking()
        return
      }
      if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault()
        setOpen(true)
        window.setTimeout(() => inputRef.current?.focus(), 0)
        return
      }
      if ((pendingRef.current || choiceRef.current) && document.activeElement === inputRef.current) {
        if (e.key === 'Escape') {
          e.preventDefault()
          setPending(null)
          setChoice(null)
          queueRef.current = []
          note('', 'Radd kela', true, 'cancelled')
        }
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.key !== TALK_KEY) return
      e.preventDefault()
      if (isListeningHeld() && !listeningRef.current && !f1Toggles.current) return
      if (f1Toggles.current) {
        f1Toggles.current = false
        setHandsFree(false)
        return
      }
      if (Date.now() - f1DownAt.current < TAP_MS) void tapOn()
      else void stopTalking()
    }
    window.addEventListener('keydown', down, true)
    window.addEventListener('keyup', up, true)
    return () => {
      window.removeEventListener('keydown', down, true)
      window.removeEventListener('keyup', up, true)
    }
  }, [startTalking, stopTalking, tapOn, note, setHandsFree, setPending, setChoice])

  const submitTyped = async () => {
    const text = typed.trim()
    if (!text) {
      if (pending) {
        const p = pending
        setPending(null)
        await execute(p, true)
      }
      return
    }
    setTyped('')
    try {
      const w = placeOf(pageRef.current)
      const reply = await voiceParse(text, w.page, pendingWord(), fieldContext(), w.section)
      await handleReply(reply)
    } catch (e) {
      note(text, `Voice service: ${e instanceof Error ? e.message : e}`, false, { key: 'engine_not_running', cue: 'error' })
    }
  }

  const openSetup = async () => {
    setShowSetup((s) => !s)
    try {
      const d = await voiceDevices()
      setDevices(d.devices || [])
    } catch {
      setDevices([])
    }
  }

  const earlier = history.filter((h) => h.heard && h.id !== last?.id).slice(0, 5)
  const svcDown = phase === 'offline' || phase === 'loading'
  const hearing = handsFree && !svcDown && ear.listening && !ear.paused && !working
  const dot =
    svcDown ? (phase === 'loading' ? 'vb-dot vb-think' : 'vb-dot vb-off')
      : handsFree ? (working ? 'vb-dot vb-think' : hearing ? 'vb-dot vb-rec' : 'vb-dot vb-ok')
        : phase === 'listening' ? 'vb-dot vb-rec'
          : phase === 'thinking' ? 'vb-dot vb-think' : 'vb-dot vb-ok'
  const status =
    phase === 'loading' ? 'Model load hot aahe…'
      : phase === 'offline' ? 'Voice service band aahe'
        : handsFree
          ? working ? 'Samajun gheto…'
            : ear.paused ? 'Bolto aahe…'
              : ear.listening
                ? gates.waiting ? 'Bola…'
                  : level?.wake_word ? 'Aadhi "Satpuda" mhana' : 'Aikto aahe…'
                : 'Mic suru hot aahe…'
          : phase === 'listening' ? 'Aikto aahe… (F1 sodha)'
            : phase === 'thinking' ? 'Samajun gheto…'
              : `Band · F1 tap = Voice ON · F1 dharun bola · ${health?.medicines ?? 0} aushadha`

  if (!open) {
    return (
      <button type="button" className="vb-fab" onClick={() => setOpen(true)} title="Voice (F1)">
        <span className={dot} /> 🎤{handsFree ? ' ON' : ''}
      </button>
    )
  }

  return (
    <div
      className={`vb-panel${phase === 'listening' || hearing ? ' vb-listening' : ''}${
        handsFree && !svcDown && gates.waiting ? ' vb-wake-wait' : ''
      }`}
    >
      <div className="vb-head">
        <span className={dot} />
        <span className="vb-status">{status}</span>
        {hearing ? (
          <span className="vb-level" title="Mic">
            <span style={{ width: `${Math.round(ear.level * 100)}%` }} />
          </span>
        ) : null}
        <button
          type="button"
          className={`vb-toggle${handsFree ? ' on' : ''}`}
          onClick={() => (handsFree ? setHandsFree(false) : void tapOn())}
          title="Voice ON: F1 na dharta bola (F1 tap karun chalu / band)"
        >
          {handsFree ? 'Voice ON' : 'Voice OFF'}
        </button>
        <button type="button" className="vb-icon" onClick={openSetup} title="Mic ani bhasha">⚙</button>
        <button type="button" className="vb-icon" onClick={() => setOpen(false)} title="Lahan kara">—</button>
      </div>

      {/* No voice service answering: the voice pack may not be on this PC yet, or is on its way. */}
      {phase === 'offline' ? <VoicePackCard onReady={poll} /> : null}

      {handsFree && !svcDown && gates.ignored > 0 ? (
        <div className="vb-ignored" title="Wake word / fakt majha awaj ne sodlele">
          {gates.ignored} dusre awaj ignore
        </div>
      ) : null}

      {showSetup ? (
        <div className="vb-setup">
          <div className="vb-source">Voice: {voiceSourceNote(voiceSwitch)}</div>
          <label>
            Mic
            <select
              value={health?.device ?? ''}
              onChange={async (e) => {
                const v = e.target.value === '' ? null : Number(e.target.value)
                await voiceConfig({ device: v })
                void poll()
              }}
            >
              <option value="">Windows default</option>
              {devices.map((d) => (
                <option key={d.index} value={d.index}>
                  {d.name}{d.default ? ' (default)' : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            Bhasha
            <select
              value={health?.language ?? 'mr'}
              onChange={async (e) => {
                await voiceConfig({ language: e.target.value || null })
                void poll()
              }}
            >
              <option value="mr">Marathi</option>
              <option value="hi">Hindi</option>
              <option value="en">English</option>
              <option value="">Auto (halu)</option>
            </select>
          </label>
          <label>
            Model
            <select
              value={health?.model ?? 'small'}
              onChange={async (e) => {
                await voiceConfig({ model: e.target.value })
                void poll()
              }}
            >
              <option value="small">small — ~1.5 s (shifaras)</option>
              <option value="large-v3-turbo">large-v3-turbo — ~6 s, jast achuk</option>
            </select>
          </label>
          <label className="vb-check">
            <input type="checkbox" checked={talkBack} onChange={(e) => setTalkBack(e.target.checked)} />
            Uttar bolun sanga
          </label>
          <label>
            Awaj
            <select
              value={gender}
              onChange={(e) => {
                const g: Gender = e.target.value === 'm' ? 'm' : 'f'
                setGenderState(g)
                setVoiceGender(g)
                // Say something in the new voice so the shop hears the change.
                note('', `Awaj: ${g === 'm' ? 'Purush' : 'Stri'}`, true, 'ok')
              }}
            >
              <option value="f">Stri</option>
              <option value="m">Purush</option>
            </select>
          </label>
          <button
            type="button"
            className="vb-small"
            onClick={async () => {
              const r = await voiceRefreshVocab()
              note(
                '',
                r.ok ? `Yaadi taaji: ${r.medicines} aushadha, ${r.customers} grahak` : `Engine: ${r.error}`,
                r.ok,
                r.ok ? 'list_refreshed' : { key: 'list_refresh_failed', cue: 'error' },
              )
            }}
          >
            Aushadh yaadi taaji kara
          </button>
          {health?.vocab_error ? <div className="vb-warn">{health.vocab_error}</div> : null}
          <VoiceLevelPanel compact />
        </div>
      ) : null}

      {choice ? (
        <div className={`vb-choices${choice.options.length > MAX_OPTIONS ? ' vb-choices-many' : ''}`}>
          <span>Konta? (1{choice.options.length > 1 ? `–${choice.options.length}` : ''} bola, kinva click kara)</span>
          {choice.options.map((o, i) => (
            <button key={`${i}-${o}`} type="button" className="vb-choice" onClick={() => void pickChoice(i + 1)}>
              <b>{i + 1}.</b>{optionLabel(o)}
            </button>
          ))}
          <button
            type="button"
            className="vb-no"
            onClick={() => {
              setChoice(null)
              queueRef.current = []
              note('', 'Radd kela', true, 'cancelled')
            }}
          >
            No (Esc)
          </button>
        </div>
      ) : null}

      {pending ? (
        <div className="vb-confirm">
          <span>{pending.say}</span>
          <button
            type="button"
            className="vb-yes"
            onClick={async () => {
              const p = pending
              setPending(null)
              await execute(p, true)
            }}
          >
            Yes (Enter)
          </button>
          <button
            type="button"
            className="vb-no"
            onClick={() => {
              setPending(null)
              note('', 'Radd kela', true, 'cancelled')
            }}
          >
            No (Esc)
          </button>
        </div>
      ) : null}

      {fieldView ? (
        <div className="vb-field">
          <span>
            <b>{fieldWord(fieldView.name)}</b>
            {fieldView.options.length
              ? ` · ${fieldView.options.length} naav — number / naav sanga, "type kar …", "band kar"`
              : ' · lihaa: "type kar …", "band kar"'}
          </span>
          <button type="button" className="vb-no" onClick={() => endField(true)}>
            Band
          </button>
        </div>
      ) : null}

      {last ? (
        <div className={`vb-last ${last.tone === 'done' ? 'vb-good' : last.tone === 'ask' ? 'vb-ask' : 'vb-bad'}${
          last.tone === 'refuse' ? ' vb-refused' : ''}`}>
          {last.heard ? <div className="vb-heard">Aikla: “{last.heard}”</div> : null}
          {last.understood ? <div className="vb-understood">Samajla: {last.understood}</div> : null}
          <div className="vb-say" style={last.tone === 'refuse' ? { color: '#e5484d' } : undefined}>{last.say}</div>
          {last.alt ? <div className="vb-alt">{last.alt}</div> : null}
          {last.secs || last.engine ? (
            <div className="vb-secs" title={last.gate}>
              {[last.secs ? `${last.secs.toFixed(1)} s` : '', last.engine || ''].filter(Boolean).join(' · ')}
            </div>
          ) : null}
        </div>
      ) : (
        <div className="vb-hint">
          Udaharan: “navin bill” · “Dolo 650 don patta” · “grahak Rajesh” · “bill save kar” ·
          “inventory ughad” · “out of stock dakhav” · “aajche bill” · “printer settings ughad” ·
          “voice commands dakhav” · “voice band kar”
        </div>
      )}

      <div className="vb-row">
        <button
          type="button"
          className={`vb-mic${phase === 'listening' ? ' on' : ''}`}
          // With Voice ON the service already listens; a second mic would fight it.
          disabled={svcDown || handsFree}
          onPointerDown={(e) => {
            e.preventDefault()
            void startTalking()
          }}
          onPointerUp={() => void stopTalking()}
          onPointerLeave={() => {
            if (listeningRef.current) void stopTalking()
          }}
          title={handsFree ? 'Voice ON aahe — thet bola' : 'Dabun dharun bola (kinva F1)'}
        >
          🎤
        </button>
        <input
          ref={inputRef}
          className="vb-input"
          placeholder="Ctrl+K · type kara: dolo 650 2 patta"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              void submitTyped()
            }
          }}
        />
      </div>

      {/* The utterances before the one above, newest first: heard → understood → result. */}
      {earlier.length ? (
        <div className="vb-history">
          {earlier.map((h, i) => (
            <div key={`${h.at}-${i}`} className={`vb-h-${h.tone === 'refuse' ? 'bad' : h.tone}`}>
              <span className="vb-h-at">{h.at}</span> “{h.heard}”
              {h.understood ? <> → <b>{h.understood}</b></> : null} → {h.say}
              {h.engine ? <span className="vb-h-eng" title={h.gate}>{h.engine}</span> : null}
              {h.alt ? <div className="vb-h-alt">{h.alt}</div> : null}
            </div>
          ))}
        </div>
      ) : null}
      {phase === 'listening' ? (
        <button type="button" className="vb-small" onClick={() => { listeningRef.current = false; setPhase('ready'); void voiceCancel() }}>
          Radd
        </button>
      ) : null}
    </div>
  )
}

/** "AI 1.2s", "Dusre: Grahak: RAMESH (Rules 0.1s)" and the gates, from a newer service's reply. */
function replyMeta(reply: VoiceReply): ReplyMeta | null {
  const out: ReplyMeta = {}
  if (reply.engine) {
    const ms = reply.engine === 'rules' ? reply.timing?.parse_ms : reply.llm_ms ?? reply.timing?.parse_ms
    out.engine = [engineWord(reply.engine), msText(ms)].filter(Boolean).join(' ')
  }
  const alt = altLine(reply.alt)
  if (alt) out.alt = alt
  const g = reply.gate
  if (g) {
    const parts: string[] = []
    if (g.wake != null) parts.push(g.wake ? 'Satpuda aikla' : 'Satpuda nahi')
    if (g.speaker != null && Number.isFinite(Number(g.speaker))) parts.push(`awaj julla ${Number(g.speaker).toFixed(2)}`)
    if (parts.length) out.gate = parts.join(' · ')
  }
  return out.engine || out.alt || out.gate ? out : null
}

function altLine(alt: VoiceAlt | undefined): string {
  if (!alt || typeof alt !== 'object') return ''
  const list = Array.isArray(alt.commands) && alt.commands.length ? alt.commands : alt.command ? [alt.command] : []
  let text = 'Samajla nahi'
  try {
    const lines = list.filter((c) => c && typeof c === 'object' && c.intent).map(understoodLine)
    if (lines.length) text = lines.join(' · ')
  } catch {
    /* an odd command from the other engine: still show that it answered */
  }
  const how = [engineWord(alt.engine), msText(alt.ms)].filter(Boolean).join(' ')
  return `Dusre: ${text}${how ? ` (${how})` : ''}`
}

/** Noise the service passed on (nothing heard, nothing understood) is not worth a spoken "Samajla nahi". */
function worthAnswering(it: ListenItem): boolean {
  if (it.note === 'too short') return false
  if (it.command) {
    const blank = !String(it.heard ?? it.command.heard ?? '').trim()
    return !(blank && (it.command.intent === 'none' || it.command.intent === 'unknown'))
  }
  return !!it.error
}
