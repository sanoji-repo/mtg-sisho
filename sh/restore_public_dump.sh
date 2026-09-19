#!/usr/bin/env bash
# restore_public_dump.sh — 公開サーバー用 dump を別名 DB に復元し、検収してから差し替える
# =========================================================================
# 公開サーバー側で走らせる。開発側の VM 内でも「予行」として同じものが走る（別 DB 名に復元するだけ）。
# 流れ: 1) sha256 検証 → 2) <DB>_new を作成（既存なら落とす）→ 3) CREATE EXTENSION pg_trgm →
#       4) pg_restore -j2 --no-owner --no-privileges --exit-on-error → 5) deck_list.player_name を DROP・Moxfield 行の source_url/deck_name を NULL →
#       6) readonly_ai を用意（無ければ作る・SELECT を GRANT・資源上限・pg_sleep 剥奪）→ 7) ANALYZE →
#       8) 煙試験（行数・name_display・曖昧名）→ 9) 差し替え（<DB> → <DB>_old、<DB>_new → <DB>）。
#       どの段で失敗しても、今動いている <DB> には触らない。<DB>_old は次回まで残す（戻せる）。
# 接続: 二通り。
#   (a) コンテナ: PUBLIC_CONTAINER=pg18-primary のように指定 → docker exec 経由で psql/pg_restore 18 を使う
#   (b) 素の PG:   PUBLIC_CONTAINER を空 → PGHOST/PGPORT/PGUSER（既定 localhost/5432/postgres）で直接
#   管理ユーザーのパスワードは PGPASSWORD（未設定なら .pgpass か trust に任せる）。
#   readonly_ai のパスワードは DB_PASS_ROAI（必須・無ければ中止）。
# 使い方: DB_PASS_ROAI=... PUBLIC_CONTAINER=pg18-primary PGUSER=devuser PGPASSWORD=... \
#           sh/restore_public_dump.sh /mnt/new_hdd/db_archives/sisho_public_20260823.dump rag_sisho
set -u
DUMP="${1:?dump ファイル}"; DB="${2:-rag_sisho}"
JOBS="${RESTORE_JOBS:-2}"
CONTAINER="${PUBLIC_CONTAINER:-}"
PGHOST="${PGHOST:-localhost}"; PGPORT="${PGPORT:-5432}"; PGUSER="${PGUSER:-postgres}"
: "${DB_PASS_ROAI:?DB_PASS_ROAI（readonly_ai のパスワード）が要る}"
log(){ echo "[$(date '+%F %T')] $*"; }
die(){ log "中止: $*"; exit 1; }

# pg_restore -j は標準入力からは動かない（ファイルが要る）→ コンテナ方式では docker cp で中へ置いてから復元する
if [ -n "$CONTAINER" ]; then
  PSQL(){ docker exec -i -e PGPASSWORD="${PGPASSWORD:-}" "$CONTAINER" psql -v ON_ERROR_STOP=1 -qAt -h localhost -U "$PGUSER" "$@"; }
  RESTORE(){ docker cp "$DUMP" "$CONTAINER:/tmp/public_restore.dump" || return 1
             docker exec -e PGPASSWORD="${PGPASSWORD:-}" "$CONTAINER" pg_restore -h localhost -U "$PGUSER" "$@" /tmp/public_restore.dump; rc=$?
             docker exec -u root "$CONTAINER" rm -f /tmp/public_restore.dump 2>/dev/null; return $rc; }
