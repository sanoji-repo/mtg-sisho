-- 15_vm_add_limited_extra.sql — 開発側（VM）側: 17Lands の追加集計 5 表を publication に足す（2026-09-02・承認「A はいる」）
-- 走らせ方（VM で）:
--   docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sh/sisho_repl/15_vm_add_limited_extra.sql
-- 順番: この 15 → 公開サーバーで 16_box_add_limited_extra.sql（表を作ってから REFRESH PUBLICATION）。
-- 設計: 生成列（wr・on_play_wr・avg_turns・gih_wr・gp_wr・avg_event_wins）は publication に載せない＝公開サーバーが同じ式で自分で計算（07〜12 と同じ掟）。
--       表の中身は色・ランク帯・カード名と集計値だけ（人の名前・ID は無い）。出典 17Lands（CC BY 4.0）。01_vm_publication.sql にも同じ行を足してある。

GRANT SELECT ON public.limited_color_stats, public.limited_matchup_stats, public.limited_format_stats,
                public.limited_card_rank_stats, public.limited_card_pick_stats TO sisho_repl;

ALTER PUBLICATION sisho_pub ADD TABLE
  public.limited_color_stats (expansion, event_type, main_colors, splash, games, wins, computed_at),
  public.limited_matchup_stats (expansion, event_type, main_colors, opp_colors, games, wins, computed_at),
  public.limited_format_stats (expansion, event_type, rank, games, wins, on_play_games, on_play_wins, turns_sum, mulligans_sum, computed_at),
  public.limited_card_rank_stats (expansion, event_type, rank, card_name, gih_games, gih_wins, gp_games, gp_wins, db_card_name, computed_at),
  public.limited_card_pick_stats (expansion, event_type, card_name, picks, maindeck_rate, sideboard_in_rate, event_wins_sum, event_losses_sum, event_picks, db_card_name, computed_at);

-- 確認: SELECT schemaname, tablename FROM pg_publication_tables WHERE pubname='sisho_pub' ORDER BY 1,2;  -- 18 表
