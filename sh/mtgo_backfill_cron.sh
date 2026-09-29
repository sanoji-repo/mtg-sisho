#!/usr/bin/env bash
# mtgo_backfill_cron.sh — MTGO 公式デッキリストのバックフィルを 1 日 1 か月ずつ cron で消化する。
#
# 仕組み: state/mtgo_backfill_queue.txt の先頭行「YYYY-MM passN」を 1 行取り出して scrape_mtgo.py --month を走らせ、
#   成功したら行を消す（失敗したら末尾へ回す＝後日やり直し）。pass2＝二周目（取得済み URL は飛ばし、302 で諦めた分だけ拾う・安い）。
#   scrape_mtgo が既に走っていれば（手動実行など）その日はスキップ。レートは scrape_mtgo.py 側（2 秒間隔）で守る。
# 行列を伸ばす: state/mtgo_backfill_queue.txt に行を足すだけ（過去へ遡るほど 302 が多い見込み＝二周目が効く）。
# cron: 0 12 * * *（夜間ジョブ 03:00〜10:00 と重ねない）。ログ: logs/mtgo_backfill_cron.log
set -u
REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
PY="${PYBIN:-python3}"
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
  # 取り込んだ deck_cards の card_id をすぐリンクする（NULL の行だけ・数秒〜数十秒）。
  #   無いと card_id で JOIN する日中の SQL が当日分を取りこぼす。
  "$PY" src/fix_deck_links.py 2>&1 | tail -2 | sed "s/^/[$(ts)] fix_deck_links: /" >> "$LOG"
  # 採用率の分子（card_format_strength／edh_card_strength）と分母（format_deck_counts）も作り直す。
  #   夜間ジョブと同じ直列化ロック（/tmp/recompute_strength.lock）を使う。
  #   ログに残すのは末尾 6 行（head だと例外の型とメッセージが切れ、パイプを早く閉じて BrokenPipeError も招く）。
  flock /tmp/recompute_strength.lock "$PY" src/recompute_card_format_strength.py 2>&1 | tail -6 | sed "s/^/[$(ts)] recompute: /" >> "$LOG"
  echo "[$(ts)] done $month $pass" >> "$LOG"
  $PY src/scrape_mtgo.py --status >> "$LOG" 2>&1 || true
else
  # 失敗した行は末尾へ回す（空の一覧を返し続ける月が行列全体を止めないように・その月は後でまた来る）
  { tail -n +2 "$Q"; echo "$line"; } > "$Q.tmp" && mv "$Q.tmp" "$Q"
  echo "[$(ts)] FAILED $month $pass（行を末尾へ・次は $(head -1 "$Q")）" >> "$LOG"
fi
