-- 01_vm_publication.sql — 工場（VM・pg18-primary・rag_dev）側: 論理レプリケーションの publisher を作る
-- （2026-08-30・本人「A でやってみて」・設計は docs/ai/SISHO_BOX.md §0）
--
-- 走らせ方（VM で）:
--   . ~/.config/mtg-rag/sisho_repl.env   # SISHO_REPL_PW（600・リポジトリに書かない）
--   docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -v pw="$SISHO_REPL_PW" \
--     -f - < sh/sisho_repl/01_vm_publication.sql
-- 前提: wal_level=logical（docker-compose.yml の command・8/30 に再起動済み）。
--
-- 設計:
--   * 列指定は「箱に今ある列」（8/30 に VM と箱で一致を確認・8/31 に面の列を追加、生成列 3 本を除外）。
--     deck_list は player_name を含まない＝列指定で流さない。
--     全表を明示リストにする理由: VM 側で ADD COLUMN しても箱が壊れない（新列は流れない）。
--     箱に列を足すときは「箱の表に ADD COLUMN → ここの列指定に足す → 箱で ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION」。
--   * ロール sisho_repl は REPLICATION 属性＋公開する列だけ SELECT（deck_list は列単位＝player_name は読めない）。
--   * mtg_cards_v2_nonlegal だけ主キーが無い → REPLICA IDENTITY FULL（2,779 → 1,919 行・card_name は一意）。
--     主キー無しの表を publication に入れたまま UPDATE/DELETE すると VM 側がエラーになるための保険。
--     REPLICA IDENTITY FULL の表には列指定を付けない（付けると UPDATE/DELETE が拒否される・8/31 実測）。
--   * Moxfield 行の source_url/deck_name の NULL 化は箱側の ENABLE ALWAYS トリガ（02_box_subscription.sql）。
--   * 二度目以降に流すと publication を作り直す＝箱の subscription が一時的に切れるので、普段は流さない。

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sisho_repl') THEN
    CREATE ROLE sisho_repl LOGIN REPLICATION;
  END IF;
END $$;
ALTER ROLE sisho_repl PASSWORD :'pw';
ALTER ROLE sisho_repl CONNECTION LIMIT 3;

GRANT SELECT ON public.mtg_cards_v2, public.mtg_cards_v2_nonlegal, public.mtg_rules, public.card_rulings,
  public.deck_cards, public.card_cooccurrence, public.edh_card_cooccurrence_v2, public.edh_card_strength,
  public.card_format_strength, public.mtgo_name_alias, public.format_deck_counts TO sisho_repl;
GRANT SELECT (id, deck_name, set_code, source, created_at, tournament_name, tournament_date, placement,
  format_name, source_url, tournament_event_id, archetype, bracket) ON public.deck_list TO sisho_repl;
-- 17Lands 集計（2026-08-31 本人 GO 15:32・03_vm_add_limited_card_stats.sql で足した分。同日 16:10 に lab17.card_stats → public.limited_card_stats へ統合。ここが正本）
GRANT SELECT ON public.limited_card_stats TO sisho_repl;
GRANT SELECT ON public.limited_color_stats, public.limited_matchup_stats, public.limited_format_stats, public.limited_card_rank_stats, public.limited_card_pick_stats TO sisho_repl;
GRANT SELECT ON public.mtg_sets TO sisho_repl;  -- 2026-09-03 セット一覧（発売日順の最新セット・draft_set の解決）

ALTER TABLE public.mtg_cards_v2_nonlegal REPLICA IDENTITY FULL;

DROP PUBLICATION IF EXISTS sisho_pub;
CREATE PUBLICATION sisho_pub FOR TABLE
  -- name_display は生成列（《日本語名/英語名》を japanese_name と card_name から作る）＝箱が自分で計算するので流さない
  -- （流すと箱側で "incompatible generated column" になって同期が止まる・8/30 初回コピーで実測）
  -- 2026-08-31（面の列・R3）: card_name / japanese_name / name_display は面の列から作る生成列＝publication に載せない（箱が自分で計算）。
  -- 名前の正本は name_en_front / name_en_back / name_ja_front / name_ja_back（＋出所 name_ja_src_*）。
  public.mtg_cards_v2 (id, type_line, oracle_text, mana_cost, colors, rarity, layout, embed_text, japanese_oracle_text, power, toughness, loyalty, cmc, color_identity, set_code, set_name, collector_number, card_faces_json, keywords, legalities, tournament_score, produced_mana, edhrec_rank, game_changer, face_cmcs, has_x, is_mana_boost, target_types, target, removal_types, removal, front_keywords, face_types, floor_cmc, draw_count, draw_x, image_url, image_url_ja, set_codes, tutor, dig, digital, name_en_front, name_en_back, name_ja_front, name_ja_back, name_ja_src_front, name_ja_src_back, rebalance_of),
  -- nonlegal だけ列指定なし: 主キーが無く REPLICA IDENTITY FULL の表に列指定を付けると UPDATE/DELETE が
  -- 「Column list used by the publication does not cover the replica identity」で拒否される（8/31 に 860 枚の合流 DELETE で実測）。
  -- 全列が流れる（生成列なし・28 列）ので列指定無しでも箱は壊れない。
  public.mtg_cards_v2_nonlegal,
  public.mtg_rules (id, rule_number, section, is_glossary, text_en, text_ja, source_version),
  public.card_rulings (id, oracle_id, card_id, card_name, source, published_at, comment, source_version),
  public.deck_list (id, deck_name, set_code, source, created_at, tournament_name, tournament_date, placement, format_name, source_url, tournament_event_id, archetype, bracket),
  public.deck_cards (id, deck_id, card_name, count, board, card_id),
  public.card_cooccurrence (card_name_a, card_name_b, co_count, source),
  public.edh_card_cooccurrence_v2 (card_id_a, card_id_b, source, deck_count),
  public.edh_card_strength (card_id, format_name, play_decks),
  public.card_format_strength (card_id, format_name, play_decks),
  public.mtgo_name_alias (mtgo_name, card_name, mtgo_id, set_code, note),
  public.format_deck_counts (format_name, total_decks),
  -- 17Lands 集計（2026-08-31）。新しい箱は dump に含まれる・稼働中の箱に足すときは 04_box_add_limited_card_stats.sql
  public.limited_card_stats (expansion, event_type, card_name, gih_games, gih_wins, gih_wr, oh_games, oh_wins, oh_wr,
    gd_games, gd_wins, gd_wr, gp_games, gp_wins, gp_wr, seen_packs, alsa, taken_count, ata,
    in_cards_v2, computed_at, db_card_name, match_kind),
  -- 17Lands 追加集計 5 表（2026-09-02・生成列は載せない・稼働中の箱に足すときは 15/16）
  public.limited_color_stats (expansion, event_type, main_colors, splash, games, wins, computed_at),
  public.limited_matchup_stats (expansion, event_type, main_colors, opp_colors, games, wins, computed_at),
  public.limited_format_stats (expansion, event_type, rank, games, wins, on_play_games, on_play_wins, turns_sum, mulligans_sum, computed_at),
  public.limited_card_rank_stats (expansion, event_type, rank, card_name, gih_games, gih_wins, gp_games, gp_wins, db_card_name, computed_at),
  public.limited_card_pick_stats (expansion, event_type, card_name, picks, maindeck_rate, sideboard_in_rate, event_wins_sum, event_losses_sum, event_picks, db_card_name, computed_at),
  -- セット一覧（2026-09-03・稼働中の箱に足すときは 17/18）
  public.mtg_sets (set_code, set_name, released_at, set_type, parent_set_code, card_count, digital);
