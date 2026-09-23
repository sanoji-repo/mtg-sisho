-- prob_functions_mana.sql — マナ基盤の確率（castable・hand）の SQL 関数
-- PL/Python は使わず PL/pgSQL で書く（公開サーバーに信頼されない言語を入れない・Python の実行中は statement_timeout が効きにくい）。
-- 数え上げは SQL の集合演算に任せる（generate_series の直積を本体の C で数える・1 件ずつのループより速い）。
-- 動的 SQL を組むが、埋め込むのは関数の中で数えた整数だけ（利用者の文字列は入らない）。重い入力は Python の入口が先に断る。
-- 走らせ方・複製の注意は prob_functions.sql と同じ（関数は論理レプリケーションで運ばれない＝開発側と公開サーバーの両方で流す・冪等）。
-- 前提: prob_functions.sql（mtg_comb・mtg_cards_seen）が先に入っていること。

-- 多変量超幾何の和: deck 枚から d 枚引いたとき、類 i（counts[i] 枚）を x_i 枚引く組み合わせのうち、
-- cond（x1..xk と :L＝x1+…+xk を使う真偽式）を満たす物の確率。lo/hi は各 x_i の範囲（探索を絞るだけ）。
-- 残り（deck − Σcounts）は「その他」の類として自動で数える。cond は関数の中で整数から組んだ物だけを渡すこと。
-- SET jit = off: generate_series の行数見積もり（既定 1000 行）の直積で費用が数百万に膨らみ JIT が毎回走る
-- （実測: 1,260 行の数え上げで 208ms、うち JIT の翻訳が大半）。関数の中だけ切る。
CREATE OR REPLACE FUNCTION mtg_multi_hypergeom(deck integer, counts integer[], lo integer[], hi integer[], d integer, cond text)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE SET jit = off AS $$
DECLARE
  k integer := coalesce(array_length(counts, 1), 0);
  other integer := deck - (SELECT coalesce(sum(c), 0) FROM unnest(counts) c);
  tab numeric[] := '{}';
  off integer[] := '{}';
  i integer; x integer;
  from_sql text := ''; w_sql text := ''; l_sql text := '0';
  q text; r numeric;
BEGIN
  IF other < 0 OR d < 0 OR d > deck THEN RETURN NULL; END IF;
  FOR i IN 1..k LOOP                       -- C(counts[i], x) を 1 本の配列に並べ、類ごとの開始位置を off に
    off := off || (coalesce(array_length(tab, 1), 0));
    FOR x IN 0..counts[i] LOOP tab := tab || mtg_comb(counts[i], x); END LOOP;
  END LOOP;
  off := off || (coalesce(array_length(tab, 1), 0));   -- 「その他」の C(other, y)
  FOR x IN 0..other LOOP tab := tab || mtg_comb(other, x); END LOOP;
  FOR i IN 1..k LOOP
    from_sql := from_sql || format(', generate_series(%s, least(%s, %s - (%s))) AS g%s(x%s)',
                                   greatest(lo[i], 0), least(hi[i], counts[i]), d, l_sql, i, i);
    w_sql := w_sql || format(' * $1[%s + x%s + 1]', off[i], i);
    l_sql := l_sql || format(' + x%s', i);
  END LOOP;
  q := format('SELECT coalesce(sum(1%s * $1[%s + (%s - (%s)) + 1]), 0) FROM (SELECT 1) AS z%s WHERE %s - (%s) <= %s AND (%s)',
              w_sql, off[k + 1], d, l_sql, from_sql, d, l_sql, other,
              replace(cond, ':L', '(' || l_sql || ')'));
  EXECUTE q INTO r USING tab;
  RETURN round(r / mtg_comb(deck, d), 6);
END $$;

-- castable: t ターン目までに、土地だけで呪文を唱えられる確率（全部の土地がアンタップで出ると仮定）。
--   counts[i]  : 土地の類 i の枚数（同じ「払える記号の種類」の土地をまとめた類）
--   canpay[i]  : 類 i が払える色の記号の種類のビット（種類 t がビット t-1）
--   need       : 選択肢 a × 種類 t の必要数（2 次元・単色混成 {2/G} の払い方の選択肢ごとに 1 行。無ければ 1 行）
--   mv         : 選択肢 a のマナ総量
-- 唱えられる ⇔ ある選択肢 a で「min(L, turn) ≥ mv[a]」かつ Hall の条件
--   （種類の空でない部分集合 S すべてで、S のどれかを払える類の枚数の和 ≥ S の必要数の和＝別々の土地で割り当てられる）。
CREATE OR REPLACE FUNCTION mtg_castable(deck integer, counts integer[], canpay integer[], need integer[], mv integer[],
                                        turn integer, on_play boolean, mull integer DEFAULT 0)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  k integer := coalesce(array_length(counts, 1), 0);
  nalt integer := array_length(mv, 1);
  ntypes integer := coalesce(array_length(need, 2), 0);
  d integer := least(mtg_cards_seen(turn, on_play, mull), deck);
  alt_sql text; alts text := ''; s integer; i integer; t integer; a integer;
  lhs text; req integer; lo integer[] := '{}'; hi integer[] := '{}';
BEGIN
  IF nalt IS NULL OR ntypes > 6 THEN RETURN NULL; END IF;
  FOR i IN 1..k LOOP lo := lo || 0; hi := hi || counts[i]; END LOOP;
  FOR a IN 1..nalt LOOP
    alt_sql := format('least(:L, %s) >= %s', turn, mv[a]);
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
  IF k = 0 THEN  -- 土地が 1 枚も無い: マナ総量 0 の選択肢があるときだけ唱えられる
    RETURN CASE WHEN EXISTS (SELECT 1 FROM unnest(mv) m WHERE m = 0) THEN 1 ELSE 0 END;
  END IF;
  RETURN mtg_multi_hypergeom(deck, counts, lo, hi, d, alts);
END $$;

-- hand: t ターン目までに見たカード（turn = 0 なら初手だけ）で、互いに重ならない束 i が lo[i]〜hi[i] 枚に入る確率
CREATE OR REPLACE FUNCTION mtg_hand_prob(deck integer, counts integer[], lo integer[], hi integer[],
                                         turn integer, on_play boolean, mull integer DEFAULT 0)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE d integer := least(CASE WHEN turn <= 0 THEN 7 - mull ELSE mtg_cards_seen(turn, on_play, mull) END, deck);
BEGIN
  RETURN mtg_multi_hypergeom(deck, counts, lo, hi, d, 'true');
END $$;
