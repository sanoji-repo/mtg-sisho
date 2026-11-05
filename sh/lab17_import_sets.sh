#!/usr/bin/env bash
# lab17_import_sets.sh — 17Lands Public Datasets（CC BY 4.0）PremierDraft の全セット搬入
#
# 一覧: sh/17lands_premier_sets.tsv（expansion \t last_updated \t draft_url \t game_url）。
#   17Lands の Public Datasets のページに載っている S3 の公開ファイルだけを対象にする
#   （Web の集計ページ・JSON API は叩かない＝docs/DATA_SOURCES.md の方針）。
# 1 セット: S3 から gz を落とす（既にあれば再利用）→ src/lab17_trial.py で集計 →
#   public.limited_card_stats へ UPSERT → src/lab17_extra_stats.py で追加 5 表 →
#   報告を $L17_REPORT_DIR（既定 $LOG_DIR/17lands）/trial_<SET>.md へ。
# 冪等: 段ごとに済みを判定する（基本集計＝limited_card_stats・追加 5 表＝limited_card_pick_stats）。
#   基本が済んだ後に追加が失敗した場合、セット全体を飛ばすと追加だけ永久に空のまま残るため。
#   引数にセット名を並べるとそれだけ（試運転用）。
# 表が無い初回は --migrate で作る（脚本は通常運転では DDL を打たない）。
# 生の CSV は再配布しない（DB に入れるのは集計値だけ・帰属は 17Lands https://www.17lands.com/）。
set -u
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
PYBIN=${PYBIN:-python3}
DIR=${L17_DIR:-${MTG_DATA_DIR:-$REPO/data}/17lands}
# セットの一覧の正本は状態の置き場の物（毎日の見張りが書き足す）。リポジトリの sh/17lands_premier_sets.tsv は
# 初めて作るときの見本＝一覧がまだ無いときだけ写す。
LIST=${L17_LIST:-${STATE_DIR:-$REPO/state}/17lands_premier_sets.tsv}
LOG=${L17_LOG:-${LOG_DIR:-$REPO/logs}/lab17_import.log}
REPORT_DIR=${L17_REPORT_DIR:-${LOG_DIR:-$REPO/logs}/17lands}
# DB は開発側（書き込みがあるので bin/dbq の読み取り専用ロールは使わない）
PSQL=${L17_PSQL:-"docker exec pg18-primary psql -U devuser -d rag_dev"}
ts() { date '+%Y-%m-%d %H:%M:%S'; }
cd "$REPO" || exit 1
# 置き場と一覧の用意に失敗したら、取り込み 0 件の「成功」で終わらせない（下の n_fail の判定より前に止める）
die() { echo "[$(ts)] 中止: $*" >> "$LOG" 2>/dev/null; echo "lab17_import_sets: $*" >&2; exit 1; }
mkdir -p "$DIR" "$(dirname "$LOG")" "$REPORT_DIR" "$(dirname "$LIST")" || die "置き場を作れません"
if [ ! -e "$LIST" ]; then
  seed="$REPO/sh/17lands_premier_sets.tsv"
  [ -f "$seed" ] || die "一覧の見本がありません: $seed"
  # 一時のファイルに写してから入れ替える（書きかけの一覧を正本として残さない）
  tmp="$LIST.tmp.$$"
  { cp "$seed" "$tmp" && mv "$tmp" "$LIST"; } || { rm -f "$tmp"; die "一覧を作れません: $LIST"; }
fi
{ [ -f "$LIST" ] && [ -r "$LIST" ]; } || die "一覧が読めません: $LIST"

only="$*"
done_basic=$($PSQL -Atc \
  "SELECT DISTINCT expansion FROM public.limited_card_stats WHERE event_type='PremierDraft'" 2>/dev/null)
done_extra=$($PSQL -Atc \
  "SELECT DISTINCT expansion FROM public.limited_card_pick_stats WHERE event_type='PremierDraft'" 2>/dev/null)

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
  have_basic=0; have_extra=0
  echo "$done_basic" | grep -qx "$code" && have_basic=1
  echo "$done_extra" | grep -qx "$code" && have_extra=1
  if [ "$have_basic" = 1 ] && [ "$have_extra" = 1 ]; then
    echo "[$(ts)] skip（両方の段が DB に有）: $code" >> "$LOG"; n_skip=$((n_skip+1)); continue
  fi
  echo "[$(ts)] === $code（$upd）===" >> "$LOG"
  if ! dl "$gurl" "$DIR/$(basename "$gurl")" || ! dl "$durl" "$DIR/$(basename "$durl")"; then
    echo "[$(ts)] FAILED download: $code" >> "$LOG"; n_fail=$((n_fail+1)); continue
  fi
  # 報告の書き出し先は器が決めて渡す（日付を双方で別々に求めると 0 時に食い違う）
  if [ "$have_basic" = 0 ]; then
    # pandas は追加の包みの置き場（MTG_PYLIBS）にある＝集計の 2 本にだけ読み込み先として足す
    if PYTHONPATH="src${MTG_PYLIBS:+:$MTG_PYLIBS}${PYTHONPATH:+:$PYTHONPATH}" $PYBIN src/lab17_trial.py --set "$code" --event PremierDraft --dir "$DIR" \
         --migrate --out "$REPORT_DIR/trial_$code.md" >> "$LOG" 2>&1; then
      echo "[$(ts)] done: $code" >> "$LOG"; n_ok=$((n_ok+1))
    else
      echo "[$(ts)] FAILED compute: $code" >> "$LOG"; n_fail=$((n_fail+1)); continue
    fi
  else
    echo "[$(ts)] 基本集計は済み: $code" >> "$LOG"
  fi
  # 追加 5 表（color/matchup/format/rank/pick）。段を分けて判定するので、ここだけ
  # 失敗した場合は次回この段からやり直せる（HOB で 0 行のまま残った穴）
  if [ "$have_extra" = 0 ]; then
    if PYTHONPATH="src${MTG_PYLIBS:+:$MTG_PYLIBS}${PYTHONPATH:+:$PYTHONPATH}" $PYBIN src/lab17_extra_stats.py --set "$code" --event PremierDraft \
         --dir "$DIR" --migrate >> "$LOG" 2>&1; then
      echo "[$(ts)] extra done: $code" >> "$LOG"
    else
      echo "[$(ts)] FAILED extra: $code" >> "$LOG"; n_fail=$((n_fail+1))
    fi
  fi
done < "$LIST"
echo "[$(ts)] === 搬入終了 ok=$n_ok skip=$n_skip fail=$n_fail ===" >> "$LOG"
$PSQL -c \
  "SELECT expansion, count(*) AS cards, sum(gih_games) AS gih_games, count(*) FILTER (WHERE NOT in_cards_v2) AS unmatched FROM public.limited_card_stats GROUP BY expansion ORDER BY expansion" >> "$LOG" 2>&1
# 失敗があれば器も失敗で終わる（最後の一覧 SQL の成功で隠さない）
[ "$n_fail" -eq 0 ] || { echo "[$(ts)] 失敗 $n_fail 件あり（exit 1）" >> "$LOG"; exit 1; }
