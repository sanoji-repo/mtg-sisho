-- 35_vm_drop_old_cooccurrence.sql — 工場（VM）側: 共起の旧 4 表を降ろす
-- 33（publication から外す）・34（公開サーバーで降ろす）の後。夜間の旧の集計 2 本・recompute の分母 2 表・import_decks の旧の共起は先に外しておく。
-- 依存（ビュー・関数）が無いことは同日に確認。戻すなら週次の pg_dump（sh/weekly_pgdump.sh）から。
-- 走らせ方: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/35_vm_drop_old_cooccurrence.sql
SET lock_timeout = '5s';
DROP TABLE public.card_cooccurrence, public.edh_card_cooccurrence_v2, public.card_scope_deck_counts, public.scope_deck_counts;
