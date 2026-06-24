let strategies = [];
let selectedStrategy = null;
let uploadedFiles = [];
let libraryStatus = null;
const DEFAULT_SYMBOL = 'XAUUSD';
let chartInstance = null;
let dataSource = 'local';
let driveFolderFiles = [];
let lastResults = null;
let candleChart = null;
let candleSeries = null;
let candleResizeObs = null;
let candleZonesPrimitive = null;
let candleMarkersApi = null;
let priceAxisWheelCleanup = null;
let candleCrosshairHandler = null;
let candlePointerMoveHandler = null;
let candlePointerLeaveHandler = null;
let candlePanEndHandler = null;
let candleChartWrap = null;
let candleLazyLoadTimer = null;
let candleIsPanning = false;
let candleState = {
  sessionId: null,
  bars: [],
  trades: [],
  loadedStart: 0,
  loadedEnd: 0,
  total: 0,
  chunkSize: 200,
  loading: false,
  activeTradeIdx: null,
  viewportLocked: false,
};

const CANDLE_VIEW_WIDTH = 150;
const RESET_LOAD_COUNT = 500;
const CANDLE_LAZY_LOAD_MARGIN = 50;

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
  libraryStatus = null;
  updateFileList();
  loadLibraryStatus();
  document.getElementById('drive-url').value = '';
  document.getElementById('drive-files').innerHTML = '';
  renderStrategyList(strategies);
}

async function loadLibraryStatus() {
  if (!selectedStrategy) return;
  try {
    const res = await fetch(
      `/api/data/library?strategy_id=${encodeURIComponent(selectedStrategy.id)}&symbol=${DEFAULT_SYMBOL}`
    );
    libraryStatus = await res.json();
    updateFileList();
  } catch (_) {
    libraryStatus = null;
  }
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
  const chips = uploadedFiles.map(f =>
    `<span class="file-chip">${f.name} <span class="remove" onclick="removeFile('${f.name.replace(/'/g, "\\'")}')">&times;</span></span>`
  ).join('');

  if (uploadedFiles.length > 0) {
    el.innerHTML = chips;
    return;
  }

  if (libraryStatus?.ready) {
    const files = libraryStatus.matches
      .filter(m => m.found)
      .map(m => `<span class="file-chip library">${DEFAULT_SYMBOL}/${m.timeframe}: ${m.filename}</span>`)
      .join('');
    el.innerHTML = `
      <div class="library-hint">
        No upload — auto-loading <strong>${DEFAULT_SYMBOL}</strong> from local data library.
      </div>
      <div class="library-files">${files}</div>`;
    return;
  }

  if (libraryStatus && !libraryStatus.ready) {
    const missing = libraryStatus.matches.filter(m => !m.found).map(m => m.timeframe).join(', ');
    el.innerHTML = `<div class="library-hint warn">Library missing timeframes for ${DEFAULT_SYMBOL}: ${missing}</div>`;
    return;
  }

  el.innerHTML = '';
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
let backtestPollTimer = null;

function getLibraryMaxDays() {
  const sel = document.getElementById('library-range');
  if (!sel) return 365;
  return parseInt(sel.value, 10);
}

function formatLoaderElapsed(ms) {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatLoaderPhase(phase) {
  const labels = {
    queued: 'Queued',
    preparing: 'Preparing data',
    load_csv: 'Loading candles',
    running: 'Running strategy',
    strategy: 'Scanning signals',
    finalizing: 'Finalizing results',
    done: 'Complete',
    failed: 'Failed',
  };
  return labels[phase] || (phase || 'Working');
}

function showBacktestLoader(title, subtitle) {
  document.getElementById('loader-title').textContent = title || 'Running backtest';
  document.getElementById('loader-text').textContent = subtitle || 'Starting...';
  document.getElementById('loader-phase').textContent = 'Queued';
  document.getElementById('loader-elapsed').textContent = '0:00';
  document.getElementById('loader-progress').style.width = '0%';
  document.getElementById('loader-log').textContent = '';
  document.getElementById('loader').classList.remove('hidden');
}

function updateBacktestLoader(job, elapsedMs) {
  const progress = Math.max(0, Math.min(100, job.progress || 0));
  document.getElementById('loader-progress').style.width = `${progress}%`;
  document.getElementById('loader-text').textContent = job.message || 'Running...';
  document.getElementById('loader-phase').textContent = formatLoaderPhase(job.phase);
  document.getElementById('loader-elapsed').textContent = formatLoaderElapsed(elapsedMs);
  const logEl = document.getElementById('loader-log');
  if (job.log_tail?.length) {
    logEl.textContent = job.log_tail.slice(-8).join('\n');
    logEl.scrollTop = logEl.scrollHeight;
  }
}

function hideBacktestLoader() {
  if (backtestPollTimer) {
    clearTimeout(backtestPollTimer);
    backtestPollTimer = null;
  }
  document.getElementById('loader').classList.add('hidden');
}

function pollBacktestJob(jobId) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const poll = async () => {
      try {
        const res = await fetch(`/api/backtest/jobs/${jobId}`);
        const job = await res.json();
        if (!res.ok) {
          reject(new Error(job.error || 'Job not found'));
          return;
        }
        updateBacktestLoader(job, Date.now() - started);
        if (job.status === 'completed') {
          resolve(job.result);
          return;
        }
        if (job.status === 'failed') {
          reject(new Error(job.error || job.message || 'Backtest failed'));
          return;
        }
        backtestPollTimer = setTimeout(poll, 500);
      } catch (err) {
        reject(err);
      }
    };
    poll();
  });
}

