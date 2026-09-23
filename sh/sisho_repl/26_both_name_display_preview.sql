-- 26_both_name_display_preview.sql — name_display に「発売前」の枝を足す。開発側と公開サーバーの両方で流す（生成列の式は複製で運ばれない）。
-- 背景: 発売前の先行収録（sync_oracle_cards.py --preview-days）で入るカードは、日本語名が無いと「英語名（日本語版なし）」になり嘘になる。
-- 判定: digital でなく vintage が not_legal ＝ 先行収録の入口しか通らない状態（既存行は 0 件・vintage banned の 101 行は not_legal でないので対象外）。
--   発売日に Scryfall が legal へ切り替えれば同期の UPDATE で式が自然に元の枝へ戻る。行に日付や印は持たない。
-- 表の書き換え（32,7xx 行・数秒）で ACCESS EXCLUSIVE を取る＝lock_timeout で待ちの行列を作らない。
SET lock_timeout = '5s';
ALTER TABLE public.mtg_cards_v2 ALTER COLUMN name_display SET EXPRESSION AS (
CASE
    WHEN name_ja_front IS NOT NULL THEN '《' || name_ja_front || '/' || name_en_front || '》'
    WHEN digital THEN name_en_front || '（日本語名未収録）'
    WHEN legalities ->> 'vintage' = 'not_legal' THEN name_en_front || '（日本語名は未収録・発売前）'
    ELSE name_en_front || '（日本語版なし）'
END);
-- 確認: SELECT count(*) FILTER (WHERE name_display LIKE '%発売前）'), count(*) FILTER (WHERE name_display LIKE '%日本語版なし）') FROM mtg_cards_v2;
