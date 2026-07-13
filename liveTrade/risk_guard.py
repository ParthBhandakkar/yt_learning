"""
Pre-trade risk checks for liveTrade (Strategy 98 and shared helpers).

Uses MT5 order_calc_profit when available; falls back to Exness contract specs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

from config import CONFIG

UTC = timezone.utc


def _parse_utc(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def signal_bar_close_time(signal_time_iso: str) -> datetime:
    """1H signal_time is the bar open; close is open + 1 hour."""
    return _parse_utc(signal_time_iso) + timedelta(hours=1)


def signal_matches_last_closed_bar(signal_time_iso: str, last_bar_open) -> bool:
    """Signal must be on the latest closed 1H bar (not an older cached bar)."""
    sig_open = _parse_utc(signal_time_iso)
    bar_open = last_bar_open.to_pydatetime() if hasattr(last_bar_open, "to_pydatetime") else last_bar_open
    if getattr(bar_open, "tzinfo", None) is None:
        bar_open = bar_open.replace(tzinfo=UTC)
    else:
        bar_open = bar_open.astimezone(UTC)
    return sig_open == bar_open


def entry_delay_ok(signal_time_iso: str, now: datetime, max_delay_sec: int) -> tuple[bool, float]:
    """
    Backtest fills at next 1H open (~0–60s after signal bar close).
    Reject late entries (e.g. engine restart long after the open).
    """
    if max_delay_sec <= 0:
        return True, 0.0
    close_t = signal_bar_close_time(signal_time_iso)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    delay = (now - close_t).total_seconds()
    return delay <= max_delay_sec, delay


def _fallback_sl_loss(symbol: str, direction: str, entry: float, sl: float, lots: float) -> float:
    """Approximate loss at SL using Exness XAUUSD contract (100 oz / lot)."""
    dist = abs(entry - sl)
    key = "".join(ch for ch in symbol.upper() if ch.isalnum())
    contract = 100.0
    if "XAU" in key or "GOLD" in key:
        contract = 100.0
    elif key.endswith("JPY") or "USDJPY" in key:
        contract = 100_000.0
    else:
        contract = 100_000.0
    # USD notional move; INR accounts use ~same order of magnitude for gold via MT5.
    usd_loss = dist * contract * lots
    if CONFIG.account_ccy.upper() == "INR":
        return usd_loss * 47.0
    return usd_loss


def sl_loss_at_stop(
    client,
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    lots: float,
) -> float:
    """Positive loss amount in account currency if price hits SL."""
    if lots <= 0 or sl <= 0:
        return 0.0
    broker_sym = client.resolve_symbol(symbol)
    if broker_sym is None:
        return _fallback_sl_loss(symbol, direction, entry, sl, lots)

    if mt5 is not None and client.ensure():
        order_type = mt5.ORDER_TYPE_BUY if direction == "long" else mt5.ORDER_TYPE_SELL
        profit = mt5.order_calc_profit(order_type, broker_sym, float(lots), float(entry), float(sl))
        if profit is not None:
            return abs(float(profit))

    return _fallback_sl_loss(symbol, direction, entry, sl, lots)


def max_allowed_risk(balance: float) -> float:
    """Effective cap: minimum of enabled INR and % limits (0 = disabled)."""
    caps: list[float] = []
    if CONFIG.max_risk_inr > 0:
        caps.append(CONFIG.max_risk_inr)
    if CONFIG.max_risk_pct > 0 and balance > 0:
        caps.append(balance * CONFIG.max_risk_pct / 100.0)
    if not caps:
        return float("inf")
    return min(caps)


def assess_entry_risk(
    client,
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    lots: float,
    balance: float,
) -> tuple[bool, str, float, float]:
    """
    Returns (ok, reason, loss_at_sl, cap).
    ok is False when loss_at_sl exceeds configured cap.
    """
    loss = sl_loss_at_stop(client, symbol, direction, entry, sl, lots)
    cap = max_allowed_risk(balance)
    if cap != float("inf") and loss > cap + 1e-6:
        pct = (loss / balance * 100.0) if balance > 0 else 0.0
        return (
            False,
            (
                f"SL risk Rs{loss:.0f} exceeds cap Rs{cap:.0f} "
                f"({pct:.1f}% of balance Rs{balance:.0f}; "
                f"MAX_RISK_INR={CONFIG.max_risk_inr:.0f} "
                f"MAX_RISK_PCT={CONFIG.max_risk_pct:.1f})"
            ),
            loss,
            cap,
        )
    return True, "", loss, cap