else
  PSQL(){ psql -v ON_ERROR_STOP=1 -qAt -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$@"; }
  RESTORE(){ pg_restore -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$@" "$DUMP"; }
fi

[ -s "$DUMP" ] || die "dump が無い: $DUMP"
if [ -s "$DUMP.sha256" ]; then
  echo "$(cat "$DUMP.sha256")  $DUMP" | sha256sum -c --quiet || die "sha256 不一致"
  log "sha256 OK"
else
  log "WARN: $DUMP.sha256 が無い（検証せず進む）"
fi

NEW="${DB}_new"; OLD="${DB}_old"
log "1) $NEW を用意"
PSQL -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$NEW'" >/dev/null
# 公開サーバーは論理レプリカ（subscription sisho_sub）で追従する運用になった。restore は「初期化・作り直し」専用。
#   subscription が生きたまま DB を差し替えると apply worker が旧 DB を掴んで DROP できず、VM 側のスロットも孤児になる → 先に公開サーバーで DROP SUBSCRIPTION sisho_sub。
NSUB=$(PSQL -d "$DB" -c "SELECT count(*) FROM pg_subscription" 2>/dev/null || echo 0)
[ "${NSUB:-0}" = "0" ] || die "$DB に subscription が $NSUB 本ある。先に 'DROP SUBSCRIPTION sisho_sub' してから（docs/ai/SISHO_BOX.md §4）"
PSQL -d postgres -c "DROP DATABASE IF EXISTS \"$NEW\"" || die "旧 $NEW を落とせない"
PSQL -d postgres -c "CREATE DATABASE \"$NEW\" TEMPLATE template0 ENCODING 'UTF8'" || die "CREATE DATABASE 失敗"
PSQL -d "$NEW" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm" || die "pg_trgm が入らない"

log "2) pg_restore -j$JOBS"
t0=$(date +%s)
RESTORE -d "$NEW" -j "$JOBS" --no-owner --no-privileges --exit-on-error || die "pg_restore 失敗（$NEW は残置・$DB は無傷）"
log "   復元 $(( $(date +%s)-t0 ))s"

log "3) player_name を落とす・readonly_ai を用意・ANALYZE"
PSQL -d "$NEW" <<SQL || die "後処理 SQL 失敗"
ALTER TABLE deck_list DROP COLUMN IF EXISTS player_name;
-- 公開サーバーには出所側の識別子も置かない。
--   * Moxfield 行: 提供元の条件は「ユーザー名が出なければ可」だが、URL からデッキページ（作者名つき）へ
--     飛べるので、URL とデッキ ID ごと置かない。
--   * MTGO 行: deck_name の末尾にある参加者キーを落とす。MTGO の公開デッキリストページ（プレイヤー名つき）と
--     突き合わせれば個人に辿り着けるため。末尾を落とすと同じイベントのデッキが同名になるので一意制約を外す。
ALTER TABLE deck_list ALTER COLUMN deck_name DROP NOT NULL;  -- deck_name は NOT NULL（dump 由来）・公開サーバーだけ外す（実測で踏んだ罠）
ALTER TABLE deck_list DROP CONSTRAINT IF EXISTS deck_list_deck_name_key;
UPDATE deck_list SET source_url = NULL, deck_name = NULL WHERE source = 'moxfield_edh' AND (source_url IS NOT NULL OR deck_name IS NOT NULL);
UPDATE deck_list SET deck_name = regexp_replace(deck_name, '_[0-9]+\$', '') WHERE source LIKE 'mtgo%' AND deck_name ~ '_[0-9]+\$';
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='readonly_ai') THEN
    CREATE ROLE readonly_ai LOGIN PASSWORD '$DB_PASS_ROAI';
  END IF;
END \$\$;
ALTER ROLE readonly_ai CONNECTION LIMIT 6;
ALTER ROLE readonly_ai SET statement_timeout = '10s';
ALTER ROLE readonly_ai SET idle_in_transaction_session_timeout = '5s';
ALTER ROLE readonly_ai SET work_mem = '16MB';
ALTER ROLE readonly_ai SET temp_file_limit = '256MB';
GRANT CONNECT ON DATABASE "$NEW" TO readonly_ai;
GRANT USAGE ON SCHEMA public TO readonly_ai;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_ai;
REVOKE EXECUTE ON FUNCTION pg_sleep(double precision) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_sleep_for(interval) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_sleep_until(timestamp with time zone) FROM PUBLIC;
ANALYZE;
SQL

log "4) 煙試験"
N_CARDS=$(PSQL -d "$NEW" -c "SELECT count(*) FROM mtg_cards_v2"); [ "${N_CARDS:-0}" -gt 30000 ] || die "mtg_cards_v2 が少なすぎる: $N_CARDS"
N_RULES=$(PSQL -d "$NEW" -c "SELECT count(*) FROM mtg_rules"); [ "${N_RULES:-0}" -gt 3000 ] || die "mtg_rules: $N_RULES"
N_DECKS=$(PSQL -d "$NEW" -c "SELECT count(*) FROM deck_list"); [ "${N_DECKS:-0}" -gt 10000 ] || die "deck_list: $N_DECKS"
DISP=$(PSQL -d "$NEW" -c "SELECT name_display FROM mtg_cards_v2 WHERE card_name='Stifle'"); [ "$DISP" = "《もみ消し/Stifle》" ] || die "name_display が変: $DISP"
SIM=$(PSQL -d "$NEW" -c "SELECT card_name FROM mtg_cards_v2 WHERE similarity(card_name,'Stifel')>0.3 ORDER BY similarity(card_name,'Stifel') DESC LIMIT 1"); [ "$SIM" = "Stifle" ] || die "pg_trgm similarity が変: $SIM"
PN=$(PSQL -d "$NEW" -c "SELECT count(*) FROM information_schema.columns WHERE table_name='deck_list' AND column_name='player_name'"); [ "$PN" = "0" ] || die "player_name が残っている"
MU=$(PSQL -d "$NEW" -c "SELECT count(*) FROM deck_list WHERE source='moxfield_edh' AND (source_url IS NOT NULL OR deck_name IS NOT NULL)"); [ "$MU" = "0" ] || die "Moxfield の URL/デッキ ID が残っている: $MU 行"
GU=$(PSQL -d "$NEW" -c "SELECT count(*) FROM deck_list WHERE source LIKE 'mtgo%' AND deck_name ~ '_[0-9]+\$'"); [ "$GU" = "0" ] || die "MTGO の参加者キーが deck_name に残っている: $GU 行"
log "   OK: cards=$N_CARDS rules=$N_RULES decks=$N_DECKS display=$DISP similarity=$SIM"

log "5) 差し替え $DB ← $NEW（旧は $OLD へ）"
PSQL -d postgres <<SQL || die "差し替え失敗（$NEW は残置・手で RENAME 可）"
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$DB','$OLD') AND pid<>pg_backend_pid();
DROP DATABASE IF EXISTS "$OLD";
DO \$\$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_database WHERE datname='$DB') THEN
    EXECUTE 'ALTER DATABASE "$DB" RENAME TO "$OLD"';
  END IF;
END \$\$;
ALTER DATABASE "$NEW" RENAME TO "$DB";
SQL
log "完了: $DB を入れ替えた（戻すなら $OLD を RENAME）。合計 $(( $(date +%s)-t0 ))s"
