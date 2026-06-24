/**
 * Canvas-drawn long/short trade zones (profit/risk rectangles + labels).
 * Zones render on the bottom z-order layer without drawBackground (avoids LWC black band).
 */
(function (global) {
  const PROFIT_FILL = 'rgba(34, 197, 94, 0.28)';
  const RISK_FILL = 'rgba(239, 68, 68, 0.28)';
  const ENTRY_LINE_COLOR = '#0a0a0a';
  const LABEL_COLOR = '#0a0a0a';
  const NORMAL_FONT = '11px "Space Mono", "Courier New", monospace';
  const HIGHLIGHT_FONT = '12px "Space Mono", "Courier New", monospace';
  const NORMAL_FILL = '#fffef5';
  const HIGHLIGHT_FILL = '#FFE500';
  const HIT_PADDING = 4;
  const AUTOSCALE_MAX_PCT = 0.12;

  function formatPnl(pnl) {
    const sign = pnl >= 0 ? '+' : '';
    return `${sign}${Number(pnl).toFixed(2)}`;
  }

  function isLongTrade(direction) {
    return String(direction).toUpperCase() === 'LONG';
  }

  function levelNearEntry(entry, level) {
    if (!Number.isFinite(level) || !Number.isFinite(entry) || entry === 0) {
      return false;
    }
    return Math.abs(level - entry) / Math.abs(entry) <= AUTOSCALE_MAX_PCT;
  }

  function clampY(y, height) {
    if (y == null || Number.isNaN(y)) return null;
    return Math.max(0, Math.min(height, y));
  }

  function drawProfitAndRiskZones(canvas, direction, left, width, entryY, tpY, slY, height) {
    const entry = clampY(entryY, height);
    const tp = clampY(tpY, height);
    const sl = clampY(slY, height);
    if (entry == null || tp == null || sl == null) return;

    if (isLongTrade(direction)) {
      if (tp >= entry || sl <= entry) return;
      canvas.fillStyle = PROFIT_FILL;
      canvas.fillRect(left, tp, width, entry - tp);
      canvas.fillStyle = RISK_FILL;
      canvas.fillRect(left, entry, width, sl - entry);
      return;
    }
    if (tp <= entry || sl >= entry) return;
    canvas.fillStyle = PROFIT_FILL;
    canvas.fillRect(left, entry, width, tp - entry);
    canvas.fillStyle = RISK_FILL;
    canvas.fillRect(left, sl, width, entry - sl);
  }

  function drawZoneLabel(canvas, x, y, text, labelId, align, highlighted, labelBounds) {
    const font = highlighted ? HIGHLIGHT_FONT : NORMAL_FONT;
    const padX = highlighted ? 6 : 5;
    const labelHeight = highlighted ? 18 : 16;
    const borderWidth = highlighted ? 3 : 2;

    canvas.font = font;
    canvas.textAlign = align;
    canvas.textBaseline = 'middle';
    const metrics = canvas.measureText(text);
    const width = metrics.width + padX * 2;
    let left;
    let textX;
    if (align === 'center') {
      left = x - width / 2;
      textX = x;
    } else if (align === 'right') {
      left = x - width;
      textX = left + padX;
      canvas.textAlign = 'left';
    } else {
      left = x;
      textX = left + padX;
      canvas.textAlign = 'left';
    }

    const top = y - labelHeight / 2;
    canvas.fillStyle = highlighted ? HIGHLIGHT_FILL : NORMAL_FILL;
    canvas.fillRect(left, top, width, labelHeight);
    canvas.strokeStyle = LABEL_COLOR;
    canvas.lineWidth = borderWidth;
    canvas.strokeRect(left + borderWidth / 2, top + borderWidth / 2, width - borderWidth, labelHeight - borderWidth);
    canvas.fillStyle = LABEL_COLOR;
    canvas.fillText(text, textX, y);

    labelBounds.push({ id: labelId, left, top, width, height: labelHeight });
  }

  class TradeZonesRenderer {
    constructor(getContext) {
      this._getContext = getContext;
    }

    draw(target) {
      const ctx = this._getContext();
      if (!ctx || ctx.trades.length === 0) return;

      ctx.labelBounds.length = 0;

      target.useMediaCoordinateSpace((scope) => {
        const canvas = scope.context;
        const height = scope.mediaSize.height;
        const timeScale = ctx.chart.timeScale();

        for (const trade of ctx.trades) {
          const x1 = timeScale.timeToCoordinate(trade.entryTime);
          const x2 = timeScale.timeToCoordinate(trade.exitTime);
          if (x1 === null || x2 === null) continue;

          const entryY = ctx.series.priceToCoordinate(trade.entryPrice);
          const tpY = ctx.series.priceToCoordinate(trade.takeProfit);
          const slY = ctx.series.priceToCoordinate(trade.stopLoss);
          if (entryY === null || tpY === null || slY === null) continue;

          const left = Math.min(x1, x2);
          const right = Math.max(x1, x2);
          const width = right - left;
          if (width < 1) continue;

          drawProfitAndRiskZones(canvas, trade.direction, left, width, entryY, tpY, slY, height);

          canvas.strokeStyle = ENTRY_LINE_COLOR;
          canvas.lineWidth = 2;
          canvas.setLineDash([]);
          canvas.beginPath();
          canvas.moveTo(left, entryY);
          canvas.lineTo(right, entryY);
          canvas.stroke();

          const labelX = left + width / 2;
          const exitText = trade.exitPrice != null
            ? `exit @ ${trade.exitPrice.toFixed(2)} (${formatPnl(trade.netPnl)})`
            : formatPnl(trade.netPnl);
          const centerLabel = `#${trade.tradeId} ${trade.direction}  entry @ ${trade.entryPrice.toFixed(2)}  →  ${exitText}  R:R ${trade.setupRr}`;
          const entryId = `${trade.tradeId}-entry`;
          const targetId = `${trade.tradeId}-target`;
          const stopId = `${trade.tradeId}-stop`;

          drawZoneLabel(canvas, labelX, entryY, centerLabel, entryId, 'center', ctx.hoveredLabelId === entryId, ctx.labelBounds);

          const edgeX = left + 6;
          drawZoneLabel(canvas, edgeX, tpY, `Target ${trade.takeProfit.toFixed(2)}`, targetId, 'left', ctx.hoveredLabelId === targetId, ctx.labelBounds);
          drawZoneLabel(canvas, edgeX, slY, `Stop ${trade.stopLoss.toFixed(2)}`, stopId, 'left', ctx.hoveredLabelId === stopId, ctx.labelBounds);
        }
      });
    }
  }

  class TradeZonesPaneView {
    constructor(getContext) {
      this._getContext = getContext;
    }

    zOrder() {
      return 'bottom';
    }

    renderer() {
      return new TradeZonesRenderer(this._getContext);
    }
  }

  class TradeZonesPrimitive {
    constructor(trades) {
      this._trades = trades || [];
      this._chart = null;
      this._series = null;
      this._rangeHandler = null;
      this._requestUpdate = null;
      this._hoveredLabelId = null;
      this._labelBounds = [];
      this._paneView = new TradeZonesPaneView(() => this._renderContext());
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
      this._hoveredLabelId = null;
      this._labelBounds = [];
    }

    setTrades(trades) {
      this._trades = trades || [];
      this._requestUpdate?.();
    }

    setHoveredLabel(id) {
      if (this._hoveredLabelId === id) return;
      this._hoveredLabelId = id;
      this._requestUpdate?.();
    }

    hitTestLabel(x, y) {
      for (let i = this._labelBounds.length - 1; i >= 0; i -= 1) {
        const bounds = this._labelBounds[i];
        const hitLeft = bounds.left - HIT_PADDING;
        const hitTop = bounds.top - HIT_PADDING;
        const hitRight = bounds.left + bounds.width + HIT_PADDING;
        const hitBottom = bounds.top + bounds.height + HIT_PADDING;
        if (x >= hitLeft && x <= hitRight && y >= hitTop && y <= hitBottom) {
          return bounds.id;
        }
      }
      return null;
    }

    autoscaleInfo() {
      if (this._trades.length === 0) return null;

      let minValue = Number.POSITIVE_INFINITY;
      let maxValue = Number.NEGATIVE_INFINITY;
      for (const trade of this._trades) {
        const entry = trade.entryPrice;
        const levels = [entry];
        if (levelNearEntry(entry, trade.stopLoss)) levels.push(trade.stopLoss);
        if (levelNearEntry(entry, trade.takeProfit)) levels.push(trade.takeProfit);
        for (const level of levels) {
          minValue = Math.min(minValue, level);
          maxValue = Math.max(maxValue, level);
        }
      }

      if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) return null;
      return { priceRange: { minValue, maxValue } };
    }

    _renderContext() {
      if (!this._chart || !this._series) return null;
      return {
        trades: this._trades,
        chart: this._chart,
        series: this._series,
        hoveredLabelId: this._hoveredLabelId,
        labelBounds: this._labelBounds,
      };
    }
  }

  global.TradeZonesPrimitive = TradeZonesPrimitive;
})(window);
