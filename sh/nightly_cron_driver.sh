#!/usr/bin/env bash
# nightly_cron_driver.sh — cron 起点の夜間ジョブドライバ
# ============================================================================
# 仕様:
#   - cron が毎日 03:00 JST に起動（別の置き場で動かすときは、その入口がこの脚本を呼ぶ）。VM が点いていない夜は cron ごと沈黙＝自然に不発
#     （3時に電源が入っている場合のみ始動、の指示をそのまま cron の性質で実現）。
#   - 1 パス = MTGTop8・MTGO・Moxfield の 3 レーンを並行起動し（空文字で無効にしたレーンは起動しない）、全部の完了を待つ。
#   - パスが終わってもまだ 9 時台なら、休憩（既定 10 分）を挟んで新パスを起動し続ける。
#   - 10:00 になったら新規起動をやめる（走行中のパスは殺さず自然完了に任せる。
#     各ジョブには nightly_scrape.sh 側の timeout 6h が掛かっている）。
#   - スクレイパーの取得済みスキップにより、続行パスは差分だけ拾う
#     （新規ゼロのパスは MTGTop8=フォーマットあたり 1 リクエスト・
#       Moxfield=一覧ページのみで個別デッキ取得ゼロ）。
#
# ジョブ列の変更: 下の JOBS_TOP8 / JOBS_MOX / JOBS_MTGO の既定値を編集する
#   （meta コードは年替わりで変わる。2026: ST=341 PI=340 MO=339 LE=338 VI=337 PAU=342・
#     EDH=343 は設計判断によりスキップ）。
#
# テスト用の環境変数（通常運用では未設定でよい）:
#   NIGHTLY_END_HOUR   この時になったら新規起動をやめる（既定 10）
#   NIGHTLY_INTERVAL   パス間の休憩秒（既定 600）
#   NIGHTLY_MAX_PASSES 0=無制限（時刻でのみ終了）・正数でパス数上限
#   NIGHTLY_LOGDIR     レポート/ログの出力先（既定 $LOG_DIR/nightly/<日付>）
#   STATE_DIR・LOG_DIR 状態とログの置き場（既定 $REPO/state・$REPO/logs）
#   PYTHONPATH         受け継いだ値は子の処理にもつなぐ（上書きしない）
#   MTG_PYLIBS         pandas などを置いた追加の包みの置き場。共起の集計にだけ読み込み先として足す
#                      （取得の脚本には足さない＝通信の部品 urllib3・idna の版が venv と入れ替わるため）
#   NIGHTLY_JOBS_TOP8 / NIGHTLY_JOBS_MOX / NIGHTLY_JOBS_MTGO  ジョブ列の差し替え（空文字でレーン無効化）
#   NIGHTLY_LANE_REPO_TOP8 / _MTGO / _MOX  その筋をどのリポジトリの sh/nightly_scrape.sh で動かすか（既定はこのドライバと同じ）
#   NIGHTLY_POST_REPO  後段（共起・毎日の監視）をどのリポジトリの脚本で動かすか（既定は同じ・一か所で選ぶ＝二回走らせない）
#                      筋ごと・後段で別の版の脚本を使い分けるための口
#   NIGHTLY_DRY_RUN=1  ロックも取らずログも書かず、何をどこから起動するかだけを出して終わる
#   NIGHTLY_DAY_OF_MONTH  月初の判定に使う日（試験用・既定は今日）
# ============================================================================
set -u

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
NIGHT=$(date +%Y%m%d)
# 状態とログの置き場（既定はリポジトリの中・別の置き場で動かすときは入口が渡す）。子の脚本にも同じ値を渡す
STATE_DIR="${STATE_DIR:-$REPO/state}"; LOG_DIR="${LOG_DIR:-$REPO/logs}"; export STATE_DIR LOG_DIR
LOGDIR="${NIGHTLY_LOGDIR:-$LOG_DIR/nightly/$NIGHT}"
END_HOUR="${NIGHTLY_END_HOUR:-10}"
INTERVAL="${NIGHTLY_INTERVAL:-600}"
MAX_PASSES="${NIGHTLY_MAX_PASSES:-0}"
JOBS_TOP8="${NIGHTLY_JOBS_TOP8-mtgtop8:ST:341 mtgtop8:PI:340 mtgtop8:MO:339 mtgtop8:LE:338 mtgtop8:VI:337 mtgtop8:PAU:342}"
# per-bracket 上限は 2500:
# 300 では Moxfield の在庫切れでなく自分で決めたバケット上限で頭打ちになる（bracket 2〜5 が
# 揃って 300 で満杯＝早期打ち切り）。1000 でも上位を取り切ってしまい、大半の回が新規 0 件で空回りする。
# 天井は Moxfield 側の totalResults=10000 キャップ（bracket 1 の 31 件はそちらの本物の枯れ）。
# 1 万件の窓の底でも views・likes が低い＝窓の中を取り切るのが足切りを兼ねる。
JOBS_MOX="${NIGHTLY_JOBS_MOX-moxfield:2,3,4,5:2500}"
# MTGO 公式デッキリスト。今月分を毎晩差分取得。月初は前月の
# 取りこぼしも拾うため 1〜3 日は前月も並べる（cur と prev の 2 ジョブ・同一レーン直列）
JOBS_MTGO="${NIGHTLY_JOBS_MTGO-mtgo:cur:all}"
# 前月分は MTGO の筋が有効なときだけ足す（無効にした筋が月初だけ走るのを防ぐ）
DOM="${NIGHTLY_DAY_OF_MONTH:-$(date +%d)}"
if [ -n "$JOBS_MTGO" ] && [ "$((10#$DOM))" -le 3 ]; then JOBS_MTGO="mtgo:$(date -d 'last month' +%Y-%m):all $JOBS_MTGO"; fi

