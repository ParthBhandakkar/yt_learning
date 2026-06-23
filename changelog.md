[IST 22-JUN-2026 12:00:00] - Auto-detect CSV column formats in load_csv; save backtest results to dashboard/out/ inside the project
[IST 22-JUN-2026 14:00:00] - Fix UnboundLocalError for timedelta in strategy_44 and strategy_52
[IST 22-JUN-2026 15:00:00] - Compute PnL stats from entry/exit prices when strategies omit pnl_pips
[IST 22-JUN-2026 16:00:00] - Add scrollable candlestick chart with entry/exit markers below results chart
[IST 22-JUN-2026 17:00:00] - Refactor price chart to TradingView Lightweight Charts with native pan/zoom
[IST 23-JUN-2026 10:00:00] - Add BACKTEST INTEGRITY NOTICE comments to all 22 strategy files documenting leaks and fixes
[IST 23-JUN-2026 18:00:00] - Fix hindsight bias in strategies 06, 13, 17, 29, 31, 39, 42 using causal_backtest helpers
[IST 23-JUN-2026 12:00:00] - Fix minor hindsight in strategies 04, 08, 44 via causal_backtest helpers
[IST 23-JUN-2026 14:00:00] - Fix major hindsight bias in strategies 54, 56, 62, 69, 77, 78, 81 via causal_backtest (per-day/bar walks, simulate_exits)
[IST 23-JUN-2026 19:00:00] - Add causal_backtest.py shared helpers; fix CRITICAL strategies 01, 05, 28, 52, 65; close-only iFVG and confirmed swings in core.py
