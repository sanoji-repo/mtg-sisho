-- 07_box_face_columns.sql — 売り場（箱 sisho・rag_sisho）側: mtg_cards_v2 に面の列 4 本＋出所の列 2 本を足す（R3-1a・2026-08-31 本人 GO）
-- 設計台帳: docs/ai/CARD_FACES_DESIGN.md（P1〜P7・D1・D7・D9）。
--
-- 順番: この 07（箱に列）→ VM で 08_vm_face_columns_publish.sql（publication の列指定に 6 列を足す）
--        → 箱で ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION WITH (copy_data = false)
--        → VM で全行に触る UPDATE（既存行の新列を箱へ流す・REFRESH は既存行を埋めない）→ 箱で件数一致を確認。
-- 走らせ方: scp → chmod 644 → sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/07_box_face_columns.sql
-- 制約は付けない（VM から流れてくる値がそのまま入る・契約は VM 側で守る）。

ALTER TABLE public.mtg_cards_v2
  ADD COLUMN IF NOT EXISTS name_en_front text,
  ADD COLUMN IF NOT EXISTS name_en_back  text,
  ADD COLUMN IF NOT EXISTS name_ja_front text,
  ADD COLUMN IF NOT EXISTS name_ja_back  text,
  ADD COLUMN IF NOT EXISTS name_ja_src_front text,   -- D9: 出所（scryfall / whisper / manual / rule_a / legacy）
  ADD COLUMN IF NOT EXISTS name_ja_src_back  text;
CREATE INDEX IF NOT EXISTS mtg_cards_v2_name_en_back_idx ON public.mtg_cards_v2 (name_en_back);
CREATE INDEX IF NOT EXISTS mtg_cards_v2_name_ja_back_idx ON public.mtg_cards_v2 (name_ja_back);
