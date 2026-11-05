-- partner_functions.sql — 共起から相方を読むための SQL 関数
-- 見方（読みの規則）: 共起は「同じ色のデッキの中の土台」「相手の札があるときの割合」「逆向きの割合」の三つと母数で読む。
--   土台が高い札は、その色の定番か、その色の大半を占める型の芯。定番かどうかは、いくつもの色の組（型）にまたがって
--   高いかで確かめる（色の中で 7 割を超える札はまれで、多くの色の上位は一つか二つの型の芯）。
--   土台が低いか中くらいなのに相手ありでほぼ 100% に跳ね、逆向きも高い組が本当の相方。
--   片方向だけ跳ねる組は、片方が頼る組か同じ型の仲間。数字が出すのは候補まで（なぜ組なのかはカードの本文で確かめる）。
-- 数え方: 同じ中身（メインとサイドの札の組）のリストは 1 本に数える。枚数は数えない。基本土地は相方に出さない。
--   「同じ色のデッキ」＝起点のカードの色を全部含むリスト（無色の起点ならそのフォーマットの全部）。土台も逆向きもこの中で数える
--   （色を混ぜると、ほかの色のデッキに入る分が逆向きを薄める）。起点の型が同じ色のリストの大半を占めると土台が上がり倍率は小さく出る
--   ＝そのときは逆向きの方が効く。数字は型の芯どうし（噛み合っていなくても一緒に入る組）も高く出すので、理由は本文で確かめる。
--   倍率には上限がある（起点のリストにしか入らない札は、何本に入っていても上限に張り付く）。倍率だけで並べず、pct_with と並べて読む。
-- 走らせ方: 開発側だけ  docker exec -i pg18-primary psql -U devuser -d rag_dev -v ON_ERROR_STOP=1 -f - < sql/partner_functions.sql
-- 公開サーバーには置かない: 呼ぶたびに直近のリストを全部読み直すので重い（モダン 90 日は公開サーバーで 8〜10 秒＝客の SQL の 10 秒の
--   打ち切りにかかる）。夜間の共起の表から引く形にして速くするまでは開発側の分析用（冪等・CREATE OR REPLACE）。
--   客には案内しない内部の道具＝公開側の MCP の道具の説明にも載せない。
--
-- 使い方: SELECT * FROM mtg_card_partners('Pioneer', 'Greasefang, Okiba Boss');
--         SELECT * FROM mtg_card_partners('Pioneer', 'Faerie Miscreant', 90, 'main', 3) ORDER BY ratio DESC;
--         サイドボードの相方: mtg_card_partners('Pioneer', 'Thoughtseize', 90, 'side')

CREATE OR REPLACE FUNCTION mtg_card_partners(
    p_format   text,
    p_card     text,
    p_days     integer DEFAULT 90,
    p_board    text    DEFAULT 'main',
    p_min_with integer DEFAULT 3)
RETURNS TABLE (
    partner        text,     -- 相方の名前の完成形《日本語名/英語名》
    n_with         integer,  -- 起点をメインに入れたリストのうち、相方も入れている本数
    pct_with       numeric,  -- その割合（%）
    n_partner      integer,  -- 同じ色のリストのうち、相方を入れている本数（起点の有無を問わない）
    pct_back       numeric,  -- 逆向き: 同じ色のリストで相方を入れたもののうち、起点も入れている割合（%）
    pct_base       numeric,  -- 同じ色のリストでの相方の普段の採用（%）＝土台
    ratio          numeric,  -- 倍率: pct_with / pct_base（同じ色の中で、起点があると相方がどれだけ増えるか）
    n_anchor_lists integer,  -- 起点をメインに入れたリストの本数（母数）
    n_color_lists  integer,  -- 同じ色のリストの本数（土台の母数）
    color_group    text      -- 起点の色（W/U/B/R/G の並び・無色なら空）
)
LANGUAGE sql STABLE AS $$
WITH anchor AS (
    SELECT id, coalesce(colors, '{}'::text[]) AS colors
    FROM mtg_cards_v2
    WHERE card_name = p_card OR name_en_front = p_card
    ORDER BY (card_name = p_card) DESC
    LIMIT 1),
