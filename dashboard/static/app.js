let strategies = [];
let selectedStrategy = null;
let uploadedFiles = [];
let chartInstance = null;
let dataSource = 'local';
let driveFolderFiles = [];
let lastResults = null;
let candleChart = null;
let candleSeries = null;
let candleResizeObs = null;
let candleState = {
  sessionId: null,
  bars: [],
  trades: [],
  loadedStart: 0,
  loadedEnd: 0,
  total: 0,
  chunkSize: 200,
  loading: false,
};

async function loadStrategies() {
  const res = await fetch('/api/strategies');
  strategies = await res.json();
  renderStrategyList(strategies);
}

function renderStrategyList(list) {
  const el = document.getElementById('strategy-list');
  el.innerHTML = list.map(s => `
    <div class="strategy-item ${selectedStrategy && selectedStrategy.id === s.id ? 'active' : ''}"
         onclick="selectStrategy('${s.id}')">
      <span class="s-name">${s.name}</span>
      <span class="s-meta">${s.file} — ${s.csv_args.length} CSV(s)</span>
      <div>${s.csv_args.map(a => {
        const tf = a.timeframe ? ` <span class="tf-badge">${a.timeframe}</span>` : '';
        return `<span class="s-csv">${a.arg}${tf}</span>`;
      }).join('')}</div>
    </div>
  `).join('');
}

function filterStrategies() {
  const q = document.getElementById('search').value.toLowerCase();
  const filtered = strategies.filter(s =>
    s.name.toLowerCase().includes(q) || s.file.toLowerCase().includes(q)
  );
  renderStrategyList(filtered);
}

async function selectStrategy(id) {
  const res = await fetch(`/api/strategies/${id}`);
  selectedStrategy = await res.json();

  document.getElementById('empty-state').classList.add('hidden');
  document.getElementById('workspace').classList.remove('hidden');
  document.getElementById('results-section').classList.add('hidden');

  document.getElementById('strat-name').textContent = selectedStrategy.name;
  document.getElementById('strat-file').textContent = selectedStrategy.file;

  const videoBtn = document.getElementById('strat-video');
  if (selectedStrategy.video) {
    videoBtn.href = selectedStrategy.video;
    videoBtn.hidden = false;
  } else {
    videoBtn.hidden = true;
  }

  renderCsvArgs(selectedStrategy.csv_args);
  renderDriveArgInputs(selectedStrategy.csv_args);
  uploadedFiles = [];
  driveFolderFiles = [];
  updateFileList();
  document.getElementById('drive-url').value = '';
  document.getElementById('drive-files').innerHTML = '';
  renderStrategyList(strategies);
}

function renderCsvArgs(args) {
  const el = document.getElementById('csv-args');
  el.innerHTML = args.map(a => `
    <div class="csv-arg-card">
      <div class="csv-arg-header">
        <code>${a.arg}</code>
        ${a.timeframe ? `<span class="tf-badge tf-lg">${a.timeframe}</span>` : ''}
      </div>
      <div class="csv-arg-desc">${a.help}</div>
    </div>
  `).join('');
}

function renderDriveArgInputs(args) {
  const el = document.getElementById('drive-file-inputs');
  el.innerHTML = args.map(a => `
    <div class="drive-arg-row">
      <code>${a.arg}</code>
      <input type="text" class="drive-file-url" data-arg="${a.arg}" placeholder="Drive file URL or ID for ${a.timeframe || 'CSV'}">
    </div>
  `).join('');
}

// Source tabs
function switchSource(source) {
  dataSource = source;
  document.querySelectorAll('.source-tab').forEach(t => t.classList.toggle('active', t.dataset.source === source));
  document.getElementById('source-local').classList.toggle('hidden', source !== 'local');
  document.getElementById('source-drive').classList.toggle('hidden', source !== 'drive');
}

// Local file upload
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  handleFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', () => {
  handleFiles(fileInput.files);
  fileInput.value = '';
});

function handleFiles(files) {
  for (const f of files) {
    if (!f.name.endsWith('.csv')) continue;
    if (!uploadedFiles.find(u => u.name === f.name)) {
      uploadedFiles.push(f);
    }
  }
  updateFileList();
}

function removeFile(name) {
  uploadedFiles = uploadedFiles.filter(f => f.name !== name);
  updateFileList();
}

