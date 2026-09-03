#!/usr/bin/env python3
"""Aggregate all completed bt_results/*_trades.json into one ranked table."""
import json, glob, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bt_metrics import compute_metrics

NAMES = {
 "s01":"Orderflow Volume Profile","s04":"Gold FRVP scalping","s05":"Macro VP + ICT",
 "s06":"Fractal inversion","s08":"VP auction breakout","s13":"3-step ICT gold (needs silver)",
 "s17":"1H pattern (1H/1M)","s28":"Multi-TF iFVG","s29":"TBV core (80% claim)",
 "s31":"10AM PO3","s39":"Mechanical 2-day bias","s42":"8AM one candle",
 "s44":"Lazy liquidity ORB","s52":"Draw on liquidity","s54":"Liquidity range",
 "s56":"Midas scalping","s62":"Trend continuation purge","s65":"US30 Judas (no US30 data)",
 "s69":"ICT Market Maker","s77":"Simple ICT liquidity","s78":"Easy ICT Judas","s81":"ICT PO3 1m scalp",
}

rows=[]
for f in sorted(glob.glob("bt_results/s*_trades.json")):
    sid=os.path.basename(f).split("_")[0]
    try:
        trades=json.load(open(f))
    except Exception:
        continue
    if not isinstance(trades,list): trades=[trades]
    m=compute_metrics(trades)
    # TP/SL hit accounting
    tp=sl=0
    for t in trades:
        ep=t.get("exit_price")
        if ep is None: continue
        if abs(ep-t.get("take_profit",1e18))<1e-6: tp+=1
        elif abs(ep-t.get("stop_loss",1e18))<1e-6: sl+=1
    m["tp_hits"]=tp; m["sl_hits"]=sl
    m["eod_or_other"]=m["closed_trades"]-tp-sl
    m["id"]=sid; m["name"]=NAMES.get(sid,sid)
    rows.append(m)

rows.sort(key=lambda r:(r.get("total_R") or -9e9), reverse=True)
print(f"{'id':>4} {'trades':>6} {'win%':>5} {'expR':>7} {'totalR':>8} {'PF':>6} {'ddR':>7} {'TP':>5} {'SL':>5} {'EOD':>6}  name")
for r in rows:
    print(f"{r['id']:>4} {r['closed_trades']:>6} {r['win_rate']:>5} {r['expectancy_R']:>7} "
          f"{r['total_R']:>8} {str(r['profit_factor']):>6} {r['max_drawdown_R']:>7} "
          f"{r['tp_hits']:>5} {r['sl_hits']:>5} {r['eod_or_other']:>6}  {r['name']}")
json.dump(rows, open("bt_results/aggregate.json","w"), indent=1, default=str)
