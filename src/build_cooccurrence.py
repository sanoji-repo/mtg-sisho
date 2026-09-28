#!/usr/bin/env python3
"""build_cooccurrence — 共起をフォーマット × 区間の母集団ごとに数える。

旧 card_cooccurrence（構築・名前鍵）と edh_card_cooccurrence_v2（EDH）と、相方検索の分母
card_scope_deck_counts・scope_deck_counts を置き換える 4 表を作る:

  cooccurrence_populations       母集団の定義（フォーマット × 区間・18 行・--migrate で種を入れる）
  cooccurrence_population_stats  母集団ごとの N・区間・品質・検収用の digest（毎晩 1 行ずつ）
  card_population_deck_counts    カードごとの M（メインにあるリスト数）と S（サイドにあるリスト数）
  card_cooccurrence_v2           組（m＝メイン同士・a<b／s＝メインの A → サイドの B・A=B も保存）

## 数え方
- 1 本＝異なるフルリスト。全 board・枚数込み・未解決の名前も含めた署名が同じリストは、母集団の中で
  取得元をまたいで 1 本に潰す。区間で絞ってから潰す（全期間の代表を区間へ流用しない）。
- メインの集合は card_id の DISTINCT。Duel Commander・Commander・Precon は統率者をメインに合流する。
- サイドの関係は構築 7 フォーマットだけ（統率者戦・デュエル・コマンダー・Precon は作らない）。
- 基本土地（型の行が Basic で始まる 12 ID）は組にも分母にも入れない。普通の土地は残す。
- 名前が解決できないカードは組に入れないが、そのリストは N と署名に残す（未解決の数は stats に）。
- mtgtop8 系の MTGO 転載行は、MTGO 公式が同じフォーマットを覆っている期間だけ外す（採用率と同じ条件）。
- 組の保存下限: 直近 90 日は 1・全期間は 2（分母は下限と無関係に全部数える）。

## 流れ
1. 1 つの REPEATABLE READ の読み取りで全部の母集団を一時表に作る（区間の終わり＝その日の UTC の 0 時・含まない）
2. 母集団ごとに検算（M ≤ N・S ≤ N・同居 ≤ 両側の分母）。破れた母集団は適用しない（前の完成版のまま）
3. 母集団ごとに 1 トランザクションで、組・分母を差分適用し、digest を取って stats を書く
   （分子と分母と stats が同じ瞬間に切り替わる＝公開サーバーの論理複製も 1 トランザクションずつ届く）

使い方:
  python src/build_cooccurrence.py --migrate      # 表を作って母集団の種を入れる（初回だけ）
  python src/build_cooccurrence.py                # 毎晩（差分適用）
  python src/build_cooccurrence.py --dry-run      # 作って検算するだけ（適用しない）
  python src/build_cooccurrence.py --only 3,4     # 一部の母集団だけ
  python src/build_cooccurrence.py --verify       # 適用の後に、表と作り直しの結果を両方向の EXCEPT で突き合わせる
"""
import argparse
import json
import sys
import time
import uuid

import psycopg2

from db_config import get_db_config, ddl_cursor

CONSTRUCTED = [
    ("Standard", ["mtgo", "mtgtop8"]),
    ("Pioneer", ["mtgo", "mtgtop8"]),
    ("Modern", ["mtgo", "mtgtop8"]),
    ("Legacy", ["mtgo", "mtgtop8"]),
    ("Premodern", ["mtgo_other"]),
    ("Pauper", ["mtgo_pauper", "mtgtop8_pauper"]),
    ("Vintage", ["mtgo_vintage", "mtgtop8_vintage"]),
]
RECENT_DAYS = 90
MIN_RECENT, MIN_ALL = 1, 2