function updateFileList() {
  const el = document.getElementById('file-list');
  if (uploadedFiles.length === 0) {
    el.innerHTML = '';
    return;
  }
  el.innerHTML = uploadedFiles.map(f =>
    `<span class="file-chip">${f.name} <span class="remove" onclick="removeFile('${f.name}')">&times;</span></span>`
  ).join('');
}

// Google Drive
async function loadDriveFolder() {
  const url = document.getElementById('drive-url').value.trim();
  if (!url) return;

  const btn = document.querySelector('#source-drive .btn-secondary');
  btn.disabled = true;
  btn.textContent = 'Loading...';
  document.getElementById('loader-text').textContent = 'Connecting to Google Drive...';
  document.getElementById('loader').classList.remove('hidden');

  try {
    const res = await fetch('/api/drive/list-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    const data = await res.json();

    if (!res.ok) {
      alert(data.error);
      return;
    }

    driveFolderFiles = data.files || [];
    renderDriveFolderFiles(driveFolderFiles);
  } catch (err) {
    alert('Error: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Browse Folder';
    document.getElementById('loader').classList.add('hidden');
  }
}

function renderDriveFolderFiles(files) {
  const el = document.getElementById('drive-files');
  if (files.length === 0) {
    el.innerHTML = '<p style="color:var(--muted)">No CSV files found in this folder.</p>';
    return;
  }

  const args = selectedStrategy.csv_args;
  el.innerHTML = `
    <p style="font-size:12px;color:var(--muted);margin-bottom:8px">Select which file maps to each required argument:</p>
    ${files.map(f => {
      const sel = args.length === 1 ? 'checked' : '';
      return `<div class="drive-file-item">
        <input type="radio" name="drive-file-select" value='${JSON.stringify(f)}' ${sel}>
        <span>${f.name}</span>
        <span style="color:var(--muted);font-size:11px">${f.size ? (f.size/1024).toFixed(0) + 'KB' : ''}</span>
      </div>`;
    }).join('')}
    ${args.length > 1 ? `
      <div class="drive-arg-picker" style="margin-top:8px">
        ${args.map((a, i) => `
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:4px;font-size:12px">
            <code style="background:#21262d;padding:2px 5px;border-radius:3px;color:var(--accent)">${a.arg}</code>
            <select class="drive-arg-select" data-arg="${a.arg}" style="flex:1;padding:4px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px">
              <option value="">-- select file --</option>
              ${files.map(f => `<option value='${JSON.stringify(f)}'>${f.name}</option>`).join('')}
            </select>
          </div>
        `).join('')}
      </div>
    ` : ''}
  `;
}

// Run backtest
async function runBacktest() {
  if (!selectedStrategy) return;
  document.getElementById('results-section').classList.add('hidden');

  if (dataSource === 'drive') {
    await runDriveBacktest();
  } else {
    await runLocalBacktest();
  }
}

async function runLocalBacktest() {
  if (uploadedFiles.length === 0) {
    alert('Please upload at least one CSV file');
    return;
  }

  const btn = document.getElementById('btn-run');
  btn.disabled = true;
  btn.textContent = 'Running...';
  document.getElementById('loader-text').textContent = 'Running backtest...';
  document.getElementById('loader').classList.remove('hidden');

  const formData = new FormData();
  formData.append('strategy_id', selectedStrategy.id);
  for (const f of uploadedFiles) {
    formData.append('files', f);
  }

  try {
    const res = await fetch('/api/backtest', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error + '\n\n' + (data.stderr || ''));
      return;
    }
    renderResults(data);
  } catch (err) {
    alert('Error: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Backtest';
    document.getElementById('loader').classList.add('hidden');
  }
}

async function runDriveBacktest() {
  const args = selectedStrategy.csv_args;
  const driveFiles = {};

  // Try folder selection first
  const selectedFileRadio = document.querySelector('input[name="drive-file-select"]:checked');
  if (selectedFileRadio) {
    const file = JSON.parse(selectedFileRadio.value);
    if (args.length === 1) {
      driveFiles[args[0].arg] = file.id;
    }
  }

  // Try multi-arg dropdowns
  document.querySelectorAll('.drive-arg-select').forEach(sel => {
    if (sel.value) {
      const file = JSON.parse(sel.value);
      driveFiles[sel.dataset.arg] = file.id;
    }
  });

  // Try individual URL inputs
  document.querySelectorAll('.drive-file-url').forEach(inp => {
    const val = inp.value.trim();
    if (val) {
      driveFiles[inp.dataset.arg] = val;
    }
  });

  if (Object.keys(driveFiles).length === 0) {
    alert('Please select or paste Drive file links for all required CSVs');
    return;
  }

  const missing = args.filter(a => !driveFiles[a.arg]);
  if (missing.length > 0) {
    alert(`Missing files for: ${missing.map(a => a.arg).join(', ')}`);
    return;
  }

  const btn = document.getElementById('btn-run');
  btn.disabled = true;
  btn.textContent = 'Running...';
  document.getElementById('loader-text').textContent = 'Downloading from Google Drive...';
  document.getElementById('loader').classList.remove('hidden');

  try {
    const res = await fetch('/api/backtest/drive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ strategy_id: selectedStrategy.id, drive_files: driveFiles })
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error + '\n\n' + (data.stderr || ''));
      return;
    }
    renderResults(data);
  } catch (err) {
    alert('Error: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Backtest';
    document.getElementById('loader').classList.add('hidden');
  }
}

// Render results
function renderResults(data) {
  const { trades, stats, stdout, saved_to } = data;
  lastResults = { trades, stats, strategy_id: selectedStrategy?.id };
  document.getElementById('results-section').classList.remove('hidden');

  const savedEl = document.getElementById('saved-path');
  if (saved_to) {
    savedEl.textContent = 'Saved to: ' + saved_to;
    savedEl.classList.remove('hidden');
  } else {
    savedEl.classList.add('hidden');
    savedEl.textContent = '';
  }

  const msgEl = document.getElementById('results-message');
  if (trades.length === 0 && stdout) {
    msgEl.textContent = 'Strategy output: ' + stdout;
    msgEl.classList.remove('hidden');
  } else if (trades.length === 0) {
    msgEl.textContent = 'No trades generated by this strategy for the given data.';
    msgEl.classList.remove('hidden');
  } else {
    msgEl.classList.add('hidden');
  }

  renderStats(stats, trades);
  renderTrades(trades);
  renderChart(trades);
  initCandleChart(data.chart_session, trades);
}

async function saveResults() {
  if (!lastResults || !selectedStrategy) {
    alert('Run a backtest first');
    return;
  }

  const btn = document.getElementById('btn-save');
  btn.disabled = true;
  btn.textContent = 'Saving...';

  try {
    const res = await fetch('/api/save-results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        strategy_id: selectedStrategy.id,
        trades: lastResults.trades,
        stats: lastResults.stats,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || 'Save failed');
      return;
    }
    const savedEl = document.getElementById('saved-path');
    savedEl.textContent = 'Saved to: ' + data.saved_to;
    savedEl.classList.remove('hidden');
  } catch (err) {
    alert('Error: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save Results to Project';
  }
}

function renderStats(stats, trades) {
  const grid = document.getElementById('stats-grid');
  const cards = [
    { label: 'Total Trades', val: stats.total_trades, cls: '' },
    { label: 'Win Rate', val: stats.win_rate + '%', cls: stats.win_rate >= 50 ? 'positive' : 'negative' },
    { label: 'Total PnL (pips)', val: stats.total_pnl_pips, cls: stats.total_pnl_pips >= 0 ? 'positive' : 'negative' },
    { label: 'Profit Factor', val: stats.profit_factor === Infinity ? '∞' : stats.profit_factor, cls: stats.profit_factor >= 1.5 ? 'positive' : (stats.profit_factor < 1 ? 'negative' : '') },
    { label: 'Avg Win', val: stats.avg_win_pips, cls: 'positive' },
    { label: 'Avg Loss', val: stats.avg_loss_pips, cls: 'negative' },
    { label: 'Wins / Losses', val: `${stats.winning_trades} / ${stats.losing_trades}`, cls: '' },
    { label: 'Max Consec Wins', val: stats.max_consecutive_wins, cls: 'positive' },
    { label: 'Max Consec Losses', val: stats.max_consecutive_losses, cls: 'negative' },
    { label: 'Best Trade', val: stats.best_trade_pips, cls: 'positive' },
    { label: 'Worst Trade', val: stats.worst_trade_pips, cls: 'negative' },
  ];
  grid.innerHTML = cards.map(c =>
    `<div class="stat-card"><span class="stat-val ${c.cls}">${c.val}</span><span class="stat-label">${c.label}</span></div>`
  ).join('');
  document.getElementById('trade-count').textContent = `${trades.length} trades`;
}

function renderTrades(trades) {
  const tbody = document.getElementById('trades-body');
  tbody.innerHTML = trades.map((t, i) => `
    <tr>
      <td>${i + 1}</td>
      <td>${t.entry_time ? t.entry_time.slice(0, 19).replace('T', ' ') : '-'}</td>
      <td>${(t.direction || '').toUpperCase()}</td>
      <td>${t.entry_price || '-'}</td>
      <td>${t.exit_price || '-'}</td>
      <td class="${t.outcome === 'win' ? 'win' : t.outcome === 'loss' ? 'loss' : ''}">${t.pnl_pips || 0}</td>
      <td><span class="${t.outcome}">${(t.outcome || '').toUpperCase()}</span></td>
      <td><button class="btn-sm" onclick="showTradeDetail(${i})">Detail</button></td>
      <td><button class="btn-sm" onclick="jumpToTrade(${i})">Chart</button></td>
    </tr>
  `).join('');
}

function renderChart(trades) {
  const ctx = document.getElementById('resultsChart').getContext('2d');
  if (chartInstance) chartInstance.destroy();

  let cumulative = 0;
  const labels = [];
  const data = [];

  trades.forEach((t, i) => {
    cumulative += t.pnl_pips || 0;
    labels.push(`#${i + 1}`);
    data.push(cumulative);
  });

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Cumulative PnL (pips)',
        data,
        borderColor: '#58a6ff',
        backgroundColor: (ctx) => {
          const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 300);
          g.addColorStop(0, 'rgba(88, 166, 255, .2)');
          g.addColorStop(1, 'rgba(88, 166, 255, 0)');
          return g;
        },
        fill: true,
        tension: .3,
        pointRadius: 3,
        pointBackgroundColor: data.map(v => v >= 0 ? '#3fb950' : '#f85149'),
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', font: { size: 10 } } },
        y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', font: { size: 10 } } },
      }
    }
  });
}

