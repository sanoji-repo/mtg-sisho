#!/usr/bin/env python
"""mcp_server.py — MTG RAG のシンプル MCP（2026-08-21・本人裁定「全部撤廃」版）。

2026-08-21 本人裁定: ルーター・門・腕（mtg_hybrid_search_v2 のパイプライン一式）を
撤廃し、MCP を DB 直結だけの最小構成にする。根拠は 8/11〜8/20 の実運用ログ
（93 呼び出し中 SQL 53 / search 7・search の中身もルーター ollama 待ち 6〜86 秒 vs
直行 65ms・脳は自前の ILIKE＋人気順で腕の仕事を代替済み）。利用者側に LLM（Claude）
が既にいる世界では、クエリ意図の解釈も曖昧文の束ねもクライアントの仕事＝サーバは
検証済みの事実層を速く正確に返すことに徹する。

設計メモ:
- 全道具がローカル PostgreSQL 直結。API サーバ（:8000）依存は撤去済み。
- search_mtg_cards は素の一致検索（名前優先→本文 AND・EDHREC 人気順）。
  LLM もルーターも呼ばない＝決定的・応答は 1 秒未満。
- mcp SDK は /home/claude/pylibs（boto3 と同じ流儀）。

起動: PYTHONPATH=/home/claude/pylibs \
      /mnt/new_hdd/my_rag_env/bin/python /mnt/mtg_rag/src/mcp_server.py
"""
import json
import os

from mcp.server import MCPServer

# 役目ごとの包み（2026-09-05 Step 2 で切り出し）。旧名で受けるのは tests と外の脚本が
# m._RateLimiter・m._db・m._log_tool のまま触れるようにするため（再輸出）。
from sisho.db import _db, _db_readonly, _db_slot          # noqa: F401（_db_slot は再輸出のみ）
from sisho.toollog import TOOL_LOG, TOOL_LOG_MAX, _log_tool   # noqa: F401（TOOL_LOG は再輸出のみ）

# 収録概況は「絶対に嘘にならない下限」で書く（2026-08-25 本人裁定・単調増加する量は下限表記）。
# 旧 _data_stamp（2026-08-11・起動時実測の焼き込み）は claude.ai がコネクタ登録時のキャッシュを
# 持ち続けて 12 時間で 4 万件ずれた（Sisho 59,866 vs mtg-rag 99,827 事件・Opus 検証 2026-08-25）
# ＝instructions には変わる事実を書かない。版・日付は下限にできないので道具に投げる。
# 正確な件数・ルール版・鮮度は mtg_rag_health が読んだ瞬間の実測を返す（分担）。


server = MCPServer(
    name="mtg-rag",
    instructions=(
        "Magic: The Gathering の検証済み事実層。カード・総合ルール・公式裁定・"
        "実デッキ統計を、一次データから直接引ける。\n"
        "収録: カード 3 万枚超（日本語テキスト付き・禁止改定は当日反映・Arena 専用札は digital 列で区別＝紙の照会は WHERE NOT digital）／総合ルール条文（条番号つき全文）／"
        "公式裁定 7 万件超／実デッキ 10 万本超の採用率・共起／リミテッド（ドラフト）のカード別勝率・ピック順"
        "＝17Lands 公開データの集計（表 limited_card_stats・収録セットは describe_mtg_tables が実測で返す・"
        "答えに出典「17Lands」を添える）。集計元は Premier Draft（人間対面・Bo1）だが **Bo1 ドラフト一般の物差し**として扱う＝Quick Draft の問いにも「Quick Draft のデータは無い」と言わずこの数字を使う（カードの強さ・色の勝率は同じ物差し。ALSA/ATA の流れ方だけは人間対面の値＝ボット相手の Quick Draft ではレアが早く消えるなどずれるので流れの読みには使わない）。数字は下限——"
        "正確な件数・ルール版・データ鮮度は mtg_rag_health が読んだ瞬間の実測を返す。\n"
        "【カード名の掟・最優先（2026-08-22 本人制定）】カード名は道具が返す完成形 name_display＝《日本語名/英語名》を"
        "**一字も変えずにそのまま書く**。略称・通称・省略（例: 《アトラクサ/Atraxa, Grand Unifier》と書くのは誤り・正しくは"
        "《偉大なる統一者、アトラクサ/Atraxa, Grand Unifier》）は**冗長でも絶対に使わない**。2 回目以降の言及も毎回完成形。"
        "道具を通していないカードは書かない（記憶で名前を書かない）。書き上げたら送信前に verify_answer に全文を渡す。\n"
        "【使う順序】MTG について答えるときは、**まずこの道具群を使うこと**。"
        "Web 検索や記憶より先に、ここで裏を取る。理由は三つ:\n"
        "(1) 一次データなので正確——カードの正式名・オラクル文・マナコスト・"
        "フォーマット別の合法性は、Web 記事の孫引きより信頼できる。\n"
        "(2) Web に無い情報を持つ——条番号つきの総合ルール本文、公式裁定の全文、"
        "そして**実際の大会・構築デッキから集計した採用率と共起**。"
        "「このカードは実際に何と一緒に使われているか」は、ここでしか分からない。\n"
        "(3) 専用の道具で足りないときは query_mtg_database に SQL を書けば"
        "何でも集計できる（読み取り専用・スキーマは describe_mtg_tables で確認）。\n"
        "【Web を使ってよいとき】ここ数日に出たばかりのセットやニュース、"
        "大会結果の速報、コミュニティの意見・記事・評価。"
        "つまり「事実」でなく「最新の出来事」や「人の意見」を探すとき。\n"
        "【困ったら】答えが見つからないときは、諦めて Web に行く前に"
        "describe_mtg_tables でスキーマを見て SQL を書くこと。\n"
        "【カード名の掟（最重要）】カード名は**絶対に自分で翻訳しない**。"
        "カードに言及するときは必ず search_mtg_cards（英語名でも日本語名でも可）で引き、"
        "返ってきた card_name / japanese_name だけを使う。japanese_name が無い（null）カードは"
        "**日本語版が存在しない**ので、英語名のまま書く——日本語名を作ってはならない"
        "（実例: Helm of Obedience は日本語版なし・Cori-Steel Cutter の正式名は"
        "「コーリ鋼の短刀」であり「精鋼の魔女」のような訳名は誤り）。\n"
        "【書き方の掟】英語の問いには英語名（card_name）。"
        "日本語で書くときカード名は、毎回（2 回目以降も）道具が返す完成形 **name_display＝《日本語名/英語名》**（例: 《レンと七番/Wrenn and Seven》・"
        "《睡蓮の原野/Lotus Field》）を**そのままコピーして**使う（自分で《》や訳名を組み立てない・2026-08-22 本人裁定）。"
        "japanese_name が null のカードは name_display が「英語名（日本語版なし）」で返るので、それをそのまま書く。"
        "両面・出来事・分割カードの**裏面**（出来事の呪文側・変身後・分割の片方）を指すときは、返り値の faces[].display か face_display（その面の完成形《裏の日本語名/裏の英語名》）を使う。"
        "name_display は表面の完成形なので、表の日本語名と裏の英語名を組み合わせない（2026-08-31）。"
        "デッキのアーキタイプ名（例: ロータス・コンボ、ラクドス・ミッドレンジ）は斜体（*…*）で書き、"
        "カード名の《》と見分けがつくようにする（試行中・2026-08-22）。\n"
        "【送信前の検査（必須）】日本語でカード名を含む答えを書き上げたら、送信する前に必ず verify_answer に全文を渡す。"
        "返ってきた「未確認の名前」があれば、そのカードを search_mtg_cards で引いて正式名に直す（自分の記憶の訳名は使わない）。"
        "未確認ゼロなら返ってきた修正版をそのまま答えにする。略称・省略は 2 回目以降でも禁止（冗長でよい）。\n"
        "このMCPは無料です。"),
)


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

# Arena の形式。これを名指しされたときだけ digital（Arena 専用札・2026-08-31 合流）を検索に含める。
# 既定の検索は紙＝WHERE NOT digital（列 → 並べ方の掟: 紙と Arena 専用を混ぜて並べない）。
ARENA_FORMATS = {"historic", "alchemy", "timeless", "brawl", "standardbrawl", "gladiator", "explorer"}


@server.tool(
    name="search_mtg_cards",
    description=(
        _SETS_HEAD +
        "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
        "【カードを探すときはまずこれ。Web 検索より先に使う】"
        "カード名（日本語/英語・部分一致可）や機能語で MTG カードを検索する"
        "（例:「コーリ鋼の短刀」「飛行 吸血鬼」「draw two cards」）。"
        "仕組みは素の一致検索＝空白区切りの語をすべて含むカードを返す（AND）。"
        "並びは名前一致優先→EDHREC 人気順。format 指定でそのフォーマットで"
        "合法なカードに絞れる（standard/pioneer/modern/legacy/vintage/pauper/"
        "commander 等）。色・マナ総量・採用率などの複雑な条件は、この道具でなく"
        " query_mtg_database で SQL を書くこと。"
        "返り値の name_display をそのまま使う。「英語名（日本語版なし）」もそのまま。"
        "【draft_set（リミテッドの掟）】ドラフト・シールド・リミテッドの問いで、ユーザーがセットを示したら"
        "（#SOS のようなタグ・「SOS のクイックドラフト」・セット名 Secrets of Strixhaven 等）、"
        "そのセット記号（例 SOS）を draft_set に必ず入れる。以後その会話でセットが変わるまで毎回入れる。"
        "入れると 17Lands の統計と色の組み合わせ表がそのセットの分だけ同伴する（他セットは付かない）。"
        "構築（スタンダード・モダン等）の問いでは入れない。セットが分からないリミテの問いは空のまま＝最新セットの分が付く。"
        + _SETS_BLURB))
