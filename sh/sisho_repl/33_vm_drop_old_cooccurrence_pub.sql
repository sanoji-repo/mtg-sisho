-- 33_vm_drop_old_cooccurrence_pub.sql — 工場（VM）側: 共起の旧 4 表を publication から外す
-- 新しい 4 表（30・31）へ道具を切り替えて本番の道で確かめた後。順番: この 33 → 公開サーバーで 34 → VM で 35（表を DROP）。
-- 走らせ方（VM で）: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/33_vm_drop_old_cooccurrence_pub.sql
ALTER PUBLICATION sisho_pub DROP TABLE public.card_cooccurrence, public.edh_card_cooccurrence_v2,
  public.card_scope_deck_counts, public.scope_deck_counts;
REVOKE SELECT ON public.card_cooccurrence, public.edh_card_cooccurrence_v2,
  public.card_scope_deck_counts, public.scope_deck_counts FROM sisho_repl;
-- 確認: SELECT count(*) FROM pg_publication_tables WHERE pubname='sisho_pub';  -- 25 → 21