// ---------------------------------------------------------------------------
// Candlestick chart — TradingView Lightweight Charts
// ---------------------------------------------------------------------------

function destroyCandleChart() {
  if (candleResizeObs) {
    candleResizeObs.disconnect();
    candleResizeObs = null;
  }
  if (candleChart) {
    candleChart.remove();
    candleChart = null;
    candleSeries = null;
  }
}

function snapToBarTime(bars, ts) {
  if (!bars.length) return ts;
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

function buildTradeMarkers(trades, bars) {
  if (!bars.length) return [];
  const tMin = bars[0].time;
  const tMax = bars[bars.length - 1].time;
  const markers = [];

  trades.forEach((t, i) => {
    const entryTs = t.entry_time ? Math.floor(new Date(t.entry_time).getTime() / 1000) : null;
    const exitTs = t.exit_time ? Math.floor(new Date(t.exit_time).getTime() / 1000) : null;
    const direction = (t.direction || '').toLowerCase();
    const outcome = t.outcome || '';

    if (entryTs && entryTs >= tMin && entryTs <= tMax) {
      markers.push({
        time: snapToBarTime(bars, entryTs),
        position: direction === 'long' ? 'belowBar' : 'aboveBar',
        color: '#2962FF',
        shape: direction === 'long' ? 'arrowUp' : 'arrowDown',
        text: `E${i + 1}`,
      });
    }
    if (exitTs && exitTs >= tMin && exitTs <= tMax) {
      const color = outcome === 'win' ? '#26a69a' : outcome === 'loss' ? '#ef5350' : '#d29922';
      markers.push({
        time: snapToBarTime(bars, exitTs),
        position: direction === 'long' ? 'aboveBar' : 'belowBar',
        color,
        shape: 'circle',
        text: `X${i + 1}`,
      });
    }
  });

  return markers.sort((a, b) => a.time - b.time);
}

function updateCandleMarkers() {
  if (!candleSeries) return;
  candleSeries.setMarkers(buildTradeMarkers(candleState.trades, candleState.bars));
}

function updateCandleLabel() {
  const el = document.getElementById('candle-window-label');
  if (!el || !candleState.total) return;
  const range = candleChart?.timeScale().getVisibleLogicalRange();
  if (!range) {
    el.textContent = `Loaded ${candleState.bars.length} of ${candleState.total} candles`;
    return;
  }
  const from = Math.max(0, Math.floor(range.from));
  const to = Math.min(candleState.bars.length, Math.ceil(range.to));
  const absFrom = candleState.loadedStart + from + 1;
  const absTo = candleState.loadedStart + to;
  el.textContent = `Viewing candles ${absFrom}–${absTo} of ${candleState.total}`;
}

async function fetchCandleChunk(start, count) {
  const res = await fetch(
    `/api/chart/${candleState.sessionId}?start=${start}&count=${count}`
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Failed to load candles');
  return data;
}

function applyCandleData(preserveRange, prepended = 0) {
  const prevRange = preserveRange ? candleChart.timeScale().getVisibleLogicalRange() : null;
  candleSeries.setData(candleState.bars);
  updateCandleMarkers();
  if (prevRange && prepended > 0) {
    candleChart.timeScale().setVisibleLogicalRange({
      from: prevRange.from + prepended,
      to: prevRange.to + prepended,
    });
  }
  updateCandleLabel();
}

async function loadInitialCandles() {
  const data = await fetchCandleChunk(0, candleState.chunkSize);
  candleState.bars = data.bars;
  candleState.loadedStart = data.start;
  candleState.loadedEnd = data.start + data.bars.length;
  candleState.total = data.total;
  candleSeries.setData(candleState.bars);
  updateCandleMarkers();
  candleChart.timeScale().fitContent();
  updateCandleLabel();
}

async function extendCandlesBackward() {
  if (candleState.loading || candleState.loadedStart <= 0) return;
  candleState.loading = true;
  try {
    const newStart = Math.max(0, candleState.loadedStart - candleState.chunkSize);
    const count = candleState.loadedStart - newStart;
    const data = await fetchCandleChunk(newStart, count);
    if (!data.bars.length) return;
    candleState.bars = [...data.bars, ...candleState.bars];
    candleState.loadedStart = newStart;
    applyCandleData(true, data.bars.length);
  } finally {
    candleState.loading = false;
  }
}

async function extendCandlesForward() {
  if (candleState.loading || candleState.loadedEnd >= candleState.total) return;
  candleState.loading = true;
  try {
    const data = await fetchCandleChunk(candleState.loadedEnd, candleState.chunkSize);
    if (!data.bars.length) return;
    candleState.bars = [...candleState.bars, ...data.bars];
    candleState.loadedEnd += data.bars.length;
    applyCandleData(false);
  } finally {
    candleState.loading = false;
  }
}

async function ensureCandlesAround(absIndex) {
  while (candleState.loadedStart > absIndex && candleState.loadedStart > 0) {
    await extendCandlesBackward();
  }
  while (candleState.loadedEnd < absIndex + 150 && candleState.loadedEnd < candleState.total) {
    await extendCandlesForward();
  }
}

function onVisibleRangeChange(range) {
  if (!range || candleState.loading) return;
  updateCandleLabel();
  if (range.from < 25) extendCandlesBackward();
  if (range.to > candleState.bars.length - 25) extendCandlesForward();
}

async function jumpToTrade(idx) {
  const trades = JSON.parse(sessionStorage.getItem('lastTrades') || '[]');
  const t = trades[idx];
  if (!t?.entry_time || !candleState.sessionId) return;

  const ts = Math.floor(new Date(t.entry_time).getTime() / 1000);
  const res = await fetch(`/api/chart/${candleState.sessionId}/locate?ts=${ts}`);
  const data = await res.json();
  if (!res.ok) return;

  await ensureCandlesAround(data.start);
  const localIdx = candleState.bars.findIndex((b) => b.time >= ts);
  if (localIdx >= 0) {
    candleChart.timeScale().setVisibleLogicalRange({
      from: Math.max(0, localIdx - 25),
      to: localIdx + 125,
    });
  }
  updateCandleLabel();
}

async function jumpToFirstTrade() {
  const trades = JSON.parse(sessionStorage.getItem('lastTrades') || '[]');
  if (trades.length) await jumpToTrade(0);
}

function resetCandleView() {
  if (!candleChart) return;
  candleChart.timeScale().fitContent();
  updateCandleLabel();
}

function initCandleChart(sessionId, trades) {
  destroyCandleChart();
  const section = document.getElementById('candle-chart-section');
  if (!sessionId) {
    section.classList.add('hidden');
    return;
  }

  section.classList.remove('hidden');
  candleState = {
    sessionId,
    bars: [],
    trades: trades || [],
    loadedStart: 0,
    loadedEnd: 0,
    total: 0,
    chunkSize: 200,
    loading: false,
  };

  const container = document.getElementById('candleChart');
  container.innerHTML = '';

  candleChart = LightweightCharts.createChart(container, {
    layout: {
      background: { color: '#131722' },
      textColor: '#d1d4dc',
    },
    grid: {
      vertLines: { color: '#1e222d' },
      horzLines: { color: '#1e222d' },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: '#758696', width: 1, style: LightweightCharts.LineStyle.Dashed },
      horzLine: { color: '#758696', width: 1, style: LightweightCharts.LineStyle.Dashed },
    },
    rightPriceScale: { borderColor: '#2a2e39' },
    timeScale: {
      borderColor: '#2a2e39',
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 8,
      barSpacing: 8,
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      axisPressedMouseMove: true,
      mouseWheel: true,
      pinch: true,
    },
  });

  candleSeries = candleChart.addCandlestickSeries({
    upColor: '#26a69a',
    downColor: '#ef5350',
    borderUpColor: '#26a69a',
    borderDownColor: '#ef5350',
    wickUpColor: '#26a69a',
    wickDownColor: '#ef5350',
  });

  candleChart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange);

  candleResizeObs = new ResizeObserver(() => {
    if (candleChart) {
      candleChart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
    }
  });
  candleResizeObs.observe(container);

  loadInitialCandles();
}

