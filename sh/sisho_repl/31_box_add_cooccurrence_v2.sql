-- 31_box_add_cooccurrence_v2.sql — 公開サーバー（sisho・rag_sisho）側: 共起の作り直しの 4 表を受けて購読を更新する
-- 走らせ方:
--   1) VM から: scp sh/sisho_repl/31_box_add_cooccurrence_v2.sql sisho:/tmp/ && ssh sisho chmod 644 /tmp/31_box_add_cooccurrence_v2.sql
--   2) 公開サーバーで:    sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/31_box_add_cooccurrence_v2.sql
-- 初回の写しは表ごとに進む（分子と分母がそろう前がありうる）＝全部の表が r になり、32 の digest が VM と一致してから道具を配備する。
-- 外部キーは付けない（VM の側で守っている・複製の適用では外部キーの引き金は動かない）。CHECK と主キーは VM（src/build_cooccurrence.py の DDL）と同じ。
-- 公開サーバーの共有バッファ（5GB）へは載せなくてよい（移行後に 5GB 以下なら可）。
CREATE TABLE IF NOT EXISTS public.cooccurrence_populations (
    population_id      integer PRIMARY KEY,
    format_name        text NOT NULL,
    window_code        text NOT NULL CHECK (window_code IN ('recent_90d', 'all')),
    sources            text[] NOT NULL CHECK (cardinality(sources) > 0),
    definition_version integer NOT NULL CHECK (definition_version > 0),
    pair_min_decks     integer NOT NULL CHECK (pair_min_decks >= 1),
    definition         jsonb NOT NULL,
    UNIQUE (format_name, window_code, definition_version));
CREATE TABLE IF NOT EXISTS public.cooccurrence_population_stats (
    population_id           integer PRIMARY KEY,
    build_id                uuid NOT NULL,
    computed_at             timestamptz NOT NULL,
    input_snapshot_at       timestamptz NOT NULL,
    window_start            date,
    window_end              date,
    first_event_date        date,
    latest_event_date       date,
    raw_deck_count          integer NOT NULL CHECK (raw_deck_count >= 0),
    deck_count              integer NOT NULL CHECK (deck_count >= 0),
    unresolved_unique_decks integer NOT NULL CHECK (unresolved_unique_decks >= 0),
    pair_row_count          bigint NOT NULL CHECK (pair_row_count >= 0),
    card_row_count          integer NOT NULL CHECK (card_row_count >= 0),
    content_digest          text NOT NULL,
    coverage                jsonb NOT NULL,
    CHECK (deck_count <= raw_deck_count),
    CHECK (unresolved_unique_decks <= deck_count),
    CHECK (window_start IS NULL OR window_start < window_end));
CREATE TABLE IF NOT EXISTS public.card_population_deck_counts (
    population_id   integer NOT NULL,
    card_id         integer NOT NULL,
    main_deck_count integer NOT NULL CHECK (main_deck_count >= 0),
    side_deck_count integer CHECK (side_deck_count >= 0),
    PRIMARY KEY (population_id, card_id),
    CHECK (main_deck_count > 0 OR COALESCE(side_deck_count, 0) > 0));
CREATE TABLE IF NOT EXISTS public.card_cooccurrence_v2 (
    population_id      integer NOT NULL,
    relation           "char" NOT NULL CHECK (relation IN ('m', 's')),
    card_id_a          integer NOT NULL,
    card_id_b          integer NOT NULL,
    cooccurrence_count integer NOT NULL CHECK (cooccurrence_count > 0),
    PRIMARY KEY (population_id, relation, card_id_a, card_id_b),
    CHECK (relation <> 'm' OR card_id_a < card_id_b));
CREATE INDEX IF NOT EXISTS card_cooccurrence_v2_m_by_b
    ON public.card_cooccurrence_v2 (population_id, card_id_b, card_id_a) WHERE relation = 'm';
GRANT SELECT ON public.cooccurrence_populations, public.cooccurrence_population_stats,
  public.card_population_deck_counts, public.card_cooccurrence_v2 TO readonly_ai;
ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION;
-- 確認（公開サーバー）: SELECT srrelid::regclass, srsubstate FROM pg_subscription_rel WHERE srrelid::regclass::text ~ 'cooccurrence|population' ORDER BY 1;  -- 4 行・全部 r
