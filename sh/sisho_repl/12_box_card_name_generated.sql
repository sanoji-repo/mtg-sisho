-- 12_box_card_name_generated.sql — 公開サーバー側: card_name を面の列から作る生成列に差し替える
-- 前提: VM で 11a が済み、公開サーバーで REFRESH PUBLICATION (copy_data=false) が済んでいる（card_name はもう流れてこない）。
-- card_name を参照する FK は無い（FK は全部 id 参照・実測）。UNIQUE 制約は列と一緒に落ち、生成列の上に一意索引で作り直す。
-- 走らせ方: scp → chmod 644 → sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/12_box_card_name_generated.sql

BEGIN;
ALTER TABLE public.mtg_cards_v2 DROP COLUMN card_name;
ALTER TABLE public.mtg_cards_v2 ADD COLUMN card_name text GENERATED ALWAYS AS (name_en_front || coalesce(' // ' || name_en_back, '')) STORED;
CREATE UNIQUE INDEX mtg_cards_v2_card_name_key ON public.mtg_cards_v2 (card_name);
COMMIT;
SELECT count(*) AS cards, count(DISTINCT card_name) AS distinct_names, count(*) FILTER (WHERE card_name LIKE '% // %') AS two_names FROM public.mtg_cards_v2;
