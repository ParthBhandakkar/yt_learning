#!/usr/bin/env python3
"""Run strategy 97 on all pairs in the Drive for 4H, log every trade with Entry/Exit/TP/SL."""
import glob
import json
import os
import sys

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
import strategy_97_trend_meanreversion as s97

PAIRS = {
    "GBPUSD": "data/GBPUSD",
    "AUDUSD": "data/AUDUSD",
    "EURUSD": "data/EURUSD",
    "NZDUSD": "data/NZDUSD",
    "USDCAD": "data/USDCAD",
    "USDCHF": "data/USDCHF",
    "USDJPY": "data/USDJPY",
    "XAUUSD": "data/XAUUSD",
}


def find_4h_csv(pair_dir):
    for pat in (
        os.path.join(pair_dir, "4h", "*.csv"),
        os.path.join(pair_dir, "*_4h_*.csv"),
        os.path.join(pair_dir, "*_4h.csv"),
    ):
        files = glob.glob(pat)
        if files:
            return sorted(files, key=len)[0]
    return None


def log_trade(t, pair):
    print(f"[{pair}] Trade #{t['trade_number']:3d} | "
          f"{t['direction'].upper():5s} | "
          f"Entry: {float(t['entry_price']):>10.5f} | "
          f"Exit:  {float(t['exit_price']):>10.5f} | "
          f"SL:    {float(t['stop_loss']):>10.5f} | "
          f"TP:    {float(t['take_profit']):>10.5f} | "
          f"Outcome: {t['outcome']:>10s} | "
          f"PnL: {float(t['pnl_R']):+7.3f}R")


def main():
    all_by_pair = {}
    for pair, pair_dir in PAIRS.items():
        csv_path = find_4h_csv(pair_dir)
        if csv_path is None:
            print(f"[{pair}] No 4H CSV found in {pair_dir}, skipping.")
            continue
        print(f"\n{'='*80}")
        print(f"  {pair} — {csv_path}")
        print(f"{'='*80}")
        df = s97._df_from_csv(csv_path)
        trades = s97.run_strategy(df, f"/tmp/s97_{pair}.json", symbol=pair)
        all_by_pair[pair] = trades
        if not trades:
            print(f"[{pair}] No trades.")
            continue
        print(f"\n{pair} — Trade Log:")
        print(f"{'-'*120}")
        for t in trades:
            log_trade(t, pair)
        print(f"{'-'*120}")
        total_r = sum(float(t["pnl_R"]) for t in trades)
        wins = sum(1 for t in trades if t["outcome"] == "win")
        losses = sum(1 for t in trades if t["outcome"] == "loss")
        print(f"[{pair}] Total: {len(trades)} trades | "
              f"Wins: {wins} | Losses: {losses} | "
              f"Total PnL: {total_r:+.3f}R")

    output_path = os.path.join(THIS, "s97_all_pairs_trades.json")
    with open(output_path, "w") as f:
        json.dump(all_by_pair, f, indent=2, default=str)
    print(f"\nAll trades saved to {output_path}")


if __name__ == "__main__":
    main()
