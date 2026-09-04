-- 06_box_digital_column.sql — 売り場（箱 sisho・rag_sisho）側: mtg_cards_v2 に digital 列を足し、name_display の式を差し替える
-- （2026-08-31・本人「(B) やろっか」＝Arena 専用札（アルケミー・A- リバランス等 860 枚）を本線 mtg_cards_v2 に digital 列付きで合流・PHASE2 の記録どおり）
--
-- 順番: この 06（箱に列を足す）→ VM で 05_vm_digital_column.sql（列＋publication の列指定に digital）→ 箱で
--        ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION WITH (copy_data = false)（下の 3 節）。
-- 走らせ方: scp → chmod 644 → sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/06_box_digital_column.sql
--
-- 設計:
--   * digital=true ＝ 紙に存在しない Arena 専用の札（Vintage 非合法・historic 等で合法）。既定の検索は紙（WHERE NOT digital）。
--   * name_display（生成列・箱が自分で計算）: 日本語名が無いとき、digital なら「（日本語名未収録）」＝Arena には日本語版があるが
--     まだ持っていない、紙なら従来の「（日本語版なし）」。生成列は式を変えられないので DROP → ADD（依存ビュー・索引なし・8/31 確認）。

-- 1) 列
ALTER TABLE public.mtg_cards_v2 ADD COLUMN IF NOT EXISTS digital boolean NOT NULL DEFAULT false;

-- 2) name_display の差し替え
ALTER TABLE public.mtg_cards_v2 DROP COLUMN IF EXISTS name_display;
ALTER TABLE public.mtg_cards_v2 ADD COLUMN name_display text GENERATED ALWAYS AS (
  CASE
    WHEN japanese_name IS NOT NULL THEN '《' || split_part(japanese_name, ' // ', 1) || '/' || split_part(card_name, ' // ', 1) || '》'
    WHEN digital THEN split_part(card_name, ' // ', 1) || '（日本語名未収録）'
    ELSE split_part(card_name, ' // ', 1) || '（日本語版なし）'
  END) STORED;

-- 3) VM 側 05 が済んだ後に（別に流す。05 の前に流すと publication に digital が無いので意味が無い）:
--   ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION WITH (copy_data = false);
--   確認: SELECT srrelid::regclass, srsubstate FROM pg_subscription_rel WHERE srrelid='public.mtg_cards_v2'::regclass;  -- r
