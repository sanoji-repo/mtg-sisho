-- 16_box_add_limited_extra.sql — 公開サーバー（sisho・rag_sisho）側: 17Lands 追加集計 5 表を受ける表を作り、購読を更新する
-- 走らせ方:
--   1) VM から:  scp sh/sisho_repl/16_box_add_limited_extra.sql sisho:/tmp/ && ssh sisho chmod 644 /tmp/16_box_add_limited_extra.sql
--   2) 公開サーバーで:     sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/16_box_add_limited_extra.sql
-- 確認（公開サーバー）: SELECT srrelid::regclass, srsubstate FROM pg_subscription_rel ORDER BY 1;   -- 18 行・全部 r
-- 設計: DDL は src/lab17_extra_stats.py の DDL と同一（生成列は公開サーバーが自分で計算・publication には載らない）。

CREATE TABLE IF NOT EXISTS public.limited_color_stats (
  expansion text NOT NULL, event_type text NOT NULL, main_colors text NOT NULL, splash boolean NOT NULL,
  games integer NOT NULL, wins integer NOT NULL,
  wr numeric(6,4) GENERATED ALWAYS AS (round(wins::numeric / nullif(games, 0), 4)) STORED,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, main_colors, splash));
CREATE TABLE IF NOT EXISTS public.limited_matchup_stats (
  expansion text NOT NULL, event_type text NOT NULL, main_colors text NOT NULL, opp_colors text NOT NULL,
  games integer NOT NULL, wins integer NOT NULL,
  wr numeric(6,4) GENERATED ALWAYS AS (round(wins::numeric / nullif(games, 0), 4)) STORED,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, main_colors, opp_colors));
CREATE TABLE IF NOT EXISTS public.limited_format_stats (
  expansion text NOT NULL, event_type text NOT NULL, rank text NOT NULL,
  games integer NOT NULL, wins integer NOT NULL, on_play_games integer NOT NULL, on_play_wins integer NOT NULL,
  turns_sum bigint NOT NULL, mulligans_sum bigint NOT NULL,
  wr numeric(6,4) GENERATED ALWAYS AS (round(wins::numeric / nullif(games, 0), 4)) STORED,
  on_play_wr numeric(6,4) GENERATED ALWAYS AS (round(on_play_wins::numeric / nullif(on_play_games, 0), 4)) STORED,
  avg_turns numeric(6,2) GENERATED ALWAYS AS (round(turns_sum::numeric / nullif(games, 0), 2)) STORED,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, rank));
CREATE TABLE IF NOT EXISTS public.limited_card_rank_stats (
  expansion text NOT NULL, event_type text NOT NULL, rank text NOT NULL, card_name text NOT NULL,
  gih_games integer NOT NULL, gih_wins integer NOT NULL, gp_games integer NOT NULL, gp_wins integer NOT NULL,
  gih_wr numeric(6,4) GENERATED ALWAYS AS (round(gih_wins::numeric / nullif(gih_games, 0), 4)) STORED,
  gp_wr numeric(6,4) GENERATED ALWAYS AS (round(gp_wins::numeric / nullif(gp_games, 0), 4)) STORED,
  db_card_name text, computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, rank, card_name));
CREATE TABLE IF NOT EXISTS public.limited_card_pick_stats (
  expansion text NOT NULL, event_type text NOT NULL, card_name text NOT NULL,
  picks integer NOT NULL, maindeck_rate numeric(6,4), sideboard_in_rate numeric(6,4),
  event_wins_sum integer, event_losses_sum integer, event_picks integer NOT NULL,
  avg_event_wins numeric(6,3) GENERATED ALWAYS AS (round(event_wins_sum::numeric / nullif(event_picks, 0), 3)) STORED,
  db_card_name text, computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, card_name));

GRANT SELECT ON public.limited_color_stats, public.limited_matchup_stats, public.limited_format_stats,
                public.limited_card_rank_stats, public.limited_card_pick_stats TO readonly_ai;

ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION;
