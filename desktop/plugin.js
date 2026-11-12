/**
 * bae (hermes-ebay-ops) — your eBay sidekick inside Hermes.
 *
 * Design Language: Warm Editorial Paper + Architectural Grid (Vercel Geist / Teenage Engineering).
 *  - Warm stone drafting-paper canvas (#F5F4F0) with a subtle 24px dot-grid weave
 *  - Continuous 1px architectural hairline grid (#DFDDD4) with zero generic puffy SaaS shadows
 *  - Delicate '+' intersection registration crosshairs & Swiss 4-color eBay print registration mark
 *    (Crimson #E53238, Cobalt #0064D2, Marigold #F5AF02, Leaf Green #288034)
 *  - Restrained (~10-15%) dot-matrix / ASCII graphic craft: whisper-subtle halftone masthead
 *    watermark, precision vertical micro-tick meters, and tactile parcel tear-stubs—keeping
 *    all eBay seller workflows, product titles, and unit economics Effortlessly Clear.
 *
 * Pure ESM, zero external dependencies, strictly inside @hermes/plugin-sdk + react.
 */
import { atom, host, queryClient, useQuery, useValue } from '@hermes/plugin-sdk'
import { createElement as h, useEffect, useRef, useState } from 'react'

let rest = null
let storage = null

const STATE_KEY = ['bae', 'state']
const refresh = () => queryClient.invalidateQueries({ queryKey: ['bae'] })

const $station = atom('catalog') // 'catalog' | 'ops' | 'bench' | 'setup'
const $filter = atom({ q: '', bucket: 'all' }) // all | staged | active | qa | sold
const $selectedSku = atom('')
const $benchSku = atom('')
const $proDrawerOpen = atom(false)
const $talk = atom({ busy: false, open: false, turns: [] })
const talkSession = { runtime: '', stored: '', primed: false, turn: 0 }

const PRIME =
  'This conversation is bae, your eBay sidekick inside Hermes (hermes-ebay-ops). ' +
  'Use the ebay_* tools for staging drafts, Magical Listing QA audits, active comps, Best Offer triage, ' +
  'and sorted bin pick-lists, and never confirm a write without explicit human approval.\n\n'

function setTalk(patch) {
  $talk.set({ ...$talk.get(), ...patch })
}
function setTurn(patch) {
  const talk = $talk.get()
  if (!talk.turns.length) return
  const turns = talk.turns.slice()
  const last = turns[turns.length - 1]
  turns[turns.length - 1] = { ...last, ...(typeof patch === 'function' ? patch(last) : patch) }
  $talk.set({ ...talk, turns })
}

async function talkOpen() {
  if (talkSession.runtime) return talkSession.runtime
  const saved = talkSession.stored || ((storage && storage.get('talk', {})) || {}).stored || ''
  if (saved) {
    try {
      const resumed = await host.request('session.resume', { session_id: saved, omit_messages: true })
      if (resumed && resumed.session_id) {
        Object.assign(talkSession, { runtime: resumed.session_id, stored: saved, primed: true })
        return talkSession.runtime
      }
    } catch {
      /* session expired; open fresh */
    }
  }
  const created = await host.request('session.create', { title: 'bae · eBay sidekick' })
  const runtime = (created && created.session_id) || ''
  if (!runtime) throw new Error('Hermes did not open a session')
  Object.assign(talkSession, { runtime, stored: created.stored_session_id || runtime, primed: false })
  if (storage) storage.set('talk', { stored: talkSession.stored })
  return runtime
}

async function askWorkbench(text) {
  const line = String(text || '').trim()
  if (!line || $talk.get().busy) return
  const turn = ++talkSession.turn
  $talk.set({
    busy: true,
    open: true,
    turns: $talk.get().turns.slice(-6).concat([{ you: line, reply: '', note: 'Checking eBay ledger…', error: '' }])
  })
  try {
    const id = await talkOpen()
    await host.request('prompt.submit', { session_id: id, text: talkSession.primed ? line : PRIME + line })
    talkSession.primed = true
    if (talkSession.turn === turn && $talk.get().busy) {
      setTurn(last => (last.reply ? {} : { note: 'Reviewing comps & listing specs…' }))
    }
  } catch (err) {
    setTalk({ busy: false })
    setTurn({ note: '', error: String((err && err.message) || err) })
  }
}

function useWorkbenchState() {
  return useQuery({
    queryKey: STATE_KEY,
    queryFn: () => rest('/state'),
    refetchInterval: 4500,
    staleTime: 1500
  })
}

function usd(cents) {
  if (cents === null || cents === undefined || Number.isNaN(Number(cents))) return '—'
  const val = Number(cents) / 100
  const sign = val < 0 ? '-' : ''
  const abs = Math.abs(val)
  if (abs >= 1000) return sign + '$' + Math.round(abs).toLocaleString()
  return sign + '$' + abs.toFixed(2)
}

