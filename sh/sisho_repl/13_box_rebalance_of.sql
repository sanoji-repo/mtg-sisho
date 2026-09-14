-- 13_box_rebalance_of.sql — 公開サーバー側: mtg_cards_v2 に rebalance_of 列（A- カード → 元カードの id）を足す（2026-08-31 設計判断「3 それでいいか」）
-- 順番: この 13 → VM で 14_vm_rebalance_of.sql（列＋publication＋充填）→ 公開サーバーで REFRESH (copy_data=false) → VM で全行に触る UPDATE。
-- 走らせ方: scp → chmod 644 → sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/13_box_rebalance_of.sql
-- 公開サーバーには FK を張らない（購読の apply 順で親子が前後しうる・VM 側の FK が契約）。
ALTER TABLE public.mtg_cards_v2 ADD COLUMN IF NOT EXISTS rebalance_of integer;
CREATE INDEX IF NOT EXISTS mtg_cards_v2_rebalance_of_idx ON public.mtg_cards_v2 (rebalance_of);
