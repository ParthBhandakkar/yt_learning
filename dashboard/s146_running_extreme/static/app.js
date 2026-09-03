"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { run: "", summary: null, page: 1, pages: 1, total: 0, signalPage: 1, signalPages: 1, signalTotal: 0, eventPage: 1, eventPages: 1, eventTotal: 0, timer: null };
const aliases = {
  time: ["entry_time_utc", "entry_time", "opened_at", "open_time", "time_utc", "timestamp"],
  side: ["side", "direction", "trade_type"], entry: ["entry_price", "entry", "open_price"],
  stop: ["stop_price", "stop", "stop_loss", "sl"], exitPrice: ["exit_price", "exit", "close_price"],
};
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const first = (object, keys, fallback = null) => {
  const lookup = Object.fromEntries(Object.entries(object || {}).map(([key, value]) => [key.toLowerCase(), value]));
  for (const key of keys) if (lookup[key.toLowerCase()] !== undefined && lookup[key.toLowerCase()] !== null && lookup[key.toLowerCase()] !== "") return lookup[key.toLowerCase()];
  return fallback;
};
const finite = (value) => value !== null && value !== "" && Number.isFinite(Number(value));
const fmt = (value, digits = 2) => finite(value) ? Number(value).toLocaleString(undefined, {minimumFractionDigits: digits, maximumFractionDigits: digits}) : "—";
const pct = (value) => finite(value) ? `${fmt(value, 1)}%` : "—";
const timeText = (value) => {
  if (value === null || value === undefined || value === "") return "—";
  const date = typeof value === "number" ? new Date(value * (value > 1e11 ? 1 : 1000)) : new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toISOString().replace("T", " ").replace(".000Z", "Z");
};
async function api(path) {
  const response = await fetch(path, {headers: {Accept: "application/json"}});
  const payload = await response.json().catch(() => ({error: response.statusText}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}
function showNotice(message, kind = "error") {
  const node = $("#notice"); node.hidden = !message; node.className = `notice ${kind}`; node.textContent = message || "";
}
function optionList(values, label) {
  return `<option value="">All ${esc(label)}</option>` + (values || []).map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join("");
}

async function boot() {
  bindControls();
  try {
    const payload = await api("/api/runs");
    const runs = payload.runs || [];
    $("#run").innerHTML = runs.map((run) => `<option value="${esc(run.run_id)}">${esc(run.label || run.run_id)}</option>`).join("");
    if (!runs.length) {
      $("#run").innerHTML = "<option>No runs found</option>"; $("#run").disabled = true;
      $("#run-meta").textContent = `No run folders found under ${payload.data_root || "the configured data directory"}.`;
      showNotice("The dashboard is ready. Generate a run under data/s146_running_extreme/runs to populate it.", "info");
      return;
    }
    state.run = runs[0].run_id; await loadRun();
  } catch (error) { showNotice(error.message); }
}
function bindControls() {
  $("#run").addEventListener("change", async (event) => { state.run = event.target.value; state.page = 1; await loadRun(); });
  for (const id of ["symbol", "model", "exit", "outcome", "page-size"]) {
    $(`#${id}`).addEventListener("change", () => { state.page = 1; loadTrades(); });
  }
  for (const id of ["signal-symbol", "signal-status", "signal-page-size"]) {
    $(`#${id}`).addEventListener("change", () => { state.signalPage = 1; loadSignals(); });
  }
  for (const id of ["event-symbol", "event-type", "event-page-size"]) {
    $(`#${id}`).addEventListener("change", () => { state.eventPage = 1; loadEvents(); });
  }
  $("#signal-search").addEventListener("input", () => { clearTimeout(state.timer); state.timer = setTimeout(() => { state.signalPage = 1; loadSignals(); }, 250); });
  $("#event-search").addEventListener("input", () => { clearTimeout(state.timer); state.timer = setTimeout(() => { state.eventPage = 1; loadEvents(); }, 250); });
  $("#search").addEventListener("input", () => { clearTimeout(state.timer); state.timer = setTimeout(() => { state.page = 1; loadTrades(); }, 250); });
  $("#prev").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; loadTrades(); } });
  $("#next").addEventListener("click", () => { if (state.page < state.pages) { state.page += 1; loadTrades(); } });
  $("#signal-prev").addEventListener("click", () => { if (state.signalPage > 1) { state.signalPage -= 1; loadSignals(); } });
  $("#signal-next").addEventListener("click", () => { if (state.signalPage < state.signalPages) { state.signalPage += 1; loadSignals(); } });
  $("#event-prev").addEventListener("click", () => { if (state.eventPage > 1) { state.eventPage -= 1; loadEvents(); } });
  $("#event-next").addEventListener("click", () => { if (state.eventPage < state.eventPages) { state.eventPage += 1; loadEvents(); } });
  $("#close-detail").addEventListener("click", () => $("#detail").close());
  $("#detail").addEventListener("click", (event) => { if (event.target === $("#detail")) $("#detail").close(); });
}
async function loadRun() {
  showNotice("");
  try {
    state.summary = await api(`/api/runs/${encodeURIComponent(state.run)}/summary`);
    renderMetadata(); renderKpis(); renderEquity(); renderBreakdowns(); populateFilters();
    for (const id of ["signal-symbol", "signal-status", "event-symbol", "event-type"]) $(`#${id}`).innerHTML = '<option value="">All</option>';
    $("#signal-search").value = ""; $("#event-search").value = "";
    state.signalPage = 1; state.eventPage = 1;
    await Promise.all([loadTrades(), loadSignals(), loadEvents()]);
  } catch (error) { showNotice(error.message); }
}
function renderMetadata() {
  const metadata = state.summary.metadata || {};
  $("#run-title").textContent = first(metadata, ["label", "name", "description"], state.run);
  const start = first(metadata, ["score_start_utc", "start_utc", "start", "from"]);
  const end = first(metadata, ["score_end_utc", "end_utc", "end", "to"]);
  const symbols = first(metadata, ["symbols", "instruments"]);
  const model = first(metadata, ["execution_model", "engine", "mode"]);
  const parts = [];
  if (start || end) parts.push(`${timeText(start)} → ${timeText(end)}`);
  if (Array.isArray(symbols)) parts.push(`${symbols.length} symbols`);
  if (model) parts.push(String(model));
  $("#run-meta").textContent = parts.join(" · ") || `Run ID: ${state.run}`;
  const ignored = new Set(["label", "name", "description", "symbols", "instruments", "score_start_utc", "score_end_utc"]);
  $("#meta-tags").innerHTML = Object.entries(metadata).filter(([key, value]) => !ignored.has(key) && ["string", "number", "boolean"].includes(typeof value)).slice(0, 6)
    .map(([key, value]) => `<span><b>${esc(key.replaceAll("_", " "))}</b>${esc(value)}</span>`).join("");
}