function usdExact(cents) {
  if (cents === null || cents === undefined || Number.isNaN(Number(cents))) return '—'
  const val = Number(cents) / 100
  const sign = val < 0 ? '-' : ''
  return sign + '$' + Math.abs(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function usdShort(cents) {
  const abs = Math.abs(Number(cents || 0)) / 100
  if (abs >= 1e6) return '$' + (abs / 1e6).toFixed(1) + 'M'
  if (abs >= 1e4) return '$' + (abs / 1e3).toFixed(1) + 'k'
  if (abs >= 1000) return '$' + Math.round(abs).toLocaleString()
  return '$' + Math.round(abs)
}

function ttlText(sec) {
  const s = Math.max(0, Number(sec || 0))
  const m = Math.floor(s / 60)
  const r = s % 60
  return m + ':' + String(r).padStart(2, '0')
}

function daysOnMarket(iso) {
  if (!iso) return null
  const ts = Date.parse(iso)
  if (!ts) return null
  return Math.max(0, Math.round((Date.now() - ts) / 86400000))
}

function ago(iso) {
  const ts = Date.parse(iso || '')
  if (!ts) return ''
  const mins = Math.max(0, Math.round((Date.now() - ts) / 60000))
  if (mins < 1) return 'just now'
  if (mins < 60) return mins + 'm ago'
  if (mins < 1440) return Math.round(mins / 60) + 'h ago'
  return Math.round(mins / 1440) + 'd ago'
}

function calcLiveEconomics(priceCents, shippingCents, feeBps, cogsCents, plgBps = 0, buyerTaxBps = 0) {
  const p = Math.max(0, Math.round(Number(priceCents || 0)))
  const ship = Math.max(0, Math.round(Number(shippingCents || 0)))
  const bps = Number(feeBps || 1360)
  const grossMerch = p + ship
  const fvfBasis = grossMerch + Math.round((grossMerch * Math.max(0, Number(buyerTaxBps || 0))) / 10000)
  const fee = Math.round((fvfBasis * bps) / 10000) + (p > 0 ? 40 : 0)
  const plgFee = Math.round((fvfBasis * Math.max(0, Number(plgBps || 0))) / 10000)
  const inadRisk = Math.round((fvfBasis * 500) / 10000)
  const totalDeductions = fee + plgFee
  const payout = p - totalDeductions
  const hasCogs = cogsCents !== null && cogsCents !== undefined && cogsCents !== '' && !Number.isNaN(Number(cogsCents))
  const cogs = hasCogs ? Math.round(Number(cogsCents)) : null
  const profit = cogs !== null ? payout - cogs : null
  const margin = profit !== null && p > 0 ? Math.round((profit / p) * 1000) / 10 : null
  return { fee, plgFee, totalDeductions, inadRisk, payout, profit, margin }
}

function calcLiveDimCheck(weightOz, lengthIn, widthIn, heightIn) {
  const oz = Math.max(0, parseFloat(weightOz) || 0)
  const actualLbs = oz > 0 ? Math.ceil(oz / 16) : 1
  const l = parseFloat(lengthIn) || 0
  const w = parseFloat(widthIn) || 0
  const hVal = parseFloat(heightIn) || 0
  if (l <= 0 || w <= 0 || hVal <= 0) {
    return { hasDims: false, cubicIn: 0, dimLbs: null, billableLbs: actualLbs, dimTriggered: false, oversize: false }
  }
  const cubicIn = Math.round(l * w * hVal)
  const maxSide = Math.max(l, w, hVal)
  const dimTriggered = cubicIn > 1728
  const dimLbs = dimTriggered ? Math.ceil((l * w * hVal) / 139) : actualLbs
  const billableLbs = Math.max(actualLbs, dimLbs)
  return {
    hasDims: true,
    cubicIn,
    dimLbs,
    billableLbs,
    dimTriggered,
    oversize: maxSide > 48
  }
}

function downloadCsvBlob(csvText, filename = 'bae-seller-hub-drafts.csv') {
  if (typeof window === 'undefined' || typeof Blob === 'undefined') return
  const blob = new Blob([String(csvText || '')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1500)
}

const SAMPLE_INKFROG_CSV =
  'CustomLabel,Title,CategoryID,StartPrice,ConditionID,Description,PicURL\n' +
  'IF-PAT-01,"Patagonia Baggies 5in Shorts Mens Large Ash Tan",57988,44.00,3000,"<div class=\\"inkfrog-wrapper\\"><style>.if{color:red}</style><p>Clean nylon shell, 15.5 in waist flat, 5 in inseam, zero snags.</p></div>",https://i.ebayimg.com/images/g/pat01/s-l1600.jpg\n' +
  'IF-PYR-02,"Vintage Pyrex Verde Avocado Green Mixing Bowl #443 2.5 Qt",870,38.00,3000,"<div id=\\"inkfrog_gallery\\"><p>Glossy avocado finish, no dishwasher chalking or rim chips.</p></div>",https://i.ebayimg.com/images/g/pyr02/s-l1600.jpg'

const SAMPLE_MYFITMENT_CSV =
  'PartNumber,Title,CategoryID,Price,ConditionID,Year,Make,Model,Trim,Notes\n' +
  'MF-DENSO-234-4260,"Denso 234-4260 Downstream Oxygen Sensor OEM Toyota Tacoma",33615,64.00,1000,2012,Toyota,Tacoma,Base 4.0L,"Direct OEM fitment downstream O2 sensor"\n' +
  'MF-DENSO-234-4260,"Denso 234-4260 Downstream Oxygen Sensor OEM Toyota Tacoma",33615,64.00,1000,2013,Toyota,Tacoma,TRD Pro 4.0L,"Direct OEM fitment downstream O2 sensor"\n' +
  'MF-DENSO-234-4260,"Denso 234-4260 Downstream Oxygen Sensor OEM Toyota 4Runner",33615,64.00,1000,2014,Toyota,4Runner,SR5 4.0L,"Direct OEM fitment downstream O2 sensor"'

const ACTION_TITLES = {
  PUBLISH_LISTING: 'Publish Listing',
  REVISE_LISTING: 'Listing QA Revision',
  RESPOND_OFFER: 'Best Offer Reply',
  BOOTSTRAP_POLICIES: 'Opt-In Business Policies',
  BULK_PUBLISH: 'Bulk Publish (≤25 SKUs)'
}

/* ============================================================================
 * WARM EDITORIAL PAPER + ARCHITECTURAL GRID CSS (Geist / Teenage Engineering)
 * ========================================================================== */

const WORKBENCH_CSS = `
.vw-root {
  --vw-paper: #e9e5dc;
  --vw-surface: #faf8f3;
  --vw-elevated: #ffffff;
  --vw-tint: #f0ece3;
  --vw-well: #e1dcd0;
  --vw-ink: #141413;
  --vw-sec: #35342f;
  --vw-muted: #56534b;
  --vw-rule: #c5bfb0;
  --vw-rule-strong: #9c9585;

  /* Swiss Print Registration eBay Palette (Tuned for High Contrast) */
  --vw-cobalt: #005bc2;
  --vw-cobalt-soft: #e6f0fc;
  --vw-crimson: #d6282e;
  --vw-crimson-soft: #fcebec;
  --vw-marigold: #d48800;
  --vw-marigold-ink: #7d4e00;
  --vw-marigold-soft: #fcf3dc;
  --vw-leaf: #196b2c;
  --vw-leaf-soft: #e4f3e8;

  --vw-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif;
  --vw-mono: 'Geist Mono', 'JetBrains Mono', 'SF Mono', ui-monospace, Menlo, monospace;

  color: var(--vw-ink);
  font-family: var(--vw-sans);
  font-size: 13px;
  line-height: 1.45;
  box-sizing: border-box;
  -webkit-font-smoothing: antialiased;
}
.vw-root *, .vw-root *::before, .vw-root *::after { box-sizing: border-box; }

.vw-page {
  min-height: 100vh;
  background-color: var(--vw-paper);
  background-image: radial-gradient(rgba(20, 20, 19, 0.09) 1px, transparent 0);
  background-size: 20px 20px;
  position: relative;
  padding: 0 0 36px;
  container-type: inline-size;
}

.vw-wrap {
  max-width: 1360px;
  margin: 0 auto;
  padding: 20px 28px 0;
}

/* Sample Store Tour Strip */
.vw-tour-strip {
  display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  padding: 7px 14px;
  background: var(--vw-well);
  border: 1px solid var(--vw-rule-strong);
  border-bottom: 0;
  font-size: 12px;
}
.vw-tour-tag {
  font-family: var(--vw-mono); font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  padding: 2px 7px; background: var(--vw-elevated); border: 1px solid var(--vw-rule-strong); color: var(--vw-ink);
}

/* Unified Architectural Sheet (Continuous 1px Grid — Zero Puffy SaaS Shadows) */
.vw-sheet-grid {
  position: relative;
  background: var(--vw-surface);
  border: 1px solid var(--vw-rule-strong);
}

/* Subtle 3px 4-Color eBay Registration Hairline along top of architectural sheet */
.vw-reg-bar {
  height: 3px;
  width: 100%;
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr;
}
.vw-reg-bar > i:nth-child(1) { background: #e53238; }
.vw-reg-bar > i:nth-child(2) { background: #0064d2; }
.vw-reg-bar > i:nth-child(3) { background: #f5af02; }
.vw-reg-bar > i:nth-child(4) { background: #288034; }

/* Delicate '+' Blueprint Corner Crosshairs */
.vw-cross {
  position: relative;
}
.vw-cross::before, .vw-cross::after {
  content: '+';
  position: absolute;
  font-family: var(--vw-mono);
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  color: #635f55;
  pointer-events: none;
  z-index: 4;
}
.vw-cross::before { top: -7px; left: -4.5px; }
.vw-cross::after { top: -7px; right: -4.5px; }

/* Masthead */
.vw-masthead {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
  padding: 18px 24px 16px;
  border-bottom: 1px solid var(--vw-rule);
  background: linear-gradient(180deg, #fcfaf5 0%, #f4f0e7 100%);
}
.vw-masthead-left {
  position: relative;
  z-index: 2;
}
.vw-masthead-right {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}
.vw-masthead-kpis {
  display: inline-grid;
  grid-flow: column;
  grid-auto-columns: auto;
  border: 1px solid var(--vw-rule-strong);
  background: var(--vw-elevated);
}
.vw-masthead-kpi {
  padding: 6px 13px;
  border-right: 1px solid var(--vw-rule);
}
.vw-masthead-kpi:last-child { border-right: 0; }
.vw-masthead-watermark {
  position: absolute;
  right: 0; top: 0; bottom: 0;
  width: 480px;
  pointer-events: none;
  opacity: 0.16;
}

.vw-kicker {
  display: flex; align-items: center; gap: 9px; flex-wrap: wrap;
  font-family: var(--vw-mono);
  font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--vw-sec);
}
.vw-cmyk-mark {
  display: inline-grid;
  grid-template-columns: repeat(4, 6px);
  gap: 2px;
  align-items: center;
}
.vw-cmyk-mark i { width: 6px; height: 6px; display: block; }

.vw-hero-numbers {
  display: flex; align-items: baseline; gap: 13px; flex-wrap: wrap;
  margin-top: 6px;
}
.vw-gmv {
  font-size: 36px;
  font-weight: 700;
  letter-spacing: -0.04em;
  line-height: 1;
  color: var(--vw-ink);
  font-variant-numeric: tabular-nums;
}
.vw-gmv-meta {
  font-family: var(--vw-mono);
  font-size: 12px;
  font-weight: 500;
  color: var(--vw-sec);
}
.vw-profit-chip {
  font-family: var(--vw-mono);
  font-size: 11.5px;
  font-weight: 700;
  padding: 2.5px 9px;
  background: var(--vw-leaf-soft);
  color: var(--vw-leaf);
  border: 1px solid rgba(25, 107, 44, 0.36);
}

.vw-tick-strip {
  letter-spacing: 1px;
  font-family: var(--vw-mono);
  font-size: 10.5px;
  padding: 2px 8px;
  background: var(--vw-elevated);
  border: 1px solid var(--vw-rule-strong);
}

/* Primary Page Navigation Bar (High-Contrast Tactile Keycap Tabs in Recessed Stone Track) */
.vw-stations {
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--vw-rule-strong);
  background: var(--vw-well);
}
.vw-station-btn {
  border: 1px solid #b3ac9c;
  border-radius: 3px;
  background: #f2efe7;
  color: var(--vw-sec);
  padding: 10px 14px 11px;
  cursor: pointer;
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 3px;
  transition: background 0.12s ease, border-color 0.12s ease, box-shadow 0.12s ease;
  position: relative;
  outline: none;
}
.vw-station-btn:hover {
  background: #fbf9f4;
  border-color: var(--vw-ink);
  color: var(--vw-ink);
}
.vw-station-btn:focus-visible {
  outline: 2px solid var(--vw-cobalt);
  outline-offset: 1px;
}
.vw-station-btn[data-vw-on="1"] {
  background: var(--vw-elevated);
  color: var(--vw-ink);
  border-color: var(--vw-ink);
  box-shadow: inset 0 3px 0 var(--vw-cobalt), 0 2px 4px rgba(20, 20, 19, 0.10);
}
.vw-station-idx {
  display: flex; justify-content: space-between; align-items: center; gap: 6px;
  font-family: var(--vw-mono); font-size: 10.5px; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase;
  color: #4a4740;
}
.vw-station-btn[data-vw-on="1"] .vw-station-idx { color: var(--vw-cobalt); }
.vw-station-badge {
  font-family: var(--vw-mono); font-size: 10.5px; font-weight: 700;
  padding: 1px 6.5px; border-radius: 2px;
  background: #dfdad0; border: 1px solid #afa898; color: var(--vw-ink);
}
.vw-station-btn[data-vw-on="1"] .vw-station-badge {
  background: var(--vw-cobalt-soft); border-color: rgba(0, 91, 194, 0.35); color: var(--vw-cobalt);
}
.vw-station-badge[data-vw-alert="1"],
.vw-station-btn[data-vw-on="1"] .vw-station-badge[data-vw-alert="1"] {
  background: var(--vw-crimson); border-color: var(--vw-crimson); color: #ffffff;
}
.vw-station-title {
  font-size: 15.5px; font-weight: 700; letter-spacing: -0.015em; color: var(--vw-ink); line-height: 1.22; margin-top: 1px;
}
.vw-station-desc {
  font-family: var(--vw-mono); font-size: 11px; font-weight: 500; color: #4d4a42; line-height: 1.3;
}
.vw-station-btn[data-vw-on="1"] .vw-station-desc {
  color: var(--vw-sec);
}

/* Tactile Keycap Buttons & Clean Inputs */
.vw-btn {
  border: 1px solid var(--vw-rule-strong);
  background: var(--vw-surface);
  color: var(--vw-ink);
  padding: 6.5px 13px;
  border-radius: 3px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex; align-items: center; gap: 6px;
  box-shadow: 0 1px 0 rgba(22, 22, 21, 0.05);
  transition: all 0.12s ease;
  font-family: var(--vw-sans);
}
.vw-btn:hover { border-color: var(--vw-ink); background: var(--vw-tint); }
.vw-btn[data-vw-kind="primary"] {
  background: var(--vw-ink);
  border-color: var(--vw-ink);
  color: #ffffff;
}
.vw-btn[data-vw-kind="primary"]:hover { background: #2c2c2a; }
.vw-btn[data-vw-kind="cobalt"] {
  background: var(--vw-cobalt);
  border-color: var(--vw-cobalt);
  color: #ffffff;
}
.vw-btn[data-vw-kind="cobalt"]:hover { filter: brightness(0.93); }
.vw-btn[data-vw-kind="emerald"] {
  background: var(--vw-leaf);
  border-color: var(--vw-leaf);
  color: #ffffff;
}
.vw-btn[data-vw-kind="danger"] {
  background: var(--vw-surface);
  border-color: rgba(229, 50, 56, 0.42);
  color: var(--vw-crimson);
}
.vw-btn[data-vw-kind="danger"]:hover { background: var(--vw-crimson-soft); border-color: var(--vw-crimson); }
.vw-btn[data-vw-kind="ghost"] {
  background: var(--vw-tint);
  border-color: var(--vw-rule);
  color: var(--vw-sec);
  box-shadow: none;
}
.vw-btn[data-vw-kind="ghost"]:hover { border-color: var(--vw-rule-strong); color: var(--vw-ink); background: var(--vw-well); }

.vw-input {
  width: 100%;
  background: var(--vw-elevated);
  border: 1px solid var(--vw-rule-strong);
  color: var(--vw-ink);
  border-radius: 3px;
  padding: 7.5px 10px;
  font-size: 13px;
  outline: none;
  font-family: var(--vw-sans);
}
.vw-input:focus {
  border-color: var(--vw-cobalt);
  box-shadow: 0 0 0 2px rgba(0, 91, 194, 0.16);
}

.vw-label-caps {
  font-family: var(--vw-mono);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--vw-muted);
}

.vw-tag {
  font-family: var(--vw-mono);
  font-size: 10.5px;
  font-weight: 600;
  padding: 1.5px 6.5px;
  border-radius: 2px;
  border: 1px solid var(--vw-rule-strong);
  background: var(--vw-tint);
  color: var(--vw-sec);
  white-space: nowrap;
  display: inline-flex; align-items: center; gap: 4px;
}
.vw-tag[data-vw-tone="good"] { color: var(--vw-leaf); border-color: rgba(25, 107, 44, 0.36); background: var(--vw-leaf-soft); }
.vw-tag[data-vw-tone="gold"] { color: var(--vw-marigold-ink); border-color: rgba(212, 136, 0, 0.48); background: var(--vw-marigold-soft); }
.vw-tag[data-vw-tone="blue"] { color: var(--vw-cobalt); border-color: rgba(0, 91, 194, 0.36); background: var(--vw-cobalt-soft); }
.vw-tag[data-vw-tone="bad"] { color: var(--vw-crimson); border-color: rgba(214, 40, 46, 0.38); background: var(--vw-crimson-soft); }

/* Station 02: Continuous Architectural Split-Ledger */
.vw-catalog-split {
  display: grid;
  grid-template-columns: minmax(0, 1.38fr) minmax(385px, 0.94fr);
  align-items: stretch;
}
.vw-ledger-col {
  border-right: 1px solid var(--vw-rule-strong);
  min-width: 0;
}
.vw-filter-bar {
  display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 11px 18px;
  border-bottom: 1px solid var(--vw-rule);
  background: #ece7dc;
}
.vw-seg {
  display: inline-flex; border: 1px solid var(--vw-rule-strong); background: var(--vw-elevated); border-radius: 3px; overflow: hidden;
}
.vw-seg-btn {
  border: 0; border-right: 1px solid var(--vw-rule); background: transparent; color: var(--vw-sec);
  padding: 5.5px 11.5px; font-family: var(--vw-mono); font-size: 11px; font-weight: 500; cursor: pointer;
}
.vw-seg-btn:last-child { border-right: 0; }
.vw-seg-btn:hover { background: var(--vw-tint); color: var(--vw-ink); }
.vw-seg-btn[data-vw-on="1"] {
  background: var(--vw-ink); color: #ffffff; font-weight: 700;
}

.vw-row {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr) 136px 124px;
  gap: 14px;
  align-items: center;
  padding: 13px 18px;
  border-bottom: 1px solid var(--vw-rule);
  cursor: pointer;
  transition: background 0.1s ease;
}
.vw-row:last-child { border-bottom: 0; }
.vw-row:hover { background: var(--vw-tint); }
.vw-row[data-vw-sel="1"] {
  background: #e8effa;
  box-shadow: inset 3.5px 0 0 var(--vw-cobalt);
}
.vw-row-title {
  font-weight: 650; font-size: 13.5px; color: var(--vw-ink); line-height: 1.34;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.vw-row-sub {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-top: 5px; font-family: var(--vw-mono); font-size: 11px; color: var(--vw-muted);
}
.vw-row-price {
  text-align: right; font-variant-numeric: tabular-nums;
}
.vw-row-price strong { display: block; font-size: 15px; font-weight: 700; color: var(--vw-ink); letter-spacing: -0.01em; }
.vw-row-price small { display: block; font-family: var(--vw-mono); font-size: 10.5px; font-weight: 600; margin-top: 2px; }

/* Studio Technical Illustration Thumbnail */
.vw-thumb {
  position: relative;
  width: 60px; height: 60px;
  background: var(--vw-elevated);
  border: 1px solid var(--vw-rule-strong);
  display: grid; place-items: center;
  flex-shrink: 0;
  overflow: hidden;
}
.vw-thumb-count {
  position: absolute; right: 2px; bottom: 2px;
  font-family: var(--vw-mono); font-size: 9px; font-weight: 600; line-height: 1.2;
  padding: 0 4px; background: rgba(20, 20, 19, 0.84); color: #ffffff;
}

/* Right Inspector Column */
.vw-inspector-col {
  padding: 20px 22px;
  background: #f4f0e7;
}

/* Swiss Voucher Approval Slip */
.vw-voucher {
  background: var(--vw-marigold-soft);
  border: 1px solid rgba(212, 136, 0, 0.55);
  border-left: 3.5px solid var(--vw-marigold);
  padding: 13px 15px;
  margin-bottom: 14px;
}

/* Perforated Physical Shipping-Label Pick Slip */
.vw-parcel-slip {
  background: var(--vw-elevated);
  border: 1px solid var(--vw-rule-strong);
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr) auto;
  align-items: center;
  transition: opacity 0.15s ease;
}
.vw-parcel-slip[data-vw-pulled="1"] { opacity: 0.55; background: var(--vw-tint); }
.vw-parcel-stub {
  padding: 13px 10px;
  background: var(--vw-tint);
  border-right: 1px dashed var(--vw-rule-strong);
  text-align: center;
  height: 100%;
  display: flex; flex-direction: column; justify-content: center; align-items: center;
}
.vw-parcel-ticks {
  font-family: var(--vw-mono); font-size: 8.5px; letter-spacing: 0.5px; color: var(--vw-muted); margin-bottom: 3px;
}
.vw-parcel-bin {
  font-family: var(--vw-mono); font-size: 15.5px; font-weight: 700; color: var(--vw-ink); letter-spacing: 0.03em;
}

/* Listing Bench 2-Column Architectural Split */
.vw-bench-split {
  display: grid;
  grid-template-columns: minmax(340px, 0.92fr) minmax(0, 1.25fr);
}
.vw-bench-left {
  padding: 20px 22px;
  border-right: 1px solid var(--vw-rule-strong);
  background: #f4f0e7;
}
.vw-bench-right {
  padding: 20px 24px;
}
.vw-dropzone {
  border: 1.5px dashed var(--vw-rule-strong);
  background: var(--vw-elevated);
  padding: 18px 14px;
  text-align: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.vw-dropzone:hover { border-color: var(--vw-cobalt); background: var(--vw-cobalt-soft); }
.vw-shot-grid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 10px;
}
.vw-shot-cell {
  border: 1px solid var(--vw-rule-strong); background: var(--vw-elevated);
  padding: 8px 10px; display: flex; align-items: center; gap: 10px;
}

.vw-slider { width: 100%; accent-color: var(--vw-cobalt); cursor: pointer; margin: 10px 0 6px; }
.vw-tier-grid {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid var(--vw-rule); margin-top: 10px;
}
.vw-tier-cell {
  padding: 8px 9px; border-right: 1px solid var(--vw-rule); background: var(--vw-tint);
  text-align: center; font-family: var(--vw-mono); font-size: 10.5px; color: var(--vw-muted);
}
.vw-tier-cell:last-child { border-right: 0; }
.vw-tier-cell[data-vw-on="1"] { background: var(--vw-leaf-soft); color: var(--vw-leaf); font-weight: 600; }
.vw-tier-cell[data-vw-cliff="1"] { background: var(--vw-crimson-soft); color: var(--vw-crimson); font-weight: 600; }

/* Sticky Editorial Seller Desk Prompt Bar */
.vw-dock {
  position: sticky;
  bottom: 12px;
  margin: 18px auto 0;
  width: min(860px, 100%);
  z-index: 30;
}
.vw-dock-frame {
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid var(--vw-ink);
  box-shadow: 0 10px 28px rgba(22, 22, 21, 0.11);
  backdrop-filter: blur(10px);
}
.vw-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  background: var(--vw-tint);
  border-bottom: 1px solid var(--vw-rule);
  overflow-x: auto;
  white-space: nowrap;
  scrollbar-width: none;
}
.vw-chips::-webkit-scrollbar { display: none; }
.vw-chip {
  font-family: var(--vw-mono); font-size: 10.5px; color: var(--vw-sec);
  background: #ffffff; border: 1px solid var(--vw-rule-strong);
  border-radius: 2px; padding: 2.5px 8px; cursor: pointer; flex-shrink: 0;
}
.vw-chip:hover { border-color: var(--vw-ink); color: var(--vw-ink); background: #ffffff; }
.vw-dock-form {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 8px 7px 14px;
}
.vw-dock-in {
  flex: 1; border: 0; background: transparent; color: var(--vw-ink);
  font-size: 13px; outline: none; font-family: var(--vw-sans);
}
.vw-dock-in::placeholder { color: var(--vw-muted); }
.vw-talk-card {
  margin-bottom: 8px; padding: 12px 15px;
  background: #ffffff; border: 1px solid var(--vw-ink);
  box-shadow: 0 8px 24px rgba(22, 22, 21, 0.10);
}

/* 350px Side Pane */
.vw-pane {
  height: 100%; overflow-y: auto;
  background-color: var(--vw-paper);
  background-image: radial-gradient(rgba(22, 22, 21, 0.055) 1px, transparent 0);
  background-size: 18px 18px;
  display: flex; flex-direction: column;
}
.vw-pane-inner {
  padding: 14px;
  display: flex; flex-direction: column; gap: 12px;
}

@container (max-width: 1080px) {
  .vw-row { grid-template-columns: 58px minmax(0, 1fr) 118px; }
  .vw-comp-cell { display: none; }
}
@container (max-width: 920px) {
  .vw-catalog-split, .vw-bench-split { grid-template-columns: 1fr; }
  .vw-ledger-col, .vw-bench-left { border-right: 0; border-bottom: 1px solid var(--vw-rule-strong); }
  .vw-stations { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .vw-masthead-right { align-items: flex-start; }
  .vw-wrap { padding: 14px 16px 0; }
}
`

function StyleTag() {
  return h('style', null, WORKBENCH_CSS)
}

/**
 * 4-Color Swiss Registration Mark (subtle homage to eBay's heritage colors).
 */
function EbayRegMark() {
  return h(
    'span',
    { className: 'vw-cmyk-mark', 'aria-hidden': 'true' },
    h('i', { style: { background: '#e53238' } }),
    h('i', { style: { background: '#0064d2' } }),
    h('i', { style: { background: '#f5af02' } }),
    h('i', { style: { background: '#288034' } })
  )
}

/**
 * Subtle (~10%) Ambient Halftone / Topographic Dot-Matrix Watermark in Masthead Background.
 * Pure printmaking texture — never competes with UI text or seller workflows.
 */
function MastheadHalftone() {
  const canvasRef = useRef(null)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const w = 520
    const hPx = 148
    canvas.width = w * dpr
    canvas.height = hPx * dpr
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, hPx)
    ctx.font = '500 9px "Geist Mono", "JetBrains Mono", "SF Mono", monospace'
    ctx.fillStyle = '#3d3b36'
    const glyphs = ['·', '·', ':', '+', '▪']
    for (let y = 8; y < hPx; y += 10) {
      for (let x = 12; x < w; x += 10) {
        const nx = x / w
        const ny = y / hPx
        const wave = Math.sin(nx * 7.5 + ny * 3.8) * 0.5 + Math.cos(nx * 4.2 - ny * 5.1) * 0.5
        const fade = Math.pow(nx, 1.3)
        const val = (wave * 0.5 + 0.5) * fade
        if (val > 0.28) {
          const idx = Math.min(glyphs.length - 1, Math.floor((val - 0.28) * 6.5))
          ctx.fillText(glyphs[idx], x, y)
        }
      }
    }
  }, [])
  return h('canvas', { ref: canvasRef, className: 'vw-masthead-watermark', 'aria-hidden': 'true' })
}

/**
 * Precision Micro-Tick Meter (tactile Teenage Engineering / Geist style).
 */
