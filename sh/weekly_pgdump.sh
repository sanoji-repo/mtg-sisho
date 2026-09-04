#!/bin/bash
# weekly_pgdump.sh — rag_dev の論理バックアップ（週次・日曜 00:00 JST・cron から）（2026-08-19）
# =========================================================================
# 背景: 2026-08-19 に WAL アーカイブが 50GB（6/16 から未掃除・ベースバックアップ無し＝復元不能な倉庫）
#       と判明。「足りなかったのは回数でなく形」＝軽い論理 dump を定期の仕組みに乗せる。
# 方式: ホストの pg_dump は 17（サーバ 18 に不可）→ コンテナ内 pg_dump 18 を docker exec し
#       stdout をホストの棚へ。ACCESS SHARE ロックのみ＝通常の読み書きと競合しない（DDL は待つ）。
#       0:00 は夜間便（03:00〜10:00）の窓の外。REPEATABLE READ スナップショット＝開始時点の一貫像。
# 出力: /mnt/new_hdd/db_archives/rag_dev_full_YYYYMMDD.dump（-Fc -Z6・約 200MB）
# 世代: 最新 KEEP 本を残して古い rag_dev_full_*.dump を削除（既定 4）
# 検収: pg_restore --list の TABLE DATA 件数が MIN_TABLES 未満なら警告して非ゼロ終了（dump は残す）
# 復元: docker exec -i pg18-primary pg_restore -h localhost -U devuser -d rag_dev < <dump>
# cron: 0 0 * * 0 /mnt/mtg_rag/sh/weekly_pgdump.sh >> /tmp/weekly_pgdump.out 2>&1
set -u
REPO=/mnt/mtg_rag
DEST=/mnt/new_hdd/db_archives
KEEP="${PGDUMP_KEEP:-4}"
MIN_TABLES="${PGDUMP_MIN_TABLES:-20}"
CONTAINER="${PGDUMP_CONTAINER:-pg18-primary}"
STAMP=$(date +%Y%m%d)
OUT="$DEST/rag_dev_full_${STAMP}.dump"
LOG="$DEST/weekly_pgdump.log"
PY=/mnt/new_hdd/my_rag_env/bin/python

log(){ echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
mkdir -p "$DEST"
PW=$($PY -c "import sys; sys.path.insert(0,'$REPO/src'); from db_config import DB_CONFIG; print(DB_CONFIG['password'])") || { log "ERROR: DB_CONFIG 読めず"; exit 1; }

log "start → $OUT"
t0=$(date +%s)
if ! docker exec -e PGPASSWORD="$PW" "$CONTAINER" pg_dump -Fc -Z 6 -h localhost -U devuser rag_dev > "$OUT" 2>>"$LOG"; then
  log "ERROR: pg_dump 失敗（rc=$?）"; rm -f "$OUT"; exit 1
fi
SIZE=$(du -h "$OUT" | cut -f1)
N=$(docker exec -i "$CONTAINER" pg_restore --list < "$OUT" 2>/dev/null | grep -c "TABLE DATA")
log "done ${SIZE} / $(( $(date +%s)-t0 ))s / TABLE DATA=${N} / sha256=$(sha256sum "$OUT" | cut -c1-16)"
if [ "${N:-0}" -lt "$MIN_TABLES" ]; then
  log "WARN: TABLE DATA が ${MIN_TABLES} 未満（${N}）＝中身を確認すること（dump は残置）"; rc=2
else
  rc=0
fi
# 世代管理（新しい順に KEEP 本残す）
ls -1t "$DEST"/rag_dev_full_*.dump 2>/dev/null | tail -n +$((KEEP+1)) | while read -r old; do
  log "rotate: rm $old"; rm -f "$old"
done
exit $rc
