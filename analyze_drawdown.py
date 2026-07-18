#!/usr/bin/env python3
"""
Analyze strategy 98 trades:
1. How many trades reached >= 100 pips in their favor (1 pip = $0.01 on XAUUSD)
2. Among those, for each trade I find the maximum adverse movement BEFORE
   price first reached 100 pips favorable. Then find the min stop distance
   such that >60% of these trades would NOT have been stopped out early.
"""
import json
import csv
from datetime import datetime, timezone

XAUUSD_PIP = 0.01
TARGET_PIPS = 200
TARGET_PRICE = TARGET_PIPS * XAUUSD_PIP  # $1.0

TRADES_JSON = "/Users/parthbhandakkar/Desktop/WorkZera/Projects/TradeBot/ytLearning copy/backtester-app/strategies/dashboard/out/s98_strategy_98_xau_trend_liquidity_trail_20260717_141427.json"
CSV_1H = "/Users/parthbhandakkar/Desktop/WorkZera/Projects/TradeBot/ytLearning copy/backtester-app/strategies/data/XAUUSD/1H/XAUUSD_1h_2021-03-02_2026-07-01.csv"

print("Loading trades...")
with open(TRADES_JSON) as f:
    data = json.load(f)
trades = data["trades"]
print(f"Total trades: {len(trades)}")

