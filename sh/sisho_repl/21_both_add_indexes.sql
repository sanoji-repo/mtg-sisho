-- 21_both_add_indexes.sql — 索引の追加 4 本（2026-09-04・本人「1 はやるか」）。VM と箱の両方で流す（論理レプリケーションは索引を運ばない）。
-- 根拠（箱の 8/29〜9/4 の統計）: deck_list 順次走査 1,714 回（脳の WHERE format_name 1,300 回）／mtg_cards_v2 772 回（search の ILIKE '%語%'・温まっていても 277ms）／
--   card_rulings 720 回・5,600 万行（裁定の道具が card_name で引くのに索引が無い）／mtg_cards_v2_nonlegal 2,750 回（索引ゼロ）。
-- CONCURRENTLY: 複製の適用と道具を止めない（トランザクションの外で流す＝psql の -f で 1 文ずつ）。pg_trgm は両方に既存。
CREATE INDEX CONCURRENTLY IF NOT EXISTS deck_list_format_date_idx ON public.deck_list (format_name, tournament_date);
CREATE INDEX CONCURRENTLY IF NOT EXISTS mtg_cards_v2_card_name_trgm ON public.mtg_cards_v2 USING gin (card_name gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS mtg_cards_v2_japanese_name_trgm ON public.mtg_cards_v2 USING gin (japanese_name gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS card_rulings_card_name_idx ON public.card_rulings (card_name);
CREATE INDEX CONCURRENTLY IF NOT EXISTS mtg_cards_v2_nonlegal_card_name_idx ON public.mtg_cards_v2_nonlegal (card_name);
-- 確認: SELECT indexrelname, idx_scan FROM pg_stat_user_indexes WHERE indexrelname IN ('deck_list_format_date_idx','mtg_cards_v2_card_name_trgm','mtg_cards_v2_japanese_name_trgm','card_rulings_card_name_idx','mtg_cards_v2_nonlegal_card_name_idx');
