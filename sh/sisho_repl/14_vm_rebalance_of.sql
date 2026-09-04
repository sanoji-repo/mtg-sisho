-- 14_vm_rebalance_of.sql — 工場（VM）側: rebalance_of 列（A- 札 → 元カードの id・FK）を足し、publication に載せ、A- 216 枚を充填（2026-08-31 本人裁定）
-- 設計: 「A- は X のリバランス」を名前の接頭辞でなく列で持つ（8/31 の議論: 両面札の A- は裏面にも A- が付き、接頭辞の文字列手術で 13 枚を取りこぼした）。
--       元カードは面ごとに A- を外した名前で探す（表・裏とも）。元が本線に無い A- は NULL（今は 0 枚のはず）。
-- 走らせ方: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/14_vm_rebalance_of.sql
-- この後: 箱で REFRESH PUBLICATION (copy_data=false) → VM で UPDATE mtg_cards_v2 SET rebalance_of = rebalance_of WHERE rebalance_of IS NOT NULL（216 行を流す）。

ALTER TABLE public.mtg_cards_v2 ADD COLUMN IF NOT EXISTS rebalance_of integer REFERENCES public.mtg_cards_v2(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS mtg_cards_v2_rebalance_of_idx ON public.mtg_cards_v2 (rebalance_of);

-- 充填（A- 札で、面ごとに A- を外した名前の元カードが本線に居るもの）
UPDATE public.mtg_cards_v2 a SET rebalance_of = b.id
  FROM public.mtg_cards_v2 b
 WHERE a.digital AND a.card_name LIKE 'A-%'
   AND NOT b.digital
   AND b.name_en_front = substr(a.name_en_front, 3)
   AND (a.name_en_back IS NULL AND b.name_en_back IS NULL OR b.name_en_back = substr(a.name_en_back, 3))
   AND a.rebalance_of IS DISTINCT FROM b.id;

-- publication の列指定に rebalance_of を足す（表を外して入れ直す・01 にも反映）
ALTER PUBLICATION sisho_pub DROP TABLE public.mtg_cards_v2;
ALTER PUBLICATION sisho_pub ADD TABLE
  public.mtg_cards_v2 (id, type_line, oracle_text, mana_cost, colors, rarity, layout, embed_text, japanese_oracle_text, power, toughness, loyalty, cmc, color_identity, set_code, set_name, collector_number, card_faces_json, keywords, legalities, tournament_score, produced_mana, edhrec_rank, game_changer, face_cmcs, has_x, is_mana_boost, target_types, target, removal_types, removal, front_keywords, face_types, floor_cmc, draw_count, draw_x, image_url, image_url_ja, set_codes, tutor, dig, digital,
    name_en_front, name_en_back, name_ja_front, name_ja_back, name_ja_src_front, name_ja_src_back, rebalance_of);

SELECT count(*) FILTER (WHERE card_name LIKE 'A-%' AND digital) AS a_cards, count(rebalance_of) AS linked,
       count(*) FILTER (WHERE card_name LIKE 'A-%' AND digital AND rebalance_of IS NULL) AS a_unlinked
  FROM public.mtg_cards_v2;
