"""sql.py — 自由 SQL の口 query_mtg_database とスキーマの窓 describe_mtg_tables
（2026-09-05 Step 3 で mcp_server.py から切り出し）。

登録（server.tool）は mcp_server.py 側。道具が 2 本あるので説明は道具ごとに
QUERY_MTG_DATABASE_DESCRIPTION・DESCRIBE_MTG_TABLES_DESCRIPTION と名前を分ける。
先頭コメントの札（#セット記号）の解決は search 側の道具（sisho/tools/cards.py の
_resolve_draft_set・_archetype_lines）を借りる＝セットの解決は 1 箇所に置く。
"""
from sisho.db import DBBusy, _db, _db_readonly
from sisho.sets_blurb import _SETS_BLURB
from sisho.toollog import TOOL_LOG_MAX, _log_tool
from sisho.tools.cards import _archetype_lines, _resolve_draft_set


# ─── 自由 SQL の口（2026-08-11・本人発案「エージェント自身が SQL を叩く路線」）───
# 鞘は三重: (1) readonly_ai ロールと (2) statement_timeout 10 秒は sisho/db.py の
# _db_readonly（原文の注記もそちらへ一緒に移した）・(3) 入口で SELECT/WITH 以外と複文を
# 拒否＋行数・セル長の上限で応答を制限（コンテキスト爆発防止）は下の query_mtg_database。


QUERY_MTG_DATABASE_DESCRIPTION = ("【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
    "【専用ツールで表せない集計は Web に行かずここで SQL】読み取り専用 SQL（PostgreSQL・SELECT/WITH のみ・1 文・10 秒・最大 50 行）。条件違いの比較（色別・セット別・ランク帯別など）は同じ表を何度も引かず GROUP BY／CASE で 1 文にまとめる（往復 1 回が速い）。"
    "主な棚: mtg_cards_v2（card_name, japanese_name, name_display, type_line, mana_cost, oracle_text, legalities, edhrec_rank）／"
    "mtg_rules／card_rulings／card_format_strength・edh_card_strength（採用率）／card_cooccurrence・edh_card_cooccurrence_v2（共起）／"
    "mtg_sets（セット発売日・set_type・エキスパンション紀元の突き合わせ用・2026-08-25）／"
    "deck_list・deck_cards（実デッキ・プレイヤー名は players 表に隔離＝非公開・deck_list は player_id）。列名は describe_mtg_tables で確認（推測しない）。"
    "結果のカード名列の右隣に <列>_display（完成形）を自動同伴＝それをそのまま書く。【内部専用・公開版に載せない（権利札 2026-08-11）】"
    "【SQL の 1 行目の掟】必ず `-- 目的` のコメントを 1 行目に書く（例: `-- #SOS 3 パック目の比較`）。"
    "リミテッドの問いでユーザーがセットを示していたら（#SOS のタグ・「SOS のクイックドラフト」等）、コメントに `#セット記号` を含める"
    "（そのセットの色の組み合わせ表が返り値に添う）。構築の問いでは記号を書かない。コメントは読むだけで SQL は書き換えない。"
    + ("セット記号は search_mtg_cards の説明にある一覧から（知らないセットでも MTG・推測しない）。" if _SETS_BLURB else "")
    + "確率の SQL 関数（データと結合するとき・単発は mtg_probability）: mtg_hypergeom_atleast(N,K,D,m)・mtg_prob_by_turn(deck,copies,turn,on_play,m,mull)・"
      "mtg_land_drops(deck,lands,turn,on_play,mull)・mtg_combo_by_turn(deck,a,b,turn,on_play,mull)・mtg_cards_seen(turn,on_play,mull)。")
