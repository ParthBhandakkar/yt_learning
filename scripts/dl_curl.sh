#!/bin/bash
# Robust Google Drive downloader (handles large-file confirm token)
DATA="$(cd "$(dirname "$0")" && pwd)/data"
dl() {
  local id="$1" out="$2"
  mkdir -p "$(dirname "$out")"
  if [ -s "$out" ]; then echo "SKIP $out ($(wc -c <"$out") bytes)"; return; fi
  echo "GET $out"
  # first attempt (works for small/medium files)
  curl -sL -c /tmp/gck.txt "https://drive.google.com/uc?export=download&id=$id" -o "$out"
  # if we got an HTML interstitial (large file), extract confirm token + uuid
  if head -c 15 "$out" | grep -qi "<!DOCTYPE\|<html"; then
    echo "  large file: resolving confirm token"
    local page; page=$(cat "$out")
    local uuid; uuid=$(echo "$page" | grep -o 'name="uuid" value="[^"]*"' | sed 's/.*value="//;s/"//')
    local cf;   cf=$(echo "$page" | grep -o 'name="confirm" value="[^"]*"' | sed 's/.*value="//;s/"//')
    curl -sL -b /tmp/gck.txt \
      "https://drive.usercontent.google.com/download?id=$id&export=download&confirm=${cf:-t}&uuid=$uuid" \
      -o "$out"
  fi
  echo "  DONE $(wc -c <"$out") bytes"
}

dl 1IZp86Ml3_Bggg8SB_HsLtwDT7wBump3s "$DATA/XAUUSD/5m/XAUUSD_5m_2021-07-02_2026-07-01.csv"
dl 1q-8BGa182ki16uruEWZYre2owmQvyM7g "$DATA/EURUSD/5m/EURUSD_5m_1999-01-04_2026-07-01.csv"
dl 15muKybRfO4dIechoc4QZybb_05y-oidw "$DATA/AUDUSD/5m/AUDUSD_5m_2021-06-14_2026-07-01.csv"
dl 1sOe0aTKsHLn-IOItGuKYR1H_lcSo2T-j "$DATA/NZDUSD/5m/NZDUSD_5m_2021-07-02_2026-07-01.csv"
dl 17BSKoWxRJCbhu80DyO9fAsWiQ2w2d-N_ "$DATA/USDCAD/5m/USDCAD_5m_2021-06-14_2026-07-01.csv"
dl 19m6kqBiSA0mTrjXmRZ_KEeZQMlvbfE6W "$DATA/USDCHF/5m/USDCHF_5m_2021-06-14_2026-07-01.csv"
dl 15JIkWOUt_z7mran8eh4uI7ow34jYiopf "$DATA/USDJPY/5m/USDJPY_5m_2021-06-14_2026-07-01.csv"
dl 1BNxSGDcM4mijxCbmTLgqG0PKNNLDGTet "$DATA/XAUUSD/1m/XAUUSD_1m_2021-07-02_2026-07-01.csv"
echo "ALL DONE"