async function startBacktestAndWait(startRequest) {
  const res = await startRequest();
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error + (data.stderr ? `\n\n${data.stderr}` : ''));
  }
  if (data.job_id) {
    return pollBacktestJob(data.job_id);
  }
  return data;
}

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
  const useLibrary = uploadedFiles.length === 0;

  if (useLibrary) {
    if (!libraryStatus) {
      await loadLibraryStatus();
    }
    if (libraryStatus && !libraryStatus.ready) {
      alert(
        `Local ${DEFAULT_SYMBOL} data is missing required timeframes for this strategy. ` +
        'Upload CSVs or add files under the data library folder.'
      );
      return;
    }
  }

  const btn = document.getElementById('btn-run');
  btn.disabled = true;
  btn.textContent = 'Running...';
  showBacktestLoader(
    'Running backtest',
    useLibrary
      ? `Library ${DEFAULT_SYMBOL} · ${document.getElementById('library-range')?.selectedOptions[0]?.text || 'default window'}`
      : `Processing ${uploadedFiles.length} uploaded file(s)...`,
  );

  try {
    const data = await startBacktestAndWait(async () => {
      if (useLibrary) {
        return fetch('/api/backtest/library', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            strategy_id: selectedStrategy.id,
            symbol: DEFAULT_SYMBOL,
            max_days: getLibraryMaxDays(),
          }),
        });
      }
      const formData = new FormData();
      formData.append('strategy_id', selectedStrategy.id);
      formData.append('symbol', DEFAULT_SYMBOL);
      formData.append('max_days', String(getLibraryMaxDays()));
      for (const f of uploadedFiles) {
        formData.append('files', f);
      }
      return fetch('/api/backtest', { method: 'POST', body: formData });
    });
    renderResults(data);
  } catch (err) {
    alert('Error: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Backtest';
    hideBacktestLoader();
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
  showBacktestLoader('Running backtest', 'Downloading from Google Drive...');

  try {
    const data = await startBacktestAndWait(() => fetch('/api/backtest/drive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ strategy_id: selectedStrategy.id, drive_files: driveFiles }),
    }));
    renderResults(data);
  } catch (err) {
    alert('Error: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Backtest';
    hideBacktestLoader();
  }
}

// Render results
function renderResults(data) {
  const { trades, stats, stdout, saved_to, data_source, library_files, symbol, data_window } = data;
  lastResults = { trades, stats, strategy_id: selectedStrategy?.id };
  if (trades) sessionStorage.setItem('lastTrades', JSON.stringify(trades));
  document.getElementById('results-section').classList.remove('hidden');

  const savedEl = document.getElementById('saved-path');
  const windowNote = data_window?.length ? `Window: ${data_window.join(' · ')}` : '';
  const sourceNote = data_source === 'library'
    ? `Data: ${symbol || DEFAULT_SYMBOL} library` + (library_files?.length ? ` (${library_files.join(', ')})` : '')
    : '';
  const combinedNote = [sourceNote, windowNote].filter(Boolean).join(' | ');
  if (saved_to) {
    savedEl.textContent = ('Saved to: ' + saved_to + (combinedNote ? ' | ' + combinedNote : ''));
    savedEl.classList.remove('hidden');
  } else if (combinedNote) {
    savedEl.textContent = combinedNote;
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
    { label: 'Profit Factor', val: (stats.profit_factor == null || stats.profit_factor === Infinity) ? '∞' : stats.profit_factor, cls: (stats.profit_factor == null || stats.profit_factor >= 1.5) ? 'positive' : (stats.profit_factor != null && stats.profit_factor < 1 ? 'negative' : '') },
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

function attachPriceAxisWheelZoom(chart, container) {
  const onWheel = (event) => {
    if (event.deltaY === 0) return;
    const priceScaleWidth = chart.priceScale('right').width();
    if (priceScaleWidth <= 0) return;
    const rect = container.getBoundingClientRect();
    if (event.clientX < rect.right - priceScaleWidth) return;

    event.preventDefault();
    event.stopPropagation();

    const priceScale = chart.priceScale('right');
    priceScale.setAutoScale(false);
    const range = priceScale.getVisibleRange();
    if (range == null) return;

    const zoomFactor = event.deltaY > 0 ? 1.08 : 1 / 1.08;
    const center = (range.from + range.to) / 2;
    const halfSpan = ((range.to - range.from) / 2) * zoomFactor;
    priceScale.setVisibleRange({ from: center - halfSpan, to: center + halfSpan });
  };

  container.addEventListener('wheel', onWheel, { passive: false, capture: true });
  return () => container.removeEventListener('wheel', onWheel, { capture: true });
}

function formatChartTime(ts) {
  const date = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(date.getTime())) return String(ts);
  const y = date.getUTCFullYear();
  const mo = String(date.getUTCMonth() + 1).padStart(2, '0');
  const d = String(date.getUTCDate()).padStart(2, '0');
  const h = String(date.getUTCHours()).padStart(2, '0');
  const m = String(date.getUTCMinutes()).padStart(2, '0');
  return `${y}-${mo}-${d} ${h}:${m}`;
}

function updateOhlcLegend(bar, isHover) {
  const legend = document.getElementById('chart-ohlc-legend');
  const timeEl = document.getElementById('chart-ohlc-time');
  const valuesEl = document.getElementById('chart-ohlc-values');
  if (!legend || !timeEl || !valuesEl || !bar) {
    legend?.classList.add('hidden');
    return;
  }
  legend.classList.remove('hidden');
  legend.classList.toggle('chart-ohlc-legend--hover', isHover);
  timeEl.textContent = formatChartTime(bar.time);
  valuesEl.innerHTML = `O <strong>${formatChartPrice(bar.open)}</strong>  H <strong>${formatChartPrice(bar.high)}</strong>  L <strong>${formatChartPrice(bar.low)}</strong>  C <strong>${formatChartPrice(bar.close)}</strong>`;
}

function findBarByTime(bars, time) {
  return bars.find((b) => b.time === time);
}

function buildTradeZoneData(trade, tradeIdx) {
  const entryPrice = parseFloat(trade.entry_price);
  const stopLoss = parseFloat(trade.stop_loss);
  const takeProfit = parseFloat(trade.take_profit);
  if (!trade.entry_time || Number.isNaN(entryPrice) || Number.isNaN(stopLoss) || Number.isNaN(takeProfit)) {
    return null;
  }

  const entryTime = Math.floor(new Date(trade.entry_time).getTime() / 1000);
  const exitTime = trade.exit_time
    ? Math.floor(new Date(trade.exit_time).getTime() / 1000)
    : entryTime + 3600;
  const risk = Math.abs(entryPrice - stopLoss);
  const reward = Math.abs(takeProfit - entryPrice);
  const dir = normalizeTradeDirection(trade.direction).toUpperCase();

  return {
    tradeId: tradeIdx + 1,
    direction: dir === 'LONG' ? 'LONG' : 'SHORT',
    entryTime,
    exitTime,
    entryPrice,
    exitPrice: trade.exit_price != null ? parseFloat(trade.exit_price) : null,
    stopLoss,
    takeProfit,
    netPnl: computeTradePnlPips(trade),
    setupRr: risk > 0 ? (reward / risk).toFixed(2) : '—',
  };
}

function buildTradeZoneDataset() {
  if (!candleState.bars.length) return [];
  const tMin = candleState.bars[0].time;
  const tMax = candleState.bars[candleState.bars.length - 1].time;
  const zones = [];

  candleState.trades.forEach((trade, idx) => {
    if (candleState.activeTradeIdx != null && idx !== candleState.activeTradeIdx) return;
    const zone = buildTradeZoneData(trade, idx);
    if (!zone) return;
    if (zone.entryTime > tMax || zone.exitTime < tMin) return;
    zones.push(zone);
  });

  return zones;
}

function updateTradeZonesPrimitive() {
  if (!candleSeries || typeof TradeZonesPrimitive === 'undefined') return;
  const zones = buildTradeZoneDataset();
  if (zones.length === 0) {
    if (candleZonesPrimitive) {
      candleSeries.detachPrimitive(candleZonesPrimitive);
      candleZonesPrimitive = null;
    }
    return;
  }
  if (!candleZonesPrimitive) {
    candleZonesPrimitive = new TradeZonesPrimitive(zones);
    candleSeries.attachPrimitive(candleZonesPrimitive);
  } else {
    candleZonesPrimitive.setTrades(zones);
  }
}

function destroyCandleChart() {
  if (candleLazyLoadTimer) {
    clearTimeout(candleLazyLoadTimer);
    candleLazyLoadTimer = null;
  }
  if (priceAxisWheelCleanup) {
    priceAxisWheelCleanup();
    priceAxisWheelCleanup = null;
  }
  if (candleResizeObs) {
    candleResizeObs.disconnect();
    candleResizeObs = null;
  }
  if (candleChart && candleCrosshairHandler) {
    candleChart.unsubscribeCrosshairMove(candleCrosshairHandler);
  }
  if (candleChartWrap) {
    if (candlePointerMoveHandler) {
      candleChartWrap.removeEventListener('pointermove', candlePointerMoveHandler);
    }
    if (candlePointerLeaveHandler) {
      candleChartWrap.removeEventListener('pointerleave', candlePointerLeaveHandler);
    }
    if (candlePanEndHandler) {
      window.removeEventListener('mouseup', candlePanEndHandler);
      window.removeEventListener('touchend', candlePanEndHandler);
    }
  }
  candleChartWrap = null;
  candlePointerMoveHandler = null;
  candlePointerLeaveHandler = null;
  candlePanEndHandler = null;
  candleIsPanning = false;
  candleZonesPrimitive = null;
  candleMarkersApi = null;
  candleCrosshairHandler = null;
  if (candleChart) {
    candleChart.remove();
    candleChart = null;
    candleSeries = null;
  }
  updateOhlcLegend(null, false);
}

function normalizeTradeDirection(direction) {
  const d = (direction || '').toLowerCase();
  if (d === 'bullish' || d === 'long') return 'long';
  if (d === 'bearish' || d === 'short') return 'short';
  return d;
}

function computeTradePnlPips(t) {
  const stored = t.pnl_pips;
  if (stored != null && stored !== 0) return Number(stored);
  const entry = parseFloat(t.entry_price);
  const exit = parseFloat(t.exit_price);
  if (!entry || Number.isNaN(exit)) return 0;
  const dir = normalizeTradeDirection(t.direction);
  const pipSize = entry >= 1000 ? 1.0 : entry >= 100 ? 0.1 : entry >= 10 ? 0.01 : 0.0001;
  const raw = dir === 'long' ? exit - entry : dir === 'short' ? entry - exit : 0;
  return Math.round((raw / pipSize) * 10) / 10;
}

function formatChartPrice(p) {
  if (p >= 1000) return p.toFixed(2);
  if (p >= 10) return p.toFixed(3);
  return p.toFixed(5);
}

function formatTradeSummary(tradeIdx) {
  const t = candleState.trades[tradeIdx];
  if (!t) return '';
  const pnl = computeTradePnlPips(t);
  const sign = pnl > 0 ? '+' : '';
  const outcome = (t.outcome || '').toUpperCase();
  const outcomePart = outcome ? ` · ${outcome}` : '';
  return `Trade #${tradeIdx + 1}: ${sign}${pnl} pips${outcomePart}`;
}

function applyViewportFromStart() {
  if (!candleChart || !candleSeries || !candleState.bars.length) return;

  candleChart.timeScale().resetTimeScale();
  candleChart.priceScale('right').applyOptions({ autoScale: true });

  const visible = Math.min(candleState.bars.length, CANDLE_VIEW_WIDTH);
  const logicalTo = Math.max(visible, 20);
  candleChart.timeScale().setVisibleLogicalRange({ from: 0, to: logicalTo });

  const range = candleChart.timeScale().getVisibleLogicalRange();
  const info = range ? candleSeries.barsInLogicalRange(range) : null;
  if (!info || info.barsInside < 1) {
    candleChart.timeScale().setVisibleLogicalRange({ from: 0, to: logicalTo });
  }
}

function showCandlesFromStart() {
  applyViewportFromStart();
}

function updateCandleOverlays() {
  updateCandleMarkers();
  updateTradeZonesPrimitive();
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
    if (candleState.activeTradeIdx != null && i !== candleState.activeTradeIdx) return;
    const entryTs = t.entry_time ? Math.floor(new Date(t.entry_time).getTime() / 1000) : null;
    const exitTs = t.exit_time ? Math.floor(new Date(t.exit_time).getTime() / 1000) : null;
    const direction = normalizeTradeDirection(t.direction);

    if (entryTs && entryTs >= tMin && entryTs <= tMax) {
      const entryPrice = parseFloat(t.entry_price);
      if (!Number.isFinite(entryPrice)) return;
      markers.push({
        time: snapToBarTime(bars, entryTs),
        position: 'atPriceMiddle',
        price: entryPrice,
        color: direction === 'long' ? '#15803d' : '#b91c1c',
        shape: direction === 'long' ? 'arrowUp' : 'arrowDown',
        text: `#${i + 1} ${direction === 'long' ? 'L' : 'S'} @ ${formatChartPrice(entryPrice)}`,
      });
    }
    if (exitTs && exitTs >= tMin && exitTs <= tMax && t.exit_price != null) {
      const exitPrice = parseFloat(t.exit_price);
      if (!Number.isFinite(exitPrice)) return;
      const pnl = computeTradePnlPips(t);
      markers.push({
        time: snapToBarTime(bars, exitTs),
        position: 'atPriceMiddle',
        price: exitPrice,
        color: pnl >= 0 ? '#15803d' : '#b91c1c',
        shape: 'circle',
        text: `#${i + 1} x ${formatChartPrice(exitPrice)}`,
      });
    }
  });

  return markers.sort((a, b) => a.time - b.time);
}