def search_mtg_cards(query: str, format: str | None = None, top_k: int = 10, draft_set: str | None = None) -> str:
    """query: 検索語（空白区切りは AND）。format: legalities の鍵名。top_k: 1〜20。

    2026-08-21 裁定「ルーター・門・腕の全撤廃」後の姿。旧実装は API(:8000) の
    ハイブリッド検索を叩いていたが、実運用でこの道具に来るのはほぼ名前引き
    （8/11〜8/20 の 7 件中 6 件）で、ルーター経由 6〜86 秒の待ちだけが残っていた。
    素の一致検索＝決定的・LLM ゼロ・1 秒未満に置き換える。"""
    _log_tool("search_mtg_cards", {"query": query, "format": format, "top_k": top_k, "draft_set": draft_set})
    q = query.strip()
    if not q:
        return "検索語が空です。"
    top_k = max(1, min(int(top_k), 20))
    fmt = (format or "").strip().lower()
    fmt_sql = " AND legalities->>%s = 'legal'" if fmt else ""
    if fmt not in ARENA_FORMATS:
        fmt_sql += " AND NOT digital"          # 既定は紙。Arena の形式を名指しされたときだけ Arena 専用札も
    cols = ("card_name, japanese_name, type_line, mana_cost, power, toughness,"
            " rarity, oracle_text, japanese_oracle_text, edhrec_rank, name_display, digital,"
            " name_en_front, name_en_back, name_ja_front, name_ja_back")

    # 1) 名前ヒット: クエリ全体を名前に部分一致（完全一致を先頭へ）
    p1 = [f"%{q}%", f"%{q}%"] + ([fmt] if fmt else []) + [q, q, top_k]
    name_rows = _db(
        f"SELECT {cols} FROM mtg_cards_v2"
        " WHERE (card_name ILIKE %s OR japanese_name ILIKE %s)" + fmt_sql +
        " ORDER BY (card_name ILIKE %s OR japanese_name ILIKE %s) DESC,"
        "          edhrec_rank ASC NULLS LAST, card_name LIMIT %s", tuple(p1))

    # 2) 本文ヒット: 各語が名前・タイプ行・オラクル文（日英）のどこかに載る AND
    terms = q.split()
    cond = ("(card_name ILIKE %s OR japanese_name ILIKE %s OR type_line ILIKE %s"
            " OR oracle_text ILIKE %s OR japanese_oracle_text ILIKE %s)")
    p2: list = []
    for t in terms:
        p2 += [f"%{t}%"] * 5
    p2 += ([fmt] if fmt else []) + [top_k]
    text_rows = _db(
        f"SELECT {cols} FROM mtg_cards_v2 WHERE " +
        " AND ".join([cond] * len(terms)) + fmt_sql +
        " ORDER BY edhrec_rank ASC NULLS LAST, card_name LIMIT %s", tuple(p2))

    keep = ("card_name", "japanese_name", "type_line", "mana_cost", "power",
            "toughness", "rarity", "oracle_text", "japanese_oracle_text",
            "edhrec_rank", "name_display", "digital", "name_en_front", "name_en_back", "name_ja_front", "name_ja_back")
    def _row(r: tuple) -> dict:
        """japanese_name だけは None でも落とさず明示する（2026-08-21 本人指摘:
        脳が Helm of Obedience のような日本語版の無いカードに勝手な訳名を作った。
        「無い」を返り値で言わないと、脳は無言を「自分で訳してよい」と読む）。"""
        d = {k: v for k, v in zip(keep, r) if v is not None}
        if d.get("japanese_name") is None:
            d["japanese_name"] = None
            d["name_note"] = ("日本語名未収録（Arena には日本語版あり）＝name_display をそのまま使う（訳名を作らない）" if d.get("digital")
                              else "日本語版なし＝name_display をそのまま使う（訳名を作らない）")
        # 面（2026-08-31 R3-4）: 多面札は faces を同伴し、裏面で当たったときはその面の完成形も返す（name_display は表面固定）
        enb = d.pop("name_en_back", None); jab = d.pop("name_ja_back", None)
        enf = d.pop("name_en_front", None); jaf = d.pop("name_ja_front", None)
        def _face_disp(en, ja):
            return f"《{ja}/{en}》" if ja else f"{en}（{'日本語名未収録' if d.get('digital') else '日本語版なし'}）"
        if enb:
            d["faces"] = [{"en": enf, "ja": jaf, "display": _face_disp(enf, jaf)}, {"en": enb, "ja": jab, "display": _face_disp(enb, jab)}]
            ql = q.lower()
            hit_back = ql in enb.lower() or (jab and q in jab)
            hit_front = ql in (enf or "").lower() or (jaf and q in jaf)
            if hit_back and not hit_front:
                d["matched_face"] = "back"
                d["face_display"] = _face_disp(enb, jab)
                d["face_note"] = "検索語は裏面（出来事・変身後・分割の片方）に当たった。その面を指すときは face_display を使う"
        if d.get("digital"):
            d["digital_note"] = "Arena 専用の札（アルケミー・A- リバランス等）＝紙には存在しない。紙の話では挙げない"
        else:
            d.pop("digital", None)
        return d

    seen, cards = set(), []
    for r in list(name_rows) + list(text_rows):
        if r[0] in seen:
            continue
        seen.add(r[0])
        cards.append(_row(r))
        if len(cards) >= top_k:
            break
    # 3) 一致ゼロのときだけ、曖昧名（pg_trgm 類似度・しきい値 0.3）で救う。
    #    実例: 「孤光のフェニックス」（正しくは弓へんの「弧光」・類似度 0.54）。
    #    旧・名前直行門が持っていた打ち間違い耐性の、SQL 一本での置き換え（2026-08-21）。
    route = "simple_match"
    if not cards:
        route = "fuzzy_name"
        p3 = [q, q, 0.3] + ([fmt] if fmt else []) + [q, q, top_k]
        fuzzy_rows = _db(
            f"SELECT {cols} FROM mtg_cards_v2"
            " WHERE greatest(similarity(card_name, %s),"
            "                similarity(coalesce(japanese_name,''), %s)) > %s"
            + fmt_sql +
            " ORDER BY greatest(similarity(card_name, %s),"
            "                   similarity(coalesce(japanese_name,''), %s)) DESC"
            " LIMIT %s", tuple(p3))
        cards = [_row(r) for r in fuzzy_rows]
    if not cards:
        return (f"該当なし: {q}"
                "（語を減らす・言い換える・または query_mtg_database で SQL を書く。"
                "日本語名が分からないカードは英語名で引き直すこと＝訳名を推測しない）")
    # 17Lands の同伴（2026-09-03 本人「同伴は速さの道具＝高確率で当たる分だけ付ける」）:
    #   draft_set あり → そのセットだけ／不明な記号 → 数字は付けず一覧を返す／なし → 最新 N セット（既定 2）だけ。
    #   それ以外のセットにしか無い札は、一行の道しるべ（limited_stats_elsewhere）に留める。
    requested = _resolve_draft_set(draft_set) if draft_set else None
    constructed = bool(fmt) and not requested and fmt.lower() not in ("limited", "draft", "sealed")
    if constructed:
        allowed: list[str] = []      # 構築の format 指定（modern 等）はドラフトの問いでない＝同伴なし（道しるべだけ）
    elif draft_set and not requested:
        allowed = []
    elif requested:
        allowed = [requested]
    else:
        allowed = [x["code"] for x in _limited_sets()[:_DRAFT_RECENT_N]]
    elsewhere = _attach_limited_stats(cards, allowed)
    archetypes = _limited_archetypes(sorted({s["set"] for c in cards for s in c.get("limited_stats", [])})) if allowed else {}
    out = {"route": route,
           "naming_rule": "name_display（完成形《日本語名/英語名》）を一字も変えずに使う。略称・通称・省略は冗長でも禁止（2 回目以降も）。「英語名（日本語版なし）」「英語名（日本語名未収録）」もそのまま（翻訳禁止・組み立て禁止）",
           "cards": cards}
    if draft_set and not requested:
        out["draft_set_note"] = (f"draft_set「{draft_set}」は手持ちに無いセット＝数字は付けていない。"
                                 f"手持ち（新しい順・記号（名前））: {_sets_line()}。正しい記号で呼び直す")
    if elsewhere:
        out["limited_stats_elsewhere"] = elsewhere
        out["limited_stats_elsewhere_note"] = ("これらの札には別のセットの 17Lands 統計がある（記号のみ列挙）。"
                                               "そのセットのドラフトの問いなら draft_set=<記号> で呼び直す。構築の問いなら無視してよい")
    if any("limited_stats" in c for c in cards):
        scope = (f"対象セット: {'・'.join(allowed)}（" + ("draft_set で指定" if requested else f"既定＝手持ちの最新 {_DRAFT_RECENT_N} セット。別のセットの問いなら draft_set を指定") + "）。")
        out["limited_stats_note"] = (scope + "limited_stats は 17Lands 公開データのセット別 Bo1 ドラフト統計（集計元は Premier Draft＝人間対面。Quick Draft の問いにもカードの強さの物差しとしてそのまま使う＝『Quick Draft のデータが無い』と言って使わないのは誤り。alsa/ata の流れ方だけは人間対面の値で、ボット相手の Quick Draft ではレアが早く消えるなどずれる）。"
                                     "gih_wr=手札に来たゲームの勝率・alsa=最後に見えたピック番号の平均（小さいほど早く消える）・"
                                     "ata=取られたピック番号の平均・gih_games は母数（500 未満はぶれる）。"
                                     "答えに出典「17Lands」を添える。無いセットの数字は書かない。"
                                     "他の列・セット横断の集計は表 limited_card_stats を query_mtg_database で。"
                                     "色の組み合わせ別の勝率は limited_color_stats・相性は limited_matchup_stats・"
                                     "ランク帯別は limited_card_rank_stats／limited_format_stats・ピック側は limited_card_pick_stats（describe_mtg_tables で列を確認）。"
                                     "ドラフトの助言（ピック・デッキの色）は limited_archetypes＝そのセットの 2 色の組み合わせ（タッチ無し・プレイ数 games の多い順・share_pct はセット内の割合・wr は勝率・baseline_wr はセット全体の勝率）を先に見る。"
                                     "share_pct が小さい（目安 1% 未満）組み合わせはそのセットでは成立していないアーキタイプ＝そのデッキを勧めない。成立している組み合わせの中で wr の高いものを軸に考える。"
                                     "3 色が主役のセット（2 色の share がどれも低い）やタッチ有りは limited_color_stats を SQL で（main_colors が 3 文字の行・splash=true）")
        if archetypes:
            out["limited_archetypes"] = archetypes
    return json.dumps(out, ensure_ascii=False, indent=1)


