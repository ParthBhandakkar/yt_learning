# Strategy Concept Overlays Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically draw TradingView-style strategy concept overlays (VP, FVG, liquidity, sessions, fib, structure, OB) on the Lightweight Charts price chart from trade `events[]`, labeled per trade number, with category toggles and active-trade-only default.

**Architecture:** Add `dashboard/static/concept-overlays.js` with a pure `buildConceptOverlays()` mapper that normalizes heterogeneous event payloads into typed overlay objects, plus a single `ConceptOverlaysPrimitive` canvas renderer (z-order `bottom`, low-opacity fills). Wire toggles and overlay refresh into existing `updateCandleOverlays()` in `app.js` without changing trade zone/marker logic.

**Tech Stack:** Lightweight Charts v5.2, vanilla JS dashboard, existing `TradeZonesPrimitive` pattern.

---

### Task 1: Overlay mapper (`concept-overlays.js`)

**Files:**
- Create: `dashboard/static/concept-overlays.js`

- [ ] **Step 1: Shared parsing helpers**

```javascript
const MIN_VALID_CHART_TS = 946684800;
function parseEventTime(ev, trade) { /* ISO -> unix sec, reject < 2000 */ }
function parsePrice(v) { /* finite float or null */ }
function tradeEntryTime(trade) { /* from entry_time */ }
function resolveOverlayEnd(trade, bars, startTs, hintTs) { /* entry, hint, or +1h capped to tMax */ }
```

- [ ] **Step 2: Event-type routers**

Map event `type` strings to overlay builders:

| Toggle | Event types | Overlay kinds |
|--------|-------------|---------------|
| `vp` | `london_profile`, `profile_established`, `macro_levels_mapped` | `session_box` + `hline` POC/VAH/VAL; `profile` only if `bins` present |
| `fvg` | `fvg_mapped`, `htf_fvg_formed`, `htf_fvg_located`, `displacement_fvg` | `zone` upper/lower → entry |
| `ob` | `order_block_entry`, `breaker_block_entry` | `zone` only when `upper`/`lower` or `ob_high`/`ob_low` exist |
| `liquidity` | `liquidity_mapped`, `liquidity_levels_marked`, `liquidity_level`, `liquidity_sweep`, `swept_level`, `equal_highs`/`equal_lows` arrays | `hline` + `marker` at sweep/entry |
| `sessions` | `range_defined`, `session_range`, `asian_range`, `london_range`, `anchor_candle`, `pre_open_levels` | `session_box` high/low |
| `fib` | `fib_079_level`, `fib_std_projection`, `fib_projection` | `hline` per level field |
| `structure` | `swing_located`, `bos_level_located`, `bos_sweep`, `entry_trigger` (MSS) | `hline` / `marker` |

- [ ] **Step 3: `buildConceptOverlays(trades, bars, activeTradeIdx, overlaySettings)`**

Filter trades when `activeTradeIdx != null`. Skip overlays outside visible bar range. Return sorted array:

```javascript
{ id, kind, concept, tradeId, startTime, endTime, priceHigh, priceLow, price, label, priority, style, bins? }
```

- [ ] **Step 4: Export on `window`**

`buildConceptOverlays`, `ConceptOverlaysPrimitive`, `DEFAULT_OVERLAY_SETTINGS`.

---

### Task 2: Canvas primitive renderer

**Files:**
- Modify: `dashboard/static/concept-overlays.js`

- [ ] **Step 1: `ConceptOverlaysRenderer.draw()`**

Draw in order: session_box/zone fills → hlines → profile histogram bars → markers → labels with collision offset (18px steps, max 3).

- [ ] **Step 2: `ConceptOverlaysPrimitive`**

Mirror `TradeZonesPrimitive`: `attached`, `detached`, `setOverlays`, `autoscaleInfo`, `zOrder() => 'bottom'`.

---

### Task 3: Wire dashboard

**Files:**
- Modify: `dashboard/static/app.js`
- Modify: `dashboard/static/index.html`
- Modify: `dashboard/static/style.css`

- [ ] **Step 1: State**

```javascript
let candleConceptsPrimitive = null;
// candleState.overlaySettings = { trades: true, fvg: true, ob: true, vp: true, liquidity: true, sessions: true, fib: true, structure: true };
```

- [ ] **Step 2: `updateConceptOverlaysPrimitive()`**

Build overlays via `buildConceptOverlays`, attach/detach primitive on `candleSeries`.

- [ ] **Step 3: `updateCandleOverlays()`**

Call `updateConceptOverlaysPrimitive()` after zones/markers. Respect `overlaySettings.trades` for zones (existing behavior unchanged when true).

- [ ] **Step 4: Toggle UI**

Compact toolbar in candle chart header: checkboxes for Trades, FVG, OB, VP, Liquidity, Sessions, Fib, Structure. `onchange` → update settings → `updateCandleOverlays()`.

- [ ] **Step 5: Script tags + cache bust `?v=20240625c`**

Include `concept-overlays.js` before `app.js`.

---

### Task 4: Validation & changelog

**Files:**
- Modify: `changelog.md`

- [ ] **Step 1: Syntax check**

```bash
node --check dashboard/static/concept-overlays.js
node --check dashboard/static/app.js
```

- [ ] **Step 2: Mapper smoke test (Node)**

```bash
node -e "const fs=require('fs'); eval(fs.readFileSync('dashboard/static/concept-overlays.js','utf8').replace('window','global')); const bars=[{time:1718442000},{time:1718445600}]; const t={entry_time:'2026-06-15T13:15:00+00:00',events:[{timestamp:'2026-06-15T07:00:00+00:00',type:'london_profile',vah:4341.87,val:4313.34,poc:4337.67}]}; console.log(global.buildConceptOverlays([t],bars,0,global.DEFAULT_OVERLAY_SETTINGS).length);"
```

Expected: positive overlay count.

- [ ] **Step 3: Append changelog**

`[IST 25-JUN-2026 HH:MM:SS] - Add automatic strategy concept overlays on price chart with category toggles`

---

## Known gaps (accepted v1)

- Order-block rectangles only when strategies log `upper`/`lower` geometry (most log description-only).
- VP histogram bars only when `bins` array exists in events (currently none).
- Partial liquidity sweeps use range high/low from prior `range_defined` when sweep event lacks price.
