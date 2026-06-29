import json, subprocess, sys, time, os
sys.path.insert(0, '.')
from core import enrich_trades_pnl
from bt_metrics import compute_metrics

PY = ".venv312/bin/python"
M1 = "data/XAUUSD/1m_1y/XAUUSD_1m.csv"
M5 = "data/XAUUSD/5m_1y/XAUUSD_5m.csv"
M15 = "data/XAUUSD/15m_1y/XAUUSD_15m.csv"
H1 = "data/XAUUSD/1h/XAUUSD_1h_2021-03-02_2026-04-22.csv"

JOBS = {
 "s29": ("strategy_29_tbv_core.py", ["--csv", M5]),
 "s56": ("strategy_56_midas_scalping.py", ["--csv15m", M15, "--csv1m", M1]),
 "s52": ("strategy_52_draw_on_liquidity.py", ["--csv15m", M15, "--csv1m", M1]),
 "s81": ("strategy_81_ict_po3_1m_scalping.py", ["--csv1h", H1, "--csv1m", M1]),
 "s28": ("strategy_28_multitf_ifvg.py", ["--csv1h", H1, "--csv1m", M1]),
}
for sid,(script,args) in JOBS.items():
    out=f"bt_results/{sid}_1y.json"
    cmd=[PY,script]+args+["--output",out]
    t0=time.time()
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=900)
    except subprocess.TimeoutExpired:
        print(f"{sid}: TIMEOUT(900s on 1y window)",flush=True); continue
    secs=round(time.time()-t0)
    if r.returncode!=0 or not os.path.exists(out):
        print(f"{sid}: ERROR {(r.stderr or r.stdout)[-200:]}",flush=True); continue
    t=json.load(open(out)); 
    if not isinstance(t,list): t=[t]
    enrich_trades_pnl(t); m=compute_metrics(t)
    print(f"{sid} (1y): trades={m['closed_trades']} win%={m['win_rate']} expR={m['expectancy_R']} "
          f"totalR={m['total_R']} PF={m['profit_factor']} ddR={m['max_drawdown_R']} ({secs}s)",flush=True)
print("DONE_1Y",flush=True)