def query_mtg_database(sql: str, max_rows: int = 30) -> str:
    _log_tool("query_mtg_database", {"sql": sql[:max(150, TOOL_LOG_MAX)]})
    # 先頭コメントの札（2026-09-03 本人「全ての SQL にコメントを付けさせ、届いたら Python を一つ通す」・読むだけで書き換えない）
    import re as _re
    _m = _re.match(r"\s*--[^\n]*?#([A-Za-z0-9_\-]{2,40})", sql)
    tag_set = _resolve_draft_set(_m.group(1).strip()) if _m else None
    # 先頭のコメント行は判定と実行から剥がす（コメントは札であって SQL の一部でない）
    sql = "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")) if sql.lstrip().startswith("--") else sql

    max_rows = max(1, min(int(max_rows), 50))
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        return "拒否: 複文（; 区切り）は実行できません。1 文だけにしてください。"
    head = stripped.split(None, 1)[0].upper() if stripped else ""
    if head not in ("SELECT", "WITH"):
        return f"拒否: SELECT / WITH で始まる読み取りクエリのみ実行できます（先頭語: {head}）。"
    try:
        cols, rows = _db_readonly(stripped, max_rows)
    except DBBusy as e:
        return str(e)          # 混雑は SQL の誤りでない＝「SQL エラー」に丸めない（errors.BUSY）
    except Exception as e:
        # 想定外の例外は丸めず素通し（PostgreSQL のエラー文がそのまま次の一手になる・errors.DB_ERROR）
        return f"SQL エラー: {str(e)[:400]}"
    if not rows:
        return "0 行（クエリは成功）。"
    cols, rows, ja_note = _attach_japanese_names(cols, rows)
    def cell(v):
        s = "" if v is None else str(v)
        return s if len(s) <= 160 else s[:157] + "…"
    lines = [" | ".join(cols)]
    lines += [" | ".join(cell(v) for v in r) for r in rows]
    note = f"\n（{len(rows)} 行返却・上限 {max_rows}）" + ja_note
    if tag_set:
        note += _archetype_lines(tag_set)
    return "\n".join(lines) + note


def _attach_japanese_names(cols: list[str], rows: list[tuple]) -> tuple[list[str], list[tuple], str]:
    """結果の文字列列のうち値が DB のカード名（正式名 or 表の名前）に当たる列の右隣に `<列>_ja` を添える。

    2026-08-22 書式ベンチの教訓: Sonnet は SQL で card_name だけ取ると自分で訳す（10 問中 11 件の創作訳・
    DB には正式名あり）。instructions の「翻訳するな」は効かないので、道具の返り値に japanese_name を
    同伴させて訳す隙を構造で塞ぐ（設計の掟: LLM の出力は信用せず構造で塞ぐ）。
    列名で推測せず値で判定（card_name_a / pname / 別名付き列でも効く）。
    日本語版なしは「日本語版なし」と明示（NULL と未一致を区別する）。

    2026-09-05（Step 5 修正 3）: 入口の「列名が *_display で終わる列が一つでもあれば全停止」を撤去した。
    脳が `SELECT count(*) AS foo_display` のように無関係な列名を付けた瞬間、結果中のカード名列への
    同伴が丸ごと消えていた（実測）＝構造で塞いだはずの穴が名前の付け方で開く。
    判定は列名でなく値・行ごとに置き換える:
      (a) その列のカード名の完成形が**同じ行の別の列に既にある**（`SELECT card_name, name_display` 型）なら添えない
      (b) 添える列名（`<列>_display`）が既に結果にあるなら添えない（衝突を作らない）
      (c) それ以外のカード名列には従来どおり右隣に添える。
    """
    str_cols = [i for i in range(len(cols))
                if any(isinstance(r[i], str) and r[i] for r in rows)]
    if not str_cols:
        return cols, rows, ""
    cands = sorted({r[i] for r in rows for i in str_cols if isinstance(r[i], str) and 0 < len(r[i]) <= 160})
    if not cands:
        return cols, rows, ""
    try:
        hits = _db(
            "SELECT card_name, name_display, name_en_front, name_en_back, name_ja_back, digital FROM mtg_cards_v2"
            " WHERE card_name = ANY(%s) OR name_en_front = ANY(%s) OR name_en_back = ANY(%s)",
            (cands, cands, cands))
    except Exception:
        return cols, rows, ""
    # 2026-08-31（R3-4）: 正式名・表面名は表面の完成形（name_display）、裏面名はその面の完成形。裏面名が本物のカード名と同じ（prepare）なら本物が勝つ
    ja_of: dict[str, str] = {}
    for en, label, enf, enb, jab, dg in hits:
        ja_of[en] = label
        ja_of[enf] = label
    for en, label, enf, enb, jab, dg in hits:
        if enb:
            ja_of.setdefault(enb, f"《{jab}/{enb}》" if jab else f"{enb}（{'日本語名未収録' if dg else '日本語版なし'}）")
    # 列ごとに「その列の値の過半がカード名」なら名前列と見なす（数字混じりの雑多な列を避ける）
    lower_cols = {c.lower() for c in cols}
    name_cols = []
    for i in str_cols:
        vals = [r[i] for r in rows if isinstance(r[i], str) and r[i]]
        if not (vals and sum(v in ja_of for v in vals) * 2 >= len(vals)):
            continue
        if f"{cols[i]}_display".lower() in lower_cols:
            continue                       # (b) 同名の列が既にある＝衝突させない
        # (a) 完成形が同じ行の別の列に既にあるなら二重に添えない（値で判定・列名は見ない）
        already = sum(1 for r in rows
                      if isinstance(r[i], str) and ja_of.get(r[i])
                      and any(j != i and r[j] == ja_of[r[i]] for j in range(len(cols))))
        if already * 2 >= len(vals):
            continue
        name_cols.append(i)
    if not name_cols:
        return cols, rows, ""
    new_cols, new_rows = [], []
    for i, c in enumerate(cols):
        new_cols.append(c)
        if i in name_cols:
            new_cols.append(f"{c}_display")
    for r in rows:
        nr = []
        for i, v in enumerate(r):
            nr.append(v)
            if i in name_cols:
                nr.append(ja_of.get(v) if isinstance(v, str) else None)
        new_rows.append(tuple(nr))
    note = ("\n（カード名の列に _display＝完成形《日本語名/英語名》を同伴。日本語で答えるときはこの文字列を一字も変えずに使い、"
            "自分で訳さない・組み立てない・略さない（2 回目以降も完成形）。「英語名（日本語版なし）」「英語名（日本語名未収録）」もそのまま書く）")
    return new_cols, new_rows, note


