-- 11b_vm_card_name_generated.sql — 開発側（VM）側・第二段: card_name を面の列から作る生成列に差し替える
-- 前提: 11a → 公開サーバー REFRESH → 公開サーバー 12 が済んでいる。
-- 面の契約 faces_back_matches_card_name（裏の有無 = 正式名の // の有無）は card_name が派生になった今は自明なので、列と一緒に落ちる。
-- 走らせ方: docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/11b_vm_card_name_generated.sql

BEGIN;
ALTER TABLE public.mtg_cards_v2 DROP CONSTRAINT IF EXISTS faces_back_matches_card_name;
ALTER TABLE public.mtg_cards_v2 DROP COLUMN card_name;
ALTER TABLE public.mtg_cards_v2 ADD COLUMN card_name text GENERATED ALWAYS AS (name_en_front || coalesce(' // ' || name_en_back, '')) STORED;
CREATE UNIQUE INDEX mtg_cards_v2_card_name_key ON public.mtg_cards_v2 (card_name);
COMMIT;
SELECT count(*) AS cards, count(DISTINCT card_name) AS distinct_names, count(*) FILTER (WHERE card_name LIKE '% // %') AS two_names FROM public.mtg_cards_v2;
