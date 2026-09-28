-- 34_box_drop_old_cooccurrence.sql — 公開サーバー側: 共起の旧 4 表の購読をやめて表を降ろす
-- 33 の後。走らせ方: scp で /tmp へ → sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/34_box_drop_old_cooccurrence.sql
-- 依存（ビュー・引き金・関数）は無い。REFRESH で購読から外れた表は写されなくなるだけで残るので DROP する。
ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION;
DROP TABLE public.card_cooccurrence, public.edh_card_cooccurrence_v2, public.card_scope_deck_counts, public.scope_deck_counts;
-- 確認: SELECT count(*) FROM pg_subscription_rel;  -- 21
