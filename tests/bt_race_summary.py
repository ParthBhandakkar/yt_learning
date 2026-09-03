#!/usr/bin/env python3
"""Final verified summary: target-before-stop race on fine data (1m gold / 5m FX)
plus transaction-cost break-even. Reuses the race engine in bt_target_stop_race.
"""
import json, os
import bt_target_stop_race as R

# Typical Exness-style round-turn cost in NATIVE pips (spread + commission).
# Rough, retail raw-spread ballpark; adjust to your broker's real fills.
TYP_COST = {
    "EURUSD": 0.8, "GBPUSD": 1.0, "AUDUSD": 0.9, "NZDUSD": 1.2,
    "USDCAD": 1.2, "USDCHF": 1.2, "USDJPY": 0.9, "XAUUSD": 3.0,  # gold ~$0.03
}

HERE = os.path.dirname(os.path.abspath(__file__))


def race_counts(pair, target_pips, stop_pips, trades, frames):
    pip = R.PIP[pair]
    tpx, spx = target_pips * pip, stop_pips * pip
    c = {"win": 0, "loss": 0, "ambiguous": 0, "open": 0}
    for t in trades:
        res, _ = R.race_trade(frames, R._ts(t["entry_time"]),
                              float(t["entry_price"]), t["direction"], tpx, spx)
        c[res] += 1
    return c


def line(pair, label, tgt, stop, trades, frames):
    c = race_counts(pair, tgt, stop, trades, frames)
    # treat ambiguous as win (target sits closer -> touched first); tiny anyway
    win = c["win"] + c["ambiguous"]
    loss = c["loss"]
    dec = win + loss
    p = win / dec if dec else 0
    rr = tgt / stop
    exp_R = p * rr - (1 - p)
    gross_exp_pips = p * tgt - (1 - p) * stop      # = break-even cost (pips)
    cost = TYP_COST[pair]
    net_exp_pips = gross_exp_pips - cost
    net_exp_R = net_exp_pips / stop
    flag = "PROFIT" if net_exp_R > 0.005 else ("~flat" if net_exp_R > -0.005 else "LOSS")
    print(f"  {label:8s} tgt={tgt:<5g} stop={stop:<4g} hit={p*100:5.1f}%  "
          f"grossExp={exp_R:+.3f}R  BEcost={gross_exp_pips:4.2f}p  "
          f"typCost={cost:>4.2f}p  netExp={net_exp_R:+.3f}R  [{flag}]")


def main():
    print("VERIFIED target-before-stop race — fine data (XAUUSD=1m, FX=5m)")
    print("BEcost = break-even round-turn cost in pips (edge dies above this).")
    print("=" * 92)
    s97 = json.load(open(os.path.join(HERE, "s97_all_pairs_trades.json")))
    for pair in sorted(s97.keys()):
        frames = R.load_pair_frames(pair)
        print(f"\n{pair}  (finest TF: {frames[0][0]})")
        t1, t2 = R.TARGETS[pair]; s1, s2 = R.STOPS[pair]
        line(pair, "s97-1x", t1, s1, s97[pair], frames)
        line(pair, "s97-2x", t2, s2, s97[pair], frames)

    print("\n" + "=" * 92)
    s98 = json.load(open(os.path.join(HERE, "dashboard", "out",
          "s98_strategy_98_xau_trend_liquidity_trail_20260717_141427.json")))["trades"]
    frames = R.load_pair_frames("XAUUSD")
    print(f"XAUUSD s98 (982 trades, finest TF: {frames[0][0]})")
    line("XAUUSD", "s98-100", 100, 387, s98, frames)
    line("XAUUSD", "s98-200", 200, 507, s98, frames)


if __name__ == "__main__":
    main()