def population_seed():
    """母集団の種（population_id は固定＝客の SQL や記録が番号で指せるように）。"""
    rows, pid = [], 0
    common = {"unit": "distinct full list (all boards, counts, unresolved names) deduplicated across sources within the window",
              "basic_lands": "excluded (type_line starts with Basic)",
              "unresolved": "kept in N and the signature, not in pairs or card counts",
              "mtgtop8_mtgo_reprint": "excluded while MTGO official covers the format"}
    for fmt, sources in CONSTRUCTED + [("Duel Commander", ["mtgo_edh", "mtgtop8_edh"])]:
        rel = ["m"] if fmt == "Duel Commander" else ["m", "s"]
        merge = fmt == "Duel Commander"
        for window, floor in (("recent_90d", MIN_RECENT), ("all", MIN_ALL)):
            pid += 1
            rows.append((pid, fmt, window, sources, 1, floor,
                         {**common, "supported_relations": rel, "commander_merged_into_main": merge,
                          "window": f"last {RECENT_DAYS} days by tournament_date, end exclusive" if window == "recent_90d"
                          else "all dated lists, end exclusive"}))
    for fmt, sources in (("Commander", ["moxfield_edh"]), ("Precon", ["mtgjson_precon"])):
        pid += 1
        rows.append((pid, fmt, "all", sources, 1, MIN_ALL,
                     {**common, "supported_relations": ["m"], "commander_merged_into_main": True,
                      "window": "all lists (no event date)", "side_board": "not counted"}))
    return rows


DDL = """
CREATE TABLE IF NOT EXISTS cooccurrence_populations (
    population_id      integer PRIMARY KEY,
    format_name        text NOT NULL,
    window_code        text NOT NULL CHECK (window_code IN ('recent_90d', 'all')),
    sources            text[] NOT NULL CHECK (cardinality(sources) > 0),
    definition_version integer NOT NULL CHECK (definition_version > 0),
    pair_min_decks     integer NOT NULL CHECK (pair_min_decks >= 1),
    definition         jsonb NOT NULL,
    UNIQUE (format_name, window_code, definition_version)
);
CREATE TABLE IF NOT EXISTS cooccurrence_population_stats (
    population_id           integer PRIMARY KEY REFERENCES cooccurrence_populations,
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
    CHECK (window_start IS NULL OR window_start < window_end)
);
CREATE TABLE IF NOT EXISTS card_population_deck_counts (
    population_id   integer NOT NULL REFERENCES cooccurrence_populations,
    card_id         integer NOT NULL REFERENCES mtg_cards_v2(id),
    main_deck_count integer NOT NULL CHECK (main_deck_count >= 0),
    side_deck_count integer CHECK (side_deck_count >= 0),
    PRIMARY KEY (population_id, card_id),
    CHECK (main_deck_count > 0 OR COALESCE(side_deck_count, 0) > 0)
);
CREATE TABLE IF NOT EXISTS card_cooccurrence_v2 (
    population_id      integer NOT NULL REFERENCES cooccurrence_populations,
    relation           "char" NOT NULL CHECK (relation IN ('m', 's')),
    card_id_a          integer NOT NULL REFERENCES mtg_cards_v2(id),
    card_id_b          integer NOT NULL REFERENCES mtg_cards_v2(id),
    cooccurrence_count integer NOT NULL CHECK (cooccurrence_count > 0),
    PRIMARY KEY (population_id, relation, card_id_a, card_id_b),
    CHECK (relation <> 'm' OR card_id_a < card_id_b)
);
CREATE INDEX IF NOT EXISTS card_cooccurrence_v2_m_by_b
    ON card_cooccurrence_v2 (population_id, card_id_b, card_id_a) WHERE relation = 'm';
"""
TABLES = ("cooccurrence_populations", "cooccurrence_population_stats", "card_population_deck_counts", "card_cooccurrence_v2")


# ─── 1. 一時表に全部の母集団を作る（1 つのスナップショット）──────────────────

