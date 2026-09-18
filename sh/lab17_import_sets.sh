#!/usr/bin/env bash
# lab17_import_sets.sh — 17Lands Public Datasets（CC BY 4.0）PremierDraft の全セット搬入（2026-08-31）
#
# 一覧: sh/17lands_premier_sets.tsv（expansion \t last_updated \t draft_url \t game_url）。
#   17Lands の Public Datasets のページに載っている S3 の公開ファイルだけを対象にする
#   （Web の集計ページ・JSON API は叩かない＝docs/DATA_SOURCES.md の方針）。
# 1 セット: S3 から gz を落とす（既にあれば再利用）→ src/lab17_trial.py で集計 →
#   public.limited_card_stats へ UPSERT → src/lab17_extra_stats.py で追加 5 表 →
#   報告を logs/17lands/trial_<SET>.md へ移す。
# 冪等: 既に public.limited_card_stats に居るセットは飛ばす。引数にセット名を並べるとそれだけ（試運転用）。
# 生の CSV は再配布しない（DB に入れるのは集計値だけ・帰属は 17Lands https://www.17lands.com/）。
set -u
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
PYBIN=${PYBIN:-python}
DIR=${L17_DIR:-$REPO/data/17lands}
LIST=${L17_LIST:-$REPO/sh/17lands_premier_sets.tsv}
LOG=${L17_LOG:-$REPO/logs/lab17_import.log}
# DB は開発側（書き込みがあるので bin/dbq の読み取り専用ロールは使わない）
PSQL=${L17_PSQL:-"docker exec pg18-primary psql -U devuser -d rag_dev"}
ts() { date '+%Y-%m-%d %H:%M:%S'; }
cd "$REPO" || exit 1
mkdir -p "$DIR" "$(dirname "$LOG")" logs/17lands

only="$*"
done_sets=$($PSQL -Atc \
  "SELECT DISTINCT expansion FROM public.limited_card_stats WHERE event_type='PremierDraft'")

dl() { # dl <url> <path>
  [ -s "$2" ] && { echo "[$(ts)]   再利用: $(basename "$2")" >> "$LOG"; return 0; }
  curl -sS --fail --retry 3 --retry-delay 10 -o "$2.part" "$1" && mv "$2.part" "$2"
}

n_ok=0; n_skip=0; n_fail=0
while IFS=$'\t' read -r exp upd durl gurl; do
  [ -n "$exp" ] || continue
  case "$exp" in \#*) continue;; esac          # 注記の行は飛ばす
  code=$(basename "$gurl"); code=${code#game_data_public.}; code=${code%.PremierDraft.csv.gz}
  if [ -n "$only" ]; then case " $only " in *" $code "*) ;; *) continue;; esac; fi
  if echo "$done_sets" | grep -qx "$code"; then
    echo "[$(ts)] skip（DB に有）: $code" >> "$LOG"; n_skip=$((n_skip+1)); continue
  fi
  echo "[$(ts)] === $code（$upd）===" >> "$LOG"
  if ! dl "$gurl" "$DIR/$(basename "$gurl")" || ! dl "$durl" "$DIR/$(basename "$durl")"; then
    echo "[$(ts)] FAILED download: $code" >> "$LOG"; n_fail=$((n_fail+1)); continue
  fi
  if $PYBIN src/lab17_trial.py --set "$code" --event PremierDraft --dir "$DIR" >> "$LOG" 2>&1; then
    rep="logs/17lands_trial_$(date +%Y%m%d).md"
    [ -f "$rep" ] && mv "$rep" "logs/17lands/trial_$code.md"
    echo "[$(ts)] done: $code" >> "$LOG"; n_ok=$((n_ok+1))
    # 追加 5 表（color/matchup/format/rank/pick）も初回搬入で同時に作る
    # （2026-09-05: 別に走らせる形だと、あるセットだけ 0 行のまま気づかない穴があった）
    if PYTHONPATH=src $PYBIN src/lab17_extra_stats.py --set "$code" --event PremierDraft --dir "$DIR" >> "$LOG" 2>&1; then
      echo "[$(ts)] extra done: $code" >> "$LOG"
    else
      echo "[$(ts)] FAILED extra: $code" >> "$LOG"
    fi
  else
    echo "[$(ts)] FAILED compute: $code" >> "$LOG"; n_fail=$((n_fail+1))
  fi
done < "$LIST"
echo "[$(ts)] === 搬入終了 ok=$n_ok skip=$n_skip fail=$n_fail ===" >> "$LOG"
$PSQL -c \
  "SELECT expansion, count(*) AS cards, sum(gih_games) AS gih_games, count(*) FILTER (WHERE NOT in_cards_v2) AS unmatched FROM public.limited_card_stats GROUP BY expansion ORDER BY expansion" >> "$LOG" 2>&1
