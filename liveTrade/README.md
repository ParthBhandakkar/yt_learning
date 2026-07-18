# liveTrade — Strategy 97 / 98 live runner (MT5 / Exness, Windows)

Runs the validated strategies live, 24/7, through your MT5 (Exness) terminal.
Pick the strategy with a command-line argument.

| Strategy | `--strategy` | Symbols | Cadence | Exit logic |
|----------|:-----------:|---------|---------|-----------|
| With-trend mean-reversion basket | **97** | EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, NZDUSD, USDCAD, XAUUSD | **4H** closed candle | fixed ATR stop + dynamic mean-revert TP + 48-bar time stop |
| XAUUSD trend + liquidity + ATR trail | **98** | XAUUSD | **1H** closed candle | ATR chandelier trailing stop (no fixed TP) |

```bat
python run.py --strategy 97          :: mean-reversion basket
python run.py --strategy 98          :: gold trend + ATR trail
python run.py --strategy 97 --check  :: connectivity/config check, then exit
```

`--strategy` overrides `STRATEGY_ID` in `.env`. `95` (legacy MSS/OB) is still available but unused.

---

## Live == backtest (verified)

The live signal code reuses the **exact indicator math and entry rule** from the
backtests, evaluated only on **closed** candles.

- **Strategy 97:** `detection_s97.py` calls the backtest's own ATR and replicates
  its SMA/EMA and the `z = (close-SMA)/ATR` fade rule. Verified by
  `test_s97_parity.py`: replaying the 4H history bar-by-bar reproduces
  **1019 / 1020 (99.9%)** of backtest entries across all 8 pairs; the single
  difference is one USDJPY signal sitting exactly on the z-threshold (sub-pip
  float boundary), not a logic error.
- **EMA200 is recursive** (`adjust=False`), so live must load enough history for
  it to converge to the full-history backtest value. `test_s97_window.py` shows
  240 bars gives 185 signal diffs, **≥800 bars gives an exact match**. The engine
  fetches **1500** 4H bars per scan (`S97_FETCH_BARS`) for a safe margin.
- **Strategy 98:** `detection_s98.py` reuses `strategy_98_xau_trend_liquidity_trail.py`
  directly on the last closed 1H bar.

Run the parity checks anytime (they need only the CSVs, not MT5):

```
python test_s97_parity.py     :: live entries vs backtest entries, per pair
python test_s97_window.py     :: how many bars EMA200 needs to converge
```

---

## What each strategy does live

### Strategy 97 (4H mean-reversion basket)
- Every 4H close: EMA200 sets the trend; a pullback stretched to `z ≥ 2.0` ATRs
  against the mean is **faded with the trend** (long dips in uptrends, short rips
  in downtrends).
- **Entry** at market right after the signal bar closes (backtest fills next-bar
  open — same risk distance `2.5×ATR = 1R`).
- **Exit** managed to match the backtest exactly:
  - hard ATR stop placed on the broker (enforced intrabar),
  - dynamic **mean-revert TP** = `SMA ± 0.5×ATR`, recomputed and pushed to the
    broker on **every** closed 4H bar,
  - **time stop**: flat at market after 48 bars.
- One position per pair; up to `MAX_CONCURRENT_TRADES` open at once.

### Strategy 98 (gold, 1H)
- 1H closed-candle scan (4H bias resampled from 1H). Entry at market on the
  signal bar close. Exit via `trade_manager_s98.py` ATR chandelier trail.
- Use `FIXED_LOT=0.01`, `SYMBOLS=` (auto → XAUUSD), `MAX_CONCURRENT_TRADES=1`.

Both engines:
- **Only scan closed candles** (the forming bar is always dropped).
- Log every cycle to `logs/` and every signal/trade to `passes/*.jsonl`.
- Enforce guards: one-trade-per-pair, max-concurrent, daily-loss pause, `DRY_RUN`.
- Email on start / execution / exit (if SMTP configured).
- Persist open-position state so a restart resumes management (`passes/positions_state_s9*.json`).

---

## Setup (Windows PC with MT5 / Exness running)

1. Install Python 3.10–3.12 and the MT5 terminal, logged into your **demo** Exness account.
2. In this folder:
   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill it in:
   - Leave `MT5_LOGIN/PASSWORD/SERVER` blank to attach to the already-logged-in terminal.
   - Leave `SYMBOLS` blank to auto-select the strategy's basket.
   - Exness symbols may have a suffix (e.g. `EURUSDm`); set `MT5_SYMBOL_SUFFIX` or leave blank to auto-resolve.
   - For s98: `MAX_CONCURRENT_TRADES=1`, `FIXED_LOT=0.01`.
4. **Validate first:**
   ```bat
   python run.py --strategy 97 --check
   ```
   Confirms MT5 connects, every symbol resolves, closed 4H candles are fetched, and shows a sample lot size.
5. **Run in DRY_RUN** (`DRY_RUN=true`) for a while — logs signals + emails, places no orders. Watch `logs/` and `passes/`.
6. Go live on the **demo** account: set `DRY_RUN=false` and run continuously:
   ```bat
   run_forever.bat 97
   ```
   `run_forever.bat` restarts the engine automatically if it exits. Edit the
   `PYTHON` line inside it if you use a specific interpreter path. Alternatively
   register `python run.py --strategy 97` with Windows Task Scheduler (at logon,
   restart on failure) or NSSM as a service.

---

## Order lifecycle smoke test (demo only)
Verify place / modify-SL / close on the demo account without touching live positions
(test uses magic `989898`):
```bat
python test_order_lifecycle.py
```

## ⚠ Risk note
At 1:2000 leverage, notional = `margin × 2000`; a full stop-out can lose several
times the margin. The verified edge is **thin** and cost-sensitive (see
`../drawdown_analysis_verified.md`): keep risk small (1–2% per trade — 10% risk
produced 60–98% drawdowns in simulation), use `MAX_DAILY_LOSS_INR` and
`MAX_CONCURRENT_TRADES`, and test on demo first. Backtests are not a guarantee of
live results.

## Files
| file | purpose |
|------|---------|
| `run.py` | entrypoint — `--strategy 97|98`, `--check` |
| `engine_s97.py` / `engine_s98.py` | 24/7 scheduler per strategy (4H / 1H closed-candle cadence) |
| `detection_s97.py` / `detection_s98.py` | live signal = exact backtest logic on closed bars |
| `trade_manager_s97.py` / `trade_manager_s98.py` | exit management (stop/TP/time-stop ; ATR trail) |
| `mt5_client.py` | MT5 connect, closed-candle fetch, sizing, place/modify/close |
| `notifier.py` | strategy-aware email alerts |
| `logging_setup.py` | per-timeframe logs + per-timeframe pass files |
| `config.py` | loads `.env` (typed) |
| `test_s97_parity.py` | proves live entries == backtest entries |
| `test_s97_window.py` | proves EMA200 fetch-window convergence |
| `test_order_lifecycle.py` | demo place/trail/close smoke test |
| `run_forever.bat` | Windows auto-restart loop |
| `logs/`, `passes/` | created at runtime |
