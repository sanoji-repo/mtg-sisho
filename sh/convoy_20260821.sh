#!/usr/bin/env bash
# convoy_20260821.sh — 新セット搬入便＋name_ja 修理 704 枚（2026-08-21・承認「やろう。」朝）
#
# 8/11 の護送船団の型を継ぐが、8/21 の「ルーター・絞り込みゲート・スコア補正部品 全撤廃」裁定により
# **埋め込み工程（rebuild_embed_text / reembed）と API 載せ替え・eval は無い**。
#   1. 退避     : 触る列を bak_convoy_20260821 へ（可逆）
#   2. 取得     : Scryfall bulk（oracle_cards / all_cards・JSONL→配列包み直し）
#   3. 搬入     : sync_oracle_cards（dry-run 確認 → apply・新カード INSERT＋既存 UPDATE）
#   4. 面導出   : add_face_cmcs / add_face_types（冪等・全行）
#   5. 日本語   : extract_japanese（**printed_name 面フォールバック修理済み**＝704 枚同時修理）
#   6. enrich   : scryfall_meta / printings / front_keywords / mana / removal / draw / tutor
#   7. 検収     : 件数・hob 日本語名・欠落 704 の残数
set -u
PY=/mnt/new_hdd/my_rag_env/bin/python
export PYTHONPATH=/home/claude/pylibs
cd /mnt/mtg_rag || exit 1
W=/tmp/claude-1003/-mnt-mtg-rag/25008f6c-85b1-4617-98c4-9498c6ec23e0/scratchpad
mkdir -p "$W"
step() { echo; echo "===== [$(date +%H:%M:%S)] $* ====="; }
die() { echo "!!!! 中断: $*"; exit 1; }

step "0. 触る列を退避（可逆にする）"
$PY - <<'PYEOF' || die "退避に失敗"
import sys; sys.path.insert(0,'src')
import psycopg2
from db_config import DB_CONFIG
conn = psycopg2.connect(**DB_CONFIG); conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("DROP TABLE IF EXISTS bak_convoy_20260821")
    cur.execute("""CREATE TABLE bak_convoy_20260821 AS
                   SELECT id, card_name, oracle_text, japanese_oracle_text,
                          japanese_name, legalities, set_codes FROM mtg_cards_v2""")
    cur.execute("SELECT COUNT(*) FROM bak_convoy_20260821")
    print("退避完了:", cur.fetchone()[0], "行 → bak_convoy_20260821")
conn.close()
PYEOF

step "1. Scryfall bulk を最新版で取得（JSONL.gz → JSON 配列へ包み直す）"
UA="mtg-rag-convoy/1.0"
URLS=$(curl -s -m 60 -H "User-Agent: $UA" -H "Accept: application/json" \
       https://api.scryfall.com/bulk-data \
  | $PY -c "import sys,json; d=json.load(sys.stdin)['data']; print('\n'.join(f\"{b['type']} {b['jsonl_download_uri']}\" for b in d if b['type'] in ('oracle_cards','all_cards')))")
[ -z "$URLS" ] && die "bulk URL の取得に失敗"
echo "$URLS" | while read -r TYPE URI; do
  case "$TYPE" in
    oracle_cards) OUT=/mnt/new_hdd/oracle_cards.json ;;
    all_cards)    OUT=/mnt/new_hdd/all_cards_scryfall.json ;;
    *) continue ;;
  esac
  echo "  取得: $TYPE → $OUT"
  curl -sL -m 5400 -H "User-Agent: $UA" "$URI" | gunzip -c | $PY -c "
import sys
o = sys.stdout; o.write('['); first = True
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    if not first: o.write(',')
    o.write(line); first = False
o.write(']')
" > "$OUT.new" || { echo "  DL/変換 失敗: $TYPE"; exit 1; }
  head -c 2 "$OUT.new" | grep -q '\[' || { echo "  形式不正: $TYPE"; exit 1; }
  [ -s "$OUT.new" ] || { echo "  空ファイル: $TYPE"; exit 1; }
  mv -f "$OUT" "$OUT.bak_20260821" 2>/dev/null
  mv -f "$OUT.new" "$OUT"
  ls -la "$OUT" | awk '{printf "  完了 %.2fGB\n", $5/1e9}'
done
[ -s /mnt/new_hdd/oracle_cards.json ] || die "oracle_cards.json が空"
$PY -c "
import json
d = json.load(open('/mnt/new_hdd/oracle_cards.json'))
print(f'  検証 oracle_cards: {len(d)} 件・先頭 {d[0].get(\"name\")}')
hob = [c for c in d if c.get('set') == 'hob']
print(f'  hob（The Hobbit）: {len(hob)} 件')
" || die "取得ファイルの検証に失敗"

step "2a. 搬入 dry-run（差分の顔ぶれを見る）"
$PY src/sync_oracle_cards.py --bulk /mnt/new_hdd/oracle_cards.json 2>&1 | tail -20 || die "dry-run に失敗"

step "2b. 搬入 apply（新カード INSERT＋既存 UPDATE）"
$PY src/sync_oracle_cards.py --bulk /mnt/new_hdd/oracle_cards.json \
    --apply --ids-out "$W/sync_ids_20260821.txt" 2>&1 | tail -15 || die "apply に失敗"

step "3. 面導出（face_cmcs/has_x/face_types・冪等）"
$PY add_face_cmcs.py 2>&1 | tail -4 || die "add_face_cmcs に失敗"
$PY src/add_face_types.py 2>&1 | tail -4 || die "add_face_types に失敗"

step "4. 日本語抽出（printed_name 面フォールバック修理済み＝704 枚同時修理）"
$PY src/extract_japanese.py 2>&1 | tail -10 || die "extract_japanese に失敗"

step "5. enrich 列（全部冪等・全行走査）"
$PY src/enrich_scryfall_meta.py 2>&1 | tail -4 || die "scryfall_meta に失敗"
$PY src/enrich_printings.py 2>&1 | tail -4 || die "printings に失敗"
$PY src/enrich_front_keywords.py 2>&1 | tail -4 || die "front_keywords に失敗"
$PY src/enrich_mana.py 2>&1 | tail -4 || die "mana に失敗"
$PY src/enrich_removal.py 2>&1 | tail -4 || die "removal に失敗"
$PY src/enrich_draw.py 2>&1 | tail -4 || die "draw に失敗"
$PY src/enrich_tutor.py 2>&1 | tail -4 || die "tutor に失敗"

step "6. 検収（数字）"
bin/dbq "SELECT count(*) AS total,
                count(*) FILTER (WHERE japanese_oracle_text IS NOT NULL AND japanese_name IS NULL) AS text_ari_name_nashi,
                count(*) FILTER (WHERE 'hob' = ANY(set_codes)) AS hob_cards,
                count(*) FILTER (WHERE 'hob' = ANY(set_codes) AND japanese_name IS NOT NULL) AS hob_ja
         FROM mtg_cards_v2"
bin/dbq "SELECT card_name, japanese_name FROM mtg_cards_v2 WHERE 'hob' = ANY(set_codes) AND japanese_name IS NOT NULL ORDER BY edhrec_rank NULLS LAST LIMIT 5"

step "7. MCP 再起動（instructions の鮮度スタンプ更新）"
XDG_RUNTIME_DIR=/run/user/1003 systemctl --user restart mtg-rag-mcp
sleep 3
XDG_RUNTIME_DIR=/run/user/1003 systemctl --user is-active mtg-rag-mcp

step "完了"
echo "退避 bak_convoy_20260821 は検収 OK まで残す（DROP は承認を得てから）"