print("Loading 1h CSV...")
candles = []
with open(CSV_1H, encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for row in reader:
        ts = int(float(row["time"]))
        candles.append({
            "timestamp": ts,
            "time_utc": row["time_utc"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        })
print(f"Loaded {len(candles)} 1h candles")

ts_list = [c["timestamp"] for c in candles]

def _ts(row, key="entry_time"):
    raw = row.get(key)
    if not raw:
        return None
    return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())

def find_idx(ts):
    for i, t in enumerate(ts_list):
        if t >= ts:
            return i
    return len(ts_list) - 1

print("\n=== Analyzing trades: adverse movement BEFORE reaching 100 pips ===")

results = []
no_data = 0
never_reached = 0

for t in trades:
    entry_ts = _ts(t, "entry_time")
    if entry_ts is None:
        no_data += 1
        continue
    
    entry_price = float(t["entry_price"])
    direction = t["direction"]
    
    entry_idx = find_idx(entry_ts)
    if entry_idx >= len(candles):
        no_data += 1
        continue
    
    # Scan forward from entry to find when trade first reaches 100 pips favorable
    qual_bar_idx = None
    for j in range(entry_idx, len(candles)):
        c = candles[j]
        if direction == "long":
            if c["high"] >= entry_price + TARGET_PRICE:
                qual_bar_idx = j
                break
        else:
            if c["low"] <= entry_price - TARGET_PRICE:
                qual_bar_idx = j
                break
    
    if qual_bar_idx is None:
        never_reached += 1
        continue
    
    # Now compute max adverse movement from entry to qual_bar (inclusive)
    # For a long: adverse = entry_price - min(low)
    # For a short: adverse = max(high) - entry_price
    min_low = float("inf")
    max_high = float("-inf")
    for j in range(entry_idx, qual_bar_idx + 1):
        c = candles[j]
        if c["low"] < min_low:
            min_low = c["low"]
        if c["high"] > max_high:
            max_high = c["high"]
    
    if direction == "long":
        adverse_price = entry_price - min_low
    else:
        adverse_price = max_high - entry_price
    
    adverse_pips = adverse_price / XAUUSD_PIP
    
    results.append({
        "trade_number": t["trade_number"],
        "direction": direction,
        "entry_price": entry_price,
        "outcome": t["outcome"],
        "adverse_pips": round(adverse_pips, 1),
        "adverse_price": adverse_price,
    })

print(f"Trades that reached 100+ pips favorable: {len(results)}")
print(f"Trades that never reached 100 pips: {never_reached}")
print(f"Trades with no data: {no_data}")

actual_winners_among_qual = sum(1 for r in results if r["outcome"] == "win")
print(f"Actual winners among them: {actual_winners_among_qual} ({actual_winners_among_qual/len(results)*100:.1f}%)")
print(f"Actual losers among them: {len(results) - actual_winners_among_qual}")

# Distribution of adverse movement before reaching target
adv_vals = sorted([r["adverse_pips"] for r in results])
print(f"\n=== Adverse movement BEFORE reaching 100 pips ===")
print(f"  Min adverse: {adv_vals[0]:.1f} pips (${adv_vals[0]*XAUUSD_PIP:.2f})")
print(f"  Max adverse: {adv_vals[-1]:.1f} pips")
print(f"  Mean adverse: {sum(adv_vals)/len(adv_vals):.1f} pips")
print(f"  Median adverse: {adv_vals[len(adv_vals)//2]:.1f} pips")

for pctile in [5, 10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 95]:
    idx = int(len(adv_vals) * pctile / 100)
    print(f"  {pctile}th percentile: {adv_vals[idx]:.1f} pips (${adv_vals[idx]*XAUUSD_PIP:.2f})")

# Find min stop for >60% "survival rate"
# "Win" = adverse movement < stop distance (trade would have survived to reach 100 pips)
print(f"\n=== Win rate (survival to 100 pips) at different stop distances ===")
print(f"{'Stop (pips)':>12} {'Survive':>8} {'Stopped':>8} {'Total':>8} {'Rate':>8}")
print("-" * 48)

best_thresh = None
best_wr = 0
for thresh in range(1, 501):
    survive = sum(1 for r in results if r["adverse_pips"] < thresh)
    total = len(results)
    wr = survive / total * 100
    if wr >= best_wr:
        best_wr = wr
        best_thresh = thresh
    if thresh <= 20 or thresh % 20 == 0 or (wr > 58 and wr < 62):
        print(f"{thresh:>11}p {survive:>8} {total-survive:>8} {total:>8} {wr:>7.1f}%")

# Find minimum stop for >60%
print(f"\n=== Finding minimum stop for >60% survival ===")
for thresh in range(1, 2001):
    survive = sum(1 for r in results if r["adverse_pips"] < thresh)
    total = len(results)
    wr = survive / total * 100
    if wr > 60:
        print(f"  Minimum stop: {thresh} pips (${thresh*XAUUSD_PIP:.2f}) -> {survive}/{total} survive ({wr:.1f}%)")
        break

# Show overall min / max survival
print(f"\n  Best possible survival rate (infinite stop): {best_wr:.1f}% at {best_thresh} pips")
print(f"  Note: even with infinite stop, only {actual_winners_among_qual} of {len(results)} are actual winners ({actual_winners_among_qual/len(results)*100:.1f}%)")

# Show some sample trades
print(f"\n=== Sample qualifying trades ===")
for r in results[:10]:
    print(f"  #{r['trade_number']} {r['direction']:>5} entry={r['entry_price']:.3f} "
          f"adverse_before_target={r['adverse_pips']:.1f}p outcome={r['outcome']}")

# Show trades with smallest adverse (best entries)
print(f"\n=== Trades with SMALLEST adverse before target (tightest entries) ===")
for r in sorted(results, key=lambda x: x["adverse_pips"])[:10]:
    print(f"  #{r['trade_number']} {r['direction']:>5} entry={r['entry_price']:.3f} "
          f"adverse={r['adverse_pips']:.1f}p outcome={r['outcome']}")

# Show trades with largest adverse (worst entries)
print(f"\n=== Trades with LARGEST adverse before target ===")
for r in sorted(results, key=lambda x: x["adverse_pips"], reverse=True)[:10]:
    print(f"  #{r['trade_number']} {r['direction']:>5} entry={r['entry_price']:.3f} "
          f"adverse={r['adverse_pips']:.1f}p outcome={r['outcome']}")