function TickMeter({ pct, ticks = 12, color = 'var(--vw-leaf)' }) {
  const filled = Math.max(0, Math.min(ticks, Math.round((Math.max(0, Math.min(100, pct)) / 100) * ticks)))
  return h(
    'span',
    { style: { fontFamily: 'var(--vw-mono)', fontSize: 10, letterSpacing: '0.5px', display: 'inline-flex', alignItems: 'center' }, 'aria-hidden': 'true' },
    h('span', { style: { color, fontWeight: 700 } }, '|'.repeat(filled)),
    h('span', { style: { color: '#9e9888' } }, '·'.repeat(Math.max(0, ticks - filled)))
  )
}

/**
 * Crisp Dieter-Rams / Editorial Studio Line-Art Product Frame (supports real uploaded photos too).
 */
function StudioThumb({ item, size = 60, customSrc = '', showCount = true }) {
  const title = String((item && item.title) || '').toLowerCase()
  const cat = String((item && item.category_id) || '')
  let kind = 'apparel'
  if (cat === '15230' || /leica|contax|olympus|fujifilm|x100|camera|rangefinder|35mm/.test(title)) kind = 'camera'
  else if (cat === '11483' || /jeans|501|denim|trouser/.test(title)) kind = 'denim'
  else if (cat === '15709' || /boot|shoe|red wing|moc/.test(title)) kind = 'boots'
  else if (/airpods|switch|walkman|cassette|oled|audio|sony|nintendo/.test(title)) kind = 'tech'

  let art = null
  if (customSrc) {
    art = h('img', { src: customSrc, alt: '', style: { width: '100%', height: '100%', objectFit: 'cover' } })
  } else if (kind === 'camera') {
    art = h(
      'svg',
      { width: size * 0.72, height: size * 0.72, viewBox: '0 0 48 48', fill: 'none' },
      h('rect', { x: 6, y: 14, width: 36, height: 23, rx: 2.5, fill: '#ffffff', stroke: '#161615', strokeWidth: 1.5 }),
      h('path', { d: 'M13 14L15.5 10H26.5L28.5 14', fill: '#efede7', stroke: '#161615', strokeWidth: 1.4 }),
      h('rect', { x: 31, y: 11, width: 6, height: 3, fill: '#ffffff', stroke: '#161615', strokeWidth: 1.3 }),
      h('circle', { cx: 25, cy: 25.5, r: 8, fill: '#f5f4f0', stroke: '#161615', strokeWidth: 1.5 }),
      h('circle', { cx: 25, cy: 25.5, r: 4, fill: '#eef4fc', stroke: '#0064d2', strokeWidth: 1.3 }),
      h('circle', { cx: 35.5, cy: 19, r: 1.6, fill: '#e53238' }),
      h('rect', { x: 10, y: 17.5, width: 5.5, height: 3.2, stroke: '#161615', strokeWidth: 1.2 })
    )
  } else if (kind === 'denim') {
    art = h(
      'svg',
      { width: size * 0.72, height: size * 0.72, viewBox: '0 0 48 48', fill: 'none' },
      h('path', { d: 'M14 8H34L36.5 40H27.5L24 22.5L20.5 40H11.5L14 8Z', fill: '#ffffff', stroke: '#161615', strokeWidth: 1.5 }),
      h('line', { x1: 14, y1: 13, x2: 34, y2: 13, stroke: '#0064d2', strokeWidth: 1.2, strokeDasharray: '2 2' }),
      h('line', { x1: 24, y1: 13, x2: 24, y2: 22, stroke: '#161615', strokeWidth: 1.2 })
    )
  } else if (kind === 'boots') {
    art = h(
      'svg',
      { width: size * 0.72, height: size * 0.72, viewBox: '0 0 48 48', fill: 'none' },
      h('path', { d: 'M13 11H24V24C24 26.5 29 28.5 36.5 29.5C38.5 30 39.5 32 39.5 35H10.5V11H13Z', fill: '#ffffff', stroke: '#161615', strokeWidth: 1.5 }),
      h('rect', { x: 9.5, y: 35, width: 31, height: 3.8, fill: '#efede7', stroke: '#161615', strokeWidth: 1.4 })
    )
  } else if (kind === 'tech') {
    art = h(
      'svg',
      { width: size * 0.72, height: size * 0.72, viewBox: '0 0 48 48', fill: 'none' },
      h('rect', { x: 8, y: 13, width: 32, height: 22, rx: 3, fill: '#ffffff', stroke: '#161615', strokeWidth: 1.5 }),
      h('rect', { x: 14, y: 16.5, width: 20, height: 15, fill: '#f5f4f0', stroke: '#161615', strokeWidth: 1.2 }),
      h('circle', { cx: 11, cy: 21, r: 1.4, fill: '#0064d2' }),
      h('circle', { cx: 37, cy: 26, r: 1.4, fill: '#e53238' })
    )
  } else {
    art = h(
      'svg',
      { width: size * 0.72, height: size * 0.72, viewBox: '0 0 48 48', fill: 'none' },
      h('path', { d: 'M17 10L24 13.5L31 10L39.5 16L36 23L32.5 20.5V38H15.5V20.5L12 23L8.5 16L17 10Z', fill: '#ffffff', stroke: '#161615', strokeWidth: 1.5 }),
      h('line', { x1: 24, y1: 13.5, x2: 24, y2: 38, stroke: '#858279', strokeWidth: 1.1 })
    )
  }

  const count = Math.max(1, (item && item.photo_count) || 3)
  return h(
    'div',
    { className: 'vw-thumb', style: { width: size, height: size } },
    art,
    showCount ? h('span', { className: 'vw-thumb-count' }, String(count)) : null
  )
}

function renderPreviewLines(md) {
  const lines = String(md || '')
    .split('\n')
    .map(l => l.trim())
    .filter(Boolean)
    .filter(l => !l.startsWith('#'))
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: 4, margin: '8px 0 12px' } },
    lines.map((raw, idx) => {
      const clean = raw.replace(/^[-*]\s+/, '').replace(/\*\*/g, '').replace(/`/g, '')
      return h(
        'div',
        { key: idx, style: { fontSize: 12, color: 'var(--vw-sec)', display: 'flex', gap: 7, lineHeight: 1.42 } },
        h('span', { style: { fontFamily: 'var(--vw-mono)', color: 'var(--vw-marigold-ink)' } }, '·'),
        h('span', null, clean)
      )
    })
  )
}

/* ============================================================================
 * STATION 02 — ARCHITECTURAL SPLIT-LEDGER & ITEM INSPECTOR
 * ========================================================================== */

function ItemSheet({ item, state, onDecide, onEnrich, onOpenOnBench, onExportCsv }) {
  const pro = state.mode === 'pro'
  const proDrawer = useValue($proDrawerOpen)
  const [cogs, setCogs] = useState('')
  const [floor, setFloor] = useState('')
  const [bin, setBin] = useState('')
  const [weight, setWeight] = useState('')

  useEffect(() => {
    if (!item) return
    setCogs(item.cogs_cents != null ? (item.cogs_cents / 100).toFixed(2) : '')
    setFloor(item.floor_price_cents != null ? (item.floor_price_cents / 100).toFixed(2) : '')
    setBin(item.bin_code || '')
    setWeight(item.package_weight_oz != null ? String(item.package_weight_oz) : '')
  }, [item && item.sku])

  if (!item) {
    return h('aside', { className: 'vw-inspector-col' }, h('div', { style: { color: 'var(--vw-muted)' } }, 'Select a listing on the left to inspect pricing, specifics, and approvals.'))
  }

  const aspectEntries = Object.entries(item.aspects || {})
  const band = item.comp_band
  const pending = item.pending_actions || []
  const dim = item.dim_check || {}
  let pinIdx = 9
  if (band) {
    const span = Math.max(100, band.p75_cents - band.p25_cents)
    pinIdx = Math.max(1, Math.min(17, Math.round(((item.list_price_cents - band.p25_cents) / span) * 14 + 2)))
  }
  const compTrack = '├' + '─'.repeat(pinIdx) + '●' + '─'.repeat(Math.max(0, 18 - pinIdx)) + '┤'

  return h(
    'aside',
    { className: 'vw-inspector-col' },
    // Studio Header
    h(
      'div',
      { style: { display: 'flex', gap: 14, alignItems: 'flex-start', paddingBottom: 16, borderBottom: '1px solid var(--vw-rule)', marginBottom: 16 } },
      h(StudioThumb, { item, size: 72 }),
      h(
        'div',
        { style: { minWidth: 0, flex: 1 } },
        h(
          'div',
          { style: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 5 } },
          h('span', { className: 'vw-tag', 'data-vw-tone': item.status === 'ACTIVE' ? 'good' : item.status === 'STAGED' ? 'gold' : 'default' }, item.status === 'STAGED' ? 'Draft' : item.status),
          pro ? h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-muted)' } }, item.sku) : null,
          pro && item.bin_code ? h('span', { className: 'vw-tag', 'data-vw-tone': 'blue' }, `Bin ${item.bin_code}`) : null,
          item.playbook && item.playbook !== 'ebay-seller-ops'
            ? h('span', { className: 'vw-tag' }, item.playbook.replace(/^ebay-/, ''))
            : null,
          dim.dim_triggered ? h('span', { className: 'vw-tag', 'data-vw-tone': 'gold' }, `139-DIM ${dim.billable_lbs} lb`) : null,
          h('span', { className: 'vw-tag', 'data-vw-tone': item.qa_score >= 85 ? 'good' : 'bad' }, `Quality ${item.qa_score}/100`)
        ),
        h('div', { style: { fontWeight: 650, fontSize: 15.5, lineHeight: 1.32, color: 'var(--vw-ink)' } }, item.title),
        h(
          'div',
          { style: { marginTop: 9, display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' } },
          h('button', { type: 'button', className: 'vw-btn', 'data-vw-kind': 'ghost', style: { padding: '5px 10px', fontSize: 11.5 }, onClick: () => onOpenOnBench(item.sku) }, 'Edit on Listing Bench →'),
          h('button', { type: 'button', className: 'vw-btn', 'data-vw-kind': 'ghost', style: { padding: '5px 10px', fontSize: 11.5 }, onClick: () => onExportCsv([item.sku]) }, 'Export CRLF Draft CSV')
        )
      )
    ),

    // Two-Step Approval Slip
    pending.map(pa =>
      h(
        'div',
        { key: pa.action_token, className: 'vw-voucher' },
        h(
          'div',
          { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 } },
          h('span', { className: 'vw-tag', 'data-vw-tone': 'gold' }, ACTION_TITLES[pa.action_type] || pa.action_type),
          h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 10.5, color: 'var(--vw-marigold-ink)' } }, `SHA ${pa.payload_sha256.slice(0, 8)} · ${ttlText(pa.seconds_remaining)}`)
        ),
        renderPreviewLines(pa.preview_card_md || pa.preview_markdown),
        h(
          'div',
          { style: { display: 'flex', gap: 8 } },
          h('button', { type: 'button', className: 'vw-btn', 'data-vw-kind': 'cobalt', onClick: () => onDecide(pa.action_token, 'APPROVE') }, 'Approve & Publish'),
          h('button', { type: 'button', className: 'vw-btn', 'data-vw-kind': 'danger', onClick: () => onDecide(pa.action_token, 'CANCEL') }, 'Cancel')
        )
      )
    ),

    // Fee & Payout Architectural Grid Box
    h(
      'div',
      { style: { border: '1px solid var(--vw-rule)', background: 'var(--vw-tint)', marginBottom: 16 } },
      h(
        'div',
        { style: { display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', borderBottom: '1px solid var(--vw-rule)' } },
        h(
          'div',
          { style: { padding: '11px 13px', borderRight: '1px solid var(--vw-rule)' } },
          h('div', { className: 'vw-label-caps' }, 'List Price'),
          h('strong', { style: { display: 'block', fontSize: 17, marginTop: 2, fontVariantNumeric: 'tabular-nums' } }, usd(item.list_price_cents))
        ),
        h(
          'div',
          { style: { padding: '11px 13px', borderRight: '1px solid var(--vw-rule)' } },
          h('div', { className: 'vw-label-caps' }, 'Est. eBay Fee'),
          h('strong', { style: { display: 'block', fontSize: 15.5, marginTop: 3, color: 'var(--vw-sec)', fontVariantNumeric: 'tabular-nums' } }, '-' + usdExact(item.estimated_fees_cents))
        ),
        h(
          'div',
          { style: { padding: '11px 13px' } },
          h('div', { className: 'vw-label-caps' }, pro && item.net_profit_cents != null ? 'Net Profit' : 'Est. Payout'),
          h(
            'strong',
            { style: { display: 'block', fontSize: 15.5, marginTop: 3, color: 'var(--vw-leaf)', fontVariantNumeric: 'tabular-nums' } },
            pro && item.net_profit_cents != null ? `${usdExact(item.net_profit_cents)} (${item.margin_pct}%)` : usdExact(item.estimated_net_payout_cents)
          )
        )
      ),
      h(
        'div',
        { style: { padding: '7px 13px', borderBottom: band ? '1px solid var(--vw-rule)' : 0, fontFamily: 'var(--vw-mono)', fontSize: 10.5, color: 'var(--vw-sec)', display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6 } },
        h('span', null, `5% PLG + ~7% Tax Basis: ${usdExact(item.plg_5pct_net_payout_cents)} net`),
        h('span', { style: { color: 'var(--vw-muted)' } }, `Very High INAD (+5%) risk: -${usdExact(item.very_high_inad_risk_cents)}`)
      ),
      band
        ? h(
            'div',
            { style: { padding: '10px 13px', fontFamily: 'var(--vw-mono)', fontSize: 11 } },
            h(
              'div',
              { style: { display: 'flex', justifyContent: 'space-between', color: 'var(--vw-muted)', marginBottom: 4 } },
              h('span', null, 'P25 ' + usd(band.p25_cents)),
              h('span', { style: { color: 'var(--vw-cobalt)', fontWeight: 600 } }, `Median ${usd(band.median_cents)} (${band.sample_count} active)`),
              h('span', null, 'P75 ' + usd(band.p75_cents))
            ),
            h('div', { style: { color: 'var(--vw-cobalt)', letterSpacing: '1px', textAlign: 'center', fontSize: 11 } }, compTrack)
          )
        : null
    ),

    // Item Specifics & Condition Notes
    h(
      'div',
      { style: { marginBottom: 16 } },
      h('div', { className: 'vw-label-caps', style: { marginBottom: 8 } }, `Item Specifics (${aspectEntries.length}) & Condition`),
      item.qa_flags && item.qa_flags.length
        ? h(
            'div',
            { style: { padding: '8px 10px', background: 'var(--vw-crimson-soft)', border: '1px solid rgba(229,50,56,0.28)', color: 'var(--vw-crimson)', fontSize: 12, marginBottom: 9 } },
            'QA Notice: ' + item.qa_flags.join(' · ')
          )
        : null,
      dim.dim_triggered && item.weight_note
        ? h(
            'div',
            { style: { padding: '7px 10px', background: 'var(--vw-marigold-soft)', border: '1px solid rgba(212,136,0,0.42)', color: 'var(--vw-marigold-ink)', fontSize: 11.5, fontFamily: 'var(--vw-mono)', marginBottom: 9 } },
            item.weight_note + (dim.recommended_box_code ? ` · Box ${dim.recommended_box_code}` : '')
          )
        : null,
      h(
        'div',
        { style: { display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 9 } },
        aspectEntries.slice(0, 8).map(([k, v]) =>
          h('span', { key: k, className: 'vw-tag' }, `${k}: ${Array.isArray(v) ? v.join(', ') : v}`)
        )
      ),
      h(
        'div',
        { style: { fontSize: 12.5, color: 'var(--vw-sec)', padding: '9px 11px', background: 'var(--vw-tint)', border: '1px solid var(--vw-rule)' } },
        item.flaws_disclosure || 'No explicit flaw or wear note recorded yet.'
      )
    ),

    // Progressive Pro Warehouse Drawer
    pro || proDrawer
      ? h(
          'div',
          { style: { paddingTop: 14, borderTop: '1px solid var(--vw-rule)' } },
          h(
            'div',
            { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 } },
            h('span', { className: 'vw-label-caps' }, 'Unit Economics & Bin Location'),
            !pro
              ? h('button', { type: 'button', style: { border: 0, background: 'transparent', color: 'var(--vw-muted)', fontSize: 11.5, cursor: 'pointer' }, onClick: () => $proDrawerOpen.set(false) }, 'Hide')
              : null
          ),
          h(
            'div',
            { style: { display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 7 } },
            h('input', { className: 'vw-input', placeholder: 'COGS $', value: cogs, onChange: e => setCogs(e.target.value) }),
            h('input', { className: 'vw-input', placeholder: 'Floor $', value: floor, onChange: e => setFloor(e.target.value) }),
            h('input', { className: 'vw-input', placeholder: 'Bin A-01', value: bin, onChange: e => setBin(e.target.value) }),
            h('input', { className: 'vw-input', placeholder: 'Oz', value: weight, onChange: e => setWeight(e.target.value) })
          ),
          h(
            'button',
            {
              type: 'button',
              className: 'vw-btn',
              style: { marginTop: 9, width: '100%', justifyContent: 'center' },
              onClick: () =>
                onEnrich(item.sku, {
                  cogs_cents: cogs === '' ? null : Math.round(parseFloat(cogs) * 100),
                  floor_price_cents: floor === '' ? null : Math.round(parseFloat(floor) * 100),
                  bin_code: bin,
                  package_weight_oz: weight === '' ? null : parseFloat(weight)
                })
            },
            pro ? 'Save Bin & Economics' : 'Save & Enable Pro Seller Tools'
          )
        )
      : h(
          'button',
          {
            type: 'button',
            className: 'vw-btn',
            'data-vw-kind': 'ghost',
            style: { width: '100%', justifyContent: 'center', fontSize: 12 },
            onClick: () => $proDrawerOpen.set(true)
          },
          '+ Add optional cost basis, offer floor, or storage bin'
        )
  )
}

function CatalogStation({ state, onDecide, onEnrich, onOpenOnBench, onExportCsv }) {
  const filter = useValue($filter)
  const selectedSku = useValue($selectedSku)
  const pro = state.mode === 'pro'
  const allItems = state.items || []

  const counts = {
    all: allItems.length,
    staged: allItems.filter(i => i.status === 'STAGED').length,
    active: allItems.filter(i => i.status === 'ACTIVE').length,
    qa: allItems.filter(i => i.qa_score < 85 && i.status !== 'SOLD').length,
    sold: allItems.filter(i => i.status === 'SOLD').length
  }

  const q = filter.q.trim().toLowerCase()
  const rows = allItems.filter(it => {
    if (filter.bucket === 'staged' && it.status !== 'STAGED') return false
    if (filter.bucket === 'active' && it.status !== 'ACTIVE') return false
    if (filter.bucket === 'qa' && (it.qa_score >= 85 || it.status === 'SOLD')) return false
    if (filter.bucket === 'sold' && it.status !== 'SOLD') return false
    if (q) {
      const hay = `${it.sku} ${it.title} ${it.bin_code || ''} ${JSON.stringify(it.aspects || {})}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })

  const activeItem = allItems.find(i => i.sku === selectedSku) || rows[0] || allItems[0] || null

  return h(
    'div',
    { className: 'vw-catalog-split vw-cross' },
    h(
      'div',
      { className: 'vw-ledger-col' },
      h(
        'div',
        { className: 'vw-filter-bar' },
        h(
          'div',
          { className: 'vw-seg' },
          [
            ['all', `All (${counts.all})`],
            ['staged', `Drafts (${counts.staged})`],
            ['active', `Active (${counts.active})`],
            ['qa', `Needs QA (${counts.qa})`],
            ['sold', `Sold (${counts.sold})`]
          ].map(([id, lbl]) =>
            h(
              'button',
              {
                key: id,
                type: 'button',
                className: 'vw-seg-btn',
                'data-vw-on': filter.bucket === id ? '1' : '0',
                onClick: () => $filter.set({ ...filter, bucket: id })
              },
              lbl
            )
          )
        ),
        h(
          'div',
          { style: { display: 'flex', gap: 7, alignItems: 'center' } },
          h('input', {
            className: 'vw-input',
            style: { width: 205, padding: '5.5px 10px', fontSize: 12 },
            placeholder: pro ? 'Filter title, SKU, or bin…' : 'Filter your listings…',
            value: filter.q,
            onChange: e => $filter.set({ ...filter, q: e.target.value })
          }),
          h(
            'button',
            {
              type: 'button',
              className: 'vw-btn',
              style: { padding: '5.5px 10px', fontSize: 11.5, whiteSpace: 'nowrap' },
              title: 'Export Seller Hub Action=Draft CSV with strict Windows CRLF line endings',
              onClick: () => onExportCsv(rows.map(r => r.sku))
            },
            '↓ CRLF Draft CSV'
          )
        )
      ),
      rows.length
        ? rows.map(it => {
            const isSel = activeItem && activeItem.sku === it.sku
            const titleLen = (it.title || '').length
            const charPct = Math.min(100, Math.round((titleLen / 80) * 100))
            const dom = daysOnMarket(it.listed_at)
            const band = it.comp_band
            let pinIdx = 4
            if (band) {
              const span = Math.max(100, band.p75_cents - band.p25_cents)
              pinIdx = Math.max(0, Math.min(8, Math.round(((it.list_price_cents - band.p25_cents) / span) * 6 + 1)))
            }
            const miniComp = '├' + '─'.repeat(pinIdx) + '●' + '─'.repeat(Math.max(0, 8 - pinIdx)) + '┤'

            return h(
              'div',
              {
                key: it.sku,
                className: 'vw-row',
                'data-vw-sel': isSel ? '1' : '0',
                onClick: () => $selectedSku.set(it.sku)
              },
              h(StudioThumb, { item: it, size: 58 }),
              h(
                'div',
                { style: { minWidth: 0 } },
                h('div', { className: 'vw-row-title' }, it.title),
                h(
                  'div',
                  { className: 'vw-row-sub' },
                  h(TickMeter, { pct: charPct, ticks: 8, color: titleLen >= 60 ? 'var(--vw-leaf)' : 'var(--vw-marigold)' }),
                  h('span', null, `${titleLen}/80`),
                  pro && it.bin_code ? h('span', { style: { color: 'var(--vw-cobalt)', fontWeight: 600 } }, `Bin ${it.bin_code}`) : null,
                  dom !== null
                    ? h('span', { style: dom >= 90 ? { color: 'var(--vw-crimson)', fontWeight: 600 } : null }, `${dom}d active`)
                    : h('span', { style: { color: 'var(--vw-marigold-ink)', fontWeight: 600 } }, 'Draft'),
                  it.package_weight_oz ? h('span', null, `${it.package_weight_oz} oz`) : null,
                  it.dim_check && it.dim_check.dim_triggered ? h('span', { className: 'vw-tag', 'data-vw-tone': 'gold' }, `139-DIM ${it.dim_check.billable_lbs}lb`) : null,
                  it.qa_score < 85 ? h('span', { className: 'vw-tag', 'data-vw-tone': 'bad' }, `QA ${it.qa_score}`) : null,
                  it.pending_actions && it.pending_actions.length ? h('span', { className: 'vw-tag', 'data-vw-tone': 'gold' }, 'Needs Approval') : null
                )
              ),
              band
                ? h(
                    'div',
                    { className: 'vw-comp-cell', style: { fontFamily: 'var(--vw-mono)', fontSize: 10.5 } },
                    h('div', { style: { display: 'flex', justifyContent: 'space-between', color: 'var(--vw-muted)', fontSize: 9.5 } }, h('span', null, usd(band.p25_cents)), h('span', null, usd(band.p75_cents))),
                    h('div', { style: { color: 'var(--vw-cobalt)', letterSpacing: '0.5px', marginTop: 2 } }, miniComp)
                  )
                : h('div', { className: 'vw-comp-cell', style: { fontFamily: 'var(--vw-mono)', fontSize: 10.5, color: 'var(--vw-muted)' } }, '—'),
              h(
                'div',
                { className: 'vw-row-price' },
                h('strong', null, usd(it.list_price_cents)),
                pro && it.net_profit_cents != null
                  ? h('small', { style: { color: 'var(--vw-leaf)' } }, `+${usd(it.net_profit_cents)} (${it.margin_pct}%)`)
                  : h('small', { style: { color: 'var(--vw-sec)' } }, `${usd(it.estimated_net_payout_cents)} payout`)
              )
            )
          })
        : h('div', { style: { padding: 32, textAlign: 'center', color: 'var(--vw-muted)' } }, 'No listings match this filter. Switch to Listing Bench to draft a new item.')
    ),
    h(ItemSheet, { item: activeItem, state, onDecide, onEnrich, onOpenOnBench, onExportCsv })
  )
}

