# liveTrade — Strategy 95 live runner (MT5 / Exness)

Runs the validated **Strategy 95** (4H liquidity sweep → 1H displacement MSS →
15M order block in OTE → 5M tap) live, 24/7, across multiple symbols, executing
through your MT5 (Exness) terminal. Signals use the **exact same detection code**
as the backtest, so live = backtest.

## What it does
- **Scans each timeframe only when its candle has CLOSED** — 4H every 4h, 1H every
  hour, 15M every 15m, 5M every 5m — and never looks at the still-forming candle.
- Stages independently per symbol with a 16h setup expiry (TTL):
  - **4H** → finds the liquidity-sweep bias  → `passes/4h_passes.jsonl`
  - **1H** → displacement-gated MSS          → `passes/1h_passes.jsonl`
  - **15M** → order block in the OTE zone     → `passes/15m_passes.jsonl`
  - **5M** → fresh tap → **executes the trade** → `passes/5m_passes.jsonl`
- **Logs every timeframe cycle** to `logs/<tf>.log` (plus `logs/engine.log`).
- **Sizing:** margin-based fixed stake — ₹`MARGIN_PER_TRADE_INR` at `LEVERAGE` (1:2000),
  lot size derived from MT5's own margin model, capped by `MAX_LOT`.
- **Management:** at +0.5R it closes 50% and moves the stop to **breakeven**; the
  runner targets +1.5R (set as the order TP). State persists across restarts.
- **Email** on every execution (and on partial/breakeven, start-up).
- **Safety:** one trade per pair, max concurrent trades, daily-loss pause, and a
  `DRY_RUN` mode that logs signals but places NO orders.

## Setup (Windows PC with MT5/Exness running)
1. Install Python 3.10–3.12 and the MT5 terminal (logged into your Exness account).
2. In this folder:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill it in (MT5 login/server, symbols, email…).
   - If your terminal is already logged in you can leave `MT5_LOGIN/PASSWORD/SERVER` blank.
   - Exness symbols sometimes have a suffix (e.g. `EURUSDm`); set `MT5_SYMBOL_SUFFIX` or
     leave blank to auto-resolve.
4. **Validate first:**
   ```
   python run.py --check
   ```
   Confirms MT5 connects, every symbol resolves, candles are fetched, and shows a
   sample lot size.
5. **Run in DRY_RUN** (`DRY_RUN=true`) for a while — it logs signals + emails but
   places no orders. Watch `logs/` and `passes/`.
6. When satisfied, set `DRY_RUN=false` (ideally first on a **demo** account) and run:
   ```
   python run.py
   ```
   Keep it running 24/7 (e.g. Windows Task Scheduler, or NSSM as a service).

## ⚠ Risk note
At 1:2000 leverage, position notional = `margin × 2000`. A full stop-out can lose
**several times** the ₹1000 margin. Use `MAX_DAILY_LOSS_INR`, `MAX_CONCURRENT_TRADES`,
and test on demo before risking real money. Backtests are not a guarantee of live results.

## Files
| file | purpose |
|------|---------|
| `run.py` | entrypoint (`--check` for diagnostics) |
| `engine.py` | 24/7 scheduler, per-TF cadence, staging, execution |
| `detection.py` | adapter over the real Strategy 95 detection |
| `mt5_client.py` | MT5 connect, closed-candle fetch, sizing, orders |
| `trade_manager.py` | partial @0.5R + breakeven, state persistence |
| `notifier.py` | email alerts |
| `logging_setup.py` | per-TF logs + per-TF pass files |
| `config.py` | loads `.env` |
| `logs/`, `passes/` | created at runtime |