# 表ごとの注記（出典・列の意味）。返り値に載せる＝脳に確実に届くのは返り値だけ
# （2026-08-22 本人裁定「MCP の返り値は自己完結」）。変わる事実（収録セット一覧）は固定文にせず実測で添える。
# 2026-08-31 まで一覧は relname だけ返していた（スキーマ落ち）→ public 以外の表は「スキーマ名.表名」で返す。
# 17Lands 集計は同日 limited_card_stats → public.limited_card_stats に統合（本人裁定・表 1 枚に別スキーマは不要）。
_TABLE_NOTES = {
    "mtg_cards_v2": (
        "カード本体。**名前の正本は面の列** name_en_front／name_en_back／name_ja_front／name_ja_back（両面札は 2 面・単面札は back が NULL）。"
        "card_name（『表 // 裏』）・japanese_name（両面揃った時だけ結合、揃わなければ NULL）・name_display（表面の完成形）は面から自動で作る生成列＝書けない。"
        "裏面（出来事・変身後・分割の片方）で引くときは name_en_back／name_ja_back。name_ja_src_front/back は日本語名の出所（scryfall／manual／rule_a／whisper／legacy）。"
        "digital=true は Arena 専用の札（アルケミー・A- リバランス・Jumpstart: Historic Horizons 等・"
        "2026-08-31 に 860 枚を合流）＝紙には存在しない。紙のカードの照会は WHERE NOT digital を付ける。"
        "name_display の「（日本語名未収録）」は Arena に日本語版はあるがこの DB がまだ持っていない印（「（日本語版なし）」とは別）。"),
    "limited_color_stats": (
        "17Lands（集計元 Premier Draft・Bo1 ドラフト一般の物差し＝Quick Draft の問いにも使う）のセット × デッキの色組み合わせ（main_colors・例 'WU'）× splash（タッチ有無）の勝率。"
        "列: games, wins, wr。「どの色の組み合わせが勝っているか」はこの表（多色カードの平均ではない・2026-09-02）。"
        "母数 games を必ず添える。出典「17Lands」。"),
    "limited_matchup_stats": (
        "17Lands のセット × 自分の色（main_colors）× 相手の色（opp_colors）の勝率（相性表）。列: games, wins, wr。"
        "相手の色は対戦中に見えた色＝欠けや過剰があり得る。母数の細いマスは読まない。"),
    "limited_format_stats": (
        "17Lands のセット × ランク帯（rank: bronze〜mythic・小文字・欠けは 'none'）の環境指標。"
        "列: games, wins, wr, on_play_games, on_play_wins, on_play_wr（先手勝率）, turns_sum, avg_turns（平均ターン数＝環境の速さ）, "
        "mulligans_sum（マリガン率は mulligans_sum/games）。セット全体はランク帯を SUM で合算。"),
    "limited_card_rank_stats": (
        "17Lands のセット × ランク帯 × 札の GIH WR / GP WR（列: gih_games, gih_wins, gih_wr, gp_games, gp_wins, gp_wr）。"
        "上位帯で評価が変わる札を見る表。母数が痩せるので gih_games < 500 は書かない。正式名は db_card_name。"),
    "limited_card_pick_stats": (
        "17Lands のセット × 札のピック側の指標。picks=取られた回数・maindeck_rate=取った札がメインに入った率の平均・"
        "sideboard_in_rate・avg_event_wins=その札を取ったドラフターの平均勝ち数（event_picks が母数）。正式名は db_card_name。"),
    "limited_card_stats": (
        "17Lands（https://www.17lands.com/）Public Datasets（CC BY 4.0）の Premier Draft（人間対面・Bo1）をセット×カードで集計した"
        "Bo1 ドラフト統計。Quick Draft（ボット対面・Bo1）の問いにもカードの強さの物差しとしてそのまま使う"
        "（「Quick Draft のデータは無い」で止まらない・alsa/ata だけは人間対面の流れ方＝Quick Draft では当てにしない）。"
        "答えに使うときは出典「17Lands」を添える。"
        "列: gih_wr=手札に来たゲームの勝率（Games In Hand）・oh_wr=初手・gd_wr=引いた・gp_wr=メインに入れた・"
        "*_games は母数（500 未満はぶれる）・alsa=最後に見えたピック番号の平均（小さいほど早く消える）・"
        "ata=取られたピック番号の平均・seen_packs/taken_count はその母数。"
        "card_name は 17lands 側の表記（両面カードは表面名・稀に文字化け）なので、正式名は db_card_name"
        "（mtg_cards_v2.card_name）を使う。"),
}