STAGE_SQL = [
    # 対象のデッキ（定義された取得元だけ・mtgtop8 の MTGO 転載は被覆期間だけ外す）
    """CREATE TEMP TABLE _cz_decks AS
       SELECT d.id AS deck_id, d.format_name, d.source, d.tournament_date
       FROM deck_list d
       JOIN (SELECT DISTINCT format_name, unnest(sources) AS source FROM cooccurrence_populations) p
         ON p.format_name = d.format_name AND p.source = d.source
       WHERE NOT COALESCE(d.source LIKE 'mtgtop8%%' AND d.tournament_name LIKE 'MTGO %%'
                 AND d.tournament_date >= (SELECT MIN(o.tournament_date) FROM deck_list o
                                           WHERE o.source LIKE 'mtgo%%' AND o.format_name = d.format_name), false)""",
    # 行（同じ board・同じカードは枚数を足す。未解決は名前のまま）
    """CREATE TEMP TABLE _cz_lines AS
       SELECT dc.deck_id, dc.board, dc.card_id,
              CASE WHEN dc.card_id IS NULL THEN dc.card_name END AS unresolved_name,
              sum(dc.count) AS n
       FROM deck_cards dc JOIN _cz_decks d ON d.deck_id = dc.deck_id
       GROUP BY 1, 2, 3, 4""",
    # 署名（不正なリスト＝枚数が正でない行を持つものは外す）
    """CREATE TEMP TABLE _cz_sig AS
       SELECT deck_id,
              md5(string_agg(board || ':' || COALESCE('i' || card_id, 'n' || unresolved_name) || 'x' || n, ','
                             ORDER BY board, COALESCE('i' || card_id, 'n' || unresolved_name))) AS sig,
              bool_or(card_id IS NULL) AS has_unresolved
       FROM _cz_lines GROUP BY deck_id HAVING bool_and(n > 0)""",
    "CREATE INDEX ON _cz_lines (deck_id)",
    "ANALYZE _cz_decks", "ANALYZE _cz_lines", "ANALYZE _cz_sig",
    # 母集団の区間（区間の終わりはスナップショットの UTC の日付・含まない）
    """CREATE TEMP TABLE _cz_pops AS
       SELECT p.population_id, p.format_name, p.window_code, p.pair_min_decks,
              p.definition -> 'supported_relations' ? 's' AS has_side,
              (p.definition ->> 'commander_merged_into_main')::boolean AS merge_commander,
              CASE WHEN p.window_code = 'recent_90d' THEN %(end)s::date - %(days)s END AS window_start,
              CASE WHEN EXISTS (SELECT 1 FROM _cz_decks d WHERE d.format_name = p.format_name
                                AND d.tournament_date IS NOT NULL) THEN %(end)s::date END AS window_end
       FROM cooccurrence_populations p
       WHERE p.population_id = ANY(%(only)s)""",
    # 区間の中のリスト（潰す前）
    """CREATE TEMP TABLE _cz_raw AS
       SELECT p.population_id, d.deck_id, d.source, d.tournament_date, s.sig, s.has_unresolved
       FROM _cz_pops p
       JOIN _cz_decks d ON d.format_name = p.format_name
       JOIN _cz_sig s ON s.deck_id = d.deck_id
       WHERE (p.window_end IS NULL OR d.tournament_date < p.window_end)
         AND (p.window_start IS NULL OR d.tournament_date >= p.window_start)""",
    # 代表（署名ごとに一番小さい deck_id）
    """CREATE TEMP TABLE _cz_pd AS
       SELECT DISTINCT ON (population_id, sig) population_id, deck_id, source, has_unresolved
       FROM _cz_raw ORDER BY population_id, sig, deck_id""",
    # メインの集合（統率者の合流は母集団の定義どおり）とサイドの集合
    # 基本土地は ID の一覧で除く（2,100 万行ごとにカード表と結合して型の行を正規表現で見ると遅い）
    "CREATE TEMP TABLE _cz_basic AS SELECT id FROM mtg_cards_v2 WHERE type_line ~ '^Basic'",
    # メインと統率者の合流は 2 つの素直な問い合わせに分ける（OR で 1 つにすると計画が崩れて 280 秒・分けて数秒）
    """CREATE TEMP TABLE _cz_main AS
       SELECT DISTINCT x.population_id, x.deck_id, x.card_id FROM (
         SELECT pd.population_id, l.deck_id, l.card_id
         FROM _cz_pd pd JOIN _cz_lines l ON l.deck_id = pd.deck_id
         WHERE l.board = 'main' AND l.card_id IS NOT NULL
         UNION ALL
         SELECT pd.population_id, l.deck_id, l.card_id
         FROM _cz_pd pd JOIN _cz_lines l ON l.deck_id = pd.deck_id
         WHERE l.board = 'commander' AND l.card_id IS NOT NULL
           AND pd.population_id IN (SELECT population_id FROM _cz_pops WHERE merge_commander)) x
       WHERE x.card_id NOT IN (SELECT id FROM _cz_basic)""",
    """CREATE TEMP TABLE _cz_side AS
       SELECT DISTINCT pd.population_id, l.deck_id, l.card_id
       FROM _cz_pd pd JOIN _cz_lines l ON l.deck_id = pd.deck_id
       WHERE l.board = 'side' AND l.card_id IS NOT NULL
         AND pd.population_id IN (SELECT population_id FROM _cz_pops WHERE has_side)
         AND l.card_id NOT IN (SELECT id FROM _cz_basic)""",
    "CREATE INDEX ON _cz_main (population_id, deck_id)",
    "CREATE INDEX ON _cz_side (population_id, deck_id)",
    "ANALYZE _cz_main", "ANALYZE _cz_side",
    # 分母（サイドを作らない母集団は S を NULL）
    """CREATE TEMP TABLE _cz_counts AS
       SELECT x.population_id, x.card_id,
              count(*) FILTER (WHERE x.b = 'm')::int AS main_deck_count,
              CASE WHEN p.has_side THEN count(*) FILTER (WHERE x.b = 's')::int END AS side_deck_count
       FROM (SELECT population_id, card_id, 'm' AS b FROM _cz_main
             UNION ALL SELECT population_id, card_id, 's' FROM _cz_side) x
       JOIN _cz_pops p USING (population_id)
       GROUP BY x.population_id, x.card_id, p.has_side""",
    """CREATE TEMP TABLE _cz_pairs (population_id int, relation "char", card_id_a int, card_id_b int,
                                    cooccurrence_count int)""",
]

