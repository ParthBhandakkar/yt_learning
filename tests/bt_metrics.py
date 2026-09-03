"""
Risk-normalized backtest metrics computed on cost-adjusted trades.

All PnL here is NET of trading costs (see core.enrich_trades_pnl). On top of the
raw pip stats we compute R-multiples (PnL / initial risk) which let us compare
strategies on different instruments and price scales on equal footing, plus an
equity curve and max drawdown so a high win-rate / negative-expectancy system
cannot hide.
"""

from __future__ import annotations

from typing import Optional

from core import infer_pip_size


def _risk_pips(trade: dict) -> Optional[float]:
    entry = trade.get("entry_price")
    sl = trade.get("stop_loss")
    if entry is None or sl is None:
        return None
    pip_size = infer_pip_size(float(entry))
    risk = abs(float(entry) - float(sl)) / pip_size
    return risk if risk > 1e-9 else None


def compute_metrics(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get("outcome") in ("win", "loss", "breakeven")
              and t.get("pnl_pips") is not None]
    n = len(closed)
    base = {
        "total_trades": len(trades),
        "closed_trades": n,
        "open_trades": sum(1 for t in trades if t.get("outcome") not in ("win", "loss", "breakeven")),
        "win_rate": 0.0,
        "net_pnl_pips": 0.0,
        "gross_pnl_pips": 0.0,
        "total_cost_pips": 0.0,
        "profit_factor": 0.0,
        "expectancy_pips": 0.0,
        "expectancy_R": 0.0,
        "total_R": 0.0,
        "avg_win_pips": 0.0,
        "avg_loss_pips": 0.0,
        "max_drawdown_R": 0.0,
        "max_drawdown_pips": 0.0,
        "trades_per_year": 0.0,
    }
    if n == 0:
        return base

    wins = [t for t in closed if t["pnl_pips"] > 0]
    losses = [t for t in closed if t["pnl_pips"] < 0]
    net = sum(t["pnl_pips"] for t in closed)
    gross = sum(t.get("pnl_gross_pips", t["pnl_pips"]) for t in closed)
    cost = sum(t.get("cost_pips", 0.0) for t in closed)
    gp = sum(t["pnl_pips"] for t in wins)
    gl = abs(sum(t["pnl_pips"] for t in losses))

    # R-multiples
    r_values = []
    for t in closed:
        rp = _risk_pips(t)
        if rp:
            r_values.append(t["pnl_pips"] / rp)
    total_R = sum(r_values)

    # Equity curve + max drawdown (in R and in pips)
    def max_dd(series: list[float]) -> float:
        peak = 0.0
        eq = 0.0
        mdd = 0.0
        for x in series:
            eq += x
            peak = max(peak, eq)
            mdd = min(mdd, eq - peak)
        return abs(mdd)

    mdd_R = max_dd(r_values) if r_values else 0.0
    mdd_pips = max_dd([t["pnl_pips"] for t in closed])

    # trades per year
    times = sorted(t.get("entry_time", "") for t in closed if t.get("entry_time"))
    tpy = 0.0
    if len(times) >= 2:
        from datetime import datetime
        try:
            a = datetime.fromisoformat(times[0].replace("Z", "+00:00"))
            b = datetime.fromisoformat(times[-1].replace("Z", "+00:00"))
            yrs = (b - a).days / 365.25
            if yrs > 0:
                tpy = n / yrs
        except (ValueError, TypeError):
            pass

    base.update({
        "win_rate": round(len(wins) / n * 100, 1),
        "net_pnl_pips": round(net, 1),
        "gross_pnl_pips": round(gross, 1),
        "total_cost_pips": round(cost, 1),
        "profit_factor": round(gp / gl, 3) if gl else (None if gp else 0.0),
        "expectancy_pips": round(net / n, 3),
        "expectancy_R": round(total_R / len(r_values), 4) if r_values else 0.0,
        "total_R": round(total_R, 2),
        "avg_win_pips": round(gp / len(wins), 2) if wins else 0.0,
        "avg_loss_pips": round(-gl / len(losses), 2) if losses else 0.0,
        "max_drawdown_R": round(mdd_R, 2),
        "max_drawdown_pips": round(mdd_pips, 1),
        "trades_per_year": round(tpy, 1),
    })
    return base