def _attach_limited_stats(cards: list[dict], sets: list[str] | None = None) -> dict[str, list[str]]:
    """検索結果の各札に、17Lands 集計（limited_card_stats）があればセット別に同伴する（2026-09-02）。

    発端: 脳が SOS の札の GIH WR を聞かれ、mtg_cards_v2 と information_schema を手探りしたまま
    表に辿り着かなかった（instructions は claude.ai に届かない＝返り値に載っていないものは無いのと同じ）。
    行が無い札にはキーを出さない（不在は無言でなく、脳が「無い」と読めるように limited_stats_note で線引き）。
    表が無い環境（旧 VM 等）では何もしない。
    2026-09-03: sets（記号の一覧）に入るセットの行だけ同伴し、それ以外のセットは card_name→[記号] で返す（道しるべ用）。"""
    names = [c["card_name"] for c in cards if c.get("card_name")]
    if not names:
        return {}
    try:
        rows = _db(
            "SELECT db_card_name, expansion, event_type, gih_games, gih_wr, oh_wr, gd_wr, alsa, ata"
            " FROM limited_card_stats WHERE db_card_name = ANY(%s)"
            " ORDER BY db_card_name, expansion", (names,))
    except Exception:
        return {}
    by: dict[str, list] = {}
    elsewhere: dict[str, list[str]] = {}
    for n, ex, ev, g, gih, oh, gd, alsa, ata in rows:
        if sets is not None and ex not in sets:
            if not ex.lower().startswith("cube"):
                elsewhere.setdefault(n, []).append(ex)
            continue
        by.setdefault(n, []).append({
            "set": ex, "event": ev, "gih_games": g,
            "gih_wr": float(gih) if gih is not None else None,
            "oh_wr": float(oh) if oh is not None else None,
            "gd_wr": float(gd) if gd is not None else None,
            "alsa": float(alsa) if alsa is not None else None,
            "ata": float(ata) if ata is not None else None,
            "source": "17Lands"})
    for c in cards:
        if c.get("card_name") in by:
            c["limited_stats"] = by[c["card_name"]]
    return elsewhere


_DRAFT_RECENT_N = int(os.environ.get("MCP_DRAFT_RECENT_SETS", "2"))
_sets_cache: dict = {"t": 0.0, "rows": []}


def _limited_sets() -> list[dict]:
    """limited_card_stats の収録セットを発売日の新しい順に（記号・名前・発売日）。Cube は除く。5 分キャッシュ。
    注意: 発売日の最新が今のドラフト環境とは限らない（2026-09-03 実測: 最新は MSH だが Arena の Quick Draft は SOS）
    ＝既定は最新 N（MCP_DRAFT_RECENT_SETS・既定 2）で取りこぼしを減らし、本命は呼び出し側の draft_set。"""
    import time
    if time.time() - _sets_cache["t"] < 300 and _sets_cache["rows"]:
        return _sets_cache["rows"]
    try:
        rows = _db(
            "SELECT s.expansion, m.set_name, m.released_at FROM (SELECT DISTINCT expansion FROM limited_card_stats) s"
            " LEFT JOIN mtg_sets m ON lower(m.set_code) = lower(s.expansion)"
            " WHERE s.expansion NOT ILIKE 'cube%%' ORDER BY m.released_at DESC NULLS LAST, s.expansion", ())
    except Exception:
        return []
    out = [{"code": r[0], "name": r[1], "released_at": str(r[2]) if r[2] else None} for r in rows]
    _sets_cache.update(t=time.time(), rows=out)
    return out


def _resolve_draft_set(s: str | None) -> str | None:
    """『#SOS』『LimitedSOS』『sos』『Secrets of Strixhaven』を記号 SOS に。手持ちに無ければ None（推測しない）。"""
    if not s:
        return None
    key = s.strip().lstrip("#").strip()
    low = key.lower()
    for pre in ("limited", "quickdraft", "quick draft", "quick", "premierdraft", "premier", "draft", "sealed"):
        if low.startswith(pre):
            key = key[len(pre):].strip(" _-:：")
            low = key.lower()
    if not key:
        return None
    sets = _limited_sets()
    for x in sets:
        if x["code"].lower() == low:
            return x["code"]
    for x in sets:
        if x["name"] and (low in x["name"].lower() or x["name"].lower() in low):
            return x["code"]
    return None


def _sets_line() -> str:
    return "・".join(f"{x['code']}（{x['name']}）" if x["name"] else x["code"] for x in _limited_sets())


def _archetype_lines(code: str) -> str:
    """query の返り値に添える、そのセットの色の組み合わせ表（1 行）。"""
    a = _limited_archetypes([code]).get(code)
    if not a:
        return ""
    pairs = "・".join(f"{p['colors']} {p['share_pct']}% wr{p['wr']}" for p in a["color_pairs"])
    return (f"\n[#{code} の色の組み合わせ（17Lands・2 色・タッチ無し・プレイ数順・share% と勝率・基準線 {a['baseline_wr']}）] {pairs}"
            "\n（share が 1% 未満の組み合わせはそのセットでは成立していない）")


def _limited_archetypes(sets: list[str]) -> dict:
    """セットごとの色の組み合わせ別勝率（2 色・タッチ無し・limited_color_stats）を search の返り値に同伴する。

    発端（2026-09-02 本人）: 「アーキタイプ別勝率を参考にしながら、と言わないと SOS に無いアーキタイプ
    （有効色）でデッキを作り出す」。返り値に載っていない表は脳が引かない前提（8/22 裁定）なので、
    札の統計を付けたセットについて 2 色の組み合わせをプレイ数順で丸ごと（10 行）載せる（勝率順だと
    母数 100 戦の組み合わせが上位に混ざって読み違える＝SOS で実測）。share_pct はセット内の割合＝成立しない
    組み合わせ（SOS の WU 0.04% 等）を脳が構造で見分けるための列。
    baseline_wr はセット全体の勝率（limited_format_stats のランク帯合算）＝組み合わせの良し悪しの基準線。
    色の列が無いセット（STX）や表が無い環境では空。"""
    if not sets:
        return {}
    try:
        rows = _db(
            "SELECT expansion, main_colors, games, wins FROM limited_color_stats"
            " WHERE expansion = ANY(%s) AND NOT splash AND length(main_colors) = 2"
            " ORDER BY expansion, games DESC", (sets,))
        base = _db(
            "SELECT expansion, sum(games), sum(wins) FROM limited_format_stats"
            " WHERE expansion = ANY(%s) GROUP BY 1", (sets,))
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for ex, g, w in base:
        out[ex] = {"baseline_wr": round(w / g, 4) if g else None, "color_pairs": []}
    tot: dict[str, int] = {}
    for ex, _mc, g, _w in rows:
        tot[ex] = tot.get(ex, 0) + (g or 0)
    for ex, mc, g, w in rows:
        out.setdefault(ex, {"baseline_wr": None, "color_pairs": []})["color_pairs"].append(
            {"colors": mc, "games": g, "share_pct": round(100.0 * g / tot[ex], 2) if tot.get(ex) else None,
             "wr": round(w / g, 4) if g else None, "source": "17Lands"})
    return {ex: v for ex, v in out.items() if v["color_pairs"]}


# ─── 確率計算の入口（2026-09-04 本人「あらゆる確率計算をどこかに格納して…」→「ひとまず最低限だけ」）───
# 計算の本体は DB の SQL 関数（sql/prob_functions.sql・超幾何・IMMUTABLE）。ここは名前付き引数で受けて同じ関数を呼び、
# 数字と式と入力の復唱を返す薄い入口。LLM に算術をさせない・自由なコード実行は置かない（箱は 2 コア・公開口）。
# データと結合したいときは query_mtg_database から関数を直接呼ぶ（例: SELECT mtg_land_drops(60, 24, 4, true)）。
_PROB_KINDS = ("at_least", "by_turn", "land_drops", "combo_by_turn")


@server.tool(
    name="mtg_probability",
    description=(
        "【確率の計算はこれ。自分で計算しない】デッキの確率を超幾何分布で厳密に計算する（DB の SQL 関数・決定的・1 ミリ秒未満）。"
        "kind: at_least=N 枚のデッキから D 枚引いて K 枚入りの札が m 枚以上／by_turn=turn ターン目までに K 枚入りの札を m 枚以上"
        "（見る枚数=初手 7−マリガン＋引き・先手は turn−1 回・後手は turn 回）／land_drops=turn ターン目まで毎ターン土地を置ける"
        "（見た札に土地が turn 枚以上）／combo_by_turn=turn ターン目までに A（copies）と B（copies_b）を両方 1 枚以上。"
        "引数: deck_size（60/40/99 等）・copies（当たりの枚数・land_drops では土地の枚数）・copies_b（combo の相方）・"
        "draws（at_least の引く枚数）・turn・on_play（先手 true／後手 false）・at_least（m・既定 1）・mulligans（既定 0）。"
        "返り値の probability を percent と一緒にそのまま書き、formula と cards_seen を添えると読者が検算できる。"
        "色マナ源の問い（例: 2 ターン目に青 2 つ）は by_turn で copies=その色のソース枚数・at_least=必要数。"
        "答えに『前提: 先手／後手・マリガン n 回』を必ず添える。"))