# 組は母集団ごとに作る（1 文で全部やると並べ替えが巨大になる）
PAIR_M_SQL = """
INSERT INTO _cz_pairs
SELECT %(pid)s, 'm', a.card_id, b.card_id, count(*)
FROM _cz_main a JOIN _cz_main b
  ON b.population_id = a.population_id AND b.deck_id = a.deck_id AND a.card_id < b.card_id
WHERE a.population_id = %(pid)s
GROUP BY a.card_id, b.card_id
HAVING count(*) >= %(floor)s"""
PAIR_S_SQL = """
INSERT INTO _cz_pairs
SELECT %(pid)s, 's', a.card_id, b.card_id, count(*)
FROM _cz_main a JOIN _cz_side b
  ON b.population_id = a.population_id AND b.deck_id = a.deck_id
WHERE a.population_id = %(pid)s
GROUP BY a.card_id, b.card_id
HAVING count(*) >= %(floor)s"""

# ─── 2. 検算 ───────────────────────────────────────────────────────────
CHECK_SQL = """
WITH n AS (SELECT count(*) AS n FROM _cz_pd WHERE population_id = %(pid)s),
c AS (SELECT * FROM _cz_counts WHERE population_id = %(pid)s),
pr AS (SELECT pr.relation, pr.cooccurrence_count AS k, ca.main_deck_count AS ma,
              CASE pr.relation WHEN 'm' THEN cb.main_deck_count ELSE cb.side_deck_count END AS nb
       FROM _cz_pairs pr
       LEFT JOIN c ca ON ca.card_id = pr.card_id_a
       LEFT JOIN c cb ON cb.card_id = pr.card_id_b
       WHERE pr.population_id = %(pid)s)
SELECT
  (SELECT n FROM n) AS n,
  -- 分母は N を超えない
  (SELECT count(*) FROM c WHERE main_deck_count > (SELECT n FROM n) OR side_deck_count > (SELECT n FROM n)) AS bad_counts,
  -- 組の両端に分母の行がある（B の欠落も数える）
  (SELECT count(*) FROM pr WHERE ma IS NULL OR nb IS NULL) AS orphan_pairs,
  -- 上限: 同居 ≤ min(M(A), 分母(B))／下限: M(A) + 分母(B) − 同居 ≤ N（成り立たない交差を止める）
  (SELECT count(*) FROM pr WHERE ma IS NOT NULL AND nb IS NOT NULL
      AND (k > least(ma, nb) OR ma + nb - k > (SELECT n FROM n))) AS bad_pairs
"""

