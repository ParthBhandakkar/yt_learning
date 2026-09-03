#!/usr/bin/env python3
"""
Analyze s97 trades for all pairs:
- For each pair, count trades reaching 100 / 200 pips favorable (native pip)
- For those, compute min stop for >60% survival using 4h OHLC data
- Also compute equivalent $ price targets for comparison with XAUUSD
"""
import json
import csv
import os
from datetime import datetime, timezone

# Exness pip sizes
PIP_SIZES = {
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "AUDUSD": 0.0001,
    "NZDUSD": 0.0001,
    "USDCAD": 0.0001,
    "USDCHF": 0.0001,
    "USDJPY": 0.01,
    "XAUUSD": 0.01,
}

TRADES_FILE = "/Users/parthbhandakkar/Desktop/WorkZera/Projects/TradeBot/ytLearning copy/backtester-app/strategies/s97_all_pairs_trades.json"
DATA_ROOT = "/Users/parthbhandakkar/Desktop/WorkZera/Projects/TradeBot/ytLearning copy/backtester-app/strategies/data"

print("Loading s97 all-pairs trades...")
with open(TRADES_FILE) as f:
    all_trades = json.load(f)

results = {}

def _ts(raw):
    if not raw:
        return None
    return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())

for pair in sorted(all_trades.keys()):
    pip = PIP_SIZES[pair]
    trades_list = all_trades[pair]
    
    print(f"\n{'='*60}")
    print(f"=== {pair} (pip={pip}) — {len(trades_list)} trades ===")
    
    # Load 4h CSV for this pair
    csv_path = os.path.join(DATA_ROOT, pair, "4h", f"{pair}_4h.csv")
    if not os.path.exists(csv_path):
        print(f"  No 4h data at {csv_path}, trying 1h...")
        csv_path = os.path.join(DATA_ROOT, pair, "1h", f"{pair}_1h.csv")
    if not os.path.exists(csv_path):
        print(f"  NO DATA for {pair}, skipping")
        continue
    
    candles = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(float(row["time"]))
            candles.append({
                "timestamp": ts,
                "high": float(row["high"]),
                "low": float(row["low"]),
            })
    ts_list = [c["timestamp"] for c in candles]
    print(f"  Loaded {len(candles)} candles from {csv_path}")
    
    def find_idx(ts):
        for i, t in enumerate(ts_list):
            if t >= ts:
                return i
        return len(ts_list) - 1
    
    # Analyze for targets: $1.00 and $2.00
    # $1.00 = 100 pips for XAUUSD, but for other pairs we use native pips
    # Two analysis approaches:
    
    # Custom pip targets equivalent to XAUUSD 100p / 200p with 2000x leverage
    EQ_TARGETS = {
        "EURUSD": (3, 6),
        "GBPUSD": (3.5, 7),
        "AUDUSD": (2, 4),
        "NZDUSD": (1.5, 3),
        "USDCAD": (3.5, 7),
        "USDCHF": (2.5, 5),
        "USDJPY": (5, 10),
        "XAUUSD": (100, 200),
    }
    t1, t2 = EQ_TARGETS.get(pair, (100, 200))
    
    results[pair] = {}
    
    for label, target_pips in [("target_1x", t1), ("target_2x", t2)]:
        target_price = target_pips * pip  # absolute price movement needed
        
        qualifying = []
        never_reached = 0
        never_reached_list = []
        no_data = 0
        
        for t in trades_list:
            entry_ts = _ts(t.get("entry_time"))
            if entry_ts is None:
                no_data += 1
                continue
            
            entry_price = float(t["entry_price"])
            direction = t["direction"]
            
            entry_idx = find_idx(entry_ts)
            if entry_idx >= len(candles):
                no_data += 1
                continue
            
            # Find first bar where target is reached
            qual_bar_idx = None
            for j in range(entry_idx, len(candles)):
                c = candles[j]
                if direction == "long":
                    if c["high"] >= entry_price + target_price:
                        qual_bar_idx = j
                        break
                else:
                    if c["low"] <= entry_price - target_price:
                        qual_bar_idx = j
                        break
            
            if qual_bar_idx is None:
                never_reached += 1
                never_reached_list.append(t)
                continue
            
            # Compute max adverse movement BEFORE reaching target
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
            
            adverse_pips = adverse_price / pip
            
            qualifying.append({
                "adverse_pips": adverse_pips,
                "adverse_price": adverse_price,
                "outcome": t["outcome"],
                "pnl_R": t.get("pnl_R", None),
                "trade_number": t["trade_number"],
                "entry_price": t["entry_price"],
                "exit_price": t.get("exit_price", None),
                "direction": direction,
            })
        
        if not qualifying:
            print(f"  {label} ({target_pips}p): ZERO qualifying trades (need {target_price:.5f} price move)")
            results[pair][label] = {
                "target_pips": target_pips,
                "target_price": target_price,
                "total": len(trades_list),
                "qualifying": 0,
                "never_reached": never_reached,
                "min_stop_60pct": None,
            }
            continue
        
        # Find min stop for >60% survival
        adv_vals = sorted([q["adverse_pips"] for q in qualifying])
        min_stop = None
        for thresh in range(1, 100001):
            survive = sum(1 for q in qualifying if q["adverse_pips"] < thresh)
            total = len(qualifying)
            wr = survive / total * 100
            if wr > 60:
                min_stop = thresh
                break
        
        # Stats
        median_adv = adv_vals[len(adv_vals)//2]
        p25 = adv_vals[int(len(adv_vals)*0.25)]
        p75 = adv_vals[int(len(adv_vals)*0.75)]
        p10 = adv_vals[int(len(adv_vals)*0.10)]
        
        # $ equivalent of the target in price terms
        eq_dollar = target_price  # absolute price movement
        
        ms_str = f"{min_stop}p (${min_stop*pip:.4f})" if min_stop else "N/A"
        print(f"  {label} ({target_pips}p = ${target_price:.4f}): "
              f"qualifying={len(qualifying)}/{len(trades_list)} "
              f"never={never_reached} "
              f"min_stop={ms_str}")
        print(f"    median_adv={median_adv:.1f}p p25={p25:.1f}p p75={p75:.1f}p")
        
        # Show non-qualifying trades
        if never_reached_list:
            print(f"    Trades that NEVER reached target ({len(never_reached_list)}):")
            for nt in never_reached_list:
                entry = float(nt["entry_price"])
                exit_p = float(nt.get("exit_price", entry))
                if nt["direction"] == "long":
                    actual_move = exit_p - entry
                else:
                    actual_move = entry - exit_p
                actual_pips = actual_move / pip
                print(f"      #{nt['trade_number']} {nt['direction']:>5} entry={entry:.5f} "
                      f"exit={exit_p:.5f} actual_move={actual_pips:.1f}p "
                      f"outcome={nt['outcome']} pnl_R={nt.get('pnl_R','?')}")
        
        results[pair][label] = {
            "pip": pip,
            "target_pips": target_pips,
            "target_price": target_price,
            "total": len(trades_list),
            "qualifying": len(qualifying),
            "never_reached": never_reached,
            "min_stop_pips": min_stop,
            "min_stop_price": min_stop * pip if min_stop else None,
            "median_adverse_pips": round(median_adv, 1),
            "p25_adverse_pips": round(p25, 1),
            "p75_adverse_pips": round(p75, 1),
            "p10_adverse_pips": round(p10, 1),
        }

# Summary
print(f"\n\n{'='*90}")
print(f"{'PAIR':>8} {'PIP':>8} {'TGT(p)':>8} {'TGT($)':>10} {'QUAL':>6} {'TOTAL':>6} {'%':>6} {'MIN_STOP(p)':>12} {'MIN_STOP($)':>12} {'MED_ADV(p)':>12}")
print(f"{'-'*90}")
for pair in sorted(results.keys()):
    pip = PIP_SIZES[pair]
    for label in ["target_1x", "target_2x"]:
        r = results[pair][label]
        qual_pct = r["qualifying"] / r["total"] * 100 if r["total"] else 0
        ms = r["min_stop_pips"]
        ms_str = f"{ms}" if ms else "N/A"
        msd = f"${ms*pip:.4f}" if ms else "N/A"
        print(f"{pair:>8} {pip:>8} {r['target_pips']:>4}p {r['target_price']:>10.4f} "
              f"{r['qualifying']:>6} {r['total']:>6} "
              f"{qual_pct:>5.1f}% {ms_str:>12} {msd:>12} {r['median_adverse_pips']:>12.1f}")
