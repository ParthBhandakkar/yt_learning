let strategies = [];
let selectedStrategy = null;
let uploadedFiles = [];
let chartInstance = null;
let dataSource = 'local';
let driveFolderFiles = [];

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
  const { trades, stats, stdout } = data;
  document.getElementById('results-section').classList.remove('hidden');

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
