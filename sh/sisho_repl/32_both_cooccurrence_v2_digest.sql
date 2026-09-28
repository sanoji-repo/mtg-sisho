-- 32_both_cooccurrence_v2_digest.sql — 共起 v2 の検収: 母集団ごとに、表の中身から数え直した digest と stats の content_digest を並べる
-- VM と公開サーバーの両方で流し、(1) 各側で recount = stored（その側の表が stats と同じ版）(2) VM と公開サーバーで行がすべて一致、を見る。
-- 夜間の取り込みの適用中に流すと版がずれる＝03:00〜10:30 を避ける。
-- VM: docker exec -i pg18-primary psql -U devuser -d rag_dev -f - < sh/sisho_repl/32_both_cooccurrence_v2_digest.sql
-- 公開サーバー: sudo -u postgres psql -d rag_sisho -f /tmp/32_both_cooccurrence_v2_digest.sql
SELECT s.population_id, p.format_name, p.window_code, s.deck_count, s.build_id,
       (SELECT 'pairs=' || count(*) || ':' || COALESCE(sum(hashtextextended(
                 relation::text || ',' || card_id_a || ',' || card_id_b || ',' || cooccurrence_count, 0)::numeric), 0)
        FROM card_cooccurrence_v2 c WHERE c.population_id = s.population_id)
       || ';' ||
       (SELECT 'cards=' || count(*) || ':' || COALESCE(sum(hashtextextended(
                 card_id || ',' || main_deck_count || ',' || COALESCE(side_deck_count::text, '-'), 0)::numeric), 0)
        FROM card_population_deck_counts d WHERE d.population_id = s.population_id) AS recount,
       s.content_digest AS stored
FROM cooccurrence_population_stats s JOIN cooccurrence_populations p USING (population_id)
ORDER BY 1;
