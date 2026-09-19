-- 18_box_add_mtg_sets.sql — 公開サーバー（sisho・rag_sisho）側: mtg_sets を受ける表を作り、購読を更新する
-- 走らせ方:
--   1) VM から: scp sh/sisho_repl/18_box_add_mtg_sets.sql sisho:/tmp/ && ssh sisho chmod 644 /tmp/18_box_add_mtg_sets.sql
--   2) 公開サーバーで:    sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/18_box_add_mtg_sets.sql
-- 確認（公開サーバー）: SELECT srrelid::regclass, srsubstate FROM pg_subscription_rel ORDER BY 1;  -- 19 行・全部 r
CREATE TABLE IF NOT EXISTS public.mtg_sets (
  set_code text PRIMARY KEY,
  set_name text NOT NULL,
  released_at date,
  set_type text,
  parent_set_code text,
  card_count integer,
  digital boolean);
GRANT SELECT ON public.mtg_sets TO readonly_ai;
ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION;
