-- prob_functions.sql — 確率計算の SQL 関数（2026-09-04 方針「ひとまず最低限だけ作ろう」）
-- 掟: LLM に算術をさせない。計算は決定的な関数（IMMUTABLE・numeric）で、返り値の入口（MCP の mtg_probability）が式と入力を書き戻す。
-- 走らせ方: VM   docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sql/prob_functions.sql
--           公開サーバー   sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/prob_functions.sql
-- 論理レプリケーションは関数を運ばないので両方で流す（冪等・CREATE OR REPLACE）。readonly_ai は既定の PUBLIC EXECUTE で呼べる。

-- 組み合わせ C(n, k)（numeric・n ≤ 1000 程度まで正確）
CREATE OR REPLACE FUNCTION mtg_comb(n integer, k integer) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE r numeric := 1; i integer;
BEGIN
  IF k < 0 OR k > n THEN RETURN 0; END IF;
  IF k > n - k THEN k := n - k; END IF;
  FOR i IN 1..k LOOP
    r := r * (n - k + i) / i;
  END LOOP;
  RETURN r;
END $$;

-- 超幾何: N 枚中 K 枚の当たりから D 枚引いて、当たりがちょうど m 枚の確率
CREATE OR REPLACE FUNCTION mtg_hypergeom_exact(n integer, k integer, d integer, m integer) RETURNS numeric
LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT CASE WHEN n <= 0 OR d < 0 OR d > n OR k < 0 OR k > n THEN NULL
              WHEN m < 0 OR m > d OR m > k OR d - m > n - k THEN 0
              ELSE round(mtg_comb(k, m) * mtg_comb(n - k, d - m) / mtg_comb(n, d), 6) END
$$;

-- 超幾何: 当たりが m 枚以上の確率
CREATE OR REPLACE FUNCTION mtg_hypergeom_atleast(n integer, k integer, d integer, m integer) RETURNS numeric
LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT CASE WHEN n <= 0 OR d < 0 OR d > n OR k < 0 OR k > n THEN NULL
              WHEN m <= 0 THEN 1
              ELSE round((SELECT coalesce(sum(mtg_comb(k, i) * mtg_comb(n - k, d - i)), 0)
                          FROM generate_series(m, least(k, d)) AS i) / mtg_comb(n, d), 6) END
$$;

-- t ターン目までに見る枚数: 初手 7 枚＋引き（先手は t-1 回・後手は t 回）。mull は マリガン回数（初手が 7-mull 枚）
CREATE OR REPLACE FUNCTION mtg_cards_seen(turn integer, on_play boolean, mull integer DEFAULT 0) RETURNS integer
LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT 7 - mull + greatest(turn - CASE WHEN on_play THEN 1 ELSE 0 END, 0)
$$;

-- t ターン目までに、K 枚入れたカードを m 枚以上引く確率
CREATE OR REPLACE FUNCTION mtg_prob_by_turn(deck integer, copies integer, turn integer, on_play boolean, m integer DEFAULT 1, mull integer DEFAULT 0) RETURNS numeric
LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT mtg_hypergeom_atleast(deck, copies, least(mtg_cards_seen(turn, on_play, mull), deck), m)
$$;

-- t ターン目まで土地を毎ターン置ける確率（＝t ターン目までに見たカードの中に土地が t 枚以上）
CREATE OR REPLACE FUNCTION mtg_land_drops(deck integer, lands integer, turn integer, on_play boolean, mull integer DEFAULT 0) RETURNS numeric
LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT mtg_hypergeom_atleast(deck, lands, least(mtg_cards_seen(turn, on_play, mull), deck), turn)
$$;

-- t ターン目までに A（a 枚）と B（b 枚）の両方を 1 枚以上引く確率（包除: 1 - P(A なし) - P(B なし) + P(両方なし)）
CREATE OR REPLACE FUNCTION mtg_combo_by_turn(deck integer, a integer, b integer, turn integer, on_play boolean, mull integer DEFAULT 0) RETURNS numeric
LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT CASE WHEN a + b > deck THEN NULL ELSE
         round(1 - (mtg_comb(deck - a, d) + mtg_comb(deck - b, d) - mtg_comb(deck - a - b, d)) / mtg_comb(deck, d), 6) END
  FROM (SELECT least(mtg_cards_seen(turn, on_play, mull), deck) AS d) s
$$;