# ─── 3. digest（公開サーバーでも同じ文で数えて一致を見る＝初回の写しの検収）─────────────
DIGEST_SQL = """
SELECT 'pairs=' || count(*) || ':' || COALESCE(sum(hashtextextended(
          relation::text || ',' || card_id_a || ',' || card_id_b || ',' || cooccurrence_count, 0)::numeric), 0)
FROM card_cooccurrence_v2 WHERE population_id = %(pid)s
UNION ALL
SELECT 'cards=' || count(*) || ':' || COALESCE(sum(hashtextextended(
          card_id || ',' || main_deck_count || ',' || COALESCE(side_deck_count::text, '-'), 0)::numeric), 0)
FROM card_population_deck_counts WHERE population_id = %(pid)s
"""

STATS_SQL = """
INSERT INTO cooccurrence_population_stats AS s
  (population_id, build_id, computed_at, input_snapshot_at, window_start, window_end,
   first_event_date, latest_event_date, raw_deck_count, deck_count, unresolved_unique_decks,
   pair_row_count, card_row_count, content_digest, coverage)
SELECT p.population_id, %(build)s, now(), %(snap)s, p.window_start, p.window_end,
       (SELECT min(tournament_date) FROM _cz_raw r WHERE r.population_id = p.population_id),
       (SELECT max(tournament_date) FROM _cz_raw r WHERE r.population_id = p.population_id),
       (SELECT count(*) FROM _cz_raw r WHERE r.population_id = p.population_id),
       (SELECT count(*) FROM _cz_pd d WHERE d.population_id = p.population_id),
       (SELECT count(*) FROM _cz_pd d WHERE d.population_id = p.population_id AND d.has_unresolved),
       %(pairs)s, %(cards)s, %(digest)s,
       jsonb_build_object(
         'raw_by_source', (SELECT jsonb_object_agg(source, n) FROM
                             (SELECT source, count(*) n FROM _cz_raw r WHERE r.population_id = p.population_id GROUP BY 1) x),
         'kept_by_source', (SELECT jsonb_object_agg(source, n) FROM
                             (SELECT source, count(*) n FROM _cz_pd d WHERE d.population_id = p.population_id GROUP BY 1) x))
FROM _cz_pops p WHERE p.population_id = %(pid)s
ON CONFLICT (population_id) DO UPDATE SET
  build_id = EXCLUDED.build_id, computed_at = EXCLUDED.computed_at, input_snapshot_at = EXCLUDED.input_snapshot_at,
  window_start = EXCLUDED.window_start, window_end = EXCLUDED.window_end,
  first_event_date = EXCLUDED.first_event_date, latest_event_date = EXCLUDED.latest_event_date,
  raw_deck_count = EXCLUDED.raw_deck_count, deck_count = EXCLUDED.deck_count,
  unresolved_unique_decks = EXCLUDED.unresolved_unique_decks, pair_row_count = EXCLUDED.pair_row_count,
  card_row_count = EXCLUDED.card_row_count, content_digest = EXCLUDED.content_digest, coverage = EXCLUDED.coverage
"""


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_tables(conn, migrate: bool):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)", (list(TABLES),))
        have = cur.fetchone()[0]
    conn.rollback()
    if have == len(TABLES) and not migrate:
        return
    if not migrate:
        raise SystemExit(f"共起の新しい表が {have}/{len(TABLES)} しかありません。初回は --migrate で作ってください。")
    with ddl_cursor(conn) as cur:
        cur.execute(DDL)
        for pid, fmt, window, sources, ver, floor, definition in population_seed():
            cur.execute("""INSERT INTO cooccurrence_populations
                           (population_id, format_name, window_code, sources, definition_version, pair_min_decks, definition)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (population_id) DO UPDATE SET format_name = EXCLUDED.format_name,
                             window_code = EXCLUDED.window_code, sources = EXCLUDED.sources,
                             definition_version = EXCLUDED.definition_version,
                             pair_min_decks = EXCLUDED.pair_min_decks, definition = EXCLUDED.definition""",
                        (pid, fmt, window, sources, ver, floor, json.dumps(definition, ensure_ascii=False)))
    log(f"表と母集団の種（{len(population_seed())} 行）を用意した")


