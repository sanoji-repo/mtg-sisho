-- 30_vm_add_cooccurrence_v2.sql — 工場（VM）側: 共起の作り直しの 4 表を publication に足す
-- 表は src/build_cooccurrence.py --migrate が作る。
-- 走らせ方（VM で）: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/30_vm_add_cooccurrence_v2.sql
-- 順番: この 30 → 公開サーバーで 31_box_add_cooccurrence_v2.sql → 32_both_cooccurrence_v2_digest.sql で両側の digest を照合してから道具を配備。
-- 旧 4 表（card_cooccurrence・edh_card_cooccurrence_v2・card_scope_deck_counts・scope_deck_counts）はここでは外さない
-- （道具を切り替えて確かめた後に 33〜35 で外す）。
GRANT SELECT ON public.cooccurrence_populations, public.cooccurrence_population_stats,
  public.card_population_deck_counts, public.card_cooccurrence_v2 TO sisho_repl;
ALTER PUBLICATION sisho_pub ADD TABLE
  public.cooccurrence_populations (population_id, format_name, window_code, sources, definition_version, pair_min_decks, definition),
  public.cooccurrence_population_stats (population_id, build_id, computed_at, input_snapshot_at, window_start, window_end,
    first_event_date, latest_event_date, raw_deck_count, deck_count, unresolved_unique_decks, pair_row_count, card_row_count,
    content_digest, coverage),
  public.card_population_deck_counts (population_id, card_id, main_deck_count, side_deck_count),
  public.card_cooccurrence_v2 (population_id, relation, card_id_a, card_id_b, cooccurrence_count);
-- 確認: SELECT count(*) FROM pg_publication_tables WHERE pubname='sisho_pub';  -- 21 → 25
