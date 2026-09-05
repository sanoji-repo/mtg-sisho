"""sets_blurb.py — 道具の説明に埋める収録セットの一覧（2026-09-05 Step 3 で mcp_server.py から切り出し）。

起動時に DB から 1 回だけ読む定数（_SETS_BLURB・_SETS_HEAD）。search_mtg_cards と
query_mtg_database の description の両方が参照するので、道具のモジュールより手前に置く
（どちらか片方の道具に置くと、もう片方が道具を import することになる）。
"""


def _startup_sets_blurb() -> str:
    """道具の説明に載せる収録セットの一覧（起動時に DB から 1 回・失敗したら空）。
    発端（2026-09-03 追試 A）: Sonnet が MSH／SOS（知識の切れ目の後のセット）を「MTG でない（Marvel Snap の話）」と
    決めつけ、道具を一度も呼ばず記憶で答えた（30 問中 3・B でも 2）。説明文は claude.ai が脳に見せるので、ここに
    一覧と「知らないセットでも必ず引く」を置く（instructions は届かない・8/22 裁定）。"""
    try:
        import psycopg2
        from db_config import DB_CONFIG
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT s.expansion, m.set_name, extract(year from m.released_at)::int"
                " FROM (SELECT DISTINCT expansion FROM limited_card_stats) s"
                " LEFT JOIN mtg_sets m ON lower(m.set_code) = lower(s.expansion)"
                " WHERE s.expansion NOT ILIKE 'cube%%' ORDER BY m.released_at DESC NULLS LAST, s.expansion")
            rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return ""
    if not rows:
        return ""
    items = "・".join(f"{c}（{n} {y}）" if n else c for c, n, y in rows)
    return (f"【収録セット（リミテッド統計あり・新しい順・記号（名前 発売年））】{items}。"
            "知らないセット名・記号（例 MSH・SOS・TLA）でも MTG のセットなので、記憶で『MTG ではない』と判断せず必ずこの道具で引く。"
            "記号はこの一覧から取り、推測しない。紛れ: STX は 2021 のストリクスヘイヴン・SOS は 2026 の Secrets of Strixhaven。")


_SETS_BLURB = _startup_sets_blurb()
# 冒頭用の短い版（2026-09-03 追試: 末尾の一覧では Sonnet の「Marvel は MTG でない」の先入観に勝てず、MSH の問いで道具を呼ばなかった。
# 説明文は全文届いていた（1,961 字を脳が引用できた）ので、位置の問題＝最初の一文に置く）
_SETS_HEAD = ((lambda m: f"【まず読む】このデータベースは MTG の最新セット {m} まで収録（Marvel Super Heroes＝MSH・Secrets of Strixhaven＝SOS も MTG の正式セット）。"
              "知らないセット名・記号・カード名は『MTG ではない』と決めつけず、必ずこの道具で引いてから答える。")(
    _SETS_BLURB.split("】", 1)[1].split("。", 1)[0].split("・")[0] if _SETS_BLURB else "") if _SETS_BLURB else "")
