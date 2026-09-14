-- 08_vm_face_columns_publish.sql — 開発側（VM）側: 出所の列 2 本を足し、publication の mtg_cards_v2 の列指定に面の列 6 本を足す（R3-1a・2026-08-31 承認）
-- 前提: 公開サーバーで 07 が済んでいる（公開サーバーに列が無いと apply が止まる）。
-- 走らせ方: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/08_vm_face_columns_publish.sql
-- この後: 公開サーバーで REFRESH PUBLICATION (copy_data=false) → VM で `UPDATE mtg_cards_v2 SET name_en_front = name_en_front`（全行に触って新列を流す）。
-- japanese_name はまだ列指定に残す（09 で生成列に差し替えるときに外す）。

ALTER TABLE public.mtg_cards_v2
  ADD COLUMN IF NOT EXISTS name_ja_src_front text,
  ADD COLUMN IF NOT EXISTS name_ja_src_back  text;

ALTER PUBLICATION sisho_pub DROP TABLE public.mtg_cards_v2;
ALTER PUBLICATION sisho_pub ADD TABLE
  public.mtg_cards_v2 (id, card_name, type_line, oracle_text, mana_cost, colors, rarity, layout, embed_text, japanese_name, japanese_oracle_text, power, toughness, loyalty, cmc, color_identity, set_code, set_name, collector_number, card_faces_json, keywords, legalities, tournament_score, produced_mana, edhrec_rank, game_changer, face_cmcs, has_x, is_mana_boost, target_types, target, removal_types, removal, front_keywords, face_types, floor_cmc, draw_count, draw_x, image_url, image_url_ja, set_codes, tutor, dig, digital,
    name_en_front, name_en_back, name_ja_front, name_ja_back, name_ja_src_front, name_ja_src_back);