def mtg_probability(kind: str, deck_size: int = 60, copies: int = 4, copies_b: int = 0, draws: int = 7,
                    turn: int = 1, on_play: bool = True, at_least: int = 1, mulligans: int = 0) -> str:
    _log_tool("mtg_probability", {"kind": kind, "deck_size": deck_size, "copies": copies, "copies_b": copies_b,
                                  "draws": draws, "turn": turn, "on_play": on_play, "at_least": at_least, "mulligans": mulligans})
    if kind not in _PROB_KINDS:
        return json.dumps({"error": f"kind は {', '.join(_PROB_KINDS)} のどれか（受け取った値: {kind!r}）"}, ensure_ascii=False)
    bad = []
    if not (1 <= deck_size <= 500): bad.append("deck_size は 1〜500")
    if not (0 <= copies <= deck_size): bad.append("copies は 0〜deck_size")
    if not (0 <= copies_b <= deck_size): bad.append("copies_b は 0〜deck_size")
    if not (0 <= draws <= deck_size): bad.append("draws は 0〜deck_size")
    if not (1 <= turn <= 60): bad.append("turn は 1〜60")
    if not (0 <= at_least <= deck_size): bad.append("at_least は 0〜deck_size")
    if not (0 <= mulligans <= 6): bad.append("mulligans は 0〜6")
    if bad:
        return json.dumps({"error": "引数の範囲外: " + "・".join(bad)}, ensure_ascii=False)
    premise = f"先手（{turn} ターン目までの引き {max(turn - 1, 0)} 回）" if on_play else f"後手（{turn} ターン目までの引き {turn} 回）"
    if mulligans:
        premise += f"・マリガン {mulligans} 回（初手 {7 - mulligans} 枚）"
    try:
        if kind == "at_least":
            rows = _db("SELECT mtg_hypergeom_atleast(%s, %s, %s, %s)", (deck_size, copies, draws, at_least))
            seen = draws; premise = f"{draws} 枚引く"
            formula = f"P(X ≥ {at_least}) = Σ C({copies}, i)·C({deck_size - copies}, {draws}−i) / C({deck_size}, {draws})  (i = {at_least}..min({copies},{draws}))"
        elif kind == "by_turn":
            rows = _db("SELECT mtg_prob_by_turn(%s, %s, %s, %s, %s, %s), mtg_cards_seen(%s, %s, %s)",
                       (deck_size, copies, turn, on_play, at_least, mulligans, turn, on_play, mulligans))
            seen = rows[0][1]
            formula = f"見る枚数 {seen} = 7−{mulligans}＋{seen - 7 + mulligans}・P(X ≥ {at_least}) 超幾何(N={deck_size}, K={copies}, D={seen})"
        elif kind == "land_drops":
            rows = _db("SELECT mtg_land_drops(%s, %s, %s, %s, %s), mtg_cards_seen(%s, %s, %s)",
                       (deck_size, copies, turn, on_play, mulligans, turn, on_play, mulligans))
            seen = rows[0][1]
            formula = f"見る枚数 {seen}・P(土地 ≥ {turn} 枚) 超幾何(N={deck_size}, K={copies}, D={seen})＝{turn} ターン目まで毎ターン土地を置ける"
        else:
            rows = _db("SELECT mtg_combo_by_turn(%s, %s, %s, %s, %s, %s), mtg_cards_seen(%s, %s, %s)",
                       (deck_size, copies, copies_b, turn, on_play, mulligans, turn, on_play, mulligans))
            seen = rows[0][1]
            formula = f"見る枚数 {seen}・1 − P(A なし) − P(B なし) ＋ P(両方なし)（包除・A={copies} 枚・B={copies_b} 枚・N={deck_size}）"
    except Exception as e:
        return json.dumps({"error": f"計算に失敗: {str(e)[:200]}（SQL 関数 mtg_* が無い環境の可能性）"}, ensure_ascii=False)
    p = rows[0][0]
    if p is None:
        return json.dumps({"error": "この入力では定義できない（枚数の整合を確認: copies+copies_b ≤ deck_size 等）"}, ensure_ascii=False)
    p = float(p)
    out = {"kind": kind, "inputs": {"deck_size": deck_size, "copies": copies, "copies_b": copies_b if kind == "combo_by_turn" else None,
                                    "draws": draws if kind == "at_least" else None, "turn": None if kind == "at_least" else turn,
                                    "on_play": None if kind == "at_least" else on_play, "at_least": at_least if kind in ("at_least", "by_turn") else None,
                                    "mulligans": None if kind == "at_least" else mulligans},
           "cards_seen": seen, "probability": round(p, 4), "percent": f"{p * 100:.1f}%", "premise": premise, "formula": formula,
           "note": "超幾何分布の厳密値（17Lands 等の実測ではない）。デッキ全体を無作為に切った前提。土地の連続配置は『見た札に土地が turn 枚以上』の近似ではなく同値。"}
    out["inputs"] = {k: v for k, v in out["inputs"].items() if v is not None}
    return json.dumps(out, ensure_ascii=False, indent=1)


# ─── Commander Spellbook（2026-09-04 本人 GO「API 解放されてるんだから使わせてもよくね」）───
# 取り込まず都度呼ぶ（8/29 survey: 第三者ツールからの API 表示は公式 docs で許容・データのライセンスは明文なし＝再配布は灰
# → DB に持たない）。返り値に出典と前提の原文を必ず載せる（本人「2 枚だけでは成立しない前提付きが多い」）。
# bracketTag・結果タグは正確性に欠ける実例（7/28・EDH Build Helper）があるので「参考」と明記。箱の砂場から backend へは
# AF_INET 許可で到達済み（9/4 実測 200・1.0 秒）。時間制限 6 秒・失敗はこの道具だけが「届かない」を返す。
_SPELLBOOK_URL = os.environ.get("SPELLBOOK_URL", "https://backend.commanderspellbook.com/find-my-combos")
_SPELLBOOK_TIMEOUT = float(os.environ.get("SPELLBOOK_TIMEOUT", "6"))