function updateCandleMarkers() {
  if (!candleSeries || !LightweightCharts.createSeriesMarkers) return;
  const markers = buildTradeMarkers(candleState.trades, candleState.bars);
  if (!candleMarkersApi) {
    candleMarkersApi = LightweightCharts.createSeriesMarkers(candleSeries, markers);
  } else {
    candleMarkersApi.setMarkers(markers);
  }
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
  let label = `Viewing candles ${absFrom}–${absTo} of ${candleState.total}`;
  if (candleState.activeTradeIdx != null) {
    label += ` · ${formatTradeSummary(candleState.activeTradeIdx)}`;
  }
  el.textContent = label;
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
  updateCandleOverlays();
  if (prevRange) {
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
  updateCandleOverlays();
  showCandlesFromStart();
  updateCandleLabel();
  updateTradeNavButtons();
  if (candleState.bars.length) {
    updateOhlcLegend(candleState.bars[candleState.bars.length - 1], false);
  }
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
    applyCandleData(true);
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

function scheduleLazyCandleLoad(range) {
  if (candleLazyLoadTimer) clearTimeout(candleLazyLoadTimer);
  candleLazyLoadTimer = setTimeout(() => {
    candleLazyLoadTimer = null;
    if (!range || candleState.loading || candleState.viewportLocked || candleIsPanning) return;
    if (range.from < CANDLE_LAZY_LOAD_MARGIN) extendCandlesBackward();
    if (range.to > candleState.bars.length - CANDLE_LAZY_LOAD_MARGIN) extendCandlesForward();
  }, 120);
}

function onVisibleRangeChange(range) {
  if (!range || candleState.loading || candleState.viewportLocked) return;
  updateCandleLabel();
  updateTradeZonesPrimitive();
  if (candleIsPanning) return;
  scheduleLazyCandleLoad(range);
}

function updateZoneLabelHover(clientX, clientY) {
  if (!candleZonesPrimitive || !candleChartWrap) return;
  const rect = candleChartWrap.getBoundingClientRect();
  const hit = candleZonesPrimitive.hitTestLabel(clientX - rect.left, clientY - rect.top);
  candleZonesPrimitive.setHoveredLabel(hit);
}

function clearZoneLabelHover() {
  candleZonesPrimitive?.setHoveredLabel(null);
}

function getChartTrades() {
  return candleState.trades.length
    ? candleState.trades
    : JSON.parse(sessionStorage.getItem('lastTrades') || '[]');
}

function updateTradeNavButtons() {
  const prevBtn = document.getElementById('btn-prev-trade');
  const nextBtn = document.getElementById('btn-next-trade');
  if (!prevBtn || !nextBtn) return;

  const trades = getChartTrades();
  const count = trades.length;
  const idx = candleState.activeTradeIdx;

  if (count === 0) {
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    return;
  }

  if (idx == null) {
    prevBtn.disabled = false;
    nextBtn.disabled = false;
    return;
  }

  prevBtn.disabled = idx <= 0;
  nextBtn.disabled = idx >= count - 1;
}

async function jumpToTrade(idx) {
  const trades = getChartTrades();
  const t = trades[idx];
  if (!t?.entry_time || !candleState.sessionId) return;

  candleState.activeTradeIdx = idx;
  candleState.viewportLocked = true;

  try {
    const ts = Math.floor(new Date(t.entry_time).getTime() / 1000);
    const res = await fetch(`/api/chart/${candleState.sessionId}/locate?ts=${ts}`);
    const data = await res.json();
    if (!res.ok) return;

    await ensureCandlesAround(data.start);
    const localIdx = candleState.bars.findIndex((b) => b.time >= ts);
    if (localIdx >= 0) {
      candleChart.timeScale().resetTimeScale();
      candleChart.priceScale('right').applyOptions({ autoScale: true });
      candleChart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, localIdx - 25),
        to: Math.min(candleState.bars.length, localIdx + 125),
      });
    }
    updateCandleOverlays();
    updateCandleLabel();
    updateTradeNavButtons();
  } finally {
    candleState.viewportLocked = false;
  }
}

