-- 22_both_add_name_en_front_idx.sql — mtg_cards_v2.name_en_front の索引（2026-09-05・本人 GO「では打って」16:5x）。VM と箱の両方で流す（索引は複製で運ばれない）。
-- 根拠: find_partner_cards（構築）の相方解決 JOIN が `split_part(card_name,' // ',1) = pname`（列に関数＝索引不可・OR で card_name の索引も死ぬ）で
--   mtg_cards_v2 32,730 行 × 共起 1,821 行の全比較＝VM 5.9 秒・箱（readonly_ai・並列 0）は statement_timeout 10 秒で落ちていた。
--   コード側を列 name_en_front（全行で split_part と同値・差 0 行）に置換し、この索引で BitmapOr → VM 0.6 秒。記録 docs/me/db_diagnostics_20260905_partner.md。
--   索引だけでは直らない（旧コードの式は索引を使えない）＝箱への配備とセット。
CREATE INDEX CONCURRENTLY IF NOT EXISTS mtg_cards_v2_name_en_front_idx ON public.mtg_cards_v2 (name_en_front);
-- 確認: SELECT indexrelname, idx_scan FROM pg_stat_user_indexes WHERE indexrelname = 'mtg_cards_v2_name_en_front_idx';
