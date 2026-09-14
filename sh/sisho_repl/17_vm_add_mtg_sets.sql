-- 17_vm_add_mtg_sets.sql — 開発側（VM）側: mtg_sets（セットの発売日・種別・1,048 行）を publication に足す（2026-09-03）
-- 発端: search の draft_set（リミテの同伴をセットで絞る）が発売日順の「最新セット」を mtg_sets から引く。公開サーバーに無く
--       _limited_sets() が空になり、#SOS の解決が全滅した（公開サーバーの tests/test_draft_set.py で 8 項目 FAIL・実測）。
-- 走らせ方（VM で）: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/17_vm_add_mtg_sets.sql
-- 順番: この 17 → 公開サーバーで 18_box_add_mtg_sets.sql。中身は公開情報（Scryfall のセット一覧）だけ。
GRANT SELECT ON public.mtg_sets TO sisho_repl;
ALTER PUBLICATION sisho_pub ADD TABLE
  public.mtg_sets (set_code, set_name, released_at, set_type, parent_set_code, card_count, digital);
-- 確認: SELECT count(*) FROM pg_publication_tables WHERE pubname='sisho_pub';  -- 19