def _spellbook_post(payload: dict) -> dict:
    import urllib.request
    req = urllib.request.Request(_SPELLBOOK_URL, data=json.dumps(payload).encode("utf-8"),
                                 headers={"content-type": "application/json", "accept": "application/json",
                                          "user-agent": "mtg-sisho-mcp/1.0 (+https://github.com/)"}, method="POST")
    with urllib.request.urlopen(req, timeout=_SPELLBOOK_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _display_map(names: list[str]) -> dict[str, str]:
    """英語名 → 完成形 name_display（DB に無い名前は「英語名（DB 未収録）」）。"""
    out = {n: f"{n}（DB 未収録）" for n in names}
    if not names:
        return out
    try:
        for cn, nd in _db("SELECT card_name, name_display FROM mtg_cards_v2 WHERE card_name = ANY(%s)", (names,)):
            out[cn] = nd
    except Exception:
        pass
    return out


@server.tool(
    name="find_combos",
    description=(
        "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く。】"
        "【コンボを探すときはこれ。記憶で組み合わせを書かない】手持ちのカード名（英語名・日本語名どちらでも）を渡すと、"
        "Commander Spellbook（公開 API・都度照会・出典を必ず添える）から、いま組めるコンボ（included）・あと 1 枚で組めるコンボ"
        "（almost_included）・色を足せば組めるコンボ（by_adding_colors）を返す。各コンボに使う札（完成形）・生み出す効果・"
        "**前提の原文（prerequisites）**・人気・出典 URL。前提付きのコンボは前提をそのまま書く（2 枚で成立するとは限らない）。"
        "bracket タグは参考（正確性に欠ける実例あり）。commanders に統率者名を入れると統率者領域を考慮する。"
        "デッキ 1 本（最大 120 枚）を渡す使い方が本来の形。日本語名は DB で英語名に直してから照会する。"))
def find_combos(card_names: list[str], commanders: list[str] | None = None, limit: int = 10) -> str:
    _log_tool("find_combos", {"n": len(card_names or []), "commanders": commanders, "limit": limit})
    names = [n.strip() for n in (card_names or []) if n and n.strip()]
    cmds = [n.strip() for n in (commanders or []) if n and n.strip()]
    if not names:
        return json.dumps({"error": "card_names が空"}, ensure_ascii=False)
    if len(names) > 120:
        return json.dumps({"error": "card_names は 120 枚まで"}, ensure_ascii=False)
    limit = max(1, min(int(limit), 30))
    # 日本語名 → 英語名（DB）。英語名はそのまま。見つからない名前はそのまま送る（Spellbook 側で無視される）
    resolved: dict[str, str] = {}
    try:
        rows = _db("SELECT card_name, japanese_name, name_ja_front FROM mtg_cards_v2"
                   " WHERE card_name = ANY(%s) OR japanese_name = ANY(%s) OR name_ja_front = ANY(%s)", (names + cmds, names + cmds, names + cmds))
        for cn, ja, jaf in rows:
            resolved[cn] = cn
            if ja: resolved[ja] = cn
            if jaf: resolved[jaf] = cn
    except Exception:
        pass
    en_main = [resolved.get(n, n) for n in names]
    en_cmd = [resolved.get(n, n) for n in cmds]
    unresolved = [n for n in names + cmds if n not in resolved and not n.isascii()]
    payload = {"main": [{"card": n, "quantity": 1} for n in en_main], "commanders": [{"card": n, "quantity": 1} for n in en_cmd]}
    try:
        data = _spellbook_post(payload)
    except Exception as e:
        return json.dumps({"error": f"Commander Spellbook に届かない（{type(e).__name__}: {str(e)[:120]}）。少し待って再試行。この道具以外は影響なし"}, ensure_ascii=False)
    res = data.get("results", data) if isinstance(data, dict) else {}
    sections = {"included": "いま組める", "almostIncluded": "あと 1 枚で組める", "almostIncludedByAddingColors": "色を足せば組める"}
    all_names: set[str] = set()
    for key in sections:
        for c in (res.get(key) or [])[:limit]:
            for u in c.get("uses") or []:
                all_names.add((u.get("card") or {}).get("name", ""))
    disp = _display_map(sorted(n for n in all_names if n))
    out: dict = {"source": "Commander Spellbook（https://commanderspellbook.com/・公開 API・照会時点の内容）",
                 "input": {"main": en_main, "commanders": en_cmd},
                 "note": ("前提（prerequisites）は原文のまま。前提付きのコンボは書かれた条件が揃わないと成立しない＝『2 枚で成立』と書かない。"
                          "bracket タグは参考（正確性に欠ける実例あり・断定しない）。人気（popularity）は Spellbook 側の集計。"
                          "答えには出典「Commander Spellbook」と各コンボの URL を添える。カード名は uses_display の完成形をそのまま書く")}
    if unresolved:
        out["unresolved_names"] = unresolved
        out["unresolved_note"] = "DB で英語名に直せなかった名前（綴りを search_mtg_cards で確認して呼び直す）"
    for key, label in sections.items():
        items = res.get(key) or []
        rows = []
        for c in items[:limit]:
            uses = [(u.get("card") or {}).get("name", "") for u in c.get("uses") or []]
            rows.append({"id": c.get("id"), "url": f"https://commanderspellbook.com/combo/{c.get('id')}/",
                         "uses": uses, "uses_display": [disp.get(n, n) for n in uses],
                         "requires_templates": [(t.get("template") or {}).get("name", "") for t in c.get("requires") or []],
                         "produces": [(p.get("feature") or {}).get("name", "") for p in c.get("produces") or []],
                         "prerequisites": " / ".join(x for x in [c.get("easyPrerequisites") or "", c.get("notablePrerequisites") or ""] if x) or None,
                         "description": (c.get("description") or "")[:600] or None,
                         "popularity": c.get("popularity"), "bracket_tag": c.get("bracketTag"), "identity": c.get("identity")})
        out[key] = {"label": label, "count_total": len(items), "shown": len(rows), "combos": rows}
    return json.dumps(out, ensure_ascii=False, indent=1)


# ─── ローカル DB 直結の道具（2026-08-10 深夜・本人「搬入が要るのでは」への答え）───
# 試作サーバーは VM に住んでいるので、mtg_rules / card_rulings（ローカルのみ・
# Aurora 未搬入）に直接手が届く。恒久版ではこの 2 本のデータを搬入 or 焼き込みする
# （工程表 v0 の 1 番・Aurora/イメージ/VPS の裁定とセット）。読み取り専用クエリのみ。

# DB の席取りと接続（_db_slot／_db／_db_readonly）は sisho/db.py へ切り出した
# （2026-09-05 Step 2）。設計の経緯（readonly_ai の上限 6・席 5・待ち 20 秒）はそちらの注記に。


@server.tool(
    name="lookup_mtg_rule",
    description=(
        "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
        "【ルールの疑問・処理の順番・用語の定義は、Web で調べる前にこれ】"
        "MTG 総合ルール（Comprehensive Rules・条文3,317＋用語集739）を引く。"
        "条番号（例: '702.19' '601.2b'）または英語キーワード（例: 'trample' 'state-based'）"
        "で検索できる。条文の原文は英語なので、必要に応じて日本語に訳して伝えること。"
        "裁定の根拠を条番号つきで示したいときに使う。"))
def lookup_mtg_rule(query: str, limit: int = 12) -> str:
    _log_tool("lookup_mtg_rule", {"query": query})

    query = query.strip()
    limit = max(1, min(int(limit), 30))
    import re as _re
    if _re.fullmatch(r"\d{1,3}(\.\d+[a-z]?)?\.?", query):
        # 前方一致は禁物: '702.19%' は 702.190（別条文）まで拾う。枝は「702.19a」
        # 形式なので「自身・直下の英字枝・直下の数字枝」だけを正規表現で許す。
        q = query.rstrip('.')
        pat = '^' + _re.escape(q) + r'(\.\d+[a-z]?|[a-z])?$'
        rows = _db(
            "SELECT rule_number, is_glossary, text_en FROM mtg_rules"
            " WHERE rule_number ~ %s ORDER BY rule_number LIMIT %s",
            (pat, limit))
    else:
        # 語検索は FTS の AND（2026-08-11・脳からのバグ報告「dies trigger simultaneous で
        # 該当なし」＝旧実装は句全体の部分一致で複数語に無力だった）。plainto_tsquery は
        # 全語 AND・語形正規化つき。ts_rank 順で条文らしさの高い順に返す。
        rows = _db(
            "SELECT rule_number, is_glossary, text_en FROM mtg_rules"
            " WHERE to_tsvector('english', text_en) @@ plainto_tsquery('english', %s)"
            " ORDER BY ts_rank(to_tsvector('english', text_en),"
            "                  plainto_tsquery('english', %s)) DESC,"
            "          is_glossary DESC, rule_number LIMIT %s",
            (query, query, limit))
        if not rows:
            # 全語 AND が不発なら OR に降りて「多く当たる順」（ts_rank が自然にやる）。
            # 脳の実クエリは概念の羅列（dies trigger simultaneous…）なので全語一致は稀。
            import re as _re2
            words = _re2.findall(r"[A-Za-z][A-Za-z'-]+", query)
            if len(words) >= 2:
                orq = " | ".join(words)
                rows = _db(
                    "SELECT rule_number, is_glossary, text_en FROM mtg_rules"
                    " WHERE to_tsvector('english', text_en) @@ to_tsquery('english', %s)"
                    " ORDER BY ts_rank(to_tsvector('english', text_en),"
                    "                  to_tsquery('english', %s)) DESC, rule_number"
                    " LIMIT %s", (orq, orq, limit))
        if not rows:   # FTS 不発（一語・固有表現等）は従来の部分一致で救う
            rows = _db(
                "SELECT rule_number, is_glossary, text_en FROM mtg_rules"
                " WHERE text_en ILIKE %s OR (is_glossary AND rule_number ILIKE %s)"
                " ORDER BY is_glossary DESC, rule_number LIMIT %s",
                (f"%{query}%", f"%{query}%", limit))
    if not rows:
        return f"該当なし: {query}（条番号または英語キーワードで検索してください）"
    out = []
    for num, glos, text in rows:
        tag = "用語集" if glos else "条文"
        out.append(f"[{tag} {num}] {text}")
    return "\n\n".join(out)


@server.tool(
    name="get_card_rulings",
    description=(
        "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
        "【特定カードの挙動・裁定は、Web の解説記事より先にこれ（公式一次情報）】"
        "カードの公式裁定（Wizards 公式 rulings・77,998 件収録）をカード名で引く。"
        "カード名は英語の正式名（例: 'Ragavan, Nimble Pilferer'）。部分一致も可。"
        "裁定の原文は英語なので、必要に応じて日本語に訳して伝えること。"))
def get_card_rulings(card_name: str, limit: int = 20) -> str:
    _log_tool("get_card_rulings", {"card_name": card_name})

    limit = max(1, min(int(limit), 40))
    rows = _db(
        "SELECT card_name, published_at, comment FROM card_rulings"
        " WHERE card_name = %s ORDER BY published_at, id LIMIT %s",
        (card_name.strip(), limit))
    if not rows:                                   # 面の名前（表・裏）→ 正式名に解決して引く（2026-08-31 R3-4）
        full = _db("SELECT card_name FROM mtg_cards_v2 WHERE name_en_front = %s OR name_en_back = %s ORDER BY (name_en_front = %s) DESC LIMIT 1",
                   (card_name.strip(), card_name.strip(), card_name.strip()))
        if full:
            rows = _db("SELECT card_name, published_at, comment FROM card_rulings WHERE card_name = %s ORDER BY published_at, id LIMIT %s",
                       (full[0][0], limit))
    if not rows:
        rows = _db(
            "SELECT card_name, published_at, comment FROM card_rulings"
            " WHERE card_name ILIKE %s ORDER BY card_name, published_at, id LIMIT %s",
            (f"%{card_name.strip()}%", limit))
    if not rows:
        return f"裁定なし: {card_name}（英語の正式カード名で検索してください）"
    out = [f"{name}（{date}）: {comment}" for name, date, comment in rows]
    return "\n\n".join(out)


def _name_variants(name: str) -> list[str]:
    """両面・分割カードの名前ゆれを吸収する候補名を返す（2026-08-13）。

    棚ごとにキーの持ち方が違うのが根本原因:
      card_cooccurrence（mtgtop8 系・古い）     → 名前キー。中身は mtgtop8 が書く
                                                  「表の名前」（例 Brazen Borrower）
      edh_card_cooccurrence_v2（7/30 新設）     → ID キー。カード表の正式名で引く
                                                  （例 Brazen Borrower // Petty Theft）
    道具の入口でこの差を吸収しないと、scope によって通る名前が逆になる
    （実測 2026-08-13: 正式名は constructed で空振り・表の名前は edh で空振り）。
    該当は card_name に ' // ' を持つ 810 枚。
    """
    out = [name]
    front = name.split(" // ")[0]
    if front != name:
        out.append(front)                       # 正式名 → 表の名前
    else:                                       # 表の名前 → 正式名（DB 引き）
        out += [r[0] for r in _db(
            "SELECT card_name FROM mtg_cards_v2 WHERE name_en_back IS NOT NULL AND (name_en_front = %s OR name_en_back = %s)",
            (name, name))]
    seen, uniq = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


@server.tool(
    name="find_partner_cards",
    description=(
        "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
        "【カードを軸にデッキを組む・相方を探すときは必ずこれを先に呼ぶ。そのカードの「現行の家」（実際に一緒に使われている札）が分かる唯一の道具で、Web にもモデルの記憶にも無い情報】"
        "指定カードと同じデッキに入りやすいカード（共起）を実デッキ集計から返す。"
        "Phase 2 のデッキ壁打ち用。scope: 'edh'（統率者・既定）/ 'constructed'"
        "（mtgtop8＋MTGO 公式の 60 枚構築）/ 'pauper' / 'vintage' / 'precon'（公式構築済み製品）。"
        "exclude_lands=True で土地を除く（汎用フェッチ等が上位を占めがちなため）。"
        "返り値は同居率 pct と lift（偶然同居の期待値比）付き。order_by='lift' で"
        "汎用札を沈めて専属シナジー順に並べ替え（既定は同居数順・2026-08-25）。"
        "カード名は英語の正式名でも表面の名前でもよい（両面・分割カードの表記ゆれは"
        "道具側で吸収する・2026-08-13）。"
        "【内部専用】この道具はデータ出自（Moxfield/mtgtop8）の許可が未決着のため"
        "公開版カセットには含めない（2026-08-11 権利札）。"))
def find_partner_cards(card_name: str, scope: str = "edh",
                       limit: int = 15, exclude_lands: bool = False,
                       order_by: str = "count") -> str:
    """共起ペアは片方向格納＝両面 UNION で引く（2026-08-11 実査）。

    名前は表記ゆれを吸収して引く（_name_variants・2026-08-13）。結果側の JOIN も
    同じ理由で表の名前を許す＝相方が両面カードのとき静かに落ちるのを防ぐ
    （実測: 相方名 215 種・7,958 ペアが落ちていた。うち 156 種は表の名前で救える）。

    2026-08-25 分母導入（本人裁定「パーセンテージ表記に賛成」）:
    - pct = n_ab / (このカード入りデッキ数)。分母は毎回 deck_cards から実測
      （edh_card_strength の play_decks は母集団の一致が未検証なので借りない）。
    - lift = pct / (相方カードの全体出現率)。1.0 ≈ 偶然同居（汎用札）・高いほど専属シナジー。
    - order_by='lift' は同居 10 本以上・生カウント上位 400 の中で並べ替え
      （少数サンプルの lift 暴発と全相方の分母計算の重さを両方避ける近似）。
    - 分母の deck_list 直数えは共起集計の重複除去・MTGO 転載除外を再現しない
      ＝pct は 2〜3% 控えめに出る近似（構築 scope でやや大きめ）。"""
    _log_tool("find_partner_cards", {"card_name": card_name, "scope": scope,
                                     "exclude_lands": exclude_lands, "order_by": order_by})
    name = card_name.strip()
    limit = max(1, min(int(limit), 30))
    if order_by not in ("count", "lift"):
        return f"order_by が不正: {order_by}（count / lift）"
    land_cond = " AND c.type_line NOT ILIKE '%%Land%%'" if exclude_lands else ""
    names = _name_variants(name)
    # lift 順は母集団を広めに取ってから並べ替える（count 順は最初から limit で足りる）
    pool_limit = 400 if order_by == "lift" else limit
    min_ab = 10 if order_by == "lift" else 1
    order_sql = "lift DESC NULLS LAST" if order_by == "lift" else "n_ab DESC"
    # デッキ側 source（分母とペアの母集団を揃える）
    deck_src = {"edh": ["moxfield_edh", "mtgtop8_edh"],
                "commander": ["moxfield_edh", "mtgtop8_edh"],
                "constructed": ["mtgtop8", "mtgo", "mtgo_other"],
                "pauper": ["mtgtop8_pauper", "mtgo_pauper"],
                "vintage": ["mtgtop8_vintage", "mtgo_vintage"],
                "precon": ["mtgjson_precon"]}.get(scope)
    if not deck_src:
        return f"scope が不正: {scope}（edh/constructed/pauper/vintage/precon）"
    if scope in ("edh", "commander"):
        pair_sql = (
            "  SELECT e.card_id_b AS pid, e.deck_count AS cnt"
            "  FROM edh_card_cooccurrence_v2 e JOIN mtg_cards_v2 a ON a.id=e.card_id_a"
            "  WHERE a.card_name = ANY(%(names)s)"
            "  UNION ALL"
            "  SELECT e.card_id_a, e.deck_count"
            "  FROM edh_card_cooccurrence_v2 e JOIN mtg_cards_v2 b ON b.id=e.card_id_b"
            "  WHERE b.card_name = ANY(%(names)s)")
        resolve = "SELECT x.pid, sum(x.cnt) AS n_ab FROM (" + pair_sql + ") x GROUP BY x.pid"
    else:
        # 2026-08-22: MTGO 公式を source に追加（mtgtop8 側は MTGO 転載行を被覆期間で除外済み＝二重なし）
        # 相方名 → カード表。正式名でも表の名前でも当たるようにする。
        # 衝突は実測ゼロ（唯一の一致は SP//dr が自分自身に当たる自己一致）。
        pair_sql = (
            "  SELECT card_name_b AS pname, co_count AS cnt FROM card_cooccurrence"
            "  WHERE card_name_a = ANY(%(names)s) AND source = ANY(%(csrc)s)"
            "  UNION ALL"
            "  SELECT card_name_a, co_count FROM card_cooccurrence"
            "  WHERE card_name_b = ANY(%(names)s) AND source = ANY(%(csrc)s)")
        resolve = (
            "SELECT cc.id AS pid, sum(x.cnt) AS n_ab FROM (" + pair_sql + ") x"
            " JOIN mtg_cards_v2 cc"
            "  ON (cc.card_name = x.pname OR split_part(cc.card_name,' // ',1) = x.pname)"
            " GROUP BY cc.id")
    rows = _db(
        "WITH pool AS (SELECT id FROM deck_list WHERE source = ANY(%(dsrc)s)),"
        " npool AS (SELECT count(*) AS n FROM pool),"
        " da AS (SELECT count(DISTINCT dc.deck_id) AS n FROM deck_cards dc"
        "        JOIN pool p ON p.id = dc.deck_id"
        "        WHERE dc.card_id IN (SELECT id FROM mtg_cards_v2 WHERE card_name = ANY(%(names)s))),"
        " agg AS (" + resolve + "),"
        " top AS (SELECT agg.pid, agg.n_ab FROM agg JOIN mtg_cards_v2 c ON c.id = agg.pid"
        "         WHERE agg.n_ab >= %(min_ab)s" + land_cond +
        "         ORDER BY agg.n_ab DESC LIMIT %(pool_limit)s),"
        " nb AS (SELECT dc.card_id, count(DISTINCT dc.deck_id) AS n FROM deck_cards dc"
        "        JOIN pool p ON p.id = dc.deck_id"
        "        WHERE dc.card_id IN (SELECT pid FROM top) GROUP BY dc.card_id)"
        " SELECT c.card_name, c.name_display, t.n_ab,"
        "        round(100.0 * t.n_ab / greatest(da.n, 1), 1) AS pct,"
        "        round((t.n_ab::numeric / greatest(da.n, 1))"
        "              / nullif(nb.n::numeric / npool.n, 0), 1) AS lift"
        " FROM top t JOIN mtg_cards_v2 c ON c.id = t.pid"
        " LEFT JOIN nb ON nb.card_id = t.pid, da, npool"
        " ORDER BY " + order_sql + " LIMIT %(limit)s",
        {"names": names, "dsrc": deck_src, "csrc": deck_src,
         "min_ab": min_ab, "pool_limit": pool_limit, "limit": limit})
    if not rows:
        return f"共起なし: {name}（scope={scope}・英語の正式カード名で指定してください）"
    out = [f"{disp}: {pct}%（{n_ab} 本同居・lift {lift if lift is not None else '?'}）"
           for en, disp, n_ab, pct, lift in rows]
    return (f"scope={scope}・order_by={order_by} の同居カード上位。"
            "pct=このカード入りデッキのうち相方も入れている割合・lift=偶然同居の期待値比"
            "（1.0≈どのデッキにも入る汎用札・高いほどこのカード専属のシナジー・"
            "order_by='lift' で専属順に並べ替え可）。"
            "名前は完成形《日本語名/英語名》を一字も変えずに使う・略称禁止・"
            "「英語名（日本語版なし）」もそのまま:\n" + "\n".join(out))


# ─── 自由 SQL の口（2026-08-11・本人発案「エージェント自身が SQL を叩く路線」）───
# 鞘は三重: (1) readonly_ai ロールと (2) statement_timeout 10 秒は sisho/db.py の
# _db_readonly（原文の注記もそちらへ一緒に移した）・(3) 入口で SELECT/WITH 以外と複文を
# 拒否＋行数・セル長の上限で応答を制限（コンテキスト爆発防止）は下の query_mtg_database。


@server.tool(
    name="query_mtg_database",
    description=("【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
        "【専用ツールで表せない集計は Web に行かずここで SQL】読み取り専用 SQL（PostgreSQL・SELECT/WITH のみ・1 文・10 秒・最大 50 行）。"
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
          "mtg_land_drops(deck,lands,turn,on_play,mull)・mtg_combo_by_turn(deck,a,b,turn,on_play,mull)・mtg_cards_seen(turn,on_play,mull)。"))
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
    except Exception as e:
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
    列名で推測せず値で判定（card_name_a / pname / 別名付き列でも効く）。結果に japanese_name 列が
    既にあれば何もしない。日本語版なしは「日本語版なし」と明示（NULL と未一致を区別する）。
    """
    if any(c.lower() in ("name_display",) or c.lower().endswith("_display") for c in cols):
        return cols, rows, ""
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
    name_cols = []
    for i in str_cols:
        vals = [r[i] for r in rows if isinstance(r[i], str) and r[i]]
        if vals and sum(v in ja_of for v in vals) * 2 >= len(vals):
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


# ─── 答案検査（2026-08-22 夕・本人裁定「選択肢 1」）────────────────────────
# 書式ベンチの残穴は「道具の返り値に出ないカードを脳が記憶で挙げて自分で訳す」型
# （Sonnet medium 10 問で 11 件・Opus low 200 問で 7 答案）。返り値側の同伴（_ja）では
# 届かないので、答案そのものを DB に当てる口を置く。脳が最後に一回呼べば届く。

_NAME_CACHE: dict = {"ts": 0.0}
_JA_STOP = {"ショック", "巻き添え", "レベルアップ", "ナズグル", "フラッシュバック", "トランプル", "生け贄",
            "破壊不能", "打ち消し", "呪禁", "瞬速", "警戒", "飛行", "速攻", "接死", "絆魂", "威迫", "到達",
            "護法", "占術", "変身", "追放", "死亡", "召集", "探査", "続唱", "親和", "奇跡", "反復", "予見",
            "待機", "変容", "超過", "明滅", "接合", "倍増", "転生", "消術", "キッカー", "サイクリング",
            "マッドネス", "モーフ", "プロテクション"}


def _names() -> dict:
    """カード名表（1 時間キャッシュ）。ja→正式名・en→(ja or None)・両面は表名も登録。"""
    import time
    if time.time() - _NAME_CACHE["ts"] < 3600 and "ja" in _NAME_CACHE:
        return _NAME_CACHE
    # 2026-08-31（R3-4）: 面の列で組む。8/31 まで裏面の英語名に表面の日本語名を割り当てていた（《厚かましい借り手/Petty Theft》型の誤接合）。
    #   en_ja: 英語（正式名／表面名／裏面名）→ 同じ粒度の日本語（無ければ None）
    #   ja_full: 日本語（空白抜き）→ 日本語（そのまま）  ja_en: 日本語 → 同じ粒度の英語
    #   裏面名は本物のカード名と同じことがある（prepare 20 枚）→ 裏面は setdefault＝本物のカードが勝つ
    rows = _db("SELECT card_name, japanese_name, digital, name_en_front, name_en_back, name_ja_front, name_ja_back FROM mtg_cards_v2", ())
    ja_full, en_ja, ja_en, digital = {}, {}, {}, set()
    for en, ja, dg, enf, enb, jaf, jab in rows:
        if dg:
            digital.update(x for x in (en, enf, enb) if x)
        en_ja[en] = ja
        en_ja[enf] = jaf
        if enb:
            en_ja.setdefault(enb, jab)
        for j, e in ((ja, en), (jaf, enf), (jab, enb)):
            if j and e:
                ja_full[j.replace(" ", "")] = j
                ja_en.setdefault(j, e)
    # 裸の英語名検出用: 日本語名があり、5 文字以上か空白入り（短い一般語を避ける）
    en_bare = sorted((e for e, j in en_ja.items() if j and (len(e) >= 5 or " " in e)), key=len, reverse=True)
    ja_bare = sorted((j for j in ja_full if len(j) >= 4 and j not in _JA_STOP), key=len, reverse=True)
    _NAME_CACHE.update({"ts": time.time(), "ja": ja_full, "en": en_ja, "ja_en": ja_en, "en_bare": en_bare, "ja_bare": ja_bare, "digital": digital})
    return _NAME_CACHE


@server.tool(
    name="verify_answer",
    description=(
        "【答えを出す前の最後の一手・必須】日本語でカード名を含む答えを書き上げたら、送信する前に必ず全文をこれに渡し、返った修正版を答えにする。"
        "答案中のカード名を DB に照合し、(1) 《》の中身が DB に無い名前（自分で訳した名前・誤字・略記）を列挙し、"
        "近い正式名の候補を添える (2) 《英語名》・《日本語名》・裸の英語名・裸の日本語名を完成形《日本語名/英語名》に直した"
        "修正版（カード名は完成形《日本語名/英語名》）の全文を返す。未確認の名前が残っていれば、そのカードを search_mtg_cards で引いてから答えること。"
        "未確認ゼロなら修正版をそのまま答えに使う。Web 不要・DB 直結・1 秒未満。"))
def verify_answer(text: str) -> str:
    import re
    _log_tool("verify_answer", {"len": len(text)})
    N = _names()
    ja_full, en_ja, ja_en = N["ja"], N["en"], N["ja_en"]
    brackets = re.findall(r"《([^》]+)》", text)
    # 《X》（English）の English（初出添え）を控える＝X が DB に無いとき正式名を引く最強の手がかり
    en_after = {b.strip(): e.strip() for b, e in re.findall(r"《([^》]+)》\s*[（(]([A-Za-z][^）)]*)[）)]", text)}
    unknown, fixed, n_fix = [], text, 0
    def _disp(ja, en):
        return f"《{ja}/{en}》"
    def _pair(ja, en):
        """同じ粒度の対から完成形を作る。正式名「A // B」なら表面の対《表ja/表en》（8/22 の掟）・面の名前ならその面の対。"""
        if " // " in en and ja and " // " in ja:
            return _disp(ja.split(" // ")[0], en.split(" // ")[0])
        return _disp(ja, en.split(" // ")[0] if " // " in en else en)
    def _noja(en):
        return f"{en}（{'日本語名未収録' if en in N.get('digital', ()) else '日本語版なし'}）"
    # (0) 「Black Lotus（ブラック・ロータス）」型（英語主・日本語添え）→《ブラック・ロータス》（Black Lotus）
    for e, j in re.findall(r"(?<![A-Za-z《])([A-Z][A-Za-z'’,\- ]{3,}?)\s*[（(]([^（）()A-Za-z]{2,})[）)]", fixed):
        e2 = e.strip()
        if en_ja.get(e2) and en_ja[e2].replace(" ", "") == j.strip().replace(" ", ""):
            fixed = re.sub(re.escape(e) + r"\s*[（(]" + re.escape(j) + r"[）)]", _disp(en_ja[e2], e2), fixed)
            n_fix += 1
    # (1) 《》の中身
    for b in dict.fromkeys(x.strip() for x in brackets):
        key = b.replace(" ", "")
        if "/" in b and " // " not in b:                # 《日本語名/英語名》の完成形＝両半分が対で一致して初めて合格
            jpart, epart = b.split("/", 1)
            jkey, ekey = jpart.strip().replace(" ", ""), epart.strip()
            true_ja = en_ja.get(ekey)
            if true_ja is not None and jkey in (true_ja.replace(" ", ""), true_ja.split(" // ")[0].replace(" ", "")):
                continue
            if ekey in en_ja:                           # 英語半分は正しい・日本語半分が違う（記憶の訳）→ 正しい完成形を第一候補に
                cand = _pair(true_ja, ekey) if true_ja else _noja(ekey)
                unknown.append((b, [cand])); continue
            if jkey in ja_full:                         # 日本語半分は正しい・英語半分が違う
                unknown.append((b, [ja_full[jkey]])); continue
            unknown.append((b, [])); continue            # 両方 DB に無い（カード自体が未収録 or 創作）
        if key in ja_full:                             # 《日本語名》だけ → 完成形へ（同じ粒度の英語と組む）
            ja_name = ja_full[key]
            en_name = ja_en.get(ja_name)
            if en_name:
                fixed = fixed.replace(f"《{b}》", _pair(ja_name, en_name)); n_fix += 1
            continue
        if b in en_ja:
            ja = en_ja[b]
            if ja:                                     # 《英語名》→《日本語名/英語名》（面の名前ならその面の対）
                fixed = fixed.replace(f"《{b}》", _pair(ja, b)); n_fix += 1
            continue                                   # 日本語版なしの英語名はそのまま
        # DB に無い: 近い正式名を候補として添える（添えられた英語名があればそれが第一候補）
        cands = []
        e = en_after.get(b)
        if e and e in en_ja:
            cands.append(en_ja[e] or _noja(e))
        cands += [c[0] for c in _db(
            "SELECT japanese_name FROM mtg_cards_v2 WHERE japanese_name IS NOT NULL"
            " AND similarity(japanese_name, %s) > 0.25"
            " ORDER BY similarity(japanese_name, %s) DESC LIMIT 3", (b, b)) if c[0] not in cands]
        unknown.append((b, cands))
    # (2) 裸の英語名（日本語名あり）→《日本語名》（英語名）。《》の中・括弧の中は触らない
    # 日本語版なしの英語名（Volcanic Island 等）は正しい表記なので保護域に入れる（中の Island を拾わない）
    noja_in = [e for e, j in en_ja.items() if not j and (len(e) >= 5 or " " in e) and e in fixed]
    prot_src = r"《[^》]*》|[（(][^）)]*[）)]|\*[^*\n]+\*"
    if noja_in:
        prot_src += "|" + "|".join(re.escape(e) for e in sorted(noja_in, key=len, reverse=True))
    protected = re.compile(prot_src)
    def _outside(pattern, repl, s):
        out, pos = [], 0
        for m in protected.finditer(s):
            out.append(pattern.sub(repl, s[pos:m.start()])); out.append(m.group(0)); pos = m.end()
        out.append(pattern.sub(repl, s[pos:]))
        return "".join(out)
    for e in (x for x in N["en_bare"] if x in fixed):
        pat = re.compile(r"(?<![A-Za-z])" + re.escape(e) + r"(?![A-Za-z])")
        new = _outside(pat, _pair(en_ja[e], e), fixed)
        n_fix += (new != fixed); fixed = new
    # (3) 裸の日本語名→《日本語名》
    for j in (x for x in N["ja_bare"] if x in fixed):
        en_name = ja_en.get(j)
        new = _outside(re.compile(re.escape(j)), _pair(j, en_name) if en_name else f"《{j}》", fixed)
        n_fix += (new != fixed); fixed = new
    # 完成形《ja/en》の直後に重複の（en）が残っていれば削る
    fixed = re.sub(r"(《[^》/]+/([^》]+)》)\s*[（(]\2[）)]", r"\1", fixed)
    # 二重に囲んでしまった箇所（《《X》》）を戻す
    fixed = re.sub(r"《《([^》]+)》》", r"《\1》", fixed)
    lines = []
    if unknown:
        lines.append(f"未確認の名前 {len(unknown)} 件（DB のどのカード名にも一致しない＝自分で訳した/誤字/略記の疑い。"
                     "search_mtg_cards で引いて正式名に直してから答えること。略称・省略は冗長でも禁止）:")
        for b, c in unknown:
            lines.append(f"  - 《{b}》 → 候補: " + ("／".join(c) if c else "（近い名前なし・日本語版なしなら英語名のまま）"))
    else:
        lines.append("未確認の名前: なし（《》の中身はすべて DB の正式名）。")
    lines.append(f"機械修正 {n_fix} 箇所（裸の英語名・《英語名》・《日本語名》・裸の日本語名 → 完成形《日本語名/英語名》）。")
    lines.append("---- 修正版（未確認ゼロならこのまま使う） ----")
    lines.append(fixed)
    return "\n".join(lines)


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


@server.tool(
    name="describe_mtg_tables",
    description=(
        "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
        "データベースの実スキーマを見る。table_name 省略で全テーブルの一覧と行数概算、"
        "指定でそのテーブルの列名・型の一覧。query_mtg_database で SQL を書く前に"
        "列名をここで確認すること。public 以外のスキーマの表があれば「スキーマ名.表名」で返り、SQL でもその形で書く。"))
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
        sch, _, tbl = table_name.strip().rpartition(".")
        sch, tbl = _ident(sch), _ident(tbl)
        if not tbl:
            return f"テーブルなし: {table_name}"
        where = f"table_name = '{tbl}'" + (f" AND table_schema = '{sch}'" if sch else "")
        cols, rows = _db_readonly(
            "SELECT table_schema, column_name, data_type FROM information_schema.columns"
            f" WHERE {where} ORDER BY table_schema, ordinal_position", 160)
        if not rows:
            return f"テーブルなし: {table_name}"
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
    except Exception as e:
        return f"エラー: {str(e)[:300]}"


@server.tool(
    name="mtg_rag_health",
    description="データ層の健全性を確認する（DB 実疎通・主要テーブルの行数と鮮度）。")
def mtg_rag_health(deep: bool = False) -> str:
    """deep は旧 API 時代の名残で互換のため受けるが、常に DB 実疎通を見る。"""
    _log_tool("mtg_rag_health", {"deep": deep})
    import time
    t0 = time.time()
    try:
        rows = _db(
            "SELECT (SELECT COUNT(*) FROM mtg_cards_v2),"
            "       (SELECT COUNT(*) FROM mtg_rules),"
            "       (SELECT COUNT(*) FROM card_rulings),"
            "       (SELECT COUNT(*) FROM deck_list),"
            "       (SELECT MAX(tournament_date)::text FROM deck_list)", ())
        n_card, n_rule, n_rul, n_deck, latest = rows[0]
        # ドラフト統計（17Lands 集計・limited_card_stats）は表が無い環境もあるので別口で・失敗は 0
        try:
            n_l17 = _db("SELECT COUNT(DISTINCT expansion) FROM limited_card_stats", ())[0][0]
        except Exception:
            n_l17 = 0
        return json.dumps({
            "status": "ok",
            "db_latency_ms": int((time.time() - t0) * 1000),
            "cards": n_card, "rules": n_rule, "rulings": n_rul,
            "decks": n_deck, "latest_deck": latest,
            "draft_stat_sets": n_l17,
            "draft_stat_note": "17Lands 集計・表 limited_card_stats・セット一覧は describe_mtg_tables"},
            ensure_ascii=False)
    except Exception as e:
        return f"health 失敗: {e}"


# ─── レート制限（2026-09-02 本人 GO「レート制限から」＝配布（接続 URL を一般に開く）前の門）───
# 入口は Funnel → 127.0.0.1:8765 の uvicorn 直結で前段の代理は無い。client IP は uvicorn の proxy_headers
# （X-Forwarded-For・127.0.0.1 からだけ信用）が解決済み＝tailnet からは 100.x・claude.ai からは Anthropic の
# 出口 160.79.106.x（一人の会話でも 30 個ほどの IP を回る・7 日 3,525 POST の実測）。だから IP 別の枠は
# 「一人当たり」ではなく「直叩きの脚本 1 本を抑える」粗い門で、箱を守る本命は総量の枠。同時実行は
# _db_slot（席 5）が別に抑える。実測の最大は IP 別 8/分・総量 22/分（脳 1 会話）。既定は IP 別 60/分・
# 総量 300/分（環境変数 MCP_RATE_PER_IP_MIN／MCP_RATE_GLOBAL_MIN・0 で無効）。tailnet（100.64.0.0/10）と
# 127.0.0.0/8 は数えない（自分の healthwatch・pingwatch）。超過は HTTP 429＋Retry-After＋JSON-RPC の error
# （脳に日本語で理由が届く）。秘密のパス以外への探り（8/30 に GCP の IP から 404 を 12 連発）も同じ枠で数える。
# 窓は滑走（直近 60 秒の時刻を deque で持つ）・拒否した呼び出しは数えない（Retry-After を正直に保つ）。
import collections as _collections
import ipaddress as _ipaddress
import logging as _logging
import time as _time

_rate_log = _logging.getLogger("uvicorn.error")


class _RateLimiter:
    """IP 別＋総量の滑走窓。uvicorn は単一イベントループなのでロック無し。clock は試験で差し替える。"""

    def __init__(self, per_ip: int, global_: int, window: float = 60.0,
                 exempt: str = "127.0.0.0/8,100.64.0.0/10", clock=_time.monotonic, max_keys: int = 5000):
        self.per_ip, self.global_, self.window, self.clock, self.max_keys = per_ip, global_, window, clock, max_keys
        self.exempt = [_ipaddress.ip_network(c.strip()) for c in exempt.split(",") if c.strip()]
        self.buckets: "_collections.OrderedDict[str, _collections.deque]" = _collections.OrderedDict()
        self.total: _collections.deque = _collections.deque()
        self.denied = 0
        self._last_warn: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> "_RateLimiter":
        return cls(per_ip=int(os.environ.get("MCP_RATE_PER_IP_MIN", "60")),
                   global_=int(os.environ.get("MCP_RATE_GLOBAL_MIN", "300")),
                   exempt=os.environ.get("MCP_RATE_EXEMPT", "127.0.0.0/8,100.64.0.0/10"))

    def _is_exempt(self, ip: str) -> bool:
        try:
            a = _ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a in n for n in self.exempt)

    def _prune(self, dq: _collections.deque, now: float) -> None:
        cut = now - self.window
        while dq and dq[0] <= cut:
            dq.popleft()

    def check(self, ip: str) -> tuple[bool, int]:
        """(通すか, 待つ秒数)。通すときはその時刻を記録する。"""
        if self._is_exempt(ip):
            return True, 0
        now = self.clock()
        self._prune(self.total, now)
        dq = self.buckets.get(ip)
        if dq is None:
            if len(self.buckets) >= self.max_keys:  # 鍵が溢れたら空の鍵を捨て、足りなければ古い順に捨てる
                for k in list(self.buckets):
                    self._prune(self.buckets[k], now)
                    if not self.buckets[k]:
                        del self.buckets[k]
                while len(self.buckets) >= self.max_keys:
                    self.buckets.popitem(last=False)
            dq = self.buckets[ip] = _collections.deque()
        else:
            self.buckets.move_to_end(ip)
            self._prune(dq, now)
        if self.per_ip and len(dq) >= self.per_ip:
            retry = max(1, int(dq[0] + self.window - now) + 1)
            self._deny(ip, f"IP 別 {self.per_ip}/分", now)
            return False, retry
        if self.global_ and len(self.total) >= self.global_:
            retry = max(1, int(self.total[0] + self.window - now) + 1)
            self._deny(ip, f"総量 {self.global_}/分", now)
            return False, retry
        dq.append(now)
        self.total.append(now)
        return True, 0

    def _deny(self, ip: str, why: str, now: float) -> None:
        self.denied += 1
        if now - self._last_warn.get(ip, -1e9) >= self.window:  # IP ごと 1 分に 1 行（洪水でログを埋めない）
            self._last_warn[ip] = now
            _rate_log.warning("[rate] 429 ip=%s reason=%s denied_total=%d", ip, why, self.denied)