# 筋ごと・後段の起動元（既定はこのドライバと同じリポジトリ）
LANE_REPO_TOP8="${NIGHTLY_LANE_REPO_TOP8:-$REPO}"
LANE_REPO_MTGO="${NIGHTLY_LANE_REPO_MTGO:-$REPO}"
LANE_REPO_MOX="${NIGHTLY_LANE_REPO_MOX:-$REPO}"
POST_REPO="${NIGHTLY_POST_REPO:-$REPO}"
bad=""
for r in "$LANE_REPO_TOP8" "$LANE_REPO_MTGO" "$LANE_REPO_MOX"; do [ -f "$r/sh/nightly_scrape.sh" ] || bad="$bad $r"; done
[ -f "$POST_REPO/src/build_cooccurrence.py" ] && [ -f "$POST_REPO/src/daily_set_watch.py" ] || bad="$bad $POST_REPO（後段）"

if [ "${NIGHTLY_DRY_RUN:-0}" = 1 ]; then
  echo "ドライバ: $REPO"
  echo "ログ: $LOGDIR"
  plan() { if [ -n "$3" ]; then echo "$1: $2/sh/nightly_scrape.sh $3"; else echo "$1: （無効）"; fi; }
  plan top8 "$LANE_REPO_TOP8" "$JOBS_TOP8"
  plan mtgo "$LANE_REPO_MTGO" "$JOBS_MTGO"
  plan mox "$LANE_REPO_MOX" "$JOBS_MOX"
  echo "後段: $POST_REPO/src/build_cooccurrence.py → $POST_REPO/src/daily_set_watch.py"
  [ -n "$bad" ] && { echo "起動元が見つからない:$bad"; exit 1; }
  exit 0
fi

mkdir -p "$LOGDIR"
DLOG=$LOGDIR/driver.log

exec 8>/tmp/nightly_cron_driver.lock
flock -n 8 || { echo "[$(date '+%F %T')] driver 二重起動を検出・中止" >> "$DLOG"; exit 1; }

dlog() { echo "[$(date '+%F %T')] $*" >> "$DLOG"; }

if [ -n "$bad" ]; then dlog "起動元が見つからないので中止:$bad"; exit 1; fi

dlog "=== 夜間ジョブドライバ開始（${END_HOUR}:00 以降は新規起動なし・休憩 ${INTERVAL}s）==="
dlog "TOP8 ジョブ列: ${JOBS_TOP8:-（無効）}"
dlog "MOX  ジョブ列: ${JOBS_MOX:-（無効）}"
dlog "MTGO ジョブ列: ${JOBS_MTGO:-（無効）}"
dlog "起動元: top8=$LANE_REPO_TOP8 mtgo=$LANE_REPO_MTGO mox=$LANE_REPO_MOX 後段=$POST_REPO（ドライバ $REPO）"