/* ============================================================================
 * STATION 03 — PHOTO-TO-LISTING BENCH
 * ========================================================================== */

function ListingBenchStation({ state, onStage, onEnrich, onDecide, onExportCsv, onImportCsv }) {
  const benchSku = useValue($benchSku)
  const items = state.items || []
  const loaded = benchSku ? items.find(i => i.sku === benchSku) : null
  const pro = state.mode === 'pro'
  const feeBps = (state.profile && state.profile.estimated_fee_bps) || 1360
  const stockedBoxes = state.stocked_boxes || []

  const [title, setTitle] = useState('')
  const [notes, setNotes] = useState('')
  const [priceCents, setPriceCents] = useState(8500)
  const [weightOz, setWeightOz] = useState('16.4')
  const [lengthIn, setLengthIn] = useState('')
  const [widthIn, setWidthIn] = useState('')
  const [heightIn, setHeightIn] = useState('')
  const [plgBps, setPlgBps] = useState(0)
  const [taxBasisOn, setTaxBasisOn] = useState(false)
  const [cogsDollars, setCogsDollars] = useState('')
  const [floorDollars, setFloorDollars] = useState('')
  const [binCode, setBinCode] = useState('')
  const [droppedPreviews, setDroppedPreviews] = useState([])
  const [csvOpen, setCsvOpen] = useState(false)
  const [csvText, setCsvText] = useState(SAMPLE_INKFROG_CSV)
  const [csvHint, setCsvHint] = useState('auto')

  useEffect(() => {
    if (loaded) {
      setTitle(loaded.title || '')
      setNotes(loaded.flaws_disclosure || '')
      setPriceCents(loaded.list_price_cents || 8500)
      setWeightOz(loaded.package_weight_oz != null ? String(loaded.package_weight_oz) : '15.0')
      const dims = loaded.package_dims || null
      setLengthIn(dims && dims.length_in ? String(dims.length_in) : '')
      setWidthIn(dims && dims.width_in ? String(dims.width_in) : '')
      setHeightIn(dims && dims.height_in ? String(dims.height_in) : '')
      setCogsDollars(loaded.cogs_cents != null ? (loaded.cogs_cents / 100).toFixed(2) : '')
      setFloorDollars(loaded.floor_price_cents != null ? (loaded.floor_price_cents / 100).toFixed(2) : '')
      setBinCode(loaded.bin_code || '')
    } else {
      setTitle('Fujifilm X100V Silver 26.1MP Compact Camera 3,400 Shutter Count Mint')
      setNotes('Clean Fujinon 23mm f/2 lens, zero dust or scratches; tiny rub mark on bottom plate tripod thread.')
      setPriceCents(149500)
      setWeightOz('16.4')
      setLengthIn('12')
      setWidthIn('9')
      setHeightIn('6')
      setCogsDollars(pro ? '940.00' : '')
      setFloorDollars(pro ? '1380.00' : '')
      setBinCode(pro ? 'C-01-03' : '')
    }
  }, [loaded && loaded.sku, pro])

  function handleDropFiles(fileList) {
    const files = Array.from(fileList || []).filter(f => f.type && f.type.startsWith('image/')).slice(0, 4)
    files.forEach(file => {
      const reader = new FileReader()
      reader.onload = ev => {
        setDroppedPreviews(prev => prev.concat([{ name: file.name, url: String(ev.target.result || '') }]).slice(-4))
      }
      reader.readAsDataURL(file)
    })
  }

  const band = (loaded && loaded.comp_band) || {
    p25_cents: Math.round(priceCents * 0.86),
    median_cents: priceCents,
    p75_cents: Math.round(priceCents * 1.14),
    sample_count: 18
  }
  const sliderMin = Math.max(500, Math.round(band.p25_cents * 0.75))
  const sliderMax = Math.max(sliderMin + 2000, Math.round(band.p75_cents * 1.25))

  const oz = parseFloat(weightOz) || 0
  const dim = calcLiveDimCheck(oz, lengthIn, widthIn, heightIn)
  const effectiveOz = dim.dimTriggered ? Math.max(oz, dim.billableLbs * 16) : oz
  const shipEst = effectiveOz <= 0 ? 795 : effectiveOz <= 8 ? 545 : effectiveOz <= 15.99 ? 785 : effectiveOz <= 32 ? 1190 : effectiveOz <= 96 ? 1580 : 2190
  const isWeightCliff = !dim.dimTriggered && oz > 15.99 && oz <= 18.5
  const cogsCents = cogsDollars !== '' && !Number.isNaN(parseFloat(cogsDollars)) ? Math.round(parseFloat(cogsDollars) * 100) : null
  const econ = calcLiveEconomics(priceCents, shipEst, feeBps, cogsCents, plgBps, taxBasisOn ? 700 : 0)
  const titleLen = title.length
  const bannedHits = ['must-have', 'elevate your wardrobe', 'stunning rare gem', 'turn heads'].filter(w =>
    (title + ' ' + notes).toLowerCase().includes(w)
  )
  const fashionShipCapWarn = priceCents >= 20000 && priceCents <= 50000
  const dimsPayload = dim.hasDims
    ? { length_in: parseFloat(lengthIn), width_in: parseFloat(widthIn), height_in: parseFloat(heightIn) }
    : null

  return h(
    'div',
    { className: 'vw-cross' },
    h(
      'div',
      { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '11px 20px', borderBottom: '1px solid var(--vw-rule)', background: 'var(--vw-tint)' } },
      h(
        'div',
        { style: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' } },
        h('span', { className: 'vw-label-caps', style: { marginRight: 4 } }, 'Active Draft:'),
        h(
          'button',
          { type: 'button', className: 'vw-btn', 'data-vw-kind': !loaded ? 'primary' : 'ghost', style: { padding: '4px 10px', fontSize: 11.5 }, onClick: () => $benchSku.set('') },
          '+ New Draft'
        ),
        items.slice(0, 4).map(it =>
          h(
            'button',
            {
              key: it.sku,
              type: 'button',
              className: 'vw-btn',
              'data-vw-kind': loaded && loaded.sku === it.sku ? 'primary' : 'ghost',
              style: { padding: '4px 10px', fontSize: 11.5 },
              onClick: () => $benchSku.set(it.sku)
            },
            it.title.slice(0, 24) + '…'
          )
        )
      ),
      h(
        'div',
        { style: { display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' } },
        h(
          'button',
          { type: 'button', className: 'vw-btn', 'data-vw-kind': csvOpen ? 'primary' : 'ghost', style: { padding: '4px 10px', fontSize: 11.5 }, onClick: () => setCsvOpen(!csvOpen) },
          csvOpen ? 'Hide CSV Importer ▴' : '⇄ Import inkFrog / myFitment CSV'
        ),
        h('span', { className: 'vw-tag', 'data-vw-tone': 'blue' }, 'Commerce Media REST · apiz.ebay.com')
      )
    ),

    csvOpen
      ? h(
          'div',
          { style: { padding: '15px 22px', borderBottom: '1px solid var(--vw-rule-strong)', background: '#eee9dd' } },
          h(
            'div',
            { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 } },
            h(
              'div',
              null,
              h('span', { className: 'vw-label-caps' }, 'Displaced-Seller CSV Migration → Bulk Inventory API (≤25 SKUs/call)'),
              h('div', { style: { fontSize: 12, color: 'var(--vw-sec)', marginTop: 2 } }, 'Strips shuttered inkFrog HTML wrappers, aggregates multi-row myFitment Year/Make/Model compatibility, and stages a single SHA-256 bulk token.')
            ),
            h(
              'div',
              { style: { display: 'flex', gap: 6, flexWrap: 'wrap' } },
              h(
                'button',
                { type: 'button', className: 'vw-btn', 'data-vw-kind': 'ghost', style: { padding: '4px 9px', fontSize: 11 }, onClick: () => { setCsvText(SAMPLE_INKFROG_CSV); setCsvHint('inkfrog') } },
                'Load Sample inkFrog CSV'
              ),
              h(
                'button',
                { type: 'button', className: 'vw-btn', 'data-vw-kind': 'ghost', style: { padding: '4px 9px', fontSize: 11 }, onClick: () => { setCsvText(SAMPLE_MYFITMENT_CSV); setCsvHint('myfitment') } },
                'Load Sample myFitment YMM CSV'
              )
            )
          ),
          h('textarea', {
            className: 'vw-input',
            style: { minHeight: 92, fontFamily: 'var(--vw-mono)', fontSize: 11.5, resize: 'vertical' },
            value: csvText,
            onChange: e => setCsvText(e.target.value)
          }),
          h(
            'div',
            { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 9, gap: 10, flexWrap: 'wrap' } },
            h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-muted)' } }, 'Zero live writes on import · Stages BULK_PUBLISH token in Morning Ops'),
            h(
              'button',
              { type: 'button', className: 'vw-btn', 'data-vw-kind': 'cobalt', onClick: () => onImportCsv(csvText, csvHint) },
              'Import CSV & Stage Bulk Approval Token →'
            )
          )
        )
      : null,

    h(
      'div',
      { className: 'vw-bench-split' },
      // LEFT: Studio Photos & Condition Disclosure
      h(
        'div',
        { className: 'vw-bench-left' },
        h('div', { className: 'vw-label-caps', style: { marginBottom: 9 } }, '01 · Photos & Condition Notes'),
        h(
          'label',
          {
            className: 'vw-dropzone',
            style: { display: 'block' },
            onDragOver: e => e.preventDefault(),
            onDrop: e => {
              e.preventDefault()
              handleDropFiles(e.dataTransfer && e.dataTransfer.files)
            }
          },
          h('input', { type: 'file', accept: 'image/*', multiple: true, style: { display: 'none' }, onChange: e => handleDropFiles(e.target.files) }),
          h('div', { style: { fontWeight: 650, color: 'var(--vw-cobalt)', fontSize: 13.5 } }, '+ Drop or select product photos'),
          h('div', { style: { fontFamily: 'var(--vw-mono)', fontSize: 10.5, color: 'var(--vw-muted)', marginTop: 4 } }, 'Up to 24 high-res frames · Staged locally until you approve')
        ),
        h(
          'div',
          { className: 'vw-shot-grid' },
          [
            ['01 · Main Angle', 'Clean 1:1 front shot', droppedPreviews[0] && droppedPreviews[0].url],
            ['02 · Back / Profile', 'Full silhouette', droppedPreviews[1] && droppedPreviews[1].url],
            ['03 · Label / Serial', 'Brand tag or hallmark', droppedPreviews[2] && droppedPreviews[2].url],
            ['04 · Scale / Wear', 'Measurements & flaws', droppedPreviews[3] && droppedPreviews[3].url]
          ].map(([code, desc, src]) =>
            h(
              'div',
              { key: code, className: 'vw-shot-cell' },
              h(StudioThumb, { item: loaded || { title }, size: 46, customSrc: src || '', showCount: false }),
              h(
                'div',
                null,
                h('div', { style: { fontSize: 11.5, fontWeight: 650, color: 'var(--vw-ink)' } }, code),
                h('div', { style: { fontSize: 11, color: 'var(--vw-muted)' } }, desc)
              )
            )
          )
        ),
        h(
          'div',
          { style: { marginTop: 16 } },
          h(
            'div',
            { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 } },
            h('span', { className: 'vw-label-caps' }, 'Condition & Measurements'),
            h(
              'span',
              { className: 'vw-tag', 'data-vw-tone': bannedHits.length ? 'bad' : 'good' },
              bannedHits.length ? `Remove filler: ${bannedHits.join(', ')}` : 'Buyer-ready copy ✓'
            )
          ),
          h('textarea', {
            className: 'vw-input',
            style: { minHeight: 78, resize: 'vertical' },
            value: notes,
            onChange: e => setNotes(e.target.value)
          })
        )
      ),

      // RIGHT: 80-Char Title + Comp Pricing + USPS Weight & 139-DIM Check
      h(
        'div',
        { className: 'vw-bench-right' },
        h(
          'div',
          { style: { marginBottom: 18 } },
          h(
            'div',
            { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 } },
            h('span', { className: 'vw-label-caps' }, '02 · Search Title (80-Character eBay Limit)'),
            h(
              'span',
              { className: 'vw-tag', 'data-vw-tone': titleLen > 80 ? 'bad' : titleLen >= 60 ? 'good' : 'gold' },
              `${titleLen}/80 chars · ${titleLen >= 60 && titleLen <= 80 ? 'Strong search density' : 'Add model or size'}`
            )
          ),
          h('input', {
            className: 'vw-input',
            style: { fontSize: 14.5, fontWeight: 600, padding: '9px 12px' },
            maxLength: 85,
            value: title,
            onChange: e => setTitle(e.target.value)
          }),
          h(
            'div',
            { style: { marginTop: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
            h(TickMeter, { pct: Math.min(100, (titleLen / 80) * 100), ticks: 36, color: titleLen > 80 ? 'var(--vw-crimson)' : titleLen >= 60 ? 'var(--vw-leaf)' : 'var(--vw-marigold)' }),
            h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 10, color: 'var(--vw-muted)' } }, `${Math.max(0, 80 - titleLen)} left`)
          )
        ),

        // Market Comp Pricing + 2026 PLG / Sales-Tax FVF Engine
        h(
          'div',
          { style: { border: '1px solid var(--vw-rule)', background: 'var(--vw-tint)', padding: '14px 16px', marginBottom: 18 } },
          h(
            'div',
            { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 } },
            h('span', { className: 'vw-label-caps' }, '03 · Buy It Now Price & 2026 Net-Margin Engine'),
            h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-sec)' } }, `P25 ${usd(band.p25_cents)} · Median ${usd(band.median_cents)} · P75 ${usd(band.p75_cents)}`)
          ),
          h(
            'div',
            { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginTop: 8 } },
            h(
              'div',
              { className: 'vw-seg' },
              [
                [0, '0% Organic'],
                [500, '5% PLG (30d)'],
                [800, '8% PLG (30d)']
              ].map(([bpsVal, lbl]) =>
                h(
                  'button',
                  {
                    key: bpsVal,
                    type: 'button',
                    className: 'vw-seg-btn',
                    style: { padding: '3.5px 9px', fontSize: 10.5 },
                    'data-vw-on': plgBps === bpsVal ? '1' : '0',
                    onClick: () => setPlgBps(bpsVal)
                  },
                  lbl
                )
              )
            ),
            h(
              'button',
              {
                type: 'button',
                className: 'vw-btn',
                'data-vw-kind': taxBasisOn ? 'primary' : 'ghost',
                style: { padding: '3.5px 9px', fontSize: 10.5, fontFamily: 'var(--vw-mono)' },
                onClick: () => setTaxBasisOn(!taxBasisOn)
              },
              taxBasisOn ? '✓ +~7% Buyer Tax FVF Basis' : '+ ~7% Buyer Tax FVF Basis'
            )
          ),
          h('input', {
            type: 'range',
            className: 'vw-slider',
            min: sliderMin,
            max: sliderMax,
            step: 100,
            value: priceCents,
            onChange: e => setPriceCents(Number(e.target.value))
          }),
          h(
            'div',
            { style: { display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 10, marginTop: 4, fontVariantNumeric: 'tabular-nums' } },
            h('div', null, h('div', { className: 'vw-label-caps' }, 'List Price'), h('strong', { style: { fontSize: 18, color: 'var(--vw-cobalt)' } }, usdExact(priceCents))),
            h('div', null, h('div', { className: 'vw-label-caps' }, 'Shipping Est.'), h('strong', { style: { fontSize: 15.5 } }, usdExact(shipEst))),
            h(
              'div',
              null,
              h('div', { className: 'vw-label-caps' }, plgBps > 0 ? 'FVF + PLG Ad' : 'Est. eBay Fee'),
              h('strong', { style: { fontSize: 15.5, color: 'var(--vw-sec)' } }, '-' + usdExact(econ.totalDeductions))
            ),
            h(
              'div',
              null,
              h('div', { className: 'vw-label-caps' }, econ.profit !== null ? 'Est. Net Profit' : 'Est. Payout'),
              h('strong', { style: { fontSize: 17.5, color: 'var(--vw-leaf)' } }, econ.profit !== null ? `${usdExact(econ.profit)} (${econ.margin}%)` : usdExact(econ.payout))
            )
          ),
          h(
            'div',
            { style: { marginTop: 8, paddingTop: 7, borderTop: '1px solid var(--vw-rule)', fontFamily: 'var(--vw-mono)', fontSize: 10.5, color: 'var(--vw-muted)', display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6 } },
            h('span', null, `Very High INAD (+5%) risk preview: -${usdExact(econ.inadRisk)}`),
            fashionShipCapWarn
              ? h('span', { style: { color: 'var(--vw-marigold-ink)', fontWeight: 600 } }, '$200–$500 band: Ship With eBay fashion cap applies — add declared coverage if apparel')
              : h('span', null, 'FVF calculated on item + shipping' + (taxBasisOn ? ' + ~7% buyer tax' : ''))
          )
        ),

        // USPS Ground Advantage Weight-Cliff + July 2026 139-DIM & Stocked Box Matcher
        h(
          'div',
          { style: { marginBottom: 18 } },
          h(
            'div',
            { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 6 } },
            h('span', { className: 'vw-label-caps' }, '04 · Weight, Stocked Box & July 2026 USPS 139-DIM'),
            dim.oversize
              ? h('span', { className: 'vw-tag', 'data-vw-tone': 'bad' }, 'Side >48" — USPS Oversize surcharge risk')
              : dim.dimTriggered
              ? h('span', { className: 'vw-tag', 'data-vw-tone': 'gold' }, `${dim.cubicIn.toLocaleString()} cu in > 1,728 · 139-DIM = ${dim.billableLbs} lb billable`)
              : isWeightCliff
              ? h('span', { className: 'vw-tag', 'data-vw-tone': 'bad' }, `${oz} oz exceeds 15.99 oz 1-lb tier — lighter mailer saves ~$4.05`)
              : h('span', { className: 'vw-tag', 'data-vw-tone': 'good' }, oz <= 15.99 ? 'Under 1-lb Ground Advantage tier ✓' : `${dim.billableLbs} lb Parcel${dim.hasDims ? ` (${dim.cubicIn} cu in ≤ 1,728)` : ''}`)
          ),
          stockedBoxes.length
            ? h(
                'div',
                { style: { display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 8 } },
                stockedBoxes.map(bx => {
                  const activeBox = String(lengthIn) === String(bx.length_in) && String(widthIn) === String(bx.width_in) && String(heightIn) === String(bx.height_in)
                  return h(
                    'button',
                    {
                      key: bx.code,
                      type: 'button',
                      className: 'vw-chip',
                      style: activeBox ? { borderColor: 'var(--vw-cobalt)', color: 'var(--vw-cobalt)', fontWeight: 700, background: 'var(--vw-cobalt-soft)' } : null,
                      onClick: () => {
                        setLengthIn(String(bx.length_in))
                        setWidthIn(String(bx.width_in))
                        setHeightIn(String(bx.height_in))
                      }
                    },
                    `${bx.code} (${bx.length_in}×${bx.width_in}×${bx.height_in})`
                  )
                })
              )
            : null,
          h(
            'div',
            { style: { display: 'grid', gridTemplateColumns: '96px repeat(3, 68px) repeat(3, minmax(0, 1fr))', gap: 7 } },
            h(
              'label',
              { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
              h('span', { className: 'vw-label-caps' }, 'Weight oz'),
              h('input', { className: 'vw-input', value: weightOz, onChange: e => setWeightOz(e.target.value) })
            ),
            h(
              'label',
              { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
              h('span', { className: 'vw-label-caps' }, 'L (in)'),
              h('input', { className: 'vw-input', value: lengthIn, onChange: e => setLengthIn(e.target.value), placeholder: '12' })
            ),
            h(
              'label',
              { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
              h('span', { className: 'vw-label-caps' }, 'W (in)'),
              h('input', { className: 'vw-input', value: widthIn, onChange: e => setWidthIn(e.target.value), placeholder: '9' })
            ),
            h(
              'label',
              { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
              h('span', { className: 'vw-label-caps' }, 'H (in)'),
              h('input', { className: 'vw-input', value: heightIn, onChange: e => setHeightIn(e.target.value), placeholder: '6' })
            ),
            h(
              'label',
              { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
              h('span', { className: 'vw-label-caps' }, 'COGS $'),
              h('input', { className: 'vw-input', value: cogsDollars, onChange: e => setCogsDollars(e.target.value), placeholder: '0.00' })
            ),
            h(
              'label',
              { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
              h('span', { className: 'vw-label-caps' }, 'Floor $'),
              h('input', { className: 'vw-input', value: floorDollars, onChange: e => setFloorDollars(e.target.value), placeholder: 'Auto' })
            ),
            h(
              'label',
              { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
              h('span', { className: 'vw-label-caps' }, 'Bin'),
              h('input', { className: 'vw-input', value: binCode, onChange: e => setBinCode(e.target.value), placeholder: 'A-01' })
            )
          ),
          h(
            'div',
            { className: 'vw-tier-grid' },
            [
              ['Up to 8 oz', '~$5.45', !dim.dimTriggered && oz > 0 && oz <= 8, false],
              ['9 – 15.9 oz', '~$7.85 · Under 1 lb', !dim.dimTriggered && oz > 8 && oz <= 15.99, false],
              ['16 – 18.5 oz', '~$11.90 · 1-lb Cliff', isWeightCliff, isWeightCliff],
              ['>1,728 cu in', dim.dimTriggered ? `139-DIM · ${dim.billableLbs} lb` : '139-DIM Guard', dim.dimTriggered, dim.dimTriggered]
            ].map(([tier, rate, active, cliff]) =>
              h(
                'div',
                { key: tier, className: 'vw-tier-cell', 'data-vw-on': active && !cliff ? '1' : '0', 'data-vw-cliff': cliff ? '1' : '0' },
                h('div', { style: { fontWeight: 600 } }, tier),
                h('div', { style: { marginTop: 2, fontSize: 10 } }, rate)
              )
            )
          )
        ),

        h(
          'div',
          { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', paddingTop: 14, borderTop: '1px solid var(--vw-rule)' } },
          h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-muted)' } }, 'Two-step write gate · Or export CRLF Draft CSV for Seller Hub'),
          h(
            'div',
            { style: { display: 'flex', gap: 8, flexWrap: 'wrap' } },
            h(
              'button',
              {
                type: 'button',
                className: 'vw-btn',
                'data-vw-kind': 'ghost',
                onClick: () => onExportCsv(loaded ? [loaded.sku] : [])
              },
              '↓ Export CRLF Draft CSV'
            ),
            loaded
              ? h(
                  'div',
                  { style: { display: 'flex', gap: 8 } },
                  h(
                    'button',
                    {
                      type: 'button',
                      className: 'vw-btn',
                      'data-vw-kind': 'primary',
                      onClick: () =>
                        onEnrich(loaded.sku, {
                          title,
                          flaws_disclosure: notes,
                          list_price_cents: priceCents,
                          package_weight_oz: oz || null,
                          package_dims: dimsPayload,
                          cogs_cents: cogsCents,
                          floor_price_cents: floorDollars ? Math.round(parseFloat(floorDollars) * 100) : null,
                          bin_code: binCode
                        })
                    },
                    'Save Changes'
                  ),
                  loaded.pending_actions && loaded.pending_actions[0]
                    ? h(
                        'button',
                        {
                          type: 'button',
                          className: 'vw-btn',
                          'data-vw-kind': 'cobalt',
                          onClick: () => onDecide(loaded.pending_actions[0].action_token, 'APPROVE')
                        },
                        'Approve & Publish'
                      )
                    : null
                )
              : h(
                  'button',
                  {
                    type: 'button',
                    className: 'vw-btn',
                    'data-vw-kind': 'cobalt',
                    onClick: () =>
                      onStage({
                        title_hint: title,
                        seller_notes: notes,
                        price_cents: priceCents,
                        package_weight_oz: oz || null,
                        package_dims: dimsPayload,
                        plg_ad_rate_bps: plgBps,
                        buyer_tax_rate_bps: taxBasisOn ? 700 : 0,
                        cogs_cents: cogsCents,
                        floor_price_cents: floorDollars ? Math.round(parseFloat(floorDollars) * 100) : null,
                        bin_code: binCode || null
                      })
                  },
                  'Stage Listing Draft →'
                )
          )
        )
      )
    )
  )
}

/* ============================================================================
 * STATION 01 — MORNING OPS: APPROVALS, BEST OFFERS & SHIPPING DISPATCH
 * ========================================================================== */

function OfferSpreadCard({ offer, feeBps, onDecide, onStageOffer }) {
  const ask = Math.max(100, offer.list_price_cents || offer.price_cents)
  const bid = offer.price_cents
  const floor = offer.floor_price_cents || Math.round(ask * 0.88)
  const midpoint = Math.round((bid + ask) / 200) * 100
  const minus5 = Math.round((ask * 0.95) / 100) * 100
  const [counterCents, setCounterCents] = useState(offer.meets_floor ? bid : floor)

  const bidPct = Math.max(6, Math.min(96, Math.round((bid / ask) * 100)))
  const previewEcon = calcLiveEconomics(counterCents, 0, feeBps, offer.cogs_cents)

  return h(
    'div',
    { style: { border: '1px solid var(--vw-rule)', background: 'var(--vw-surface)', padding: '14px 16px', marginBottom: 12 } },
    h(
      'div',
      { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 5 } },
      h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-muted)' } }, `${offer.best_offer_id} · Buyer ${offer.buyer_username}`),
      offer.is_awaiting_payment
        ? h('span', { className: 'vw-tag', 'data-vw-tone': 'gold' }, 'Accepted · Awaiting Buyer Payment')
        : h('span', { className: 'vw-tag', 'data-vw-tone': offer.meets_floor ? 'good' : 'bad' }, offer.meets_floor ? 'Meets Floor ✓' : `Below ${usd(floor)} Floor`)
    ),
    h('div', { style: { fontWeight: 650, fontSize: 14, color: 'var(--vw-ink)' } }, offer.title),
    offer.buyer_message
      ? h('div', { style: { fontSize: 12, color: 'var(--vw-sec)', fontStyle: 'italic', marginTop: 4 } }, `"${offer.buyer_message}"`)
      : null,

    h(
      'div',
      { style: { margin: '10px 0 9px', padding: '8px 10px', background: 'var(--vw-tint)', border: '1px solid var(--vw-rule)' } },
      h(
        'div',
        { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 } },
        h(TickMeter, { pct: bidPct, ticks: 26, color: offer.meets_floor ? 'var(--vw-leaf)' : 'var(--vw-cobalt)' }),
        h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 10.5, color: 'var(--vw-sec)' } }, `${bidPct}% of ask`)
      ),
      h(
        'div',
        { style: { display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-sec)' } },
        h('span', { style: { color: 'var(--vw-cobalt)', fontWeight: 600 } }, `Offer ${usd(bid)}`),
        h('span', { style: { color: 'var(--vw-ink)' } }, `Floor ${usd(floor)}`),
        h('span', null, `Buy It Now ${usd(ask)}`)
      )
    ),

    offer.is_awaiting_payment
      ? h(
          'div',
          { style: { padding: '8px 10px', background: 'var(--vw-tint)', border: '1px solid var(--vw-rule)', fontSize: 11.5, color: 'var(--vw-sec)' } },
          'eBay Instant-Payment Rule: Listing stays active for other buyers (`SellerAccept`) until checkout completes.'
        )
      : offer.staged_action_token
      ? h(
          'button',
          { type: 'button', className: 'vw-btn', 'data-vw-kind': 'cobalt', style: { width: '100%', justifyContent: 'center' }, onClick: () => onDecide(offer.staged_action_token, 'APPROVE') },
          'Approve & Send Staged Offer Reply →'
        )
      : h(
          'div',
          null,
          h(
            'div',
            { style: { display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 9 } },
            [
              ['Accept', bid],
              ['Floor', floor],
              ['Midpoint', midpoint],
              ['5% Off', minus5]
            ].map(([lbl, val]) =>
              h(
                'button',
                {
                  key: lbl,
                  type: 'button',
                  className: 'vw-btn',
                  'data-vw-kind': counterCents === val ? 'primary' : 'ghost',
                  style: { padding: '4px 9px', fontSize: 11, fontFamily: 'var(--vw-mono)' },
                  onClick: () => setCounterCents(val)
                },
                `${lbl} ${usd(val)}`
              )
            )
          ),
          h(
            'div',
            { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 } },
            h(
              'span',
              { style: { fontFamily: 'var(--vw-mono)', fontSize: 11.5, color: 'var(--vw-leaf)', fontWeight: 600 } },
              previewEcon.profit !== null
                ? `At ${usd(counterCents)} → +${usdExact(previewEcon.profit)} (${previewEcon.margin}%)`
                : `At ${usd(counterCents)} → ${usdExact(previewEcon.payout)} payout`
            ),
            h(
              'button',
              {
                type: 'button',
                className: 'vw-btn',
                'data-vw-kind': 'cobalt',
                onClick: () => onStageOffer(offer, counterCents === bid ? 'ACCEPT' : 'COUNTER', counterCents === bid ? null : counterCents)
              },
              counterCents === bid ? `Stage Accept ${usd(bid)}` : `Stage Counter ${usd(counterCents)}`
            )
          )
        )
  )
}

