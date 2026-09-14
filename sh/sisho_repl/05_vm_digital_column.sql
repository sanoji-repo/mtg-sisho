-- 05_vm_digital_column.sql — 開発側（VM）側: mtg_cards_v2 に digital 列・name_display の式差し替え・publication の列指定に digital
-- （2026-08-31・方針「(B) やろっか」。設計は 06_box_digital_column.sql の頭書きと docs/ai/PHASE2.md の Alchemy/Historic 節）
--
-- 順番: 公開サーバーで 06（列を先に）→ この 05 → 公開サーバーで REFRESH PUBLICATION (copy_data=false) → VM で sh/migrate_digital_20260831.sql（860 枚の合流）。
-- 走らせ方: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/05_vm_digital_column.sql
--
-- 注意: publication の列指定は「表を外して足し直す」しか変えられない（PG18）。外している間の mtg_cards_v2 への書き込みは公開サーバーへ流れない
--       ので、この SQL と公開サーバーの REFRESH の間で mtg_cards_v2 を書かない（夜間ジョブは 03:00〜・Scryfall 同期は手動）。
--       公開サーバー側は copy_data=false で表を再登録する（既に中身がある表を COPY し直すと主キー衝突で止まる）。

-- 1) 列
ALTER TABLE public.mtg_cards_v2 ADD COLUMN IF NOT EXISTS digital boolean NOT NULL DEFAULT false;

-- 2) name_display の差し替え（公開サーバーと同じ式）
ALTER TABLE public.mtg_cards_v2 DROP COLUMN IF EXISTS name_display;
ALTER TABLE public.mtg_cards_v2 ADD COLUMN name_display text GENERATED ALWAYS AS (
  CASE
    WHEN japanese_name IS NOT NULL THEN '《' || split_part(japanese_name, ' // ', 1) || '/' || split_part(card_name, ' // ', 1) || '》'
    WHEN digital THEN split_part(card_name, ' // ', 1) || '（日本語名未収録）'
    ELSE split_part(card_name, ' // ', 1) || '（日本語版なし）'
  END) STORED;

-- 3) publication の列指定に digital を足す（01_vm_publication.sql の mtg_cards_v2 の項と同じ列＋digital。01 にも反映済み）
ALTER PUBLICATION sisho_pub DROP TABLE public.mtg_cards_v2;
ALTER PUBLICATION sisho_pub ADD TABLE
  public.mtg_cards_v2 (id, card_name, type_line, oracle_text, mana_cost, colors, rarity, layout, embed_text, japanese_name, japanese_oracle_text, power, toughness, loyalty, cmc, color_identity, set_code, set_name, collector_number, card_faces_json, keywords, legalities, tournament_score, produced_mana, edhrec_rank, game_changer, face_cmcs, has_x, is_mana_boost, target_types, target, removal_types, removal, front_keywords, face_types, floor_cmc, draw_count, draw_x, image_url, image_url_ja, set_codes, tutor, dig, digital);

-- 4) nonlegal は列指定なしで載せ直す（REPLICA IDENTITY FULL の表に列指定があると、合流の DELETE が
--    「Column list used by the publication does not cover the replica identity」で拒否される・8/31 実測）
ALTER PUBLICATION sisho_pub DROP TABLE public.mtg_cards_v2_nonlegal;
ALTER PUBLICATION sisho_pub ADD TABLE public.mtg_cards_v2_nonlegal;

-- 確認: SELECT tablename, attnames FROM pg_publication_tables WHERE pubname='sisho_pub' AND tablename LIKE 'mtg_cards_v2%';
--       mtg_cards_v2 は digital を含む 45 列・nonlegal は NULL（全列）