class _RateLimitASGI:
    """ASGI の外皮。HTTP だけ数え、超過は 429（JSON-RPC の error 本文つき）で返し、本体には渡さない。"""

    def __init__(self, app, limiter: _RateLimiter):
        self.app, self.limiter = app, limiter

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        client = scope.get("client")
        ip = client[0] if client else "?"
        ok, retry = self.limiter.check(ip)
        if ok:
            return await self.app(scope, receive, send)
        body = json.dumps({"jsonrpc": "2.0", "id": None, "error": {
            "code": -32000,
            "message": (f"混雑: 呼び出しが多すぎます（この接続元からの上限に達しました）。{retry} 秒待ってから"
                        "もう一度呼んでください。まとめて引ける問いは 1 回の SQL に寄せると回数が減ります。")}},
            ensure_ascii=False).encode("utf-8")
        headers = [(b"content-type", b"application/json; charset=utf-8"),
                   (b"retry-after", str(retry).encode()),
                   (b"content-length", str(len(body)).encode())]
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": body})


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "http":
        # リモート版（一時公開試験・2026-08-10）: 127.0.0.1 に束縛し、外への口は
        # Cloudflare 即席トンネルが持つ。DNS rebinding 防御はトンネルの Host 名
        # （毎回ランダム）が allowed_hosts に書けないため、この一時試験に限り無効化。
        # 恒久のリモート版（工程表 3 番）では allowed_hosts を固定ドメインで縫うこと。
        from mcp.server.transport_security import TransportSecuritySettings
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
        # 待ち受けパス（2026-08-22・本人裁定「2 で」）: Funnel のホスト名は CT ログで公開される
        # ので、秘密は URL のパスに持たせる。既定 /mcp・本番は unit の EnvironmentFile
        # （~/.config/mtg-rag/mcp.env・claude 専用ホーム）から MCP_HTTP_PATH を注入。
        http_path = os.environ.get("MCP_HTTP_PATH", "/mcp")
        # stateless（2026-08-29・売り場で採用）: claude.ai のコネクタは道具呼び出しをセッション ID
        # 無しで送ってくることがあり、既定（stateful）だと「Bad Request: Missing session ID」で
        # 全滅する（箱の実測・health 1 回成功→以降 400）。道具はすべて独立（サーバ発の通知なし）
        # なので、リクエストごとに独立処理しても失うものは無い。MCP_STATELESS=1 で有効。
        stateless = os.environ.get("MCP_STATELESS", "") in ("1", "true", "yes")
        import uvicorn
        app = server.streamable_http_app(
            streamable_http_path=http_path,
            stateless_http=stateless,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
            host="127.0.0.1")
        # 停止は 3 秒で切り上げる（2026-08-31 17:45 の実測: 箱の再起動で uvicorn が「接続が閉じるのを待つ」まま
        # systemd の TimeoutStopSec=15 に掛かり SIGKILL → 'timeout' 失敗 → OnFailure（Discord＋ビープ）が鳴った。
        # claude.ai のコネクタが SSE を掴んだままにするので、待っても閉じない。SDK の run() は uvicorn.Config に
        # graceful の上限を渡さないため、ここで uvicorn を直接組む。道具は 1 秒未満で返るので 3 秒あれば取りこぼさない）
        # レート制限の外皮（配布前の門・上の _RateLimiter を参照）。uvicorn の proxy_headers が
        # X-Forwarded-For を client に解決した後に見るので、Funnel 越しでも実 IP で数えられる。
        app = _RateLimitASGI(app, _RateLimiter.from_env())
        uvicorn.run(app, host="127.0.0.1", port=port,
                    log_level=server.settings.log_level.lower(), timeout_graceful_shutdown=3)
    else:
        server.run("stdio")