def _ident(s: str) -> str:
    """識別子の無害化（英数字と _ だけ残す）。_db_readonly はプレースホルダを受けないので文字種で守る。"""
    return "".join(ch for ch in s if ch.isalnum() or ch == "_")


def _limited_sets_note() -> str:
    """limited_card_stats の収録セットを実測で一行に（無いセットを書かないための一覧）。表が無ければ空文字。"""
    try:
        _, srows = _db_readonly(
            "SELECT expansion, event_type, count(*) FROM limited_card_stats"
            " GROUP BY 1, 2 ORDER BY 2, 1", 200)
    except Exception:
        return ""
    if not srows:
        return ""
    by_ev: dict[str, list[str]] = {}
    for exp, ev, n in srows:
        by_ev.setdefault(ev, []).append(f"{exp}({n})")
    parts = [f"{ev}: " + "・".join(v) for ev, v in by_ev.items()]
    return ("  収録セット（expansion 列の値・括弧はカード数・実測）: " + "／".join(parts)
            + "。ここに無いセットは持っていない（無いものは書かない）。event_type は集計元の名前（PremierDraft）＝"
            "Bo1 ドラフト一般（Quick Draft を含む）の物差しとして使う。")



DESCRIBE_MTG_TABLES_DESCRIPTION = (
    "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
    "データベースの実スキーマを見る。table_name 省略で全テーブルの一覧と行数概算、"
    "指定でそのテーブルの列名・型の一覧。query_mtg_database で SQL を書く前に"
    "列名をここで確認すること。public 以外のスキーマの表があれば「スキーマ名.表名」で返り、SQL でもその形で書く。")