function MorningOpsStation({ state, onDecide, onStageOffer, onTogglePick, onInspectSku, onOpenBench }) {
  const pending = state.pending_actions || []
  const offers = state.offers || []
  const picks = state.pick_list || []
  const hauls = state.hauls || []
  const stale = state.stale_diagnostics || []
  const pro = state.mode === 'pro'
  const feeBps = (state.profile && state.profile.estimated_fee_bps) || 1360
  const pulledCount = picks.filter(p => p.packed).length
  const topHaul = hauls[0] || null

  return h(
    'div',
    { className: 'vw-cross' },
    topHaul || (pro && stale.length)
      ? h(
          'div',
          {
            style: {
              display: 'grid',
              gridTemplateColumns: topHaul && pro && stale.length ? 'repeat(2, minmax(0, 1fr))' : '1fr',
              borderBottom: '1px solid var(--vw-rule-strong)',
              background: '#f2ede2'
            }
          },
          topHaul
            ? h(
                'div',
                { style: { padding: '14px 22px', borderRight: pro && stale.length ? '1px solid var(--vw-rule)' : 0 } },
                h(
                  'div',
                  { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 6 } },
                  h('span', { className: 'vw-label-caps' }, `Haul & Death-Pile Velocity · ${topHaul.label}`),
                  h(
                    'span',
                    { className: 'vw-tag', 'data-vw-tone': topHaul.breakeven_reached ? 'good' : 'gold' },
                    topHaul.breakeven_reached ? 'Break-Even Reached ✓' : `Break-Even Left: ${usd(topHaul.remaining_breakeven_cents)}`
                  )
                ),
                h(
                  'div',
                  { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' } },
                  h(
                    'div',
                    { style: { display: 'flex', alignItems: 'center', gap: 9, fontFamily: 'var(--vw-mono)', fontSize: 11.5, color: 'var(--vw-sec)' } },
                    h(TickMeter, { pct: topHaul.pct_listed, ticks: 14, color: 'var(--vw-cobalt)' }),
                    h('span', null, `${topHaul.listed_count}/${topHaul.item_count} listed (${topHaul.pct_listed}%)`),
                    h('span', { style: { color: 'var(--vw-leaf)', fontWeight: 700 } }, `${topHaul.sprint_minutes}m ${topHaul.sprint_items}-item sprint → ~${usd(topHaul.sprint_ebay_balance_unlock_cents)} eBay Balance`)
                  ),
                  h(
                    'button',
                    { type: 'button', className: 'vw-btn', 'data-vw-kind': 'ghost', style: { padding: '4px 10px', fontSize: 11.5 }, onClick: () => onOpenBench('') },
                    'Start 3-Item Sprint →'
                  )
                )
              )
            : null,
          pro && stale.length
            ? h(
                'div',
                { style: { padding: '14px 22px' } },
                h(
                  'div',
                  { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 6 } },
                  h('span', { className: 'vw-label-caps' }, `90–180d Cassini Stale Diagnostics (${stale.length})`),
                  h('span', { className: 'vw-tag', 'data-vw-tone': 'blue' }, 'Gentle 5% Daily Cap')
                ),
                stale.slice(0, 2).map(st =>
                  h(
                    'div',
                    { key: st.sku, style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, fontSize: 12 } },
                    h(
                      'div',
                      { style: { minWidth: 0 } },
                      h('strong', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11.5 } }, `${st.sku} (${st.age_days}d)`),
                      ' · ',
                      h('span', { style: { color: 'var(--vw-sec)' } }, st.strategy === 'IN_PLACE_ASPECT_UPGRADE' ? 'Upgrade Specifics In-Place' : `End & Recreate at ${usd(st.suggested_price_cents)}`)
                    ),
                    h(
                      'button',
                      { type: 'button', className: 'vw-btn', 'data-vw-kind': 'ghost', style: { padding: '3px 9px', fontSize: 11 }, onClick: () => onInspectSku(st.sku) },
                      'Inspect →'
                    )
                  )
                )
              )
            : null
        )
      : null,
    h(
      'div',
      { style: { display: 'grid', gridTemplateColumns: offers.length ? 'repeat(2, minmax(0, 1fr))' : '1fr', borderBottom: '1px solid var(--vw-rule)' } },
      // Left Column: Two-Step Approvals
      h(
        'div',
        { style: { padding: '20px 22px', borderRight: offers.length ? '1px solid var(--vw-rule)' : 0 } },
        h('div', { className: 'vw-label-caps', style: { marginBottom: 12 } }, `Two-Step Approval Queue (${pending.length})`),
        pending.length
          ? pending.map(pa =>
              h(
                'div',
                { key: pa.action_token, className: 'vw-voucher' },
                h(
                  'div',
                  { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 } },
                  h('span', { className: 'vw-tag', 'data-vw-tone': 'gold' }, ACTION_TITLES[pa.action_type] || pa.action_type),
                  h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 10.5, color: 'var(--vw-marigold-ink)' } }, `SHA ${pa.payload_sha256.slice(0, 8)} · ${ttlText(pa.seconds_remaining)}`)
                ),
                h('div', { style: { fontWeight: 650, fontSize: 14, marginTop: 7, color: 'var(--vw-ink)' } }, pa.item_title),
                renderPreviewLines(pa.preview_card_md || pa.preview_markdown),
                h(
                  'div',
                  { style: { display: 'flex', gap: 8 } },
                  h('button', { type: 'button', className: 'vw-btn', 'data-vw-kind': 'cobalt', onClick: () => onDecide(pa.action_token, 'APPROVE') }, 'Approve & Execute on eBay'),
                  h('button', { type: 'button', className: 'vw-btn', 'data-vw-kind': 'danger', onClick: () => onDecide(pa.action_token, 'CANCEL') }, 'Cancel')
                )
              )
            )
          : h('div', { style: { padding: 16, border: '1px solid var(--vw-rule)', background: 'var(--vw-tint)', color: 'var(--vw-muted)' } }, 'All staged changes have been reviewed.')
      ),

      // Right Column: Best Offers
      offers.length
        ? h(
            'div',
            { style: { padding: '20px 22px' } },
            h('div', { className: 'vw-label-caps', style: { marginBottom: 12 } }, `Best Offers (${offers.length})`),
            offers.map(off => h(OfferSpreadCard, { key: off.best_offer_id, offer: off, feeBps, onDecide, onStageOffer }))
          )
        : null
    ),

    // Bottom Section: Sorted Warehouse Pick-List
    h(
      'div',
      { style: { padding: '20px 22px', background: 'var(--vw-tint)' } },
      h(
        'div',
        { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 } },
        h('span', { className: 'vw-label-caps' }, pro ? `Ready to Ship · Sorted Bin Pick-List (${pulledCount}/${picks.length} Pulled)` : `Paid Orders Ready to Ship (${pulledCount}/${picks.length})`),
        picks.length
          ? h(TickMeter, { pct: Math.round((pulledCount / Math.max(1, picks.length)) * 100), ticks: 16, color: 'var(--vw-leaf)' })
          : null
      ),
      picks.length
        ? h(
            'div',
            { style: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 } },
            picks.map((r, idx) =>
              h(
                'div',
                { key: r.order_id, className: 'vw-parcel-slip', 'data-vw-pulled': r.packed ? '1' : '0' },
                h(
                  'div',
                  { className: 'vw-parcel-stub' },
                  h('div', { className: 'vw-parcel-ticks' }, '|| | ||| || | |||'),
                  h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 9.5, color: 'var(--vw-muted)', letterSpacing: '0.06em' } }, pro ? `STOP 0${idx + 1}` : 'PAID'),
                  h('span', { className: 'vw-parcel-bin' }, pro ? r.bin_code : 'PACK')
                ),
                h(
                  'div',
                  { style: { padding: '12px 15px', minWidth: 0 } },
                  h('div', { style: { fontWeight: 600, fontSize: 13.5, color: 'var(--vw-ink)', textDecoration: r.packed ? 'line-through' : 'none' } }, r.title),
                  h(
                    'div',
                    { style: { fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-sec)', marginTop: 4 } },
                    `${usd(r.total_cents)} · ${r.buyer_username} · #${r.order_id}`
                  ),
                  h(
                    'div',
                    { style: { fontSize: 11, color: 'var(--vw-muted)', marginTop: 2 } },
                    r.package_weight_oz ? `${r.package_weight_oz} oz · USPS Ground Advantage` : 'Weigh package before printing label'
                  )
                ),
                h(
                  'div',
                  { style: { padding: '12px 14px' } },
                  h(
                    'button',
                    { type: 'button', className: 'vw-btn', 'data-vw-kind': r.packed ? 'ghost' : 'emerald', onClick: () => onTogglePick(r.order_id) },
                    r.packed ? 'Pulled ✓' : pro ? 'Mark Pulled' : 'Mark Packed'
                  )
                )
              )
            )
          )
        : h('div', { style: { color: 'var(--vw-muted)' } }, 'No paid orders awaiting shipment.')
    )
  )
}