async function jumpToFirstTrade() {
  const trades = getChartTrades();
  if (trades.length) await jumpToTrade(0);
}

async function jumpToPreviousTrade() {
  const trades = getChartTrades();
  if (!trades.length) return;

  let idx = candleState.activeTradeIdx;
  if (idx == null) {
    await jumpToTrade(trades.length - 1);
    return;
  }
  if (idx <= 0) return;
  await jumpToTrade(idx - 1);
}

async function jumpToNextTrade() {
  const trades = getChartTrades();
  if (!trades.length) return;

  let idx = candleState.activeTradeIdx;
  if (idx == null) {
    await jumpToTrade(0);
    return;
  }
  if (idx >= trades.length - 1) return;
  await jumpToTrade(idx + 1);
}

async function resetCandleView() {
  if (!candleChart || !candleState.sessionId) return;
  candleState.loading = true;
  candleState.viewportLocked = true;
  try {
    const data = await fetchCandleChunk(0, RESET_LOAD_COUNT);
    if (!data.bars.length) {
      throw new Error('No candle data available at the start of the series');
    }

    candleState.bars = data.bars;
    candleState.loadedStart = data.start;
    candleState.loadedEnd = data.start + data.bars.length;
    candleState.total = data.total;
    candleState.activeTradeIdx = null;

    candleSeries.setData(candleState.bars);
    applyViewportFromStart();
    updateCandleOverlays();
    updateCandleLabel();
    updateTradeNavButtons();
  } catch (err) {
    console.error('resetCandleView failed:', err);
    alert('Could not reset chart view: ' + err.message);
  } finally {
    candleState.loading = false;
    candleState.viewportLocked = false;
  }
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
    activeTradeIdx: null,
    viewportLocked: false,
  };

  const container = document.getElementById('candleChart');
  const outer = document.getElementById('candle-chart-outer');
  container.innerHTML = '';

  const colorType = LightweightCharts.ColorType?.Solid ?? 0;
  candleChart = LightweightCharts.createChart(container, {
    layout: {
      background: { type: colorType, color: '#fffef5' },
      textColor: '#0a0a0a',
      fontFamily: '"Space Mono", "Courier New", monospace',
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: '#e5e5e5' },
      horzLines: { color: '#e5e5e5' },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {
        color: '#0a0a0a',
        width: 1,
        style: LightweightCharts.LineStyle.Dashed,
        labelBackgroundColor: '#fffef5',
      },
      horzLine: {
        color: '#0a0a0a',
        width: 1,
        style: LightweightCharts.LineStyle.Dashed,
        labelBackgroundColor: '#fffef5',
      },
    },
    rightPriceScale: {
      borderColor: '#0a0a0a',
      borderVisible: true,
      autoScale: true,
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
    timeScale: {
      borderColor: '#0a0a0a',
      borderVisible: true,
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 8,
      barSpacing: 10,
      minBarSpacing: 4,
      shiftVisibleRangeOnNewBar: false,
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      axisPressedMouseMove: { time: true, price: true },
      mouseWheel: true,
      pinch: true,
    },
    kineticScroll: {
      mouse: false,
      touch: false,
    },
  });

  const seriesType = LightweightCharts.CandlestickSeries || 'Candlestick';
  candleSeries = typeof seriesType === 'string'
    ? candleChart.addCandlestickSeries({
        upColor: '#22c55e',
        downColor: '#ef4444',
        borderUpColor: '#0a0a0a',
        borderDownColor: '#0a0a0a',
        wickUpColor: '#0a0a0a',
        wickDownColor: '#0a0a0a',
      })
    : candleChart.addSeries(seriesType, {
        upColor: '#22c55e',
        downColor: '#ef4444',
        borderUpColor: '#0a0a0a',
        borderDownColor: '#0a0a0a',
        wickUpColor: '#0a0a0a',
        wickDownColor: '#0a0a0a',
      });

  candleChart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange);

  const defaultBar = () => (candleState.bars.length ? candleState.bars[candleState.bars.length - 1] : null);
  candleCrosshairHandler = (param) => {
    if (!param.point) {
      clearZoneLabelHover();
      updateOhlcLegend(defaultBar(), false);
      return;
    }

    if (candleZonesPrimitive) {
      const hit = candleZonesPrimitive.hitTestLabel(param.point.x, param.point.y);
      candleZonesPrimitive.setHoveredLabel(hit);
    }

    if (param.time != null && param.seriesData && param.seriesData.size > 0) {
      const seriesData = param.seriesData.get(candleSeries);
      if (seriesData && seriesData.open != null) {
        updateOhlcLegend({ time: param.time, ...seriesData }, true);
        return;
      }
    }

    const bar = param.time ? findBarByTime(candleState.bars, param.time) : null;
    updateOhlcLegend(bar || defaultBar(), Boolean(bar));
  };
  candleChart.subscribeCrosshairMove(candleCrosshairHandler);

  const wrap = outer || container.parentElement;
  candleChartWrap = wrap || container;
  candlePointerMoveHandler = (event) => {
    updateZoneLabelHover(event.clientX, event.clientY);
  };
  candlePointerLeaveHandler = () => {
    clearZoneLabelHover();
  };
  candlePanEndHandler = () => {
    if (!candleIsPanning) return;
    candleIsPanning = false;
    const range = candleChart?.timeScale().getVisibleLogicalRange();
    if (range) scheduleLazyCandleLoad(range);
  };
  candleChartWrap.addEventListener('pointermove', candlePointerMoveHandler);
  candleChartWrap.addEventListener('pointerleave', candlePointerLeaveHandler);
  candleChartWrap.addEventListener('mousedown', () => { candleIsPanning = true; });
  candleChartWrap.addEventListener('touchstart', () => { candleIsPanning = true; }, { passive: true });
  window.addEventListener('mouseup', candlePanEndHandler);
  window.addEventListener('touchend', candlePanEndHandler);

  priceAxisWheelCleanup = attachPriceAxisWheelZoom(candleChart, candleChartWrap);
  candleResizeObs = new ResizeObserver(() => {
    if (candleChart) {
      candleChart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
    }
  });
  candleResizeObs.observe(wrap || container);

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
  if (
    (args[0] === '/api/backtest' || args[0] === '/api/backtest/library' || args[0] === '/api/backtest/drive')
    && args[1]?.method === 'POST'
  ) {
    const cloned = res.clone();
    const data = await cloned.json();
    if (data.trades) sessionStorage.setItem('lastTrades', JSON.stringify(data.trades));
  }
  return res;
};

loadStrategies();