function renderKpis() {
  const summary = state.summary.summary || {};
  const cards = [
    ["Trades", first(summary, ["total_trades", "trades"], 0), "Completed & open"],
    ["Win rate", pct(first(summary, ["win_rate_pct", "win_rate"])), `${first(summary, ["wins"], 0)} wins / ${first(summary, ["losses"], 0)} losses`],
    ["Net R", fmt(first(summary, ["net_r", "total_r"])), "Risk-adjusted return"],
    ["Profit factor", fmt(first(summary, ["profit_factor", "pf"])), "Gross wins ÷ losses"],
    ["Max drawdown", `${fmt(first(summary, ["max_drawdown_r", "drawdown_r"]))} R`, "Peak-to-trough"],
  ];
  $("#kpis").innerHTML = cards.map(([label, value, hint], index) => `<article class="kpi kpi-${index}"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(hint)}</small></article>`).join("");
}
function renderEquity() {
  const curve = state.summary.summary?.equity_curve || [];
  if (!curve.length) { $("#equity").innerHTML = '<p class="empty">No trade returns available for an equity curve.</p>'; return; }
  const values = curve.map((point) => Number(first(point, ["equity_r", "equity", "value"], 0)));
  const min = Math.min(0, ...values), max = Math.max(0, ...values), spread = max - min || 1;
  const width = 1000, height = 250, pad = 28;
  const x = (index) => pad + index * (width - 2 * pad) / Math.max(1, values.length - 1);
  const y = (value) => height - pad - (value - min) * (height - 2 * pad) / spread;
  const line = values.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(value).toFixed(1)}`).join(" ");
  const area = `${line} L${x(values.length - 1)},${height - pad} L${x(0)},${height - pad} Z`;
  const zero = y(0);
  $("#equity").innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Cumulative R equity curve"><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#57e3c2" stop-opacity=".3"/><stop offset="1" stop-color="#57e3c2" stop-opacity="0"/></linearGradient></defs><line class="zero" x1="${pad}" y1="${zero}" x2="${width-pad}" y2="${zero}"/><path class="area" d="${area}"/><path class="equity-line" d="${line}"/></svg><div class="chart-range"><span>${fmt(min)} R</span><b>${fmt(values.at(-1))} R final</b><span>${fmt(max)} R</span></div>`;
}
function breakdownTable(items) {
  if (!items?.length) return '<p class="empty">No data</p>';
  const maxTrades = Math.max(...items.map((item) => Number(item.trades) || 0), 1);
  return items.map((item) => `<div class="break-row"><div><b title="${esc(item.name)}">${esc(item.name)}</b><span class="${Number(item.net_r) >= 0 ? "positive" : "negative"}">${Number(item.net_r) >= 0 ? "+" : ""}${fmt(item.net_r)} R</span></div><div class="bar"><i style="width:${Math.max(3, Number(item.trades) * 100 / maxTrades)}%"></i></div><small>${esc(item.trades)} trades · ${pct(item.win_rate_pct)} win</small></div>`).join("");
}
function renderBreakdowns() {
  const data = state.summary.summary?.breakdowns || {};
  $("#symbols").innerHTML = breakdownTable(data.symbol); $("#models").innerHTML = breakdownTable(data.model); $("#exits").innerHTML = breakdownTable(data.exit);
}
function populateFilters() {
  const filters = state.summary.available_filters || {};
  $("#symbol").innerHTML = optionList(filters.symbols, "symbols"); $("#model").innerHTML = optionList(filters.models, "models");
  $("#exit").innerHTML = optionList(filters.exits, "exits"); $("#outcome").innerHTML = optionList(filters.outcomes, "outcomes");
}