/* ============================================================================
 * STATION 04 — STORE SETUP, SAFEGUARDS & AUDIT LOGS
 * ========================================================================== */

function SetupAndLogStation({ state, onTour, onRunCron }) {
  const [actorFilter, setActorFilter] = useState('ALL')
  const [sandboxOpen, setSandboxOpen] = useState(false)
  const [cronResults, setCronResults] = useState({})
  const [runningCron, setRunningCron] = useState('')

  const steps = (state.setup && state.setup.steps) || []
  const doneCount = steps.filter(s => s.done).length
  const isLiveConfigured = !state.tour && ((state.totals && state.totals.total_items) || 0) > 0
  const showToursBlock = !isLiveConfigured || sandboxOpen

  const allEvents = state.activity || []
  const userEventsCount = allEvents.filter(e => e.actor === 'USER').length
  const agentEventsCount = allEvents.filter(e => e.actor !== 'USER').length
  const filteredEvents = allEvents.filter(ev => {
    if (actorFilter === 'USER') return ev.actor === 'USER'
    if (actorFilter === 'AGENT') return ev.actor !== 'USER'
    return true
  })

  const feePct = (((state.profile && state.profile.estimated_fee_bps) || 1360) / 100).toFixed(1)
  const cacheStats = state.aspect_cache_stats || { cached_categories: 0, daily_quota_limit: 5000, ttl_days: 7 }
  const pm = state.partner_milestones || {}
  const cronBlueprints = state.cron_blueprints || []
  const playbooks = state.playbooks || []

  function triggerCron(jobId) {
    setRunningCron(jobId)
    onRunCron(jobId)
      .then(res => {
        if (res) {
          setCronResults(prev => ({ ...prev, [jobId]: res }))
        }
      })
      .finally(() => setRunningCron(''))
  }

  return h(
    'div',
    { className: 'vw-cross', style: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' } },
    h(
      'div',
      { style: { padding: '20px 22px', borderRight: '1px solid var(--vw-rule-strong)', background: '#f4f0e7' } },
      h(
        'div',
        { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 } },
        h('span', { className: 'vw-label-caps' }, 'Store Profile & eBay Routing'),
        h('span', { className: 'vw-tag', 'data-vw-tone': state.tour ? 'gold' : 'good' }, state.tour ? 'Sandbox Tour Active' : 'Live Store Active')
      ),
      h(
        'div',
        { style: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', border: '1px solid var(--vw-rule-strong)', background: 'var(--vw-elevated)', marginBottom: 18 } },
        h(
          'div',
          { style: { padding: '9px 12px', borderRight: '1px solid var(--vw-rule)', borderBottom: '1px solid var(--vw-rule)' } },
          h('div', { className: 'vw-label-caps' }, 'Marketplace'),
          h('strong', { style: { fontFamily: 'var(--vw-mono)', fontSize: 13, display: 'block', marginTop: 2 } }, (state.profile && state.profile.marketplace_id) || 'EBAY_US')
        ),
        h(
          'div',
          { style: { padding: '9px 12px', borderBottom: '1px solid var(--vw-rule)' } },
          h('div', { className: 'vw-label-caps' }, 'Est. Fee Schedule'),
          h('strong', { style: { fontFamily: 'var(--vw-mono)', fontSize: 13, display: 'block', marginTop: 2 } }, `${feePct}% + $0.40`)
        ),
        h(
          'div',
          { style: { padding: '9px 12px', borderRight: '1px solid var(--vw-rule)' } },
          h('div', { className: 'vw-label-caps' }, 'Publishing Route'),
          h('strong', { style: { fontSize: 12.5, display: 'block', marginTop: 2 } }, state.profile && state.profile.policies_opted_in ? 'Inventory REST API' : 'Casual C2C Fallback')
        ),
        h(
          'div',
          { style: { padding: '9px 12px' } },
          h('div', { className: 'vw-label-caps' }, 'Taxonomy Quota Cache'),
          h('strong', { style: { fontFamily: 'var(--vw-mono)', fontSize: 12.5, display: 'block', marginTop: 2 } }, `${cacheStats.cached_categories} cached · 5,000/d`)
        )
      ),

      h(
        'div',
        { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 9 } },
        h('span', { className: 'vw-label-caps' }, 'Store Setup Checklist'),
        h('span', { className: 'vw-tag', 'data-vw-tone': doneCount === steps.length ? 'good' : 'blue' }, `${doneCount}/${steps.length} Complete`)
      ),
      h(
        'div',
        { style: { border: '1px solid var(--vw-rule-strong)', background: 'var(--vw-elevated)', marginBottom: 18 } },
        steps.map((st, idx) =>
          h(
            'div',
            { key: idx, style: { padding: '10px 12px', borderTop: idx ? '1px solid var(--vw-rule)' : 0, display: 'flex', gap: 10, alignItems: 'flex-start' } },
            h('span', { className: 'vw-tag', 'data-vw-tone': st.done ? 'good' : 'gold', style: { marginTop: 1 } }, st.done ? '✓ Done' : `0${idx + 1}`),
            h(
              'div',
              { style: { minWidth: 0 } },
              h('div', { style: { fontWeight: 650, fontSize: 12.5, color: 'var(--vw-ink)' } }, st.title),
              h('div', { style: { fontSize: 11.5, color: 'var(--vw-sec)', marginTop: 2 } }, st.detail)
            )
          )
        )
      ),

      h('div', { className: 'vw-label-caps', style: { marginBottom: 8 } }, 'Seller Safeguards & 2026 eBay Compliance'),
      h(
        'div',
        { style: { border: '1px solid var(--vw-rule)', background: 'var(--vw-surface)', padding: '2px 12px', marginBottom: 16 } },
        (state.setup.rules || []).map((r, idx) =>
          h(
            'div',
            { key: idx, style: { padding: '8.5px 0', borderTop: idx ? '1px solid var(--vw-rule)' : 0 } },
            h('div', { style: { fontWeight: 650, fontSize: 12.5, color: 'var(--vw-ink)' } }, r.title),
            h('div', { style: { fontSize: 11.5, color: 'var(--vw-sec)', marginTop: 2 } }, r.detail)
          )
        )
      ),

      // @ebay/npm-public-api-mcp Pairing + Application Growth Check + Dec 31, 2026 Partner Cutoff
      h(
        'div',
        { style: { border: '1px solid var(--vw-rule-strong)', background: 'var(--vw-elevated)', padding: '12px 14px', marginBottom: 16 } },
        h(
          'div',
          { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 6 } },
          h('span', { className: 'vw-label-caps' }, 'OpenAPI MCP Pairing & 2026 Partner Milestones'),
          h('span', { className: 'vw-tag', 'data-vw-tone': 'gold' }, `Cutoff ${pm.partner_cutoff_date || '2026-12-31'}`)
        ),
        h('div', { style: { fontSize: 11.5, color: 'var(--vw-sec)', marginBottom: 7 } }, pm.growth_check_note || '7-day SQLite aspect cache guards your 5,000/day Taxonomy quota; request Application Growth Check to scale higher.'),
        pm.mcp_config_snippet
          ? h(
              'pre',
              {
                style: {
                  margin: '0 0 8px',
                  padding: '8px 10px',
                  background: 'var(--vw-tint)',
                  border: '1px solid var(--vw-rule)',
                  fontFamily: 'var(--vw-mono)',
                  fontSize: 10.5,
                  color: 'var(--vw-ink)',
                  overflowX: 'auto'
                }
              },
              pm.mcp_config_snippet
            )
          : null,
        h('div', { style: { fontSize: 11, color: 'var(--vw-muted)' } }, pm.partner_cutoff_note || '')
      ),

      // Progressive Tour Visibility: hidden automatically once the user is set up on their live store!
      showToursBlock
        ? h(
            'div',
            { style: { paddingTop: 12, borderTop: '1px solid var(--vw-rule)' } },
            h(
              'div',
              { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 } },
              h('span', { className: 'vw-label-caps' }, 'Sample Store Tours (Isolated tour.db)'),
              isLiveConfigured
                ? h('button', { type: 'button', style: { border: 0, background: 'transparent', fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-muted)', cursor: 'pointer' }, onClick: () => setSandboxOpen(false) }, 'Hide ▴')
                : null
            ),
            h('p', { style: { margin: '0 0 10px', fontSize: 12, color: 'var(--vw-sec)' } }, 'Explore casual C2C or Pro Store sample data without touching your live inventory ledger:'),
            (state.tours || []).map(tr => {
              const on = state.tour && state.tour.id === tr.id
              return h(
                'div',
                {
                  key: tr.id,
                  style: {
                    padding: '10px 12px',
                    border: '1px solid ' + (on ? 'var(--vw-cobalt)' : 'var(--vw-rule-strong)'),
                    background: on ? 'var(--vw-cobalt-soft)' : 'var(--vw-elevated)',
                    marginBottom: 8,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: 10
                  }
                },
                h('div', null, h('div', { style: { fontWeight: 650, fontSize: 12.5 } }, tr.title), h('div', { style: { fontSize: 11, color: 'var(--vw-muted)' } }, tr.subtitle)),
                h('button', { type: 'button', className: 'vw-btn', 'data-vw-kind': on ? 'cobalt' : 'ghost', onClick: () => onTour('start', tr.id) }, on ? 'Active' : 'Launch Tour')
              )
            }),
            state.tour
              ? h('button', { type: 'button', className: 'vw-btn', 'data-vw-kind': 'danger', style: { marginTop: 2 }, onClick: () => onTour('leave') }, 'Exit Tour & Use Live Store')
              : null
          )
        : h(
            'div',
            { style: { paddingTop: 10, borderTop: '1px solid var(--vw-rule)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
            h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-muted)' } }, 'Live store active · Sample tours hidden'),
            h('button', { type: 'button', className: 'vw-btn', 'data-vw-kind': 'ghost', style: { padding: '3px 9px', fontSize: 11 }, onClick: () => setSandboxOpen(true) }, 'Open Sandbox Tours ▸')
          )
    ),
    h(
      'div',
      { style: { padding: '20px 22px' } },
      // Zero-Token (--no-agent) Scheduled Cron Blueprints
      cronBlueprints.length
        ? h(
            'div',
            { style: { marginBottom: 18, paddingBottom: 16, borderBottom: '1px solid var(--vw-rule)' } },
            h(
              'div',
              { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 9 } },
              h('span', { className: 'vw-label-caps' }, 'Zero-Token Scheduled Cron Blueprints (--no-agent)'),
              h('span', { className: 'vw-tag', 'data-vw-tone': 'good' }, '$0 LLM Burn · [SILENT] Guard')
            ),
            h(
              'div',
              { style: { border: '1px solid var(--vw-rule-strong)', background: 'var(--vw-elevated)' } },
              cronBlueprints.map((bp, idx) => {
                const res = cronResults[bp.id]
                return h(
                  'div',
                  { key: bp.id, style: { padding: '10px 12px', borderTop: idx ? '1px solid var(--vw-rule)' : 0 } },
                  h(
                    'div',
                    { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 } },
                    h(
                      'div',
                      { style: { minWidth: 0 } },
                      h(
                        'div',
                        { style: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' } },
                        h('strong', { style: { fontSize: 12.5, color: 'var(--vw-ink)' } }, bp.title),
                        h('span', { className: 'vw-tag' }, bp.human_schedule),
                        h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 10.5, color: 'var(--vw-muted)' } }, bp.script)
                      ),
                      h('div', { style: { fontSize: 11.5, color: 'var(--vw-sec)', marginTop: 2 } }, bp.summary)
                    ),
                    h(
                      'button',
                      {
                        type: 'button',
                        className: 'vw-btn',
                        'data-vw-kind': 'ghost',
                        style: { padding: '4px 9px', fontSize: 11, whiteSpace: 'nowrap' },
                        disabled: runningCron === bp.id,
                        onClick: () => triggerCron(bp.id)
                      },
                      runningCron === bp.id ? 'Running…' : 'Run Check'
                    )
                  ),
                  res
                    ? h(
                        'div',
                        {
                          style: {
                            marginTop: 7,
                            padding: '7px 9px',
                            background: res.silent ? 'var(--vw-leaf-soft)' : 'var(--vw-tint)',
                            border: '1px solid var(--vw-rule)',
                            fontFamily: 'var(--vw-mono)',
                            fontSize: 10.5,
                            whiteSpace: 'pre-wrap',
                            color: res.silent ? 'var(--vw-leaf)' : 'var(--vw-ink)'
                          }
                        },
                        res.silent ? '[SILENT] — Zero actionable alerts (0 LLM tokens used).' : res.output
                      )
                    : null
                )
              })
            )
          )
        : null,

      // 5 Bundled Category SKILL.md Playbooks
      playbooks.length
        ? h(
            'div',
            { style: { marginBottom: 18, paddingBottom: 14, borderBottom: '1px solid var(--vw-rule)' } },
            h(
              'div',
              { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 } },
              h('span', { className: 'vw-label-caps' }, `Bundled Category SKILL.md Playbooks (${playbooks.length})`),
              h('span', { className: 'vw-tag', 'data-vw-tone': 'blue' }, 'Auto-Routed by Leaf Category')
            ),
            h(
              'div',
              { style: { display: 'flex', flexDirection: 'column', gap: 6 } },
              playbooks.map(pb =>
                h(
                  'div',
                  { key: pb.id, style: { padding: '7px 10px', border: '1px solid var(--vw-rule)', background: 'var(--vw-tint)', display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' } },
                  h('strong', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-cobalt)' } }, pb.id),
                  h('span', { style: { fontSize: 11.5, color: 'var(--vw-sec)', flex: 1 } }, pb.rule)
                )
              )
            )
          )
        : null,

      h(
        'div',
        { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 } },
        h('span', { className: 'vw-label-caps' }, `Activity & Audit Logs (${filteredEvents.length})`),
        h(
          'div',
          { className: 'vw-seg' },
          [
            ['ALL', `All (${allEvents.length})`],
            ['USER', `Approvals (${userEventsCount})`],
            ['AGENT', `Agent (${agentEventsCount})`]
          ].map(([id, lbl]) =>
            h(
              'button',
              { key: id, type: 'button', className: 'vw-seg-btn', 'data-vw-on': actorFilter === id ? '1' : '0', onClick: () => setActorFilter(id) },
              lbl
            )
          )
        )
      ),
      filteredEvents.length
        ? filteredEvents.slice(0, 14).map((ev, idx) =>
            h(
              'div',
              { key: ev.event_id, style: { display: 'flex', justifyContent: 'space-between', gap: 10, padding: '9.5px 0', borderTop: idx ? '1px solid var(--vw-rule)' : 0 } },
              h(
                'div',
                { style: { minWidth: 0 } },
                h(
                  'div',
                  { style: { display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' } },
                  h('span', { className: 'vw-tag', 'data-vw-tone': ev.actor === 'USER' ? 'good' : 'blue' }, ev.actor),
                  h('strong', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11.5, color: 'var(--vw-ink)' } }, ev.event_type),
                  ev.sku ? h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 10.5, color: 'var(--vw-muted)' } }, ev.sku) : null
                ),
                h('div', { style: { fontSize: 12.5, color: 'var(--vw-sec)', marginTop: 3 } }, ev.title)
              ),
              h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-muted)', whiteSpace: 'nowrap' } }, ago(ev.created_at))
            )
          )
        : h('div', { style: { padding: 18, border: '1px solid var(--vw-rule)', background: 'var(--vw-tint)', color: 'var(--vw-muted)' } }, 'No audit events match this filter yet.')
    )
  )
}

