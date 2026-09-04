#!/usr/bin/env bash
# mtgo_backfill_cron.sh — MTGO 公式デッキリストのバックフィルを 1 日 1 か月ずつ cron で消化する（2026-08-22 本人依頼）。
#
# 仕組み: state/mtgo_backfill_queue.txt の先頭行「YYYY-MM passN」を 1 行取り出して scrape_mtgo.py --month を走らせ、
#   成功したら行を消す（失敗したら残す＝翌日やり直し）。pass2＝二周目（取得済み URL は飛ばし、302 で諦めた分だけ拾う・安い）。
#   scrape_mtgo が既に走っていれば（手動便など）その日はスキップ。レートは scrape_mtgo.py 側（2 秒間隔）で守る。
# 行列を伸ばす: state/mtgo_backfill_queue.txt に行を足すだけ（過去へ遡るほど 302 が多い見込み＝二周目が効く）。
# cron: 0 12 * * *（夜間便 03:00〜10:00 と重ねない）。ログ: logs/mtgo_backfill_cron.log
set -u
REPO=/mnt/mtg_rag
PY=/mnt/new_hdd/my_rag_env/bin/python
Q="$REPO/state/mtgo_backfill_queue.txt"
LOG="$REPO/logs/mtgo_backfill_cron.log"
cd "$REPO" || exit 1
ts() { date '+%Y-%m-%d %H:%M:%S'; }
if pgrep -f 'src/scrape_mtgo.py' >/dev/null; then
  echo "[$(ts)] skip: scrape_mtgo.py が走行中" >> "$LOG"; exit 0
fi
[ -s "$Q" ] || { echo "[$(ts)] queue empty" >> "$LOG"; exit 0; }
line=$(head -1 "$Q"); month=${line%% *}; pass=${line##* }
echo "[$(ts)] start $month $pass（残り $(wc -l < "$Q") 行）" >> "$LOG"
if $PY src/scrape_mtgo.py --month "$month" >> "$LOG" 2>&1; then
  tail -n +2 "$Q" > "$Q.tmp" && mv "$Q.tmp" "$Q"
  echo "[$(ts)] done $month $pass" >> "$LOG"
  $PY src/scrape_mtgo.py --status >> "$LOG" 2>&1 || true
else
  echo "[$(ts)] FAILED $month $pass（行は残す・翌日再試行）" >> "$LOG"
fi
