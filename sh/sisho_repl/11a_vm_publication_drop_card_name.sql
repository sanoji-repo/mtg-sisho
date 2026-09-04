-- 11a_vm_publication_drop_card_name.sql — 工場（VM）側・第一段: publication の列指定から card_name を外す（R3-3・2026-08-31 本人 GO）
-- card_name は面の列（name_en_front / name_en_back）から作る生成列にする（P1・D2）。生成列は publication に載せられないので先に外す。
-- 順番: この 11a → 箱で REFRESH PUBLICATION (copy_data=false) → 箱で 12_box_card_name_generated.sql → VM で 11b。
-- 走らせ方: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/11a_vm_publication_drop_card_name.sql
-- 注意: この後 VM で mtg_cards_v2 を書かないこと（11b が済むまで）。夜間便 03:00 の前に終える。

ALTER PUBLICATION sisho_pub DROP TABLE public.mtg_cards_v2;
ALTER PUBLICATION sisho_pub ADD TABLE
  public.mtg_cards_v2 (id, type_line, oracle_text, mana_cost, colors, rarity, layout, embed_text, japanese_oracle_text, power, toughness, loyalty, cmc, color_identity, set_code, set_name, collector_number, card_faces_json, keywords, legalities, tournament_score, produced_mana, edhrec_rank, game_changer, face_cmcs, has_x, is_mana_boost, target_types, target, removal_types, removal, front_keywords, face_types, floor_cmc, draw_count, draw_x, image_url, image_url_ja, set_codes, tutor, dig, digital,
    name_en_front, name_en_back, name_ja_front, name_ja_back, name_ja_src_front, name_ja_src_back);
SELECT array_length(attnames,1) AS published_cols, 'card_name' = ANY(attnames) AS has_card_name FROM pg_publication_tables WHERE pubname='sisho_pub' AND tablename='mtg_cards_v2';