/* ============================================================================
 * TALK DOCK + WORKBENCH ROOT + 350PX SIDE PANE
 * ========================================================================== */

function TalkDock({ pro }) {
  const talk = useValue($talk)
  const [input, setInput] = useState('')
  const prompts = pro
    ? [
        'Audit my Carhartt Magical Listing',
        'Diagnose 90d+ Cassini stale listings',
        'Check comps on Leica M6 TTL',
        'Export Seller Hub CRLF Draft CSV'
      ]
    : [
        'Draft a listing from photos',
        'Export Seller Hub CRLF Draft CSV',
        'Price check Patagonia fleece',
        'What needs my approval?'
      ]

  function send(e) {
    if (e) e.preventDefault()
    if (!input.trim()) return
    const msg = input
    setInput('')
    askWorkbench(msg)
  }

  return h(
    'div',
    { className: 'vw-dock' },
    talk.open && talk.turns.length
      ? h(
          'div',
          { className: 'vw-talk-card' },
          h(
            'div',
            { style: { display: 'flex', justifyContent: 'space-between', marginBottom: 6 } },
            h(
              'span',
              { className: 'vw-label-caps', style: { display: 'inline-flex', alignItems: 'center', gap: 7 } },
              h(EbayRegMark),
              h('span', { style: { textTransform: 'none', fontWeight: 700, color: 'var(--vw-ink)' } }, 'bae'),
              '· eBay sidekick'
            ),
            h('button', { type: 'button', style: { border: 0, background: 'transparent', color: 'var(--vw-muted)', fontFamily: 'var(--vw-mono)', fontSize: 11, cursor: 'pointer' }, onClick: () => setTalk({ open: false }) }, 'Close')
          ),
          talk.turns.slice(-2).map((tr, i) =>
            h(
              'div',
              { key: i, style: { marginBottom: 8, fontSize: 12.5 } },
              h('div', { style: { fontWeight: 600, color: 'var(--vw-cobalt)' } }, 'You: ' + tr.you),
              tr.reply ? h('div', { style: { whiteSpace: 'pre-wrap', marginTop: 3, color: 'var(--vw-ink)' } }, tr.reply) : null,
              tr.note ? h('div', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11.5, color: 'var(--vw-muted)', marginTop: 2 } }, '· ' + tr.note) : null,
              tr.error ? h('div', { style: { color: 'var(--vw-crimson)', marginTop: 2 } }, tr.error) : null
            )
          )
        )
      : null,
    h(
      'div',
      { className: 'vw-dock-frame' },
      h(
        'div',
        { className: 'vw-chips' },
        h('span', { style: { fontFamily: 'var(--vw-mono)', fontSize: 9.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--vw-muted)', marginRight: 2 } }, 'Prompts'),
        prompts.map(p => h('button', { key: p, type: 'button', className: 'vw-chip', onClick: () => askWorkbench(p) }, p))
      ),
      h(
        'form',
        { className: 'vw-dock-form', onSubmit: send },
        h(EbayRegMark),
        h('input', {
          className: 'vw-dock-in',
          placeholder: 'ask bae to draft from photos, check eBay comps, clean up a listing, or counter an offer…',
          value: input,
          onChange: e => setInput(e.target.value)
        }),
        h('button', { type: 'submit', className: 'vw-btn', 'data-vw-kind': 'primary', disabled: talk.busy, style: { textTransform: 'none' } }, talk.busy ? 'working…' : 'ask bae')
      )
    )
  )
}

