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
  };

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
  const OB_TYPES = new Set(['order_block_entry', 'breaker_block_entry']);
  const SESSION_TYPES = new Set([
    'range_defined', 'session_range', 'asian_range', 'london_range', 'anchor_candle', 'pre_open_levels',
  ]);
  const LIQUIDITY_TYPES = new Set([
    'liquidity_mapped', 'liquidity_levels_marked', 'liquidity_level', 'liquidity_sweep',
    'swept_level', 'pre_open_liquidity', 'structural_boundaries',
  ]);
  const FIB_TYPES = new Set(['fib_079_level', 'fib_std_projection', 'fib_projection']);
  const STRUCTURE_TYPES = new Set([
    'swing_located', 'bos_level_located', 'bos_sweep', 'entry_trigger', 'mss_fvg_entry',
  ]);

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
    events.forEach((ev, i) => {
      if (!OB_TYPES.has(ev.type)) return;
      const upper = parsePrice(ev.upper ?? ev.ob_high ?? ev.high);
      const lower = parsePrice(ev.lower ?? ev.ob_low ?? ev.low);
      if (upper == null || lower == null) return;
      const startTime = parseEventTime(ev, trade);
      if (startTime == null) return;
      const endTime = resolveOverlayEnd(trade, ctx.bars, startTime, ctx.entryTs);
      if (!inVisibleRange(startTime, endTime, ctx.tMin, ctx.tMax)) return;
      overlays.push({
        id: makeId(tradeId, 'ob', 'zone', i),
        kind: 'zone',
        concept: 'ob',
        tradeId,
        startTime,
        endTime,
        priceHigh: Math.max(upper, lower),
        priceLow: Math.min(upper, lower),
        label: `#${tradeId} OB`,
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

    events.forEach((ev, i) => {
      if (ev.type === 'range_defined') {
        lastRange = { ...extractRangeLevels(ev), startTime: parseEventTime(ev, trade) };
      }
    });

    events.forEach((ev, i) => {
      if (!LIQUIDITY_TYPES.has(ev.type)) return;
      const startTime = parseEventTime(ev, trade);
      if (startTime == null) return;

      if (ev.type === 'liquidity_sweep') {
        let sweepPrice = null;
        let lineStart = lastRange?.startTime ?? startTime;
        const dir = (ev.direction || ev.sweep_dir || '').toLowerCase();
        if (lastRange) {
          if (dir.includes('high')) sweepPrice = lastRange.high;
          else if (dir.includes('low')) sweepPrice = lastRange.low;
        }
        if (sweepPrice == null) {
          sweepPrice = parsePrice(ev.level ?? ev.price ?? ev.swept_level);
        }
        const endTime = startTime;
        if (sweepPrice != null && inVisibleRange(lineStart, endTime, ctx.tMin, ctx.tMax)) {
          overlays.push({
            id: makeId(tradeId, 'liquidity', 'hline-sweep', i),
            kind: 'hline',
            concept: 'liquidity',
            tradeId,
            startTime: lineStart,
            endTime,
            price: sweepPrice,
            label: null,
            priority: 40,
            style: { line: STYLES.liquidity.line, dash: [6, 3] },
          });
        }
        overlays.push({
          id: makeId(tradeId, 'liquidity', 'marker-sweep', i),
          kind: 'marker',
          concept: 'liquidity',
          tradeId,
          startTime,
          endTime: startTime,
          price: sweepPrice ?? parsePrice(trade.entry_price),
          label: `#${tradeId} Sweep`,
          priority: 55,
          style: { marker: STYLES.liquidity.marker },
          markerShape: 'circle',
        });
        return;
      }

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
      }
    });
    return overlays;
  }

  function buildConceptOverlays(trades, bars, activeTradeIdx, overlaySettings) {
    if (!Array.isArray(trades) || !bars?.length) return [];
    const settings = { ...DEFAULT_OVERLAY_SETTINGS, ...overlaySettings };
    const tMin = bars[0].time;
    const tMax = bars[bars.length - 1].time;
    const compact = activeTradeIdx != null;
    const overlays = [];

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

  function placeLabel(canvas, x, y, text, align, placed, options = {}) {
    if (!text) return false;
    const reserved = options.reserved || [];
    const paneWidth = options.paneWidth ?? 0;
    const paneHeight = options.paneHeight ?? 0;
    const tryOffsets = options.tryOffsets || [
      [0, 0], [0, -14], [0, 14], [0, -28], [0, 28], [-20, 0], [20, 0], [0, -42], [0, 42],
    ];

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
        canvas.fillStyle = 'rgba(255, 254, 245, 0.88)';
        canvas.fillRect(rect.left, rect.top, rect.width, rect.height);
        canvas.strokeStyle = 'rgba(10, 10, 10, 0.55)';
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

        for (const ov of ctx.overlays) {
          const x1 = timeScale.timeToCoordinate(ov.startTime);
          const x2 = timeScale.timeToCoordinate(ov.endTime ?? ov.startTime);
          if (x1 === null && x2 === null) continue;

          const left = x1 != null && x2 != null ? Math.min(x1, x2) : (x1 ?? x2);
          const right = x1 != null && x2 != null ? Math.max(x1, x2) : (x1 ?? x2);
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
                placeLabel(canvas, left + width - 4, labelY, ov.label, 'right', placedLabels, labelOpts);
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
                placeLabel(canvas, left + 2, y, ov.label, 'left', placedLabels, {
                  ...labelOpts,
                  tryOffsets: [[0, -12], [0, 12], [0, -24], [0, 24], [0, -36]],
                });
              }
            }
            continue;
          }

          if (ov.kind === 'marker' && ov.price != null) {
            const x = timeScale.timeToCoordinate(ov.startTime);
            const y = ctx.series.priceToCoordinate(ov.price);
            if (x == null || y == null) continue;
            canvas.fillStyle = ov.style?.marker || '#0a0a0a';
            const r = 4;
            if (ov.markerShape === 'square') {
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
                placeLabel(canvas, x + 7, y - 12, ov.label, 'left', placedLabels, {
                  ...labelOpts,
                  tryOffsets: [[0, -12], [0, 12], [12, -12], [12, 12], [-12, -12], [0, -24]],
                });
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
      return 'bottom';
    }

    renderer() {
      return new ConceptOverlaysRenderer(this._getContext);
    }
  }

  class ConceptOverlaysPrimitive {
    constructor(overlays) {
      this._overlays = overlays || [];
      this._reservedLabelRects = [];
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

    autoscaleInfo() {
      if (this._overlays.length === 0) return null;
      let minValue = Number.POSITIVE_INFINITY;
      let maxValue = Number.NEGATIVE_INFINITY;

      for (const ov of this._overlays) {
        if (ov.priceHigh != null) {
          minValue = Math.min(minValue, ov.priceLow, ov.priceHigh);
          maxValue = Math.max(maxValue, ov.priceLow, ov.priceHigh);
        }
        if (ov.price != null) {
          minValue = Math.min(minValue, ov.price);
          maxValue = Math.max(maxValue, ov.price);
        }
      }

      if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) return null;
      return { priceRange: { minValue, maxValue } };
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
