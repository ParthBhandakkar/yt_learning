# S146 Running-Extreme Dashboard

Standalone, dependency-free, read-only browser for backtest runs in:

- `data/s146_running_extreme/runs/<run-id>/`
- `data/s146_running_extreme/raw/` (symbol 5-minute candles)

## Start manually

From the repository root (`d:\WorkZera\Projects\strategies\yt_learning`):

```powershell
python dashboard\s146_running_extreme\server.py
```

Then open <http://127.0.0.1:8016>. Stop it with `Ctrl+C`.

The server never starts on import and accepts only `GET` and `HEAD`. It uses Python's standard library and writes no data. Optional `--host` and `--port` arguments are available; defaults are `127.0.0.1` and `8016`.

## Expected run data

The reader is intentionally schema-tolerant. It recognizes common JSON/JSONL/CSV names such as `manifest.json`, `summary.json`, `trades.csv`, and `events.jsonl`, with fallback names documented by the implementation. Missing KPI values are calculated from trade R values. Raw 5-minute CSV files are discovered recursively by symbol and `5m`/`m5` in their path.

## APIs

- `GET /api/runs`
- `GET /api/runs/{run_id}/summary`
- `GET /api/runs/{run_id}/trades?page=1&page_size=50&symbol=&model=&exit=&outcome=&q=`
- `GET /api/runs/{run_id}/trades/{trade_id}`
- `GET /api/runs/{run_id}/trades/{trade_id}/candles?before=72&after=144`

The dashboard includes three complete read-only views: executed trades with an inspector containing each trade's event timeline, all detector-valid signals (including spread/portfolio rejections), and the merged event stream across every symbol. The API routes are documented in `backtests\s146_running_extreme\README.md`.