function WorkbenchPage() {
  const query = useWorkbenchState()
  const station = useValue($station)
  const state = query.data

  function decide(token, decision) {
    rest('/confirm', { method: 'POST', body: { action_token: token, decision } })
      .then(() => {
        host.notify({ kind: 'success', message: decision === 'APPROVE' ? 'Approved & executed on eBay.' : 'Action cancelled.' })
        refresh()
      })
      .catch(err => host.notifyError(err, 'Approval gate rejected action'))
  }

  function enrich(sku, body) {
    rest(`/item/${encodeURIComponent(sku)}/enrich`, { method: 'POST', body })
      .then(res => {
        host.notify({ kind: 'success', message: res.pro_ops_enabled ? 'Saved & enabled Pro Seller tools!' : 'Updated listing details.' })
        refresh()
      })
      .catch(err => host.notifyError(err, 'Could not update item'))
  }

  function stageNew(body) {
    rest('/stage', { method: 'POST', body })
      .then(res => {
        $selectedSku.set(res.sku)
        $station.set('catalog')
        host.notify({ kind: 'success', message: `Staged ${res.sku} — approve on the right inspector to publish.` })
        refresh()
      })
      .catch(err => host.notifyError(err, 'Could not stage draft'))
  }

  function exportCsv(skus) {
    const body = Array.isArray(skus) && skus.length ? { skus } : { status_filter: 'ALL' }
    rest('/export/draft-csv', { method: 'POST', body })
      .then(res => {
        downloadCsvBlob(res.csv_text, res.filename || 'bae-seller-hub-drafts.csv')
        host.notify({ kind: 'success', message: `Exported ${res.row_count} row(s) with Windows CRLF (Action=Draft).` })
      })
      .catch(err => host.notifyError(err, 'Could not export CRLF Draft CSV'))
  }

  function importCsv(csvText, sourceFormat) {
    rest('/import/csv', { method: 'POST', body: { csv_text: csvText, source_format: sourceFormat || 'auto' } })
      .then(res => {
        $station.set('ops')
        host.notify({
          kind: 'success',
          message: `Imported ${res.imported_count} ${res.detected_format.toUpperCase()} SKU(s) — approve bulk token in Morning Ops.`
        })
        refresh()
      })
      .catch(err => host.notifyError(err, 'Could not import migration CSV'))
  }

  function runCron(jobId) {
    return rest(`/cron/${encodeURIComponent(jobId)}/run`, { method: 'POST', body: {} })
      .then(res => {
        host.notify({
          kind: 'info',
          message: res.silent ? `${jobId}: [SILENT] ($0 tokens)` : `${jobId}: actionable digest generated ($0 tokens)`
        })
        return res
      })
      .catch(err => {
        host.notifyError(err, 'Cron check failed')
        return null
      })
  }

  function stageOffer(off, action, counterCents) {
    rest('/offer/stage', {
      method: 'POST',
      body: { best_offer_id: off.best_offer_id, ebay_item_id: off.ebay_item_id, sku: off.sku, action, counter_price_cents: counterCents }
    })
      .then(() => {
        host.notify({ kind: 'info', message: `Staged ${action} — approve slip to send to buyer.` })
        refresh()
      })
      .catch(err => host.notifyError(err, 'Could not stage offer reply'))
  }

  function togglePick(orderId) {
    rest(`/pick/${encodeURIComponent(orderId)}/toggle`, { method: 'POST', body: {} })
      .then(refresh)
      .catch(err => host.notifyError(err, 'Could not update pick slip'))
  }

  function switchTour(action, tourId) {
    rest('/tour', { method: 'POST', body: { action, tour_id: tourId } })
      .then(() => {
        $selectedSku.set('')
        $benchSku.set('')
        host.notify({ kind: 'info', message: action === 'leave' ? 'Returned to live store ledger.' : 'Switched sample seller tour.' })
        refresh()
      })
      .catch(err => host.notifyError(err, 'Could not switch tour'))
  }

  function openOnBench(sku) {
    $benchSku.set(sku || '')
    $station.set('bench')
  }

  function inspectSku(sku) {
    $selectedSku.set(sku || '')
    $station.set('catalog')
  }

  if (!state) {
    return h('div', { className: 'vw-root vw-page' }, h(StyleTag), h('div', { className: 'vw-wrap' }, h('div', { className: 'vw-sheet-grid', style: { padding: 24 } }, 'loading bae…')))
  }

  const t = state.totals
  const pro = state.mode === 'pro'
  const gross = Math.max(1, t.active_value_cents)
  const feeTicks = Math.max(2, Math.min(8, Math.round((t.est_fees_total_cents / gross) * 20)))
  const cogsTicks = pro ? Math.max(2, Math.min(10, Math.round((t.cogs_total_cents / gross) * 20))) : 0
  const keepTicks = Math.max(2, 20 - feeTicks - cogsTicks)
  const opsCount = t.pending_approvals_count + t.active_offers_count + t.unpacked_pick_count

  const stationDefs = [
    {
      id: 'ops',
      code: '01 · Tasks',
      name: 'Morning Ops',
      count: opsCount,
      alert: opsCount > 0,
      sub: pro ? `${t.pending_approvals_count} approve · ${state.offers.length} offers · ${t.unpacked_pick_count} ship` : `${t.pending_approvals_count} approvals · ${t.unpacked_pick_count} to ship`
    },
    {
      id: 'catalog',
      code: '02 · Catalog',
      name: 'Listings',
      count: t.total_items,
      alert: false,
      sub: `${t.active_count} active · ${t.qa_alerts_count} QA alerts`
    },
    {
      id: 'bench',
      code: '03 · Create',
      name: 'Listing Bench',
      count: '+ New',
      alert: false,
      sub: 'Photos · 139-DIM · CSV'
    },
    {
      id: 'setup',
      code: '04 · Store',
      name: 'Setup & Logs',
      count: (state.activity || []).length,
      alert: false,
      sub: `${state.profile.marketplace_id || 'EBAY_US'} · ${state.profile.policies_opted_in ? 'Inventory API' : 'Casual C2C'}`
    }
  ]

  return h(
    'div',
    { className: 'vw-root vw-page' },
    h(StyleTag),
    h(
      'div',
      { className: 'vw-wrap' },
      state.tour
        ? h(
            'div',
            { className: 'vw-tour-strip' },
            h(
              'div',
              { style: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' } },
              h('span', { className: 'vw-tour-tag' }, 'Sample Store'),
              h('strong', null, state.tour.title),
              h('span', { style: { color: 'var(--vw-sec)' } }, '— ' + state.tour.subtitle)
            ),
            h(
              'div',
              { style: { display: 'flex', gap: 6 } },
              (state.tours || []).map(tr =>
                h(
                  'button',
                  {
                    key: tr.id,
                    type: 'button',
                    className: 'vw-btn',
                    'data-vw-kind': state.tour.id === tr.id ? 'primary' : 'ghost',
                    style: { padding: '4px 10px', fontSize: 11.5 },
                    onClick: () => switchTour('start', tr.id)
                  },
                  tr.persona === 'pro' ? 'Pro Store Tour' : 'Casual C2C Tour'
                )
              ),
              h('button', { type: 'button', className: 'vw-btn', 'data-vw-kind': 'ghost', style: { padding: '4px 10px', fontSize: 11.5 }, onClick: () => switchTour('leave') }, 'Exit')
            )
          )
        : null,

      // Unified Architectural Sheet (Masthead + Active Seller Station)
      h(
        'div',
        { className: 'vw-sheet-grid vw-cross' },
        h('div', { className: 'vw-reg-bar', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'), h('i')),
        h(
          'header',
          { className: 'vw-masthead' },
          h(MastheadHalftone),
          h(
            'div',
            { className: 'vw-masthead-left' },
            h(
              'div',
              { className: 'vw-kicker' },
              h(EbayRegMark),
              h('span', { style: { textTransform: 'none', fontWeight: 700, fontSize: '13.5px', letterSpacing: '-0.02em', color: 'var(--vw-ink)' } }, 'bae'),
              '· your eBay sidekick',
              h('span', { className: 'vw-tag', 'data-vw-tone': pro ? 'blue' : 'good' }, pro ? 'Pro Store · Inventory API' : 'Casual C2C · Simple Mode')
            ),
            h(
              'div',
              { className: 'vw-hero-numbers' },
              h('span', { className: 'vw-gmv' }, usdShort(t.active_value_cents || t.staged_value_cents)),
              h('span', { className: 'vw-gmv-meta' }, `${t.active_count} active · ${t.staged_count} draft · ${t.sold_count} sold`),
              pro && t.potential_profit_cents > 0
                ? h('span', { className: 'vw-profit-chip' }, `+${usdShort(t.potential_profit_cents)} est. profit (${t.avg_margin_pct}% margin)`)
                : h('span', { className: 'vw-profit-chip' }, `${usdShort(t.est_net_proceeds_cents)} est. payout`)
            )
          ),
          h(
            'div',
            { className: 'vw-masthead-right' },
            h(
              'div',
              { className: 'vw-masthead-kpis' },
              h(
                'div',
                { className: 'vw-masthead-kpi' },
                h('div', { style: { fontFamily: 'var(--vw-mono)', fontSize: 9.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--vw-muted)' } }, pro ? 'Net Profit' : 'Est. Payout'),
                h('div', { style: { fontFamily: 'var(--vw-mono)', fontSize: 13.5, fontWeight: 700, color: 'var(--vw-leaf)', marginTop: 1 } }, usd(pro ? t.potential_profit_cents : t.est_net_proceeds_cents))
              ),
              pro
                ? h(
                    'div',
                    { className: 'vw-masthead-kpi' },
                    h('div', { style: { fontFamily: 'var(--vw-mono)', fontSize: 9.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--vw-muted)' } }, 'Store COGS'),
                    h('div', { style: { fontFamily: 'var(--vw-mono)', fontSize: 13.5, fontWeight: 700, color: 'var(--vw-ink)', marginTop: 1 } }, usd(t.cogs_total_cents))
                  )
                : null,
              h(
                'div',
                { className: 'vw-masthead-kpi' },
                h('div', { style: { fontFamily: 'var(--vw-mono)', fontSize: 9.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--vw-muted)' } }, 'Est. eBay Fees'),
                h('div', { style: { fontFamily: 'var(--vw-mono)', fontSize: 13.5, fontWeight: 600, color: 'var(--vw-sec)', marginTop: 1 } }, '-' + usd(t.est_fees_total_cents))
              )
            ),
            h(
              'div',
              { style: { display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--vw-mono)', fontSize: 10, color: 'var(--vw-muted)' } },
              h('span', null, pro ? 'Profit / COGS / Fee Split' : 'Payout / Fee Split'),
              h(
                'span',
                { className: 'vw-tick-strip', 'aria-hidden': 'true' },
                h('span', { style: { color: 'var(--vw-leaf)', fontWeight: 700 } }, '|'.repeat(keepTicks)),
                cogsTicks > 0 ? h('span', { style: { color: '#56534b' } }, ':'.repeat(cogsTicks)) : null,
                h('span', { style: { color: 'var(--vw-marigold)' } }, '·'.repeat(feeTicks))
              )
            )
          )
        ),
        h(
          'nav',
          { className: 'vw-stations vw-cross', 'aria-label': 'eBay Seller Stations' },
          stationDefs.map(st =>
            h(
              'button',
              {
                key: st.id,
                type: 'button',
                className: 'vw-station-btn',
                'data-vw-on': station === st.id ? '1' : '0',
                onClick: () => $station.set(st.id)
              },
              h(
                'div',
                { className: 'vw-station-idx' },
                h('span', null, st.code),
                h('span', { className: 'vw-station-badge', 'data-vw-alert': st.alert ? '1' : '0' }, String(st.count))
              ),
              h('div', { className: 'vw-station-title' }, st.name),
              h('div', { className: 'vw-station-desc' }, st.sub)
            )
          )
        ),

        station === 'catalog' ? h(CatalogStation, { state, onDecide: decide, onEnrich: enrich, onOpenOnBench: openOnBench, onExportCsv: exportCsv }) : null,
        station === 'ops' ? h(MorningOpsStation, { state, onDecide: decide, onStageOffer: stageOffer, onTogglePick: togglePick, onInspectSku: inspectSku, onOpenBench: openOnBench }) : null,
        station === 'bench' ? h(ListingBenchStation, { state, onStage: stageNew, onEnrich: enrich, onDecide: decide, onExportCsv: exportCsv, onImportCsv: importCsv }) : null,
        station === 'setup' ? h(SetupAndLogStation, { state, onTour: switchTour, onRunCron: runCron }) : null
      ),

      h(TalkDock, { pro })
    )
  )
}

function SidePane() {
  const query = useWorkbenchState()
  const state = query.data
  if (!state) return h('div', { className: 'vw-root vw-pane' }, h(StyleTag), h('div', { className: 'vw-pane-inner' }, 'loading bae…'))
  const pending = state.pending_actions || []
  const picks = (state.pick_list || []).filter(p => !p.packed)

  return h(
    'div',
    { className: 'vw-root vw-pane' },
    h(StyleTag),
    h('div', { className: 'vw-reg-bar', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'), h('i')),
    h(
      'div',
      { className: 'vw-pane-inner' },
      h(
        'div',
        { className: 'vw-sheet-grid vw-cross', style: { padding: '15px 16px' } },
        h(
          'div',
          { className: 'vw-kicker' },
          h(EbayRegMark),
          h('span', { style: { textTransform: 'none', fontWeight: 700, fontSize: '13px', letterSpacing: '-0.02em', color: 'var(--vw-ink)' } }, 'bae'),
          `· ${state.mode === 'pro' ? 'Pro Store' : 'Casual C2C'}`
        ),
        h('div', { style: { fontSize: 26, fontWeight: 700, marginTop: 5, letterSpacing: '-0.03em' } }, usdShort(state.totals.active_value_cents) + ' active'),
        h('div', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11, color: 'var(--vw-sec)', marginTop: 4 } }, `${pending.length} approvals · ${state.offers.length} offers · ${picks.length} to ship`),
        h(
          'button',
          { type: 'button', className: 'vw-btn', 'data-vw-kind': 'primary', style: { marginTop: 11, width: '100%', justifyContent: 'center' }, onClick: () => host.navigate('/bae') },
          'Open Seller Desk'
        )
      ),
      pending.map(pa =>
        h(
          'div',
          { key: pa.action_token, className: 'vw-voucher', style: { marginBottom: 0 } },
          h('div', { className: 'vw-label-caps', style: { color: 'var(--vw-marigold-ink)' } }, `${ACTION_TITLES[pa.action_type] || pa.action_type} · ${ttlText(pa.seconds_remaining)}`),
          h('div', { style: { fontWeight: 650, margin: '5px 0 9px', fontSize: 12.5, color: 'var(--vw-ink)' } }, pa.item_title),
          h(
            'div',
            { style: { display: 'flex', gap: 6 } },
            h(
              'button',
              {
                type: 'button',
                className: 'vw-btn',
                'data-vw-kind': 'cobalt',
                onClick: () => rest('/confirm', { method: 'POST', body: { action_token: pa.action_token, decision: 'APPROVE' } }).then(refresh)
              },
              'Approve'
            ),
            h(
              'button',
              {
                type: 'button',
                className: 'vw-btn',
                'data-vw-kind': 'danger',
                onClick: () => rest('/confirm', { method: 'POST', body: { action_token: pa.action_token, decision: 'CANCEL' } }).then(refresh)
              },
              'Cancel'
            )
          )
        )
      )
    )
  )
}

function InChatItemCard({ attrs }) {
  const query = useWorkbenchState()
  const sku = String((attrs && attrs.sku) || '').trim()
  if (!sku || !query.data) return null
  const item = (query.data.items || []).find(i => i.sku === sku)
  if (!item) return null
  return h(
    'div',
    { className: 'vw-root vw-sheet-grid', style: { margin: '8px 0', maxWidth: 520, display: 'flex', gap: 12, alignItems: 'center', padding: '11px 14px' } },
    h(StyleTag),
    h(StudioThumb, { item, size: 50 }),
    h(
      'div',
      null,
      h('div', { className: 'vw-kicker' }, h(EbayRegMark), `${item.sku} · ${item.status}`),
      h('div', { style: { fontWeight: 650, fontSize: 13.5, margin: '2px 0' } }, item.title),
      h('div', { style: { fontFamily: 'var(--vw-mono)', fontSize: 11.5, color: 'var(--vw-cobalt)' } }, `${usd(item.list_price_cents)} · Quality ${item.qa_score}/100`)
    )
  )
}

export default {
  id: 'hermes-ebay-ops',
  name: 'bae',
  register(ctx) {
    rest = ctx.rest
    storage = ctx.storage
    const heartbeat = () => {
      ctx.rest('/hello', { method: 'POST', body: {} }).catch(() => {})
    }
    heartbeat()
    ctx.setInterval(heartbeat, 30000)
    ctx.onEvent('plugin.hermes-ebay-ops.changed', refresh)

    const mine = event =>
      Boolean(talkSession.runtime) &&
      $talk.get().busy &&
      String((event && (event.session_id || event.sessionId)) || '') === talkSession.runtime

    ctx.onEvent('message.delta', event => {
      const text = event && event.payload && event.payload.text
      if (mine(event) && typeof text === 'string') {
        setTurn(last => ({ reply: last.reply + text, note: '' }))
      }
    })
    ctx.onEvent('message.complete', event => {
      if (!mine(event)) return
      const payload = (event && event.payload) || {}
      setTurn(last => ({
        note: '',
        reply: payload.text || payload.rendered || last.reply || 'Done.',
        error: typeof payload.error === 'string' ? payload.error : ''
      }))
      setTalk({ busy: false })
      refresh()
    })

    ctx.registerMany([
      { id: 'pane', area: 'panes', title: 'bae', data: { placement: 'right', width: '350px' }, render: () => h(SidePane) },
      { id: 'page', area: 'routes', data: { path: '/bae' }, render: () => h(WorkbenchPage) },
      { id: 'nav', area: 'sidebar.nav', data: { path: '/bae', label: 'bae', codicon: 'package' } },
      { id: 'card', area: 'transcript.directives', data: { name: 'bae-card', render: props => h(InChatItemCard, props) } },
      { id: 'open', area: 'palette', data: { id: 'bae.open', label: 'bae: open eBay seller desk', keywords: ['bae', 'ebay', 'listings', 'inventory', 'seller'], run: () => host.navigate('/bae') } }
    ])
  }
}
