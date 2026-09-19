-- 23_both_add_text_trgm_idx.sql — mtg_cards_v2 の本文 3 列に pg_trgm の GIN 索引。VM と公開サーバーの両方で流す（索引は複製で運ばれない）。
-- 根拠: search_mtg_cards の本文 AND 検索（p2: card_name／japanese_name／type_line／oracle_text／japanese_oracle_text の ILIKE '%語%' を語ごとに OR・語どうしを AND）が
--   1 検索 339ms のうち平均 260ms（77%・公開サーバーの実引数 187 通りを VM で再生）。名前 2 列には trgm 索引があるが本文 3 列に無く、32,730 行の seq scan だった。
--   TEMP 表で同じ SQL 172 本を比較: 無索引 mean 225ms → 索引あり mean 11ms（p50 3ms）・結果の不一致 0・遅くなった検索 0。
--   2 字以下の日本語（「粗石」等）はトライグラムが取れず planner が seq scan を選ぶ＝従来どおり。索引 3 本で約 27MB・構築 3 秒。
-- 前提: pg_trgm は導入済み（card_name／japanese_name の trgm 索引が既にある）。
CREATE INDEX CONCURRENTLY IF NOT EXISTS mtg_cards_v2_type_line_trgm ON public.mtg_cards_v2 USING gin (type_line gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS mtg_cards_v2_oracle_text_trgm ON public.mtg_cards_v2 USING gin (oracle_text gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS mtg_cards_v2_japanese_oracle_text_trgm ON public.mtg_cards_v2 USING gin (japanese_oracle_text gin_trgm_ops);
-- 確認: SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)), idx_scan FROM pg_stat_user_indexes WHERE indexrelname LIKE 'mtg_cards_v2_%_trgm';