decks AS (
    SELECT id FROM deck_list
    WHERE format_name = p_format AND tournament_date >= current_date - p_days),
sig AS (   -- リストの中身の指紋（同じ中身は 1 本に数える）
    SELECT dc.deck_id, md5(string_agg(dc.board || ':' || dc.card_id, ',' ORDER BY dc.board, dc.card_id)) AS s
    FROM deck_cards dc JOIN decks d ON d.id = dc.deck_id
    WHERE dc.card_id IS NOT NULL
    GROUP BY dc.deck_id),
lists AS (SELECT DISTINCT ON (s) deck_id FROM sig ORDER BY s, deck_id),
cards AS (
    SELECT DISTINCT dc.deck_id, dc.card_id, dc.board
    FROM deck_cards dc JOIN lists l ON l.deck_id = dc.deck_id
    WHERE dc.card_id IS NOT NULL),
list_colors AS (
    SELECT c.deck_id, array_agg(DISTINCT col) AS cols
    FROM cards c JOIN mtg_cards_v2 m ON m.id = c.card_id
    CROSS JOIN LATERAL unnest(coalesce(m.colors, '{}'::text[])) AS col
    WHERE c.board = 'main'
    GROUP BY c.deck_id),
color_lists AS (
    SELECT l.deck_id
    FROM lists l CROSS JOIN anchor a
    LEFT JOIN list_colors lc ON lc.deck_id = l.deck_id
    WHERE coalesce(lc.cols, '{}'::text[]) @> a.colors),
anchor_lists AS (
    SELECT DISTINCT c.deck_id FROM cards c JOIN anchor a ON a.id = c.card_id WHERE c.board = 'main'),
counts AS (
    SELECT c.card_id,
           count(DISTINCT c.deck_id) FILTER (WHERE c.deck_id IN (SELECT deck_id FROM anchor_lists)) AS n_with,
           count(DISTINCT c.deck_id) FILTER (WHERE c.deck_id IN (SELECT deck_id FROM color_lists))  AS n_color
    FROM cards c
    WHERE c.board = p_board
    GROUP BY c.card_id),
na AS (SELECT count(*) AS n FROM anchor_lists),
nc AS (SELECT count(*) AS n FROM color_lists)
SELECT m.name_display,
       k.n_with::integer,
       round(100.0 * k.n_with / nullif(na.n, 0), 1),
       k.n_color::integer,
       round(100.0 * k.n_with / nullif(k.n_color, 0), 1),
       round(100.0 * k.n_color / nullif(nc.n, 0), 1),
       round((k.n_with::numeric / nullif(na.n, 0)) / nullif(k.n_color::numeric / nullif(nc.n, 0), 0), 2),
       na.n::integer,
       nc.n::integer,
       array_to_string(a.colors, '')
FROM counts k
JOIN mtg_cards_v2 m ON m.id = k.card_id
CROSS JOIN na CROSS JOIN nc CROSS JOIN anchor a
WHERE k.card_id <> a.id
  AND k.n_with >= p_min_with
  AND coalesce(m.type_line, '') !~ 'Basic Land'
ORDER BY k.n_with DESC, m.card_name
$$;

COMMENT ON FUNCTION mtg_card_partners(text, text, integer, text, integer) IS
  '共起から相方を読む: 同じ色の土台（pct_base）・相手ありの割合（pct_with）・逆向き（pct_back）・倍率（ratio）と母数。土台が高い札は定番か型の芯（いくつもの色の組にまたがるかで見分ける）、低い土台から相手ありでほぼ 100% に跳ねて逆向きも高い組が相方の候補。倍率は上限に張り付くので pct_with と並べて読む。理由は本文で確かめる。';
