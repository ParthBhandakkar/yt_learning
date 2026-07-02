/**
 * Strategy concept overlays: maps trade events[] to chart primitives (VP, FVG, liquidity, etc.).
 */
(function (global) {
  const MIN_VALID_CHART_TS = 946684800;
  const DEFAULT_SPAN_SECONDS = 3600;
  const LABEL_FONT = '9px "Space Mono", "Courier New", monospace';
  const LABEL_PAD = 3;
  const LABEL_HEIGHT = 12;

  const DEFAULT_OVERLAY_SETTINGS = {
    trades: true,
    fvg: true,
    ob: true,
    vp: true,
    liquidity: true,
    sessions: true,
    fib: true,
    structure: true,
    htf: true,
  };

  const HTF_BOUNDARY_SPECS = [
    { tag: '4H', seconds: 14400, line: '#7c3aed', width: 1.5, dash: [10, 5], endDash: [3, 5], priority: 2, labelRow: 0 },
    { tag: '1H', seconds: 3600, line: '#0284c7', width: 1, dash: [6, 4], endDash: [2, 4], priority: 4, labelRow: 1 },
    { tag: '15m', seconds: 900, line: '#94a3b8', width: 1, dash: [4, 3], endDash: [2, 3], priority: 6, labelRow: 2 },
  ];

  const STYLES = {
    vp: { fill: 'rgba(99, 102, 241, 0.10)', line: '#6366f1', poc: '#4f46e5' },
    fvg: { bullFill: 'rgba(34, 197, 94, 0.14)', bearFill: 'rgba(239, 68, 68, 0.14)', line: '#0a0a0a' },
    ob: { fill: 'rgba(245, 158, 11, 0.14)', line: '#b45309' },
    liquidity: { line: '#dc2626', fill: 'rgba(220, 38, 38, 0.08)', marker: '#dc2626' },
    sessions: { fill: 'rgba(14, 165, 233, 0.10)', line: '#0284c7' },
    fib: { line: '#7c3aed', dash: [4, 3] },
    structure: { line: '#0a0a0a', marker: '#0a0a0a' },
  };

  const VP_TYPES = new Set(['london_profile', 'profile_established', 'macro_levels_mapped']);
  const FVG_TYPES = new Set(['fvg_mapped', 'htf_fvg_formed', 'htf_fvg_located', 'displacement_fvg']);
  const OB_TYPES = new Set(['order_block_entry', 'breaker_block_entry', 'order_block']);
  const SESSION_TYPES = new Set([
    'range_defined', 'session_range', 'asian_range', 'london_range', 'anchor_candle', 'pre_open_levels',
  ]);
  const LIQUIDITY_TYPES = new Set([
    'liquidity_mapped', 'liquidity_levels_marked', 'liquidity_level', 'liquidity_level_formed',
    'liquidity_sweep', 'swept_level', 'pre_open_liquidity', 'structural_boundaries',
  ]);
  const FIB_TYPES = new Set(['fib_079_level', 'fib_std_projection', 'fib_projection']);
  const STRUCTURE_TYPES = new Set([
    'swing_located', 'bos_level_located', 'bos_sweep', 'entry_trigger', 'mss_fvg_entry', 'mss',
  ]);
  const TRADE_LIFECYCLE_TYPES = new Set(['entry_tap', 'partial_take_profit', 'final_exit', 'exit']);

  function isLiquiditySweepType(type) {
    return type === 'liquidity_sweep' || type === 'bearish_sweep' || type === 'bullish_sweep';
  }

  function parsePrice(value) {
    if (value == null || value === '') return null;
    const n = typeof value === 'number' ? value : parseFloat(value);
    return Number.isFinite(n) ? n : null;
  }

  function parseEventTime(ev, trade) {
    const raw = ev?.timestamp || ev?.time;
    if (!raw) {
      if (trade?.entry_time) return parseEventTime({ timestamp: trade.entry_time }, null);
      return null;
    }
    const ts = Math.floor(new Date(raw).getTime() / 1000);
    return Number.isFinite(ts) && ts >= MIN_VALID_CHART_TS ? ts : null;
  }

  function snapToBarTime(bars, ts) {
    if (!bars || !bars.length || ts == null) return ts;
    let best = bars[0].time;
    let bestDiff = Math.abs(best - ts);
    for (const b of bars) {
      const diff = Math.abs(b.time - ts);
      if (diff < bestDiff) {
        bestDiff = diff;
        best = b.time;
      }
    }
    return best;
  }

  function snapToBarOnOrAfter(bars, ts) {
    if (!bars?.length || ts == null) return ts;
    for (let i = 0; i < bars.length; i += 1) {
      if (bars[i].time >= ts) return bars[i].time;
    }
    return bars[bars.length - 1].time;
  }

  function barPeriodSeconds(bars) {
    if (!bars || bars.length < 2) return 300;
    const gaps = [];
    for (let i = 1; i < bars.length; i += 1) {
      const g = bars[i].time - bars[i - 1].time;
      if (g > 0) gaps.push(g);
    }
    if (!gaps.length) return 300;
    gaps.sort((a, b) => a - b);
    return gaps[Math.floor(gaps.length / 2)];
  }

  function barIndexAtOrAfter(bars, ts) {
    if (!bars?.length || ts == null) return 0;
    for (let i = 0; i < bars.length; i += 1) {
      if (bars[i].time >= ts) return i;
    }
    return bars.length - 1;
  }

  function shiftBarTime(bars, ts, deltaBars) {
    if (!bars?.length || ts == null) return ts;
    const idx = barIndexAtOrAfter(bars, ts);
    const target = Math.max(0, Math.min(bars.length - 1, idx + deltaBars));
    return bars[target].time;
  }

  function extendBarTime(bars, ts, deltaBars) {
    if (!bars?.length || ts == null || deltaBars === 0) return ts;
    const period = barPeriodSeconds(bars);
    const idx = barIndexAtOrAfter(bars, ts);
    const targetIdx = idx + deltaBars;
    if (targetIdx >= 0 && targetIdx < bars.length) return bars[targetIdx].time;
    return ts + deltaBars * period;
  }

  function levelTouchTolerance(price) {
    const p = Math.abs(Number(price) || 0);
    return Math.max(p * 0.00001, 0.0001);
  }

  function barTouchedLiquidity(bar, price, directionHint, tolerance) {
    if (!bar || price == null) return false;
    const high = parsePrice(bar.high);
    const low = parsePrice(bar.low);
    if (high == null || low == null) return false;
    const dir = (directionHint || '').toLowerCase();
    if (dir.includes('high') || dir.includes('bearish')) return high >= price - tolerance;
    if (dir.includes('low') || dir.includes('bullish')) return low <= price + tolerance;
    return low <= price + tolerance && high >= price - tolerance;
  }

  function liquidityTouchDistance(bar, price, directionHint) {
    const high = parsePrice(bar?.high);
    const low = parsePrice(bar?.low);
    if (high == null || low == null || price == null) return Number.POSITIVE_INFINITY;
    const dir = (directionHint || '').toLowerCase();
    if (dir.includes('high') || dir.includes('bearish')) return Math.abs(high - price);
    if (dir.includes('low') || dir.includes('bullish')) return Math.abs(low - price);
    if (low <= price && high >= price) return 0;
    return Math.min(Math.abs(high - price), Math.abs(low - price));
  }

  function findLiquidityTouchBarTime(bars, price, rawStart, rawEnd, directionHint) {
    if (!bars?.length || price == null) return null;
    const startIdx = barIndexAtOrAfter(bars, rawStart);
    const endIdx = barIndexAtOrAfter(bars, rawEnd);
    const from = Math.max(0, Math.min(startIdx, endIdx));
    const to = Math.min(bars.length - 1, Math.max(startIdx, endIdx));
    const tolerance = levelTouchTolerance(price);

    for (let i = from; i <= to; i += 1) {
      if (barTouchedLiquidity(bars[i], price, directionHint, tolerance)) {
        return bars[i].time;
      }
    }

    let bestIdx = from;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (let i = from; i <= to; i += 1) {
      const distance = liquidityTouchDistance(bars[i], price, directionHint);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIdx = i;
      }
    }
    return bars[bestIdx]?.time ?? null;
  }

  // LWC anchors a bar at its open timestamp (left edge). The wick low/high
  // sits at the visual centre of the candle — apply a pixel offset at draw time
  // instead of storing fractional timestamps (those make timeToCoordinate fail
  // when the chart is panned or squeezed).
  function halfBarWidthPx(timeScale, barOpenTime, barPeriodSec) {
    const x0 = timeScale.timeToCoordinate(barOpenTime);
    if (x0 == null) return 0;
    const x1 = timeScale.timeToCoordinate(barOpenTime + barPeriodSec);
    if (x1 != null) return Math.abs(x1 - x0) / 2;
    return 0;
  }

  function xAtBar(timeScale, barOpenTime, barPeriodSec, align) {
    const x = timeScale.timeToCoordinate(barOpenTime);
    if (x == null) return null;
    if (align === 'center') return x + halfBarWidthPx(timeScale, barOpenTime, barPeriodSec);
    return x;
  }

  function resolveOverlayX(timeScale, ov, which, paneWidth, visibleRange) {
    const isStart = which === 'start';
    const rawTime = isStart ? ov.startTime : (ov.endTime ?? ov.startTime);
    const align = isStart ? ov.alignStart : ov.alignEnd;
    const period = ov.barPeriod || 300;
    let x = align === 'center'
      ? xAtBar(timeScale, rawTime, period, 'center')
      : timeScale.timeToCoordinate(rawTime);
    if (x == null && visibleRange) {
      if (isStart && rawTime < visibleRange.from) x = 0;
      if (!isStart && rawTime > visibleRange.to) x = paneWidth;
    }
    return x;
  }

  function findEventTimestamp(events, types) {
    const wanted = new Set(Array.isArray(types) ? types : [types]);
    for (const ev of events) {
      if (!wanted.has(ev.type)) continue;
      const ts = parseEventTime(ev, null);
      if (ts != null) return ts;
    }
    return null;
  }

  const ZONE_VISUAL_EXTEND_BARS = 4;

  function tradeEntryTime(trade) {
    if (!trade?.entry_time) return null;
    return parseEventTime({ timestamp: trade.entry_time }, null);
  }

  function tradeExitTime(trade) {
    if (!trade?.exit_time) return null;
    const ts = parseEventTime({ timestamp: trade.exit_time }, null);
    if (!ts) return null;
    const outcome = (trade.outcome || '').toLowerCase();
    if (outcome === 'open') return null;
    return ts;
  }

  function resolveOverlayEnd(trade, bars, startTs, hintTs) {
    const entryTs = tradeEntryTime(trade);
    const tMax = bars.length ? bars[bars.length - 1].time : null;
    let end = hintTs || entryTs;
    if (end == null || end <= startTs) {
      end = startTs + DEFAULT_SPAN_SECONDS;
    }
    if (entryTs != null && entryTs > startTs) {
      end = Math.min(end, entryTs);
    }
    const exitTs = tradeExitTime(trade);
    if (exitTs != null && exitTs > startTs) {
      end = Math.max(end, Math.min(exitTs, entryTs || exitTs));
    }
    if (tMax != null) end = Math.min(end, tMax);
    return end > startTs ? end : startTs + DEFAULT_SPAN_SECONDS;
  }

  function inVisibleRange(startTime, endTime, tMin, tMax) {
    if (startTime == null) return false;
    const end = endTime ?? startTime;
    return end >= tMin && startTime <= tMax;
  }

  function makeId(tradeId, concept, kind, idx) {
    return `${tradeId}-${concept}-${kind}-${idx}`;
  }

  function fvgFillStyle(ev) {
    const dir = (ev.direction || '').toLowerCase();
    if (dir === 'bullish' || dir === 'long') return STYLES.fvg.bullFill;
    if (dir === 'bearish' || dir === 'short') return STYLES.fvg.bearFill;
    return 'rgba(148, 163, 184, 0.14)';
  }

  function fvgLabel(ev, tradeId) {
    const desc = ev.description || '';
    const tfMatch = desc.match(/\b(\d+[mhd])\b/i);
    const tf = tfMatch ? tfMatch[1] : '';
    const prefix = tf ? `${tf} ` : '';
    return `#${tradeId} ${prefix}FVG`;
  }

  function extractRangeLevels(ev) {
    const high = parsePrice(ev.range_high ?? ev.high ?? ev.session_high);
    const low = parsePrice(ev.range_low ?? ev.low ?? ev.session_low);
    return { high, low };
  }

  function mapVpOverlays(trade, tradeId, events, ctx) {
    const overlays = [];
    const entryTs = ctx.entryTs;
    events.forEach((ev, i) => {
      if (!VP_TYPES.has(ev.type)) return;
      const startTime = parseEventTime(ev, trade);
      if (startTime == null) return;
      const vah = parsePrice(ev.vah ?? ev.VAH);
      const val = parsePrice(ev.val ?? ev.VAL);
      const poc = parsePrice(ev.poc ?? ev.POC);
      const endTime = resolveOverlayEnd(trade, ctx.bars, startTime, entryTs);
      if (!inVisibleRange(startTime, endTime, ctx.tMin, ctx.tMax)) return;

      if (vah != null && val != null) {
        overlays.push({
          id: makeId(tradeId, 'vp', 'session_box', i),
          kind: 'session_box',
          concept: 'vp',
          tradeId,
          startTime,
          endTime,
          priceHigh: Math.max(vah, val),
          priceLow: Math.min(vah, val),
          label: `#${tradeId} VP`,
          priority: 10,
          style: STYLES.vp,
        });
      }

      const levels = [
        { price: poc, color: STYLES.vp.poc, dash: [] },
        { price: vah, color: STYLES.vp.line, dash: [5, 4] },
        { price: val, color: STYLES.vp.line, dash: [5, 4] },
      ];
      levels.forEach((lvl, j) => {
        if (lvl.price == null) return;
        overlays.push({
          id: makeId(tradeId, 'vp', `hline-${j}`, i),
          kind: 'hline',
          concept: 'vp',
          tradeId,
          startTime,
          endTime,
          price: lvl.price,
          label: null,
          priority: 20 + j,
          style: { line: lvl.color, dash: lvl.dash },
        });
      });

      const bins = ev.bins || ev.profile_bins;
      if (Array.isArray(bins) && bins.length > 0 && vah != null && val != null) {
        overlays.push({
          id: makeId(tradeId, 'vp', 'profile', i),
          kind: 'profile',
          concept: 'vp',
          tradeId,
          startTime,
          endTime,
          priceHigh: Math.max(vah, val),
          priceLow: Math.min(vah, val),
          bins,
          priority: 15,
          style: STYLES.vp,
        });
      }
    });
    return overlays;
  }

  function mapFvgOverlays(trade, tradeId, events, ctx) {
    const overlays = [];
    events.forEach((ev, i) => {
      if (!FVG_TYPES.has(ev.type)) return;
      const upper = parsePrice(ev.upper);
      const lower = parsePrice(ev.lower);
      if (upper == null || lower == null) return;
      const startTime = parseEventTime(ev, trade);
      if (startTime == null) return;
      const endTime = resolveOverlayEnd(trade, ctx.bars, startTime, ctx.entryTs);
      if (!inVisibleRange(startTime, endTime, ctx.tMin, ctx.tMax)) return;
      overlays.push({
        id: makeId(tradeId, 'fvg', 'zone', i),
        kind: 'zone',
        concept: 'fvg',
        tradeId,
        startTime,
        endTime,
        priceHigh: Math.max(upper, lower),
        priceLow: Math.min(upper, lower),
        label: fvgLabel(ev, tradeId),
        priority: 30,
        style: { fill: fvgFillStyle(ev), line: STYLES.fvg.line },
      });
    });
    return overlays;
  }

  function mapObOverlays(trade, tradeId, events, ctx) {
    const overlays = [];
    const touchTs = findEventTimestamp(events, 'entry_tap') || ctx.entryTs;

    events.forEach((ev, i) => {
      if (!OB_TYPES.has(ev.type)) return;
      const upper = parsePrice(ev.upper ?? ev.ob_high ?? ev.high);
      const lower = parsePrice(ev.lower ?? ev.ob_low ?? ev.low);
      if (upper == null || lower == null) return;
      const rawStart = parseEventTime(ev, trade);
      if (rawStart == null) return;

      const barPeriod = barPeriodSeconds(ctx.bars);
      const startTime = snapToBarOnOrAfter(ctx.bars, rawStart);
      let endTime = touchTs != null
        ? snapToBarOnOrAfter(ctx.bars, touchTs)
        : resolveOverlayEnd(trade, ctx.bars, startTime, ctx.entryTs);

      const startIdx = barIndexAtOrAfter(ctx.bars, startTime);
      const endIdx = barIndexAtOrAfter(ctx.bars, endTime);
      // If the OB is tapped on the very next candle, extend a few bars so the
      // zone is wide enough to read on the chart.
      if (endIdx - startIdx <= 1) {
        endTime = extendBarTime(ctx.bars, endTime, ZONE_VISUAL_EXTEND_BARS);
      }

      const tMax = ctx.bars.length ? ctx.bars[ctx.bars.length - 1].time : null;
      if (tMax != null) endTime = Math.min(endTime, tMax);
      if (endTime <= startTime) {
        endTime = extendBarTime(ctx.bars, startTime, ZONE_VISUAL_EXTEND_BARS);
      }

      if (!inVisibleRange(startTime, endTime, ctx.tMin, ctx.tMax)) return;
      overlays.push({
        id: makeId(tradeId, 'ob', 'zone', i),
        kind: 'zone',
        concept: 'ob',
        tradeId,
        startTime,
        endTime,
        alignStart: 'center',
        barPeriod,
        priceHigh: Math.max(upper, lower),
        priceLow: Math.min(upper, lower),
        label: stepLabel(i, ctx, 'OB'),
        priority: 35,
        style: STYLES.ob,
      });
    });
    return overlays;
  }

  function mapSessionOverlays(trade, tradeId, events, ctx) {
    const overlays = [];
    events.forEach((ev, i) => {
      if (!SESSION_TYPES.has(ev.type)) return;
      const { high, low } = extractRangeLevels(ev);
      if (high == null || low == null) return;
      const startTime = parseEventTime(ev, trade);
      if (startTime == null) return;
      const endTime = resolveOverlayEnd(trade, ctx.bars, startTime, ctx.entryTs);
      if (!inVisibleRange(startTime, endTime, ctx.tMin, ctx.tMax)) return;
      overlays.push({
        id: makeId(tradeId, 'sessions', 'session_box', i),
        kind: 'session_box',
        concept: 'sessions',
        tradeId,
        startTime,
        endTime,
        priceHigh: Math.max(high, low),
        priceLow: Math.min(high, low),
        label: `#${tradeId} Range`,
        priority: 12,
        style: STYLES.sessions,
      });
    });
    return overlays;
  }

  function collectLiquidityLevels(ev) {
    const levels = [];
    const push = (p) => {
      const price = parsePrice(p);
      if (price != null) levels.push(price);
    };
    if (Array.isArray(ev.equal_highs)) ev.equal_highs.forEach(push);
    if (Array.isArray(ev.equal_lows)) ev.equal_lows.forEach(push);
    push(ev.level);
    push(ev.price);
    push(ev.liquidity_level);
    push(ev.swept_level);
    if (Array.isArray(ev.levels)) ev.levels.forEach(push);
    if (ev.liquidity_levels && typeof ev.liquidity_levels === 'object') {
      const ll = ev.liquidity_levels;
      push(ll.high);
      push(ll.low);
    }
    return levels;
  }

  function mapLiquidityOverlays(trade, tradeId, events, ctx) {
    const overlays = [];
    let lastRange = null;

    // First pass: capture any range_defined for strategies that emit it
    events.forEach((ev) => {
      if (ev.type === 'range_defined') {
        lastRange = { ...extractRangeLevels(ev), startTime: parseEventTime(ev, trade) };
      }
    });

    // Track liquidity levels that have been formed but not yet swept.
    // When a sweep occurs we draw a bounded line from the formation candle
    // to the sweep candle (exactly the segment the user wants).
    const pendingLevels = new Map(); // price -> { startTime, index }

    events.forEach((ev, i) => {
      if (!LIQUIDITY_TYPES.has(ev.type) && !isLiquiditySweepType(ev.type)) return;
      const startTime = parseEventTime(ev, trade);
      if (startTime == null) return;

      if (isLiquiditySweepType(ev.type)) {
        let sweepPrice = null;
        const dir = (ev.direction || ev.sweep_dir || ev.type || '').toLowerCase();
        if (lastRange) {
          if (dir.includes('high') || dir.includes('bearish')) sweepPrice = lastRange.high;
          else if (dir.includes('low') || dir.includes('bullish')) sweepPrice = lastRange.low;
        }
        if (sweepPrice == null) {
          sweepPrice = parsePrice(ev.level ?? ev.price ?? ev.swept_level);
        }
        if (sweepPrice == null) return;

        // Find a pending liquidity_level_formed at (or very near) this price
        let pending = pendingLevels.get(sweepPrice);
        if (!pending) {
          for (const [p, rec] of pendingLevels.entries()) {
            if (Math.abs(p - sweepPrice) < 0.01) { pending = rec; break; }
          }
        }

        // The strategy event timestamp can be the HTF candle open. Start from
        // the first loaded candle whose wick actually touches this liquidity
        // price, then run through the sweep and entry area.
        const rawStart = pending ? pending.startTime : startTime;
        const rawSweep = startTime;
        const rawEntry = findEventTimestamp(events, 'entry_tap') || ctx.entryTs;
        const barPeriod = barPeriodSeconds(ctx.bars);
        const touchedBarTime = findLiquidityTouchBarTime(ctx.bars, sweepPrice, rawStart, rawSweep, dir);
        const lineStart = touchedBarTime ?? snapToBarOnOrAfter(ctx.bars, rawStart);
        const sweepTime = snapToBarOnOrAfter(ctx.bars, rawSweep);
        let lineEnd = rawEntry != null
          ? snapToBarOnOrAfter(ctx.bars, rawEntry)
          : sweepTime;
        lineEnd = Math.max(lineEnd, sweepTime);

        const sweepIdx = barIndexAtOrAfter(ctx.bars, sweepTime);
        const endIdx = barIndexAtOrAfter(ctx.bars, lineEnd);
        if (endIdx - sweepIdx <= 1) {
          lineEnd = extendBarTime(ctx.bars, lineEnd, ZONE_VISUAL_EXTEND_BARS);
        }

        const tMax = ctx.bars.length ? ctx.bars[ctx.bars.length - 1].time : null;
        if (tMax != null) lineEnd = Math.min(lineEnd, tMax);

        if (inVisibleRange(lineStart, lineEnd, ctx.tMin, ctx.tMax)) {
          overlays.push({
            id: makeId(tradeId, 'liquidity', 'hline-sweep', i),
            kind: 'hline',
            concept: 'liquidity',
            tradeId,
            startTime: lineStart,
            endTime: lineEnd,
            alignStart: 'center',
            barPeriod,
            price: sweepPrice,
            label: stepLabel(pending ? pending.index : i, ctx, 'Liq'),
            priority: 40,
            style: { line: STYLES.liquidity.line, dash: [5, 4] },
          });
        }

        overlays.push({
          id: makeId(tradeId, 'liquidity', 'marker-sweep', i),
          kind: 'marker',
          concept: 'liquidity',
          tradeId,
          startTime: sweepTime,
          endTime: sweepTime,
          alignStart: 'center',
          barPeriod,
          price: sweepPrice,
          label: stepLabel(i, ctx, 'Sweep'),
          priority: 55,
          style: { marker: STYLES.liquidity.marker },
          markerShape: 'circle',
        });

        if (pending) {
          // Remove the level once it has been swept
          pendingLevels.delete(sweepPrice);
          for (const [p, rec] of pendingLevels.entries()) {
            if (rec === pending) { pendingLevels.delete(p); break; }
          }
        }
        return;
      }

      if (ev.type === 'liquidity_level_formed') {
        const price = parsePrice(ev.price ?? ev.level);
        if (price == null) return;
        // Store it; the actual bounded line will be drawn when the sweep occurs
        pendingLevels.set(price, { startTime, index: i });
        return;
      }

      // Other liquidity events (mapped levels, etc.) keep their existing behavior
      const levels = collectLiquidityLevels(ev);
      const endTime = resolveOverlayEnd(trade, ctx.bars, startTime, ctx.entryTs);
      levels.forEach((price, j) => {
        if (!inVisibleRange(startTime, endTime, ctx.tMin, ctx.tMax)) return;
        overlays.push({
          id: makeId(tradeId, 'liquidity', `hline-${j}`, i),
          kind: 'hline',
          concept: 'liquidity',
          tradeId,
          startTime,
          endTime,
          price,
          label: ctx.compact ? null : `#${tradeId} Liq`,
          priority: 38 + j,
          style: { line: STYLES.liquidity.line, dash: [5, 4] },
        });
      });
    });
    return overlays;
  }

  function mapFibOverlays(trade, tradeId, events, ctx) {
    const overlays = [];
    const fibFields = [
      ['level', '0.79'],
      ['fib_079', '0.79'],
      ['fib_2_0', '2.0'],
      ['neg_2_0', '-2.0'],
      ['neg_2_5', '-2.5'],
      ['fib_0_5', '0.5'],
      ['fib_0_618', '0.618'],
    ];

    events.forEach((ev, i) => {
      if (!FIB_TYPES.has(ev.type)) return;
      const startTime = parseEventTime(ev, trade);
      if (startTime == null) return;
      const endTime = resolveOverlayEnd(trade, ctx.bars, startTime, ctx.entryTs);
      if (!inVisibleRange(startTime, endTime, ctx.tMin, ctx.tMax)) return;

      let added = 0;
      fibFields.forEach(([field, tag]) => {
        const price = parsePrice(ev[field]);
        if (price == null) return;
        overlays.push({
          id: makeId(tradeId, 'fib', `${field}-${i}`, added),
          kind: 'hline',
          concept: 'fib',
          tradeId,
          startTime,
          endTime,
          price,
          label: ctx.compact ? null : `#${tradeId} Fib ${tag}`,
          priority: 45 + added,
          style: STYLES.fib,
        });
        added += 1;
      });

      const swingHigh = parsePrice(ev.swing_high);
      const swingLow = parsePrice(ev.swing_low);
      if (swingHigh != null) {
        overlays.push({
          id: makeId(tradeId, 'fib', `sh-${i}`, 0),
          kind: 'hline',
          concept: 'fib',
          tradeId,
          startTime,
          endTime,
          price: swingHigh,
          label: null,
          priority: 44,
          style: { line: '#a78bfa', dash: [2, 2] },
        });
      }
      if (swingLow != null) {
        overlays.push({
          id: makeId(tradeId, 'fib', `sl-${i}`, 0),
          kind: 'hline',
          concept: 'fib',
          tradeId,
          startTime,
          endTime,
          price: swingLow,
          label: null,
          priority: 44,
          style: { line: '#a78bfa', dash: [2, 2] },
        });
      }
    });
    return overlays;
  }

  function mapStructureOverlays(trade, tradeId, events, ctx) {
    const overlays = [];
    events.forEach((ev, i) => {
      if (!STRUCTURE_TYPES.has(ev.type)) return;
      const startTime = parseEventTime(ev, trade);
      if (startTime == null) return;
      const endTime = resolveOverlayEnd(trade, ctx.bars, startTime, ctx.entryTs);

      const level = parsePrice(ev.level ?? ev.bos_level ?? ev.price);
      if (ev.type === 'swing_located' && level != null) {
        if (!inVisibleRange(startTime, endTime, ctx.tMin, ctx.tMax)) return;
        overlays.push({
          id: makeId(tradeId, 'structure', 'hline', i),
          kind: 'hline',
          concept: 'structure',
          tradeId,
          startTime,
          endTime,
          price: level,
          label: ctx.compact ? null : `#${tradeId} Swing`,
          priority: 50,
          style: STYLES.structure,
        });
        return;
      }

      if (ev.type === 'bos_level_located' && level != null) {
        if (!inVisibleRange(startTime, endTime, ctx.tMin, ctx.tMax)) return;
        overlays.push({
          id: makeId(tradeId, 'structure', 'bos', i),
          kind: 'hline',
          concept: 'structure',
          tradeId,
          startTime,
          endTime,
          price: level,
          label: ctx.compact ? null : `#${tradeId} BOS`,
          priority: 52,
          style: { line: '#0a0a0a', dash: [8, 4] },
        });
        return;
      }

      if (ev.type === 'entry_trigger' || ev.type === 'mss_fvg_entry') {
        const price = level ?? parsePrice(trade.entry_price);
        if (price == null || !inVisibleRange(startTime, startTime, ctx.tMin, ctx.tMax)) return;
        overlays.push({
          id: makeId(tradeId, 'structure', 'marker', i),
          kind: 'marker',
          concept: 'structure',
          tradeId,
          startTime,
          endTime: startTime,
          price,
          label: `#${tradeId} MSS`,
          priority: 58,
          style: STYLES.structure,
          markerShape: 'square',
        });
        return;
      }

      if (ev.type === 'mss' && level != null) {
        if (!inVisibleRange(startTime, endTime, ctx.tMin, ctx.tMax)) return;
        overlays.push({
          id: makeId(tradeId, 'structure', 'mss-hline', i),
          kind: 'hline',
          concept: 'structure',
          tradeId,
          startTime,
          endTime,
          price: level,
          label: stepLabel(i, ctx, 'MSS'),
          priority: 48,
          style: { line: STYLES.structure.line, dash: [8, 4] },
        });
        overlays.push({
          id: makeId(tradeId, 'structure', 'mss-marker', i),
          kind: 'marker',
          concept: 'structure',
          tradeId,
          startTime,
          endTime: startTime,
          price: level,
          label: stepLabel(i, ctx, 'MSS'),
          priority: 58,
          style: STYLES.structure,
          markerShape: 'square',
        });
      }
    });
    return overlays;
  }

  function mapTradeLifecycleOverlays(trade, tradeId, events, ctx) {
    // Entry / partial / exit are rendered as native Lightweight Charts markers in
    // app.js (buildEventMarkers). Drawing them again here duplicates markers and
    // labels, so this mapper intentionally returns nothing.
    return [];
  }

  function htfCandleBounds(ts, periodSec) {
    const start = Math.floor(ts / periodSec) * periodSec;
    return { start, end: start + periodSec };
  }

  function pushHtfBoundaryPair(overlays, tradeId, spec, candleOpenTs, ctx) {
    if (candleOpenTs == null) return;
    const { start, end } = htfCandleBounds(candleOpenTs, spec.seconds);
    if (!inVisibleRange(start, end, ctx.tMin, ctx.tMax)) return;

    overlays.push({
      id: `htf-${tradeId}-${spec.tag}-start-${start}`,
      kind: 'vline',
      concept: 'htf',
      tradeId,
      startTime: start,
      endTime: start,
      edge: 'start',
      labelRow: spec.labelRow,
      label: `${spec.tag} start`,
      priority: spec.priority,
      style: { line: spec.line, dash: spec.dash, width: spec.width },
    });
    overlays.push({
      id: `htf-${tradeId}-${spec.tag}-end-${start}`,
      kind: 'vline',
      concept: 'htf',
      tradeId,
      startTime: end,
      endTime: end,
      edge: 'end',
      labelRow: spec.labelRow,
      label: `${spec.tag} end`,
      priority: spec.priority + 1,
      style: { line: spec.line, dash: spec.endDash, width: spec.width },
    });
  }

  function mapTradeHtfBoundaries(trade, tradeId, events, ctx) {
    const overlays = [];
    if (!events?.length) return overlays;

    let sweepTs = null;
    let mssTs = null;
    let obTs = null;

    events.forEach((ev) => {
      const ts = parseEventTime(ev, trade);
      if (ts == null) return;
      if (isLiquiditySweepType(ev.type) && sweepTs == null) sweepTs = ts;
      if (ev.type === 'mss' && mssTs == null) mssTs = ts;
      if (OB_TYPES.has(ev.type) && obTs == null) obTs = ts;
    });

    pushHtfBoundaryPair(overlays, tradeId, HTF_BOUNDARY_SPECS[0], sweepTs, ctx);
    pushHtfBoundaryPair(overlays, tradeId, HTF_BOUNDARY_SPECS[1], mssTs, ctx);
    pushHtfBoundaryPair(overlays, tradeId, HTF_BOUNDARY_SPECS[2], obTs, ctx);

    return overlays;
  }

  function buildConceptOverlays(trades, bars, activeTradeIdx, overlaySettings) {
    if (!Array.isArray(trades) || !bars?.length) return [];
    const settings = { ...DEFAULT_OVERLAY_SETTINGS, ...overlaySettings };
    const tMin = bars[0].time;
    const tMax = bars[bars.length - 1].time;
    const compact = activeTradeIdx != null;
    const overlays = [];

    const ctx = {
      bars,
      tMin,
      tMax,
      compact,
    };

    trades.forEach((trade, idx) => {
      if (activeTradeIdx != null && idx !== activeTradeIdx) return;
      const tradeId = trade.trade_number || idx + 1;
      const events = trade.events || [];
      const ctx = {
        trade,
        tradeId,
        bars,
        tMin,
        tMax,
        entryTs: tradeEntryTime(trade),
        compact,
      };

      if (settings.vp) overlays.push(...mapVpOverlays(trade, tradeId, events, ctx));
      if (settings.fvg) overlays.push(...mapFvgOverlays(trade, tradeId, events, ctx));
      if (settings.ob) overlays.push(...mapObOverlays(trade, tradeId, events, ctx));
      if (settings.sessions) overlays.push(...mapSessionOverlays(trade, tradeId, events, ctx));
      if (settings.liquidity) overlays.push(...mapLiquidityOverlays(trade, tradeId, events, ctx));
      if (settings.fib) overlays.push(...mapFibOverlays(trade, tradeId, events, ctx));
      if (settings.structure) overlays.push(...mapStructureOverlays(trade, tradeId, events, ctx));
      if (settings.htf) overlays.push(...mapTradeHtfBoundaries(trade, tradeId, events, ctx));
      if (settings.trades) overlays.push(...mapTradeLifecycleOverlays(trade, tradeId, events, ctx));
    });

    return overlays.sort((a, b) => a.priority - b.priority);
  }

  function rectsOverlap(a, b, pad = 0) {
    return a.left < b.left + b.width + pad && a.left + a.width + pad > b.left
      && a.top < b.top + b.height + pad && a.top + a.height + pad > b.top;
  }

  function clampRect(rect, paneWidth, paneHeight) {
    const left = Math.max(2, Math.min(rect.left, paneWidth - rect.width - 2));
    const top = Math.max(2, Math.min(rect.top, paneHeight - rect.height - 2));
    return { ...rect, left, top };
  }

  function stepLabel(i, ctx, name) {
    const step = i + 1;
    return ctx.compact ? `${step} ${name}` : `#${ctx.tradeId} ${step} ${name}`;
  }

  function placeLabel(canvas, x, y, text, align, placed, options = {}) {
    if (!text) return false;
    const reserved = options.reserved || [];
    const paneWidth = options.paneWidth ?? 0;
    const paneHeight = options.paneHeight ?? 0;
    // Keep labels close to their anchor so the story stays readable; the leader
    // line connects the label back to the exact point if it has to shift.
    const tryOffsets = options.tryOffsets || [
      [0, 0], [10, -14], [-10, -14], [10, 14], [-10, 14], [0, -26], [0, 26],
    ];
    const drawLeader = options.drawLeader !== false;

    canvas.font = LABEL_FONT;
    canvas.textAlign = align;
    canvas.textBaseline = 'middle';
    const metrics = canvas.measureText(text);
    const width = metrics.width + LABEL_PAD * 2;
    const height = LABEL_HEIGHT;

    for (const [dx, dy] of tryOffsets) {
      const anchorX = x + dx;
      const anchorY = y + dy;
      let left;
      if (align === 'right') left = anchorX - width;
      else if (align === 'center') left = anchorX - width / 2;
      else left = anchorX;

      let top = anchorY - height / 2;
      let rect = { left, top, width, height };
      if (paneWidth > 0 && paneHeight > 0) {
        rect = clampRect(rect, paneWidth, paneHeight);
      }

      const blocked = placed.some((p) => rectsOverlap(p, rect, 2))
        || reserved.some((p) => rectsOverlap(p, rect, 2));
      if (!blocked) {
        placed.push(rect);
        if (drawLeader && (Math.abs(rect.left + rect.width / 2 - x) > 4 || Math.abs(rect.top + rect.height / 2 - y) > 4)) {
          canvas.strokeStyle = 'rgba(10, 10, 10, 0.4)';
          canvas.lineWidth = 1;
          canvas.setLineDash([2, 2]);
          canvas.beginPath();
          canvas.moveTo(x, y);
          canvas.lineTo(rect.left + rect.width / 2, rect.top + rect.height / 2);
          canvas.stroke();
          canvas.setLineDash([]);
        }
        canvas.fillStyle = 'rgba(255, 254, 245, 0.92)';
        canvas.fillRect(rect.left, rect.top, rect.width, rect.height);
        canvas.strokeStyle = 'rgba(10, 10, 10, 0.65)';
        canvas.lineWidth = 1;
        canvas.strokeRect(rect.left + 0.5, rect.top + 0.5, rect.width - 1, rect.height - 1);
        canvas.fillStyle = '#0a0a0a';
        let textX;
        if (align === 'center') textX = rect.left + rect.width / 2;
        else if (align === 'right') textX = rect.left + rect.width - LABEL_PAD;
        else textX = rect.left + LABEL_PAD;
        canvas.textAlign = align === 'right' ? 'right' : align === 'center' ? 'center' : 'left';
        canvas.fillText(text, textX, rect.top + rect.height / 2);
        return true;
      }
    }
    return false;
  }

  function drawProfileBars(canvas, overlay, boxLeft, boxWidth, series, style) {
    const bins = overlay.bins;
    if (!Array.isArray(bins) || bins.length === 0 || boxWidth < 8) return;
    let maxVol = 0;
    for (const b of bins) {
      const vol = parsePrice(b.volume ?? b.vol ?? b.count) ?? 0;
      if (vol > maxVol) maxVol = vol;
    }
    if (maxVol <= 0) return;

    const priceHigh = overlay.priceHigh;
    const priceLow = overlay.priceLow;
    const range = priceHigh - priceLow;
    if (range <= 0) return;

    const barMaxWidth = Math.min(boxWidth * 0.45, 80);
    canvas.fillStyle = style.fill || 'rgba(99, 102, 241, 0.35)';

    bins.forEach((b) => {
      const price = parsePrice(b.price ?? b.level);
      const vol = parsePrice(b.volume ?? b.vol ?? b.count) ?? 0;
      if (price == null || vol <= 0) return;
      const y = series.priceToCoordinate(price);
      const binH = Math.max(2, (parsePrice(b.height) ?? range / bins.length) / range * 40);
      if (y == null) return;
      const w = (vol / maxVol) * barMaxWidth;
      canvas.fillRect(boxLeft + 2, y - binH / 2, w, binH);
    });
  }

  const PANE_FILL = '#fffef5';

  class ConceptOverlaysRenderer {
    constructor(getContext) {
      this._getContext = getContext;
    }

    drawBackground(target) {
      // Fill the FULL device-pixel canvas (bitmap space) so the 2x retina
      // bitmap is never left black-through on hover redraws.
      target.useBitmapCoordinateSpace((scope) => {
        const canvas = scope.context;
        canvas.fillStyle = PANE_FILL;
        canvas.fillRect(0, 0, scope.bitmapSize.width, scope.bitmapSize.height);
      });
    }

    draw(target) {
      const ctx = this._getContext();
      if (!ctx || ctx.overlays.length === 0) return;

      target.useMediaCoordinateSpace((scope) => {
        const canvas = scope.context;
        const paneWidth = scope.mediaSize.width;
        const paneHeight = scope.mediaSize.height;
        const timeScale = ctx.chart.timeScale();
        const placedLabels = [];
        const reserved = ctx.reservedLabelRects || [];
        const labelOpts = { paneWidth, paneHeight, reserved };
        const drawnLabelKeys = new Set();

        const visibleRange = timeScale.getVisibleRange?.() || null;

        for (const ov of ctx.overlays) {
          let x1 = resolveOverlayX(timeScale, ov, 'start', paneWidth, visibleRange);
          let x2 = resolveOverlayX(timeScale, ov, 'end', paneWidth, visibleRange);
          if (x1 === null && x2 === null) continue;

          const left = x1 != null && x2 != null ? Math.min(x1, x2) : (x1 ?? x2 ?? 0);
          const right = x1 != null && x2 != null ? Math.max(x1, x2) : (x1 ?? x2 ?? paneWidth);
          const width = Math.max(right - left, 2);

          if (ov.kind === 'zone' || ov.kind === 'session_box') {
            const yHigh = ctx.series.priceToCoordinate(ov.priceHigh);
            const yLow = ctx.series.priceToCoordinate(ov.priceLow);
            if (yHigh == null || yLow == null) continue;
            const top = Math.min(yHigh, yLow);
            const height = Math.abs(yLow - yHigh);
            if (height < 1) continue;

            canvas.fillStyle = ov.style?.fill || 'rgba(148, 163, 184, 0.12)';
            canvas.fillRect(left, top, width, height);
            if (ov.kind === 'session_box') {
              canvas.strokeStyle = ov.style?.line || '#64748b';
              canvas.lineWidth = 1;
              canvas.setLineDash([4, 3]);
              canvas.strokeRect(left + 0.5, top + 0.5, width - 1, height - 1);
              canvas.setLineDash([]);
            } else {
              canvas.strokeStyle = ov.style?.line || '#0a0a0a';
              canvas.lineWidth = 1;
              canvas.strokeRect(left + 0.5, top + 0.5, width - 1, height - 1);
            }

            if (ov.kind === 'profile' || (ov.bins && ov.bins.length)) {
              drawProfileBars(canvas, ov, left, width, ctx.series, ov.style || {});
            }

            if (ov.label) {
              const labelKey = `${ov.label}|${Math.round(top)}|${Math.round(left)}`;
              if (!drawnLabelKeys.has(labelKey)) {
                drawnLabelKeys.add(labelKey);
                const labelY = top + Math.min(height - 6, 10);
                placeLabel(canvas, left + 4, labelY, ov.label, 'left', placedLabels, labelOpts);
              }
            }
            continue;
          }

          if (ov.kind === 'profile') {
            const yHigh = ctx.series.priceToCoordinate(ov.priceHigh);
            const yLow = ctx.series.priceToCoordinate(ov.priceLow);
            if (yHigh == null || yLow == null) continue;
            drawProfileBars(canvas, ov, left, width, ctx.series, ov.style || {});
            continue;
          }

          if (ov.kind === 'vline') {
            const x = timeScale.timeToCoordinate(ov.startTime);
            if (x == null) continue;
            canvas.strokeStyle = ov.style?.line || '#64748b';
            canvas.lineWidth = ov.style?.width || 1;
            canvas.setLineDash(ov.style?.dash || [4, 4]);
            canvas.beginPath();
            canvas.moveTo(x, 0);
            canvas.lineTo(x, paneHeight);
            canvas.stroke();
            canvas.setLineDash([]);
            if (ov.label) {
              const row = ov.labelRow || 0;
              const y = ov.edge === 'end'
                ? paneHeight - 10 - row * 12
                : 8 + row * 12;
              const labelKey = `${ov.label}|${Math.round(x)}|${ov.edge}`;
              if (!drawnLabelKeys.has(labelKey)) {
                drawnLabelKeys.add(labelKey);
                placeLabel(canvas, x + 3, y, ov.label, 'left', placedLabels, labelOpts);
              }
            }
            continue;
          }

          if (ov.kind === 'hline' && ov.price != null) {
            const y = ctx.series.priceToCoordinate(ov.price);
            if (y == null) continue;
            canvas.strokeStyle = ov.style?.line || '#0a0a0a';
            canvas.lineWidth = 1;
            canvas.setLineDash(ov.style?.dash || []);
            canvas.beginPath();
            canvas.moveTo(left, y);
            canvas.lineTo(left + width, y);
            canvas.stroke();
            canvas.setLineDash([]);
            if (ov.label) {
              const labelKey = `${ov.label}|${Math.round(y)}`;
              if (!drawnLabelKeys.has(labelKey)) {
                drawnLabelKeys.add(labelKey);
                placeLabel(canvas, left + 2, y, ov.label, 'left', placedLabels, labelOpts);
              }
            }
            continue;
          }

          if (ov.kind === 'marker' && ov.price != null) {
            const x = resolveOverlayX(timeScale, ov, 'start', paneWidth, visibleRange);
            const y = ctx.series.priceToCoordinate(ov.price);
            if (x == null || y == null) continue;
            const r = 6;
            // White halo so the marker stays visible over candles and zones.
            canvas.fillStyle = '#fffef5';
            if (ov.markerShape === 'arrow') {
              const dir = ov.markerDirection === 'up' ? -1 : 1;
              canvas.beginPath();
              canvas.moveTo(x, y + dir * 9);
              canvas.lineTo(x - 8, y - dir * 4);
              canvas.lineTo(x + 8, y - dir * 4);
              canvas.closePath();
              canvas.fill();
            } else if (ov.markerShape === 'square') {
              canvas.fillRect(x - r - 1.5, y - r - 1.5, (r + 1.5) * 2, (r + 1.5) * 2);
            } else {
              canvas.beginPath();
              canvas.arc(x, y, r + 1.5, 0, Math.PI * 2);
              canvas.fill();
            }
            canvas.fillStyle = ov.style?.marker || '#0a0a0a';
            if (ov.markerShape === 'arrow') {
              const dir = ov.markerDirection === 'up' ? -1 : 1;
              canvas.beginPath();
              canvas.moveTo(x, y + dir * 7);
              canvas.lineTo(x - 6, y - dir * 3);
              canvas.lineTo(x + 6, y - dir * 3);
              canvas.closePath();
              canvas.fill();
            } else if (ov.markerShape === 'square') {
              canvas.fillRect(x - r, y - r, r * 2, r * 2);
            } else {
              canvas.beginPath();
              canvas.arc(x, y, r, 0, Math.PI * 2);
              canvas.fill();
            }
            if (ov.label) {
              const labelKey = `${ov.label}|${Math.round(x)}|${Math.round(y)}`;
              if (!drawnLabelKeys.has(labelKey)) {
                drawnLabelKeys.add(labelKey);
                placeLabel(canvas, x + 8, y, ov.label, 'left', placedLabels, labelOpts);
              }
            }
          }
        }
      });
    }
  }

  class ConceptOverlaysPaneView {
    constructor(getContext) {
      this._getContext = getContext;
    }

    zOrder() {
      return 'normal';
    }

    renderer() {
      return new ConceptOverlaysRenderer(this._getContext);
    }
  }

  class ConceptOverlaysPrimitive {
    constructor(overlays) {
      this._overlays = overlays || [];
      this._reservedLabelRects = [];
      this._autoscaleBand = null;
      this._chart = null;
      this._series = null;
      this._rangeHandler = null;
      this._requestUpdate = null;
      this._paneView = new ConceptOverlaysPaneView(() => this._renderContext());
    }

    paneViews() {
      return [this._paneView];
    }

    attached(param) {
      this._chart = param.chart;
      this._series = param.series;
      this._requestUpdate = () => param.requestUpdate();
      this._rangeHandler = () => param.requestUpdate();
      param.chart.timeScale().subscribeVisibleLogicalRangeChange(this._rangeHandler);
    }

    detached() {
      if (this._chart && this._rangeHandler) {
        this._chart.timeScale().unsubscribeVisibleLogicalRangeChange(this._rangeHandler);
      }
      this._chart = null;
      this._series = null;
      this._rangeHandler = null;
      this._requestUpdate = null;
    }

    setOverlays(overlays) {
      this._overlays = overlays || [];
      this._requestUpdate?.();
    }

    setReservedLabelRects(rects) {
      this._reservedLabelRects = rects || [];
      this._requestUpdate?.();
    }

    setAutoscaleBand(band) {
      this._autoscaleBand = band && Number.isFinite(band.min) && Number.isFinite(band.max)
        ? { min: band.min, max: band.max }
        : null;
      this._requestUpdate?.();
    }

    autoscaleInfo() {
      const band = this._autoscaleBand;
      if (!band) return null;
      const span = Math.max(band.max - band.min, 0);
      const center = (band.min + band.max) / 2;
      const margin = Math.max(span * 0.2, Math.abs(center) * 0.01);
      return {
        priceRange: {
          minValue: band.min - margin,
          maxValue: band.max + margin,
        },
      };
    }

    _renderContext() {
      if (!this._chart || !this._series) return null;
      return {
        overlays: this._overlays,
        chart: this._chart,
        series: this._series,
        reservedLabelRects: this._reservedLabelRects,
      };
    }
  }

  global.buildConceptOverlays = buildConceptOverlays;
  global.ConceptOverlaysPrimitive = ConceptOverlaysPrimitive;
  global.DEFAULT_OVERLAY_SETTINGS = DEFAULT_OVERLAY_SETTINGS;
})(typeof window !== 'undefined' ? window : global);