function tradeQuery() {
  const params = new URLSearchParams({page: state.page, page_size: $("#page-size").value, direction: "desc"});
  for (const id of ["symbol", "model", "exit", "outcome"]) if ($(`#${id}`).value) params.set(id, $(`#${id}`).value);
  if ($("#search").value.trim()) params.set("q", $("#search").value.trim());
  return params;
}
async function loadTrades() {
  if (!state.run) return;
  const body = $("#trades"); body.innerHTML = '<tr><td colspan="11" class="empty">Loading trades…</td></tr>';
  try {
    const payload = await api(`/api/runs/${encodeURIComponent(state.run)}/trades?${tradeQuery()}`);
    state.page = payload.page; state.pages = payload.pages; state.total = payload.total;
    renderTrades(payload.trades || []); updatePager();
  } catch (error) { body.innerHTML = `<tr><td colspan="11" class="empty negative">${esc(error.message)}</td></tr>`; }
}
function signalQuery() {
  const params = new URLSearchParams({page: state.signalPage, page_size: $("#signal-page-size").value, direction: "desc"});
  for (const id of ["signal-symbol", "signal-status"]) if ($(`#${id}`).value) params.set(id, $(`#${id}`).value);
  if ($("#signal-search").value.trim()) params.set("q", $("#signal-search").value.trim());
  return params;
}
async function loadSignals() {
  if (!state.run) return;
  const body = $("#signals"); body.innerHTML = '<tr><td colspan="8" class="empty">Loading detector signals…</td></tr>';
  try {
    const payload = await api(`/api/runs/${encodeURIComponent(state.run)}/signals?${signalQuery()}`);
    state.signalPage = payload.page; state.signalPages = payload.pages; state.signalTotal = payload.total;
    const available = payload.available_filters || {};
    if (!$("#signal-symbol").options.length || $("#signal-symbol").options.length === 1) $("#signal-symbol").innerHTML = optionList(available.symbols, "symbols");
    if (!$("#signal-status").options.length || $("#signal-status").options.length === 1) $("#signal-status").innerHTML = optionList(available.statuses, "statuses");
    renderSignals(payload.signals || []); updateSignalPager();
  } catch (error) { body.innerHTML = `<tr><td colspan="8" class="empty negative">${esc(error.message)}</td></tr>`; }
}
function renderSignals(signals) {
  $("#signal-count").textContent = `(${state.signalTotal.toLocaleString()})`;
  if (!signals.length) { $("#signals").innerHTML = '<tr><td colspan="8" class="empty">No detector signals match these filters.</td></tr>'; return; }
  $("#signals").innerHTML = signals.map((signal) => {
    const status = String(signal._status || "unknown"), rejection = signal._rejection || "—";
    const direction = first(signal, ["direction", "side"], "—");
    return `<tr><td>${esc(signal._index + 1)}</td><td class="time">${esc(timeText(first(signal, ["signal_time_utc", "signal_time", "time_utc", "timestamp"])))}</td><td><b>${esc(signal._symbol)}</b></td><td>${esc(direction)}</td><td>${esc(first(signal, ["model", "entry_model"], "—"))}</td><td>${esc(first(signal, ["entry_zone_id", "zone_id"], "—"))}</td><td><span class="pill ${status === "executed" ? "win" : status === "rejected" ? "loss" : ""}">${esc(status)}</span></td><td class="muted">${esc(rejection)}</td></tr>`;
  }).join("");
}
function updateSignalPager() {
  $("#signal-page-label").textContent = `Page ${state.signalPage.toLocaleString()} of ${state.signalPages.toLocaleString()}`;
  $("#signal-prev").disabled = state.signalPage <= 1; $("#signal-next").disabled = state.signalPage >= state.signalPages;
}
function eventQuery() {
  const params = new URLSearchParams({page: state.eventPage, page_size: $("#event-page-size").value, direction: "desc"});
  for (const id of ["event-symbol", "event-type"]) if ($(`#${id}`).value) params.set(id, $(`#${id}`).value);
  if ($("#event-search").value.trim()) params.set("q", $("#event-search").value.trim());
  return params;
}
async function loadEvents() {
  if (!state.run) return;
  const body = $("#events"); body.innerHTML = '<tr><td colspan="6" class="empty">Loading events…</td></tr>';
  try {
    const payload = await api(`/api/runs/${encodeURIComponent(state.run)}/events?${eventQuery()}`);
    state.eventPage = payload.page; state.eventPages = payload.pages; state.eventTotal = payload.total;
    const events = payload.events || [];
    const available = payload.available_filters || {};
    if ($("#event-symbol").options.length === 1) $("#event-symbol").innerHTML = optionList(available.symbols, "symbols");
    if ($("#event-type").options.length === 1) $("#event-type").innerHTML = optionList(available.types, "event types");
    renderEventsTable(events); updateEventPager();
  } catch (error) { body.innerHTML = `<tr><td colspan="6" class="empty negative">${esc(error.message)}</td></tr>`; }
}
function renderEventsTable(events) {
  $("#event-count").textContent = `(${state.eventTotal.toLocaleString()})`;
  if (!events.length) { $("#events").innerHTML = '<tr><td colspan="6" class="empty">No events match these filters.</td></tr>'; return; }
  $("#events").innerHTML = events.map((event) => `<tr><td class="time">${esc(timeText(first(event, ["time_utc", "timestamp", "time", "datetime", "at"])))}</td><td><b>${esc(first(event, ["event", "type", "name", "action"], "Event"))}</b></td><td>${esc(first(event, ["symbol", "instrument"], "—"))}</td><td class="muted">${esc(first(event, ["signal_id", "trade_id", "zone_id"], "—"))}</td><td>${esc(first(event, ["reason", "model", "direction"], "—"))}</td><td class="event-json">${esc(JSON.stringify(event))}</td></tr>`).join("");
}
function updateEventPager() {
  $("#event-page-label").textContent = `Page ${state.eventPage.toLocaleString()} of ${state.eventPages.toLocaleString()}`;
  $("#event-prev").disabled = state.eventPage <= 1; $("#event-next").disabled = state.eventPage >= state.eventPages;
}
function renderTrades(trades) {
  $("#trade-count").textContent = `(${state.total.toLocaleString()})`;
  if (!trades.length) { $("#trades").innerHTML = '<tr><td colspan="11" class="empty">No trades match these filters.</td></tr>'; return; }
  $("#trades").innerHTML = trades.map((trade) => {
    const result = trade._outcome || "unknown", r = Number(trade._r || 0);
    return `<tr><td>${esc(trade._index + 1)}</td><td class="time">${esc(timeText(first(trade, aliases.time)))}</td><td><b>${esc(trade._symbol)}</b></td><td>${esc(first(trade, aliases.side, "—"))}</td><td>${esc(trade._model)}</td><td class="num">${fmt(first(trade, aliases.entry), 5)}</td><td class="num">${fmt(first(trade, aliases.stop), 5)}</td><td class="num">${fmt(first(trade, aliases.exitPrice), 5)}</td><td class="num ${r >= 0 ? "positive" : "negative"}"><b>${r >= 0 ? "+" : ""}${fmt(r)}</b></td><td><span class="pill ${esc(result)}">${esc(result)}</span></td><td><button class="view" data-id="${esc(trade._id)}">Inspect</button></td></tr>`;
  }).join("");
  document.querySelectorAll("button.view").forEach((button) => button.addEventListener("click", () => openTrade(button.dataset.id)));
}
function updatePager() {
  $("#page-label").textContent = `Page ${state.page.toLocaleString()} of ${state.pages.toLocaleString()}`;
  $("#prev").disabled = state.page <= 1; $("#next").disabled = state.page >= state.pages;
}
function detailGrid(trade) {
  const fields = [
    ["Symbol", trade._symbol], ["Side", first(trade, aliases.side, "—")], ["Model", trade._model], ["Result", trade._outcome],
    ["R multiple", `${Number(trade._r) >= 0 ? "+" : ""}${fmt(trade._r)} R`], ["Entry UTC", timeText(first(trade, aliases.time))],
    ["Entry", fmt(first(trade, aliases.entry), 5)], ["Stop", fmt(first(trade, aliases.stop), 5)], ["Exit", fmt(first(trade, aliases.exitPrice), 5)],
    ["Exit reason", trade._exit],
  ];
  return `<div class="detail-grid">${fields.map(([label, value]) => `<div><span>${esc(label)}</span><b>${esc(value)}</b></div>`).join("")}</div>`;
}

