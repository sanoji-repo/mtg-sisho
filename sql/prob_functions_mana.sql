-- prob_functions_mana.sql — マナ基盤の確率（castable・hand）の SQL 関数
-- PL/Python は使わず PL/pgSQL で書く（公開サーバーに信頼されない言語を入れない・Python の実行中は statement_timeout が効きにくい）。
-- 数え上げは SQL の集合演算に任せる（generate_series の直積を本体の C で数える・1 件ずつのループより速い）。
-- 走らせ方・複製の注意は prob_functions.sql と同じ（関数は論理レプリケーションで運ばれない＝開発側と公開サーバーの両方で流す・冪等）。
-- 前提: prob_functions.sql（mtg_comb・mtg_cards_seen）が先に入っていること。
--
-- 公開の約束:
--   - どの関数も PUBLIC に EXECUTE がある＝利用者が query_mtg_database から直接呼べる。だから
--     **SQL の断片（文字列）を引数で受け取らない**。動的 SQL に埋め込むのは、関数の中で検査済みの整数だけ。
--     旧版の共通関数 mtg_multi_hypergeom(… cond text) は任意の SELECT を関数の中で走らせられたので廃止（下で DROP）。
--   - **入口（Python）の上限を SQL 側でも同じかそれより厳しく検査する**＝直接呼ばれても計算量が膨らまない。
--     範囲外は NULL を返す（入口は NULL を「この入力では定義できない」として返す）。
--   - 数え上げの概算（類ごとの取りうる枚数の積 × 選択肢の数）が 300,000 を超えたら NULL。

DROP FUNCTION IF EXISTS mtg_multi_hypergeom(integer, integer[], integer[], integer[], integer, text);

-- 配列が 1 次元・長さ n・NULL 要素なし・全部 0 以上か（検査の部品・引数は配列だけ＝SQL の断片を受け取らない）
CREATE OR REPLACE FUNCTION mtg__int_array_ok(a integer[], n integer) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT a IS NOT NULL AND (n = 0 OR array_ndims(a) = 1) AND coalesce(array_length(a, 1), 0) = n
         AND NOT EXISTS (SELECT 1 FROM unnest(a) v WHERE v IS NULL OR v < 0)
$$;

-- castable: t ターン目までに、土地だけで呪文を唱えられる確率（全部の土地がアンタップで出ると仮定）。
--   counts[i]  : 土地の類 i の枚数（同じ「払える記号の種類」の土地をまとめた類・最大 8 類）
--   canpay[i]  : 類 i が払える色の記号の種類のビット（種類 t がビット t-1・最大 6 種類）
--   need       : 選択肢 a × 種類 t の必要数（2 次元・単色混成 {2/G} の払い方の選択肢ごとに 1 行・最大 8 行）
--   mv         : 選択肢 a で必要な土地の数
-- 唱えられる ⇔ ある選択肢 a で「min(L, turn) ≥ mv[a]」かつ Hall の条件
--   （種類の空でない部分集合 S すべてで、S のどれかを払える類の枚数の和 ≥ S の必要数の和＝別々の土地で割り当てられる）。
-- SET jit = off: generate_series の行数見積もり（既定 1000 行）の直積で費用が数百万に膨らみ JIT が毎回走る
-- （実測: 1,260 行の数え上げで 208ms、うち JIT の翻訳が大半）。関数の中だけ切る。
CREATE OR REPLACE FUNCTION mtg_castable(deck integer, counts integer[], canpay integer[], need integer[], mv integer[],
                                        turn integer, on_play boolean, mull integer DEFAULT 0)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE SET jit = off AS $$
DECLARE
  k integer := coalesce(array_length(counts, 1), 0);
  nalt integer := coalesce(array_length(mv, 1), 0);
  ntypes integer := coalesce(array_length(need, 2), 0);
  d integer; other integer; work numeric := 1;
  tab numeric[] := '{}'; off integer[] := '{}';
  from_sql text := ''; w_sql text := ''; l_sql text := '0';
  alt_sql text; alts text := ''; lhs text; req integer;
  s integer; i integer; t integer; a integer; x integer; q text; r numeric;
