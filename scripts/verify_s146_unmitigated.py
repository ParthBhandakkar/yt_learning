#!/usr/bin/env python3
"""Verify the S146 "unmitigated" claim against real 4H bars.

The engine logs every destination POI as `state: "unmitigated"`, meaning no 4H
bar has traded into the zone since the break of structure confirmed it. That
claim decides every take-profit level, so it should be checked against price
rather than trusted.

This reads `logs/s146_4h_events.log`, then for each POI replays the 4H CSV in
`data/<SYMBOL>/4h/` applying the backtest's own overlap test:

    touched  <=>  bar.low <= zone.upper AND bar.high >= zone.lower

Both sides must overlap. A bar that gaps entirely past the zone satisfies one
side without ever trading in it, and must not count as mitigation.

Verdicts:
  OK        no bar touched the zone within the data available -> claim holds
  VIOLATION a bar did touch it -> the zone was NOT unmitigated
  PARTIAL   data ends before the POI was used -> checked as far as data allows
  NO_DATA   no CSV, or CSV ends before the zone was even confirmed

Deliberately dependency-free (csv + stdlib only) so it runs anywhere.

Usage:
    python scripts/verify_s146_unmitigated.py
    python scripts/verify_s146_unmitigated.py --symbol EURUSD --verbose
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "liveTrade" / "logs"
DATA_DIR = REPO / "data"
H4_SECONDS = 4 * 3600


def utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def load_events(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line.split(" | ", 2)[2]))
        except Exception:
            continue
    return rows


def load_bars(symbol: str) -> list[tuple[int, float, float]]:
    """(timestamp, high, low) for every 4H bar found for `symbol`."""
    folder = DATA_DIR / symbol
    if not folder.is_dir():
        return []
    files: list[Path] = []
    for sub in folder.iterdir():
        if sub.is_dir() and sub.name.lower() == "4h":
            files.extend(sorted(sub.glob("*.csv")))
    bars: list[tuple[int, float, float]] = []
    for path in files:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    bars.append((int(float(row["time"])),
                                 float(row["high"]), float(row["low"])))
                except (KeyError, TypeError, ValueError):
                    continue
    bars.sort(key=lambda b: b[0])
    return bars


def first_touch(bars, lower: float, upper: float, after: int):
    """First bar at or after `after` that overlaps [lower, upper] on both sides."""
    for stamp, high, low in bars:
        if stamp < after:
            continue
        if low <= upper and high >= lower:
            return stamp, high, low
    return None


def overlap_pips(symbol: str, lower: float, upper: float,
                 high: float, low: float) -> float:
    """How deep the bar actually penetrated the zone, in pips.

    Reported because a sub-pip overlap is far more likely to be a difference
    between two price feeds than a real mitigation, and the two cases deserve
    different treatment.
    """
    pip = 0.01 if "JPY" in symbol.upper() else 0.0001
    depth = min(high, upper) - max(low, lower)
    return depth / pip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", help="check one symbol only")
    ap.add_argument("--verbose", action="store_true", help="list every zone")
    args = ap.parse_args()

    events = load_events(LOG_DIR / "s146_4h_events.log")
    trades = load_events(LOG_DIR / "s146_trades.log")
    if not events:
        print("no 4H events found — nothing to verify")
        return 1

    # POIs that actually produced a trade matter most; flag them.
    traded = {(t["symbol"], t["destination_lower"], t["destination_upper"])
              for t in trades if "destination_lower" in t}

    seen: dict[tuple, dict] = {}
    for row in events:
        if row.get("type") != "destination_available":
            continue
        if args.symbol and row.get("symbol") != args.symbol.upper():
            continue
        key = (row["symbol"], row["lower"], row["upper"], row["confirmed_at"])
        seen.setdefault(key, row)

    bars_cache: dict[str, list] = {}
    tally = {"OK": 0, "VIOLATION": 0, "MARGINAL": 0, "PARTIAL": 0, "NO_DATA": 0}
    violations: list[str] = []
    covered_traded: set[tuple] = set()
    uncovered: set[str] = set()

    for (symbol, lower, upper, confirmed_at), row in sorted(seen.items()):
        if symbol not in bars_cache:
            bars_cache[symbol] = load_bars(symbol)
        bars = bars_cache[symbol]
        is_traded = (symbol, lower, upper) in traded
        mark = " *TRADED*" if is_traded else ""

        if not bars or bars[-1][0] < confirmed_at:
            tally["NO_DATA"] += 1
            if is_traded:
                uncovered.add(symbol)
            if args.verbose:
                print(f"NO_DATA   {symbol:7} {lower}-{upper} "
                      f"confirmed {utc(confirmed_at)}{mark}")
            continue

        if is_traded:
            covered_traded.add((symbol, lower, upper))

        hit = first_touch(bars, lower, upper, confirmed_at)
        data_end = bars[-1][0]
        if hit is not None:
            stamp, high, low = hit
            depth = overlap_pips(symbol, lower, upper, high, low)
            # Under ~1 pip is within the spread between two feeds; call it out
            # separately instead of declaring the algorithm wrong.
            label = "MARGINAL " if depth < 1.0 else "VIOLATION"
            tally["MARGINAL" if depth < 1.0 else "VIOLATION"] += 1
            msg = (f"{label} {symbol:7} {lower}-{upper} confirmed {utc(confirmed_at)} "
                   f"-> touched {utc(stamp)} (bar {low}-{high}, "
                   f"depth {depth:.2f} pips){mark}")
            if depth >= 1.0:
                violations.append(msg)
            print(msg)
            continue

        # Untouched, but is the data recent enough to cover the whole window?
        if data_end < int(datetime.now(timezone.utc).timestamp()) - H4_SECONDS:
            tally["PARTIAL"] += 1
            if args.verbose:
                print(f"PARTIAL   {symbol:7} {lower}-{upper} clean through "
                      f"{utc(data_end)}{mark}")
        else:
            tally["OK"] += 1
            if args.verbose:
                print(f"OK        {symbol:7} {lower}-{upper} clean through "
                      f"{utc(data_end)}{mark}")

    checked_traded = len(covered_traded)

    print()
    print("=" * 72)
    print(f"zones checked      : {sum(tally.values())}")
    for name in ("OK", "PARTIAL", "MARGINAL", "VIOLATION", "NO_DATA"):
        print(f"  {name:9}        : {tally[name]}")
    print(f"traded POIs covered: {checked_traded} of {len(traded)}")
    if tally["VIOLATION"]:
        print()
        print(f"{tally['VIOLATION']} zone(s) were reported unmitigated but price traded "
              f"more than a pip into them. Either unmitigated_zones() is wrong, or the "
              f"live feed and the CSV feed disagree materially.")
    if tally["MARGINAL"]:
        print()
        print(f"{tally['MARGINAL']} zone(s) were clipped by under a pip. That is inside "
              f"the gap between two price feeds, so it does not falsify the logic — but "
              f"it does show mitigation is being decided by sub-pip margins. Consider a "
              f"small tolerance so the verdict is feed-independent.")
    if not tally["VIOLATION"] and not tally["MARGINAL"]:
        print()
        print("No contradictions found in the bars available.")
    if checked_traded < len(traded) and traded:
        print()
        print(f"WARNING: {len(traded) - checked_traded} of {len(traded)} POIs that "
              f"actually produced trades could NOT be checked — no 4H CSV covers them.")
        if uncovered:
            print(f"         missing 4H data for: {', '.join(sorted(uncovered))}")
        print("         Export those symbols to verify the levels your real targets "
              "were built on.")
    return 2 if tally["VIOLATION"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