def stage(conn, only: list[int]) -> dict:
    """一時表を 1 つの REPEATABLE READ で作る。返り値は母集団ごとの (下限, サイドの有無) とスナップショットの時刻。"""
    conn.set_session(isolation_level="REPEATABLE READ")
    with conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '512MB'")
        cur.execute("SELECT now(), (now() AT TIME ZONE 'UTC')::date")
        snap, end = cur.fetchone()
        params = {"end": end, "days": RECENT_DAYS, "only": only}
        for sql in STAGE_SQL:
            t = time.time()
            cur.execute(sql, params)
            head = " ".join(sql.split())[:60]
            log(f"  {head} … {cur.rowcount if cur.rowcount >= 0 else ''} 行 {time.time() - t:.1f}s")
        cur.execute("SELECT population_id, format_name, window_code, pair_min_decks, has_side FROM _cz_pops ORDER BY 1")
        pops = cur.fetchall()
        for pid, fmt, window, floor, has_side in pops:
            t = time.time()
            cur.execute(PAIR_M_SQL, {"pid": pid, "floor": floor})
            nm = cur.rowcount
            ns = 0
            if has_side:
                cur.execute(PAIR_S_SQL, {"pid": pid, "floor": floor})
                ns = cur.rowcount
            log(f"  組 {pid:2d} {fmt} {window}: m {nm:,} ・ s {ns:,}  {time.time() - t:.1f}s")
        cur.execute("CREATE INDEX ON _cz_pairs (population_id)")
        cur.execute("ANALYZE _cz_pairs")
        cur.execute("ANALYZE _cz_counts")
    conn.commit()           # 一時表はセッションの間は残る（ON COMMIT DROP を付けていない）
    conn.set_session(isolation_level="READ COMMITTED")
    return {"snap": snap, "end": end, "pops": pops}


def check(conn, pid) -> dict:
    with conn.cursor() as cur:
        cur.execute(CHECK_SQL, {"pid": pid})
        n, bad_counts, orphan, bad_pairs = cur.fetchone()
    conn.rollback()
    return {"n": n, "bad_counts": bad_counts, "bad_pairs": bad_pairs, "orphan_pairs": orphan,
            "ok": n > 0 and bad_counts == 0 and bad_pairs == 0 and orphan == 0}


def apply_population(conn, pid, snap) -> dict:
    """1 つの母集団の組・分母・stats を 1 トランザクションで確定する。"""
    from diff_apply import diff_apply
    try:
        p = diff_apply(conn, "card_cooccurrence_v2",
                       keys=("population_id", "relation", "card_id_a", "card_id_b"), vals=("cooccurrence_count",),
                       select_sql="SELECT population_id, relation, card_id_a, card_id_b, cooccurrence_count"
                                  " FROM _cz_pairs WHERE population_id = %s",
                       params=(pid,), scope_where="{t}.population_id = %s", scope_params=(pid,),
                       work_mem="512MB", temp_name="_cz_new_pairs")
        c = diff_apply(conn, "card_population_deck_counts",
                       keys=("population_id", "card_id"), vals=("main_deck_count", "side_deck_count"),
                       select_sql="SELECT population_id, card_id, main_deck_count, side_deck_count"
                                  " FROM _cz_counts WHERE population_id = %s",
                       params=(pid,), scope_where="{t}.population_id = %s", scope_params=(pid,),
                       temp_name="_cz_new_counts")
        with conn.cursor() as cur:
            cur.execute(DIGEST_SQL, {"pid": pid})
            digest = ";".join(r[0] for r in cur.fetchall())
            cur.execute(STATS_SQL, {"pid": pid, "build": str(uuid.uuid4()), "snap": snap, "digest": digest,
                                    "pairs": p["new_rows"], "cards": c["new_rows"]})
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"pairs": p, "cards": c, "digest": digest}