function renderEvents(events) {
  if (!events.length) return '<p class="empty">No linked events were found for this trade.</p>';
  return `<div class="timeline">${events.map((event) => {
    const when = first(event, ["time_utc", "timestamp", "time", "datetime", "at"]);
    const type = first(event, ["event", "type", "name", "action", "status"], "Event");
    const detail = first(event, ["message", "detail", "reason", "description"], "");
    return `<article><time>${esc(timeText(when))}</time><b>${esc(type)}</b>${detail ? `<p>${esc(detail)}</p>` : ""}</article>`;
  }).join("")}</div>`;
}
function candleChart(payload) {
  const candles = (payload.candles || []).filter((bar) => [bar.open, bar.high, bar.low, bar.close].every(finite));
  if (!candles.length) return `<div class="empty chart-empty">No ${esc(payload.symbol || "symbol")} 5m candle file was found under data/s146_running_extreme/raw.</div>`;
  const width = 1000, height = 300, top = 15, bottom = 30;
  const min = Math.min(...candles.map((bar) => Number(bar.low))), max = Math.max(...candles.map((bar) => Number(bar.high))), range = max - min || 1;
  const step = width / candles.length, bodyWidth = Math.max(1, Math.min(7, step * .65));
  const y = (price) => top + (max - Number(price)) * (height - top - bottom) / range;
  const marks = candles.map((bar, index) => {
    const x = (index + .5) * step, open = y(bar.open), close = y(bar.close), high = y(bar.high), low = y(bar.low);
    const rising = Number(bar.close) >= Number(bar.open), klass = rising ? "up" : "down";
    return `<g class="${klass}"><line x1="${x}" y1="${high}" x2="${x}" y2="${low}"/><rect x="${x-bodyWidth/2}" y="${Math.min(open,close)}" width="${bodyWidth}" height="${Math.max(1,Math.abs(close-open))}"/></g>`;
  }).join("");
  const entryX = payload.entry_timestamp ? candles.findIndex((bar) => bar.timestamp >= payload.entry_timestamp) : -1;
  const exitX = payload.exit_timestamp ? candles.findIndex((bar) => bar.timestamp >= payload.exit_timestamp) : -1;
  const marker = (index, label, klass) => index < 0 ? "" : `<g class="marker ${klass}"><line x1="${(index+.5)*step}" y1="0" x2="${(index+.5)*step}" y2="${height-bottom}"/><text x="${(index+.5)*step+4}" y="12">${label}</text></g>`;
  return `<div class="candle-wrap"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${marks}${marker(entryX,"ENTRY","entry")}${marker(exitX,"EXIT","exit")}</svg><div><span>${esc(timeText(candles[0].time))}</span><b>${esc(payload.symbol)} · 5m · ${candles.length} bars</b><span>${esc(timeText(candles.at(-1).time))}</span></div></div>`;
}
async function openTrade(id) {
  const dialog = $("#detail"); $("#detail-title").textContent = `Trade ${id}`; $("#detail-body").innerHTML = '<p class="empty">Loading trade and candles…</p>'; dialog.showModal();
  try {
    const base = `/api/runs/${encodeURIComponent(state.run)}/trades/${encodeURIComponent(id)}`;
    const [detail, candles] = await Promise.all([api(base), api(`${base}/candles`)]);
    $("#detail-title").textContent = `${detail.trade._symbol} · Trade ${detail.trade._id}`;
    $("#detail-body").innerHTML = `${detailGrid(detail.trade)}<section class="detail-section"><h3>5-minute price action</h3>${candleChart(candles)}</section><section class="detail-columns"><div><h3>Event timeline</h3>${renderEvents(detail.events || [])}</div><div><h3>Complete trade record</h3><pre>${esc(JSON.stringify(detail.trade, null, 2))}</pre></div></section>`;
  } catch (error) { $("#detail-body").innerHTML = `<p class="empty negative">${esc(error.message)}</p>`; }
}

boot();