def describe_mtg_tables(table_name: str | None = None) -> str:
    _log_tool("describe_mtg_tables", {"table": table_name})

    try:
        if not table_name:
            cols, rows = _db_readonly(
                "SELECT schemaname, relname, n_live_tup FROM pg_stat_user_tables"
                " ORDER BY n_live_tup DESC", 60)
            names = [r[1] if r[0] == "public" else f"{r[0]}.{r[1]}" for r in rows]
            lines = ["テーブル | 行数概算"] + [f"{n} | {r[2]}" for n, r in zip(names, rows)]
            if any("." in n for n in names):
                lines.append("（「スキーマ名.表名」の形の表は SQL でもその形で書く。public の表はそのまま）")
            for qname, note in _TABLE_NOTES.items():
                if qname in names:
                    lines.append(f"- {qname}: {note}")
                    if qname == "limited_card_stats":
                        extra = _limited_sets_note()
                        if extra:
                            lines.append(extra)
            return "\n".join(lines)
        # 列一覧。「スキーマ名.表名」でも表名だけでも受ける（スキーマ無しなら該当する全スキーマを出す）
        sch_in, _, tbl_in = table_name.strip().rpartition(".")
        sch, tbl = _ident(sch_in), _ident(tbl_in)
        # 2026-09-05（Step 6 作業 2）: 削った結果が元と違うなら**検索せずに断る**。
        # _ident は使えない文字を黙って捨てるので、`deck-list` は `decklist` に化けて
        # 「テーブルなし: deck-list」＝「そんな表は無い」と嘘をつく（実際は表名の書き方の問題）。
        # 無害化そのものは残す（_db_readonly はプレースホルダを受けない＝文字種で守る鞘）。
        # 返り値の形はテキストのまま（この道具はもともとテキストを返す・error_kind は
        # sisho/errors.py の INVALID_IDENTIFIER＝棚卸し上の名前で、返り値には載せない）。
        if tbl != tbl_in or sch != sch_in:
            bad = tbl_in if tbl != tbl_in else sch_in
            what = "表名" if tbl != tbl_in else "スキーマ名"
            return (f"{what}に使えない文字: 「{bad}」（英数字と _ のみ）。"
                    "describe_mtg_tables() を引数なしで呼ぶと一覧が出る")
        if not tbl:
            return f"テーブルなし: {table_name}（describe_mtg_tables() を引数なしで呼ぶと一覧が出る）"
        where = f"table_name = '{tbl}'" + (f" AND table_schema = '{sch}'" if sch else "")
        cols, rows = _db_readonly(
            "SELECT table_schema, column_name, data_type FROM information_schema.columns"
            f" WHERE {where} ORDER BY table_schema, ordinal_position", 160)
        if not rows:
            return f"テーブルなし: {table_name}（describe_mtg_tables() を引数なしで呼ぶと一覧が出る）"
        out = []
        for schema in dict.fromkeys(r[0] for r in rows):
            qname = tbl if schema == "public" else f"{schema}.{tbl}"
            out.append(f"{qname} の列:")
            out += [f"{r[1]} | {r[2]}" for r in rows if r[0] == schema]
            if schema != "public":
                out.append(f"（SQL では {qname} とスキーマ付きで書く。{tbl} だけでは引けない）")
            if qname in _TABLE_NOTES:
                out.append(f"- {qname}: {_TABLE_NOTES[qname]}")
                if qname == "limited_card_stats":
                    extra = _limited_sets_note()
                    if extra:
                        out.append(extra)
        return "\n".join(out)
    except DBBusy as e:
        return str(e)          # 混雑（errors.BUSY）
    except Exception as e:
        return f"エラー: {str(e)[:300]}"        # 想定外は素通し（errors.DB_ERROR）