def verify(conn, pid) -> bool:
    from diff_apply import verify as v
    a = v(conn, "card_cooccurrence_v2", ("population_id", "relation", "card_id_a", "card_id_b"), ("cooccurrence_count",),
          "SELECT population_id, relation, card_id_a, card_id_b, cooccurrence_count FROM _cz_pairs WHERE population_id = %s",
          (pid,), scope_where="{t}.population_id = %s", scope_params=(pid,))
    conn.rollback()
    b = v(conn, "card_population_deck_counts", ("population_id", "card_id"), ("main_deck_count", "side_deck_count"),
          "SELECT population_id, card_id, main_deck_count, side_deck_count FROM _cz_counts WHERE population_id = %s",
          (pid,), scope_where="{t}.population_id = %s", scope_params=(pid,))
    conn.rollback()
    return a["ok"] and b["ok"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--migrate", action="store_true", help="表を作り、母集団の種を入れる（初回だけ）")
    ap.add_argument("--dry-run", action="store_true", help="作って検算するだけ（適用しない）")
    ap.add_argument("--only", help="母集団の番号（カンマ区切り）")
    ap.add_argument("--verify", action="store_true", help="適用の後に、表と作り直しの結果を突き合わせる")
    args = ap.parse_args()

    t0 = time.time()
    conn = psycopg2.connect(**get_db_config())
    conn.autocommit = False
    failed = []
    try:
        ensure_tables(conn, args.migrate)
        only = [int(x) for x in args.only.split(",")] if args.only else [r[0] for r in population_seed()]
        log(f"[1/3] 母集団 {len(only)} 個を 1 つのスナップショットで作る")
        st = stage(conn, only)
        log(f"  スナップショット {st['snap']}・区間の終わり {st['end']}（含まない）")
        log("[2/3] 検算 → [3/3] 母集団ごとに適用")
        for pid, fmt, window, _floor, _side in st["pops"]:
            ck = check(conn, pid)
            if not ck["ok"]:
                failed.append(pid)
                log(f"  {pid:2d} {fmt} {window}: 検算で止めた（前の完成版のまま） {ck}")
                continue
            if args.dry_run:
                log(f"  {pid:2d} {fmt} {window}: 検算 ok N={ck['n']:,}（--dry-run なので適用しない）")
                continue
            r = apply_population(conn, pid, st["snap"])
            ok = verify(conn, pid) if args.verify else None
            log(f"  {pid:2d} {fmt} {window}: N={ck['n']:,}・組 {r['pairs']['new_rows']:,}"
                f"（削除 {r['pairs']['deleted']:,}・更新 {r['pairs']['updated']:,}・追加 {r['pairs']['inserted']:,}）"
                f"・分母 {r['cards']['new_rows']:,}（削除 {r['cards']['deleted']:,}・更新 {r['cards']['updated']:,}・"
                f"追加 {r['cards']['inserted']:,}）・{r['pairs']['seconds'] + r['cards']['seconds']:.0f}s"
                + ("" if ok is None else f"・verify {'ok' if ok else 'NG'}"))
            if ok is False:
                failed.append(pid)
        if not args.dry_run:
            with conn.cursor() as cur:
                for t in TABLES:
                    cur.execute(f"ANALYZE {t}")
            conn.commit()
    finally:
        conn.close()
    log(f"完了 {time.time() - t0:.0f}s" + (f"・止めた母集団 {failed}" if failed else ""))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
