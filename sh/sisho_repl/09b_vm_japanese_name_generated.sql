-- 09b_vm_japanese_name_generated.sql（第二段: VM の差し替え・箱の 10 の後） — 工場（VM）側: japanese_name と name_display を「面の列から作る生成列」に差し替える（R3-1b・2026-08-31 本人 GO）
-- 設計台帳 P1・P2・D2・D4・D5: 派生は DB が計算し、コードは書けない。japanese_name は両面揃った時だけ結合、揃わなければ NULL。
-- 生成列は別の生成列を参照できないので、name_display も面の列から直接作る（旧式は japanese_name を参照していた）。
--
-- 順番: 1) この節（publication から japanese_name を外す）→ 箱で REFRESH PUBLICATION (copy_data=false)
--        → 2) 箱で 10_box_japanese_name_generated.sql → 3) この節の後半（VM の差し替え）。

-- （初版は psql の \if で二段にしたが \if は文字列比較を受け付けず走らなかった → 09a/09b に分割・2026-08-31 21:1x）
-- 走らせ方: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/09b_vm_japanese_name_generated.sql

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
