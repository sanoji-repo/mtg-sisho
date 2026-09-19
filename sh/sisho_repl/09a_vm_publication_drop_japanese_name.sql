-- 09a_vm_publication_drop_japanese_name.sql（第一段: publication から japanese_name を外す） — 開発側（VM）側: japanese_name と name_display を「面の列から作る生成列」に差し替える
-- 設計台帳 P1・P2・D2・D4・D5: 派生は DB が計算し、コードは書けない。japanese_name は両面揃った時だけ結合、揃わなければ NULL。
-- 生成列は別の生成列を参照できないので、name_display も面の列から直接作る（旧式は japanese_name を参照していた）。
--
-- 順番: 1) この節（publication から japanese_name を外す）→ 公開サーバーで REFRESH PUBLICATION (copy_data=false)
--        → 2) 公開サーバーで 10_box_japanese_name_generated.sql → 3) この節の後半（VM の差し替え）。

-- （初版は psql の \if で二段にしたが \if は文字列比較を受け付けず走らなかった → 09a/09b に分割）
-- 走らせ方: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/09a_vm_publication_drop_japanese_name.sql

ALTER PUBLICATION sisho_pub DROP TABLE public.mtg_cards_v2;
ALTER PUBLICATION sisho_pub ADD TABLE
  public.mtg_cards_v2 (id, card_name, type_line, oracle_text, mana_cost, colors, rarity, layout, embed_text, japanese_oracle_text, power, toughness, loyalty, cmc, color_identity, set_code, set_name, collector_number, card_faces_json, keywords, legalities, tournament_score, produced_mana, edhrec_rank, game_changer, face_cmcs, has_x, is_mana_boost, target_types, target, removal_types, removal, front_keywords, face_types, floor_cmc, draw_count, draw_x, image_url, image_url_ja, set_codes, tutor, dig, digital,
    name_en_front, name_en_back, name_ja_front, name_ja_back, name_ja_src_front, name_ja_src_back);
SELECT array_length(attnames,1) AS published_cols FROM pg_publication_tables WHERE pubname='sisho_pub' AND tablename='mtg_cards_v2';