pass=0
while :; do
  h=$((10#$(date +%H)))
  if [ "$h" -ge "$END_HOUR" ]; then
    dlog "時刻 ${h}時 >= ${END_HOUR}時 → 新規起動を終了"
    break
  fi

  pass=$((pass+1))
  dlog "--- パス $pass 起動 ---"
  pids=() names=()
  if [ -n "$JOBS_TOP8" ]; then
    NIGHTLY_LANE=top8 NIGHTLY_LOGDIR="$LOGDIR" "$LANE_REPO_TOP8/sh/nightly_scrape.sh" $JOBS_TOP8 &
    pids+=($!); names+=(top8)
  fi
  if [ -n "$JOBS_MTGO" ]; then
    NIGHTLY_LANE=mtgo NIGHTLY_LOGDIR="$LOGDIR" "$LANE_REPO_MTGO/sh/nightly_scrape.sh" $JOBS_MTGO &
    pids+=($!); names+=(mtgo)
  fi
  if [ -n "$JOBS_MOX" ]; then
    NIGHTLY_LANE=mox NIGHTLY_LOGDIR="$LOGDIR" "$LANE_REPO_MOX/sh/nightly_scrape.sh" $JOBS_MOX &
    pids+=($!); names+=(mox)
  fi
  if [ "${#pids[@]}" -eq 0 ]; then
    dlog "有効なレーンなし・終了"
    break
  fi
  i=0
  for pid in "${pids[@]}"; do
    wait "$pid"
    dlog "--- パス $pass レーン ${names[$i]} 完了 rc=$?"
    i=$((i+1))
  done

  if [ "$MAX_PASSES" -gt 0 ] && [ "$pass" -ge "$MAX_PASSES" ]; then
    dlog "MAX_PASSES=$MAX_PASSES 到達（テスト用上限）・終了"
    break
  fi
  h=$((10#$(date +%H)))
  if [ "$h" -ge "$END_HOUR" ]; then
    dlog "時刻 ${h}時 >= ${END_HOUR}時 → 休憩せず終了"
    break
  fi
  dlog "休憩 ${INTERVAL}s → 次パスへ"
  sleep "$INTERVAL"
done

# 共起（card_cooccurrence_v2）: フォーマット × 区間（直近 90 日・全期間）の 18 母集団を
# 1 つのスナップショットで作り、検算して母集団ごとに組・分母・統計を 1 トランザクションで差分適用する
# （src/build_cooccurrence.py）。検算で止めた母集団は前の完成版のまま残る。
# 失敗しても夜間ジョブ全体は失敗扱いにしない（共起は検索本線でなく壁打ち道具箱の材料）。
dlog "--- 共起 v2（母集団）開始 ---"
PYTHONPATH="$POST_REPO/src${MTG_PYLIBS:+:$MTG_PYLIBS}${PYTHONPATH:+:$PYTHONPATH}" "${PYBIN:-python3}" "$POST_REPO/src/build_cooccurrence.py" >> "$LOGDIR/cooccurrence.log" 2>&1
dlog "--- 共起 v2（母集団）完了 rc=$? ---"

# Scryfall・17Lands の毎日の監視（src/daily_set_watch.py）:
# 毎日無条件に oracle_cards bulk（24MB 級）で冪等同期＝新セット・アルケミー・禁止改定が翌朝に入る。
# /sets 1 コールで接近セットと発売直後の日本語名欠けを報告。17Lands は新セットの出現だけ自動で
# 初回の取り込み（取り込み済みセットの更新は報告のみ）。失敗しても夜間ジョブ全体は失敗扱いにしない。
dlog "--- 毎日監視（Scryfall/17Lands）開始 ---"
"${PYBIN:-python3}" "$POST_REPO/src/daily_set_watch.py" >> "$LOGDIR/set_watch.log" 2>&1
dlog "--- 毎日監視 完了 rc=$? ---"

dlog "=== ドライバ終了（総パス $pass）==="
exit 0
