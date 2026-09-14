-- 03_vm_add_limited_card_stats.sql — 開発側（VM）側: 17Lands 集計 public.limited_card_stats を publication に足す
-- （2026-08-31・承認 15:32「公開サーバーでも読めるように」。15:3x は lab17.card_stats として追加し、16:10 に設計判断で
--   public.limited_card_stats へ統合・改名した。このファイルは統合後の形＝今の実態を再現する手順）
--
-- 走らせ方（VM で）:
--   docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/03_vm_add_limited_card_stats.sql
-- 順番: この 03 → 公開サーバーで 04_box_add_limited_card_stats.sql（表を作ってから REFRESH PUBLICATION）。
--
-- 設計:
--   * 他の 12 表と同じく列を明示（VM で ADD COLUMN しても公開サーバーが壊れない）。主キー (expansion, event_type, card_name) あり。
--   * 表の中身はカード名と集計値だけ（人の名前・URL・ID は無い）。出典 17Lands（CC BY 4.0）＝帰属は MCP の返り値と README に。
--   * 01_vm_publication.sql を流し直すと publication が作り直されるので、01 にも同じ表を足してある（01 が正本）。
--   * make_sales_dump.sh の TABLES にも入れてある＝新しい公開サーバーは dump で表ごと届く。この 03/04 は「稼働中の公開サーバーに後から足す」用。

GRANT SELECT ON public.limited_card_stats TO sisho_repl;

ALTER PUBLICATION sisho_pub ADD TABLE
  public.limited_card_stats (expansion, event_type, card_name, gih_games, gih_wins, gih_wr, oh_games, oh_wins, oh_wr,
    gd_games, gd_wins, gd_wr, gp_games, gp_wins, gp_wr, seen_packs, alsa, taken_count, ata,
    in_cards_v2, computed_at, db_card_name, match_kind);

-- 確認: SELECT schemaname, tablename FROM pg_publication_tables WHERE pubname='sisho_pub' ORDER BY 1,2;  -- 13 表
