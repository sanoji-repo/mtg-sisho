#!/usr/bin/env bash
# make_public_dump.sh — 公開サーバー（読み取り専用）用の pg_dump を作る（2026-08-23・開発側＝家の VM で走らせる）
# =========================================================================
# 背景: 公開する MCP の後ろを家の PC から別の公開サーバー（Pi／シンクライアント／VPS）へ出すため、
#       「公開サーバーに要る表だけ」を -Fc で書き出す。開発側だけの表（players・name_ja_manual・card_cooccurrence_nonlegal・deck_cards_nonlegal）は含めない。
#       実測（2026-08-23・VM）: 表 12 本・約 75MB・12 秒（ジャッジパネルの評価者が計測）。
# 方式: コンテナ内 pg_dump 18（ホストの pg_dump は 17 でサーバ 18 に不可）を docker exec。
#       --no-owner --no-privileges＝所有者と GRANT は含めない（公開サーバー側の restore_public_dump.sh が
#       readonly_ai を作って GRANT し直す＝「役割が無いから失敗」を構造で消す）。
#       -t で表を名指し＝拡張（pg_trgm）は dump に入らない → 復元側で CREATE EXTENSION する。
# 注意: プレイヤー名は 2026-08-31 から players 表（表指定に含めない）にだけあり、deck_list.player_name 列は廃止＝dump に名前は入らない。
#       復元側の DROP COLUMN IF EXISTS は旧 dump 用の保険。dump は自分の公開サーバーの間（Tailscale 私有網）でしか動かさない＝再配布ではない。
# 出力: /mnt/new_hdd/db_archives/sisho_public_YYYYMMDD.dump と .sha256
# 使い方: sh/make_public_dump.sh            （出力先は PUBLIC_DUMP_DIR で上書き可）
set -u
REPO=/mnt/mtg_rag
DEST="${PUBLIC_DUMP_DIR:-/mnt/new_hdd/db_archives}"
CONTAINER="${PGDUMP_CONTAINER:-pg18-primary}"
PY=/mnt/new_hdd/my_rag_env/bin/python
STAMP=$(date +%Y%m%d)
OUT="$DEST/sisho_public_${STAMP}.dump"
# 公開サーバーに要る表（mcp_server.py が読む表＋describe で見せてよい表）。増やすときはここに足す
# （publication の表と揃える＝sh/sisho_repl/01 と同じ 19 表・limited_card_stats は 2026-08-31・limited_* 5 表は 2026-09-02・mtg_sets は 2026-09-03 追加）。
TABLES=(mtg_cards_v2 mtg_cards_v2_nonlegal mtg_rules card_rulings deck_list deck_cards
        card_format_strength edh_card_strength format_deck_counts
        card_scope_deck_counts scope_deck_counts
        card_cooccurrence edh_card_cooccurrence_v2 mtgo_name_alias
        limited_card_stats
        limited_color_stats limited_matchup_stats limited_format_stats limited_card_rank_stats limited_card_pick_stats
        mtg_sets)

mkdir -p "$DEST"
PW=$($PY -c "import sys; sys.path.insert(0,'$REPO/src'); from db_config import DB_CONFIG; print(DB_CONFIG['password'])") || { echo "ERROR: DB_CONFIG 読めず"; exit 1; }
TOPT=(); for t in "${TABLES[@]}"; do TOPT+=(-t "public.$t"); done

echo "[$(date '+%F %T')] start → $OUT（表 ${#TABLES[@]} 本）"
t0=$(date +%s)
if ! docker exec -e PGPASSWORD="$PW" "$CONTAINER" pg_dump -Fc -Z 6 --no-owner --no-privileges \
       -h localhost -U devuser "${TOPT[@]}" rag_dev > "$OUT"; then
  echo "ERROR: pg_dump 失敗"; rm -f "$OUT"; exit 1
fi
sha256sum "$OUT" | awk '{print $1}' > "$OUT.sha256"
N=$(docker exec -i "$CONTAINER" pg_restore --list < "$OUT" 2>/dev/null | grep -c "TABLE DATA")
echo "[$(date '+%F %T')] done $(du -h "$OUT" | cut -f1) / $(( $(date +%s)-t0 ))s / TABLE DATA=$N / sha256=$(cut -c1-16 "$OUT.sha256")"
[ "$N" -eq "${#TABLES[@]}" ] || { echo "WARN: TABLE DATA の数が表の数と違う（$N vs ${#TABLES[@]}）"; exit 2; }