BEGIN
  -- 入力の検査（Python の入口と同じかそれより厳しく）
  IF deck IS NULL OR turn IS NULL OR on_play IS NULL OR mull IS NULL
     OR deck NOT BETWEEN 1 AND 500 OR turn NOT BETWEEN 1 AND 20 OR mull NOT BETWEEN 0 AND 6
     OR k > 8 OR nalt NOT BETWEEN 1 AND 8 OR ntypes NOT BETWEEN 1 AND 6
     OR NOT mtg__int_array_ok(counts, k) OR NOT mtg__int_array_ok(canpay, k) OR NOT mtg__int_array_ok(mv, nalt)
     OR need IS NULL OR array_ndims(need) <> 2 OR array_length(need, 1) <> nalt
     OR EXISTS (SELECT 1 FROM unnest(need) v WHERE v IS NULL OR v < 0)
     OR EXISTS (SELECT 1 FROM unnest(canpay) v WHERE v >= (1 << ntypes)) THEN
    RETURN NULL;
  END IF;
  other := deck - (SELECT coalesce(sum(c), 0) FROM unnest(counts) c);
  IF other < 0 THEN RETURN NULL; END IF;
  d := least(mtg_cards_seen(turn, on_play, mull), deck);
  FOR i IN 1..k LOOP work := work * (least(counts[i], d) + 1); END LOOP;
  IF work * nalt > 300000 THEN RETURN NULL; END IF;
  IF k = 0 THEN  -- 土地が 1 枚も無い: 必要な土地 0 の選択肢があるときだけ唱えられる
    RETURN CASE WHEN EXISTS (SELECT 1 FROM unnest(mv) m WHERE m = 0) THEN 1 ELSE 0 END;
  END IF;
  -- C(counts[i], x) を 1 本の配列に並べ、類ごとの開始位置を off に。最後に「その他」の C(other, y)
  FOR i IN 1..k LOOP
    off := off || (coalesce(array_length(tab, 1), 0));
    FOR x IN 0..counts[i] LOOP tab := tab || mtg_comb(counts[i], x); END LOOP;
  END LOOP;
  off := off || (coalesce(array_length(tab, 1), 0));
  FOR x IN 0..least(other, d) LOOP tab := tab || mtg_comb(other, x); END LOOP;
  FOR i IN 1..k LOOP
    from_sql := from_sql || format(', generate_series(0, least(%s, %s - (%s))) AS g%s(x%s)', counts[i], d, l_sql, i, i);
    w_sql := w_sql || format(' * $1[%s + x%s + 1]', off[i], i);
    l_sql := l_sql || format(' + x%s', i);
  END LOOP;
  -- 条件式（検査済みの整数だけから組む）: 選択肢ごとに「置ける土地 ≥ 必要数」と Hall の不等式、選択肢どうしは OR
  FOR a IN 1..nalt LOOP
    alt_sql := format('least(%s, %s) >= %s', l_sql, turn, mv[a]);
    FOR s IN 1..(1 << ntypes) - 1 LOOP
      req := 0;
      FOR t IN 1..ntypes LOOP
        IF (s >> (t - 1)) & 1 = 1 THEN req := req + need[a][t]; END IF;
      END LOOP;
      CONTINUE WHEN req = 0;
      lhs := '0';
      FOR i IN 1..k LOOP
        IF canpay[i] & s <> 0 THEN lhs := lhs || format(' + x%s', i); END IF;
      END LOOP;
      alt_sql := alt_sql || format(' AND (%s) >= %s', lhs, req);
    END LOOP;
    alts := alts || CASE WHEN a > 1 THEN ' OR ' ELSE '' END || '(' || alt_sql || ')';
  END LOOP;
  q := format('SELECT coalesce(sum(1%s * $1[%s + (%s - (%s)) + 1]), 0) FROM (SELECT 1) AS z%s WHERE %s - (%s) <= %s AND (%s)',
              w_sql, off[k + 1], d, l_sql, from_sql, d, l_sql, other, alts);
  EXECUTE q INTO r USING tab;
  RETURN round(r / mtg_comb(deck, d), 6);
END $$;

-- hand: t ターン目までに見たカード（turn = 0 なら初手だけ）で、互いに重ならない束 i が lo[i]〜hi[i] 枚に入る確率（束は最大 6）
CREATE OR REPLACE FUNCTION mtg_hand_prob(deck integer, counts integer[], lo integer[], hi integer[],
                                         turn integer, on_play boolean, mull integer DEFAULT 0)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE SET jit = off AS $$
DECLARE
  k integer := coalesce(array_length(counts, 1), 0);
  d integer; other integer; work numeric := 1;
  tab numeric[] := '{}'; off integer[] := '{}';
  from_sql text := ''; w_sql text := ''; l_sql text := '0';
  i integer; x integer; q text; r numeric;
BEGIN
  IF deck IS NULL OR turn IS NULL OR on_play IS NULL OR mull IS NULL
     OR deck NOT BETWEEN 1 AND 500 OR turn NOT BETWEEN 0 AND 20 OR mull NOT BETWEEN 0 AND 6
     OR k NOT BETWEEN 1 AND 6
     OR NOT mtg__int_array_ok(counts, k) OR NOT mtg__int_array_ok(lo, k) OR NOT mtg__int_array_ok(hi, k)
     OR EXISTS (SELECT 1 FROM generate_series(1, k) j WHERE lo[j] > hi[j]) THEN
    RETURN NULL;
  END IF;
  other := deck - (SELECT coalesce(sum(c), 0) FROM unnest(counts) c);
  IF other < 0 THEN RETURN NULL; END IF;
  d := least(CASE WHEN turn = 0 THEN 7 - mull ELSE mtg_cards_seen(turn, on_play, mull) END, deck);
  FOR i IN 1..k LOOP work := work * (greatest(least(hi[i], counts[i], d) - lo[i], 0) + 1); END LOOP;
  IF work > 300000 THEN RETURN NULL; END IF;
  FOR i IN 1..k LOOP
    off := off || (coalesce(array_length(tab, 1), 0));
    FOR x IN 0..counts[i] LOOP tab := tab || mtg_comb(counts[i], x); END LOOP;
  END LOOP;
  off := off || (coalesce(array_length(tab, 1), 0));
  FOR x IN 0..least(other, d) LOOP tab := tab || mtg_comb(other, x); END LOOP;
  FOR i IN 1..k LOOP
    from_sql := from_sql || format(', generate_series(%s, least(%s, %s, %s - (%s))) AS g%s(x%s)',
                                   lo[i], hi[i], counts[i], d, l_sql, i, i);
    w_sql := w_sql || format(' * $1[%s + x%s + 1]', off[i], i);
    l_sql := l_sql || format(' + x%s', i);
  END LOOP;
  q := format('SELECT coalesce(sum(1%s * $1[%s + (%s - (%s)) + 1]), 0) FROM (SELECT 1) AS z%s WHERE %s - (%s) <= %s',
              w_sql, off[k + 1], d, l_sql, from_sql, d, l_sql, other);
  EXECUTE q INTO r USING tab;
  RETURN round(r / mtg_comb(deck, d), 6);
END $$;
