-- 02_box_subscription.sql — 公開サーバー（sisho・rag_sisho）側: Moxfield の NULL 化トリガ → 12 表を空に → subscription
-- 設計は docs/PUBLIC_SERVER.md を参照。
--
-- 走らせ方（公開サーバーで・postgres として）。パスワードは接続文字列に入れず、公開サーバーの postgres の ~/.pgpass に置く
-- （pg_subscription に秘密が残らない・値は VM の ~/.config/mtg-rag/sisho_repl.env の SISHO_REPL_PW）:
--   1) VM から:  scp sh/sisho_repl/02_box_subscription.sql sisho:/tmp/
--   2) 公開サーバーで:     echo "<開発側の tailnet アドレス>:5435:rag_dev:sisho_repl:<SISHO_REPL_PW>" | sudo -u postgres tee -a /var/lib/postgresql/.pgpass >/dev/null
--                sudo -u postgres chmod 600 /var/lib/postgresql/.pgpass
--   3) 公開サーバーで:     sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 \
--                  -v conn="host=<開発側の tailnet アドレス> port=5435 dbname=rag_dev user=sisho_repl sslmode=disable connect_timeout=10" \
--                  -f /tmp/02_box_subscription.sql
-- 前提: VM 側 01 が済んでいる／Tailscale ACL で tag:sisho → <開発側の tailnet アドレス>:5435 が通る／VM の docker が <開発側の tailnet アドレス>:5435 で聞いている。
-- 疎通の先行確認（公開サーバーで）: psql "host=<開発側の tailnet アドレス> port=5435 dbname=rag_dev user=sisho_repl sslmode=disable" -Atc "select 1"（postgres として・.pgpass が効いていれば 1）
-- 注意: TRUNCATE で公開サーバーの 12 表は一度空になり、初回コピー（約 0.9GB・HDD）で埋まるまで MCP の返り値は欠ける（公開口は未配布＝設計者だけ）。
--       sslmode=disable は Tailscale（WireGuard）が経路を暗号化しているため。VM の PG は ssl=off。

-- 1) Moxfield 行は公開サーバーに URL もデッキ ID も置かない（提供元から「ユーザー名が出なければ可」との了承を得ている。こちらの説明は「no URL or deck ID」）。
--    購読側の apply は session_replication_role=replica で普通のトリガは鳴らない → ENABLE ALWAYS（初回コピーの COPY でも鳴る）。
--    公開サーバーの deck_list.deck_name は dump 由来で NOT NULL（VM と同じ）→ NULL を入れるので公開サーバーだけ制約を外す（初回コピーで踏んだ罠）。
ALTER TABLE public.deck_list ALTER COLUMN deck_name DROP NOT NULL;
--    MTGO 行は末尾を落とすと同じイベントのデッキが同名になるので、公開サーバーでは deck_name の
--    一意制約も外す（論理レプリケーションは主キー id で当てるので購読は壊れない）。
ALTER TABLE public.deck_list DROP CONSTRAINT IF EXISTS deck_list_deck_name_key;
CREATE OR REPLACE FUNCTION public.sisho_scrub_public() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.source = 'moxfield_edh' THEN
    NEW.source_url := NULL;
    NEW.deck_name  := NULL;
  ELSIF NEW.source LIKE 'mtgo%' AND NEW.deck_name IS NOT NULL THEN
    NEW.deck_name  := regexp_replace(NEW.deck_name, '_[0-9]+$', '');
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS sisho_scrub_moxfield ON public.deck_list;
DROP TRIGGER IF EXISTS sisho_scrub_public   ON public.deck_list;
CREATE TRIGGER sisho_scrub_public BEFORE INSERT OR UPDATE ON public.deck_list
  FOR EACH ROW EXECUTE FUNCTION public.sisho_scrub_public();
ALTER TABLE public.deck_list ENABLE ALWAYS TRIGGER sisho_scrub_public;

-- 2) 空にしてから初回コピー（dump の中身は捨て、VM の今と一致させる）。FK 4 本があるので 12 表を一文で。
TRUNCATE public.mtg_cards_v2, public.mtg_cards_v2_nonlegal, public.mtg_rules, public.card_rulings,
  public.deck_list, public.deck_cards, public.card_cooccurrence, public.edh_card_cooccurrence_v2,
  public.edh_card_strength, public.card_format_strength, public.mtgo_name_alias, public.format_deck_counts;

-- 3) subscription（VM 側にスロット sisho_sub が作られる。やめるときは公開サーバーで DROP SUBSCRIPTION sisho_sub＝VM のスロットも消える）
CREATE SUBSCRIPTION sisho_sub
  CONNECTION :'conn'
  PUBLICATION sisho_pub
  WITH (copy_data = true, create_slot = true, enabled = true);

-- 確認（公開サーバー）: SELECT subname, pid, received_lsn, latest_end_lsn FROM pg_stat_subscription;
--            SELECT srrelid::regclass, srsubstate FROM pg_subscription_rel;   -- 全部 r（ready）になれば初回コピー完了
-- 確認（VM）: SELECT slot_name, active, wal_status FROM pg_replication_slots;  -- sisho_sub が active・wal_status=reserved
