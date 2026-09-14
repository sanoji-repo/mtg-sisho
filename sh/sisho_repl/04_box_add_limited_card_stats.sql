-- 04_box_add_limited_card_stats.sql — 公開サーバー（sisho・rag_sisho）側: limited_card_stats を受ける表を作り、購読を更新する
-- （2026-08-31・承認 15:32・VM 側 03 の後に流す。稼働中の公開サーバーに後から表を足すときの手順＝新しい公開サーバーは dump に含まれるので不要）
--
-- 走らせ方:
--   1) VM から:  scp sh/sisho_repl/04_box_add_limited_card_stats.sql sisho:/tmp/ && ssh sisho chmod 644 /tmp/04_box_add_limited_card_stats.sql
--                （scp は 600 で置くので postgres が読めない → 644 に。8/31 に踏んだ罠）
--   2) 公開サーバーで:     sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/04_box_add_limited_card_stats.sql
-- 確認（公開サーバー）: SELECT srrelid::regclass, srsubstate FROM pg_subscription_rel ORDER BY 1;   -- 13 行・全部 r
--            SELECT count(*), count(DISTINCT expansion) FROM public.limited_card_stats;    -- VM と同じ数
--
-- 設計:
--   * 列は VM の public.limited_card_stats と同名（論理レプリケーションは列名で対応づける・順序は問わない）。
--   * readonly_ai（MCP が使うロール）に SELECT。public の表なので他の 12 表と同じ扱い。

CREATE TABLE IF NOT EXISTS public.limited_card_stats (
  expansion   text NOT NULL,
  event_type  text NOT NULL,
  card_name   text NOT NULL,
  gih_games int, gih_wins int, gih_wr numeric(6,4),
  oh_games  int, oh_wins  int, oh_wr  numeric(6,4),
  gd_games  int, gd_wins  int, gd_wr  numeric(6,4),
  gp_games  int, gp_wins  int, gp_wr  numeric(6,4),
  seen_packs int, alsa numeric(6,2), taken_count int, ata numeric(6,2),
  in_cards_v2 boolean,
  computed_at timestamptz NOT NULL DEFAULT now(),
  db_card_name text,
  match_kind   text,
  PRIMARY KEY (expansion, event_type, card_name)
);

GRANT SELECT ON public.limited_card_stats TO readonly_ai;

-- publication に足された表を購読に取り込む（初回コピーは 3MB・数秒）
ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION;
