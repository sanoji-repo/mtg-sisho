-- 10_box_japanese_name_generated.sql — 売り場（箱）側: japanese_name と name_display を面の列から作る生成列に（R3-1b・2026-08-31）
-- 前提: VM で 09 の step=pub が済み、箱で REFRESH PUBLICATION (copy_data=false) が済んでいる（japanese_name はもう流れてこない）。
-- 式は VM の 09 と同じ（箱が自分で計算する）。
-- 走らせ方: scp → chmod 644 → sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/10_box_japanese_name_generated.sql

BEGIN;
ALTER TABLE public.mtg_cards_v2 DROP COLUMN IF EXISTS name_display;
ALTER TABLE public.mtg_cards_v2 DROP COLUMN japanese_name;
ALTER TABLE public.mtg_cards_v2 ADD COLUMN japanese_name text GENERATED ALWAYS AS (
  CASE WHEN name_en_back IS NULL THEN name_ja_front
       WHEN name_ja_front IS NOT NULL AND name_ja_back IS NOT NULL THEN name_ja_front || ' // ' || name_ja_back
  END) STORED;
ALTER TABLE public.mtg_cards_v2 ADD COLUMN name_display text GENERATED ALWAYS AS (
  CASE WHEN name_ja_front IS NOT NULL THEN '《' || name_ja_front || '/' || name_en_front || '》'
       WHEN digital THEN name_en_front || '（日本語名未収録）'
       ELSE name_en_front || '（日本語版なし）'
  END) STORED;
COMMIT;
SELECT count(japanese_name) AS ja_names,
       count(*) FILTER (WHERE name_en_back IS NOT NULL AND japanese_name IS NOT NULL) AS multi_both,
       count(*) FILTER (WHERE japanese_name LIKE '% // % // %') AS doubled,
       count(*) FILTER (WHERE name_en_back IS NOT NULL AND japanese_name IS NOT NULL AND japanese_name NOT LIKE '% // %') AS partial
FROM public.mtg_cards_v2;