// Trade detail modal
function showTradeDetail(idx) {
  const trades = JSON.parse(sessionStorage.getItem('lastTrades'));
  if (!trades) return;
  const t = trades[idx];

  document.getElementById('modal-trade-id').textContent = `#${idx + 1}`;

  const events = (t.events || []).map(e => `
    <div class="event-item">
      <span class="event-time">${e.timestamp ? e.timestamp.slice(0, 19).replace('T', ' ') : ''}</span>
      <span class="event-type">${e.type || e.direction || ''}</span>
      <div class="event-desc">${e.description || JSON.stringify(e)}</div>
    </div>
  `).join('');

  document.getElementById('modal-body').innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
      <div><strong>Direction</strong><br>${(t.direction || '').toUpperCase()}</div>
      <div><strong>Outcome</strong><br><span class="${t.outcome}">${(t.outcome || '').toUpperCase()}</span></div>
      <div><strong>Entry</strong><br>${t.entry_price || '-'} ${t.entry_time ? 'at ' + t.entry_time.slice(0, 19).replace('T', ' ') : ''}</div>
      <div><strong>Exit</strong><br>${t.exit_price || '-'} ${t.exit_time ? 'at ' + t.exit_time.slice(0, 19).replace('T', ' ') : ''}</div>
      <div><strong>PnL (pips)</strong><br>${t.pnl_pips || 0}</div>
      <div><strong>Stop Loss / TP</strong><br>${t.stop_loss || '-'} / ${t.take_profit || '-'}</div>
    </div>
    ${t.reason ? `<p style="color:var(--muted);font-size:13px;margin-bottom:12px"><strong>Reason:</strong> ${t.reason}</p>` : ''}
    <h4 style="font-size:13px;color:var(--muted);margin-bottom:8px">Execution Steps</h4>
    ${events || '<p style="color:var(--muted)">No step data available</p>'}
  `;
  document.getElementById('modal').classList.remove('hidden');
}

function closeModal() {
  document.getElementById('modal').classList.add('hidden');
}
document.getElementById('modal').addEventListener('click', (e) => {
  if (e.target === document.getElementById('modal')) closeModal();
});

// Store trades in sessionStorage
const origFetch = window.fetch;
window.fetch = async function(...args) {
  const res = await origFetch.apply(this, args);
  if ((args[0] === '/api/backtest' || args[0] === '/api/backtest/drive') && args[1]?.method === 'POST') {
    const cloned = res.clone();
    const data = await cloned.json();
    if (data.trades) sessionStorage.setItem('lastTrades', JSON.stringify(data.trades));
  }
  return res;
};

loadStrategies();
