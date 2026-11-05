"""cards.py — カード検索の道具 search_mtg_cards。

登録（server.tool）は mcp_server.py 側。ここは DESCRIPTION と素の関数だけを持つ。
同伴（17Lands 統計・収録セットの解決・in_draft_set・色の組み合わせ表）の持ち主は sisho/limited.py
（sql.py と draft.py も同じ所から借りる）。
"""
import json
import os

from sisho import errors
from sisho import limited as _limited
from sisho.db import LANE_LIGHT, _db
from sisho.names import face_display
from sisho.preview import apply_preview_to_cards, preview_notes
from sisho.sets_blurb import _SETS_BLURB, _SETS_HEAD
from sisho.toollog import _log_tool


# Arena の形式。これを名指しされたときだけ digital（Arena 専用カード）を検索に含める。
# 既定の検索は紙＝WHERE NOT digital（列 → 並べ方の掟: 紙と Arena 専用を混ぜて並べない）。
ARENA_FORMATS = {"historic", "alchemy", "timeless", "brawl", "standardbrawl", "gladiator"}

_CARD_COLS: tuple[str, ...] = (
    "card_name", "japanese_name", "type_line", "mana_cost", "power", "toughness",
    "rarity", "oracle_text", "japanese_oracle_text", "edhrec_rank", "name_display", "digital",
    # 収録セットの一覧（返り値の set_codes＝どのセットに入っているかを客が見る用）。
    # in_draft_set はこれで判定しない（ドラフトのプールは limited_card_stats だけが正本）。
    # 面の列 4 本は末尾に置いたまま（_row がそこを前提に扱う・test_card_cols が縫っている）。
    "set_codes",
    "name_en_front", "name_en_back", "name_ja_front", "name_ja_back",
)

# 不明な format の扱い（取り決め: 一意で近いなら直して再検索し返り値に必ず書く／
# 複数候補なら直さず選ばせる／遠ければ一覧）。legalities->>'modrn' を引くだけだと、
# 「そんな鍵は無い」と「その鍵で合法なカードが無い」が同じ「該当なし」に潰れるため。
_FORMATS_CACHE: dict = {"keys": []}
# 一意判定のしきい値。difflib.SequenceMatcher の比（0〜1）で 0.8。実物（DB の鍵 23 個）で決めた:
#   打ち間違い（modrn 0.909・standrad 0.875・brwl 0.889・comander 0.941・bralw 0.800・modren 0.833）は
#   すべて 0.800 以上で、次点は 0.750 以下（mordern→premodern 0.750・standar→standardbrawl 0.700）。
#   一方「別のフォーマットの名前」は 0.783 以下に落ちる（duel commander→commander 0.783・edh→predh 0.750・
#   canlander→commander 0.667・explorer→premodern 0.471・limited 0.429）＝ここが誤発動の境目。
#   有効な鍵どうしの最大の近さも modern/premodern 0.800 なので、1 語が二つの鍵に 0.8 以上で並ぶのは稀。
#   並んだとき（standardbrwl → standardbrawl 0.960・standard 0.800）は自動解釈せず選ばせる側に倒す。
_FORMAT_CUTOFF = 0.8


def _valid_formats() -> list[str]:
    """legalities の鍵の一覧（プロセスに 1 回だけ引く）。引けなければ空。

    全 32,730 行の jsonb を展開すると VM で 380ms かかり、公開サーバーでは軽いレーンの 1 秒も
    重いレーンの 10 秒も超えて落ちる（落ちると下の _check_format は検査できない）。鍵はカードごとに
    変わらないので**先頭 100 行で足りる**
    （実測: 100 行でも 23 鍵すべて揃う・1.3ms。欠けるのは competitivebrawl を持たない 1 枚だけ
    なので、1 行では不足しうるが 100 行なら他が埋める）。
    線は重いまま（既定）にしてある——プロセスに 1 回しか引かないので、速くなったからといって
    予約スロットを使う理由がない。
    """
    if _FORMATS_CACHE["keys"]:
        return _FORMATS_CACHE["keys"]
    try:
        rows = _db("SELECT DISTINCT k FROM (SELECT legalities FROM mtg_cards_v2 LIMIT 100) t,"
                   " jsonb_object_keys(t.legalities) k ORDER BY 1", ())
    except Exception:
        return []          # 呼び出し側（_check_format）が「検査できなかった」と言う
    _FORMATS_CACHE["keys"] = [r[0] for r in rows]
    return _FORMATS_CACHE["keys"]


def _check_format(fmt: str) -> tuple[str, str, str | None]:
    """format の鍵を検査する。返り値は (使う鍵, 返り値に載せる注記, 断りの JSON か None)。

    - 一覧にある → そのまま（注記なし）
    - 一覧に無く、十分近い候補が **1 つだけ** → その鍵で検索し、何をしたかを注記で必ず言う
    - 候補が複数 → 検索せず選ばせる（誤って別のフォーマットで引くのが最悪＝誤発動は有害）
    - 何にも似ていない → 検索せず一覧を返す
    """
    import difflib
    valid = _valid_formats()
    if not fmt or fmt in valid:
        return fmt, "", None
    if not valid:
        # 鍵の一覧を引けなかった＝検査できない。通すが黙っては通さない。
        # ここで素通しすると、綴りが違う format が legalities->>'…' に渡り、
        # 全部 NULL ≠ 'legal' で「該当なし」に見える（クライアントには区別がつかない）。
        return fmt, (f"format「{fmt}」は検査できなかった（legalities の鍵の一覧を DB から引けなかった）。"
                     "0 件なら鍵の綴りを疑う"), None
    listing = "・".join(valid)
    cands = difflib.get_close_matches(fmt, valid, n=5, cutoff=_FORMAT_CUTOFF)
    if len(cands) == 1:
        return cands[0], (f"format「{fmt}」は legalities の鍵に無いので「{cands[0]}」と解釈して検索した"
                          f"（有効な鍵: {listing}）。違うなら正しい鍵で呼び直す"), None
    if cands:
        kind = errors.AMBIGUOUS_FORMAT
        err = (f"format が不明: 「{fmt}」。近い鍵が複数あるので推測しない（候補: {'・'.join(cands)}）。"
               "どれかを指定して呼び直す")
    else:
        kind = errors.UNKNOWN_FORMAT
        err = (f"format が不明: 「{fmt}」（legalities にその鍵は無い＝『合法なカードが無い』のではなく『鍵が無い』）。"
               "有効な鍵から選んで呼び直すか、format を外して検索する")
    return fmt, "", errors.err_json(kind, err, valid_formats=valid)


DESCRIPTION = (
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
    "draft_set は**検索を絞る指定ではない**（その会話のセットを伝えるもの）＝別のセットのカードも候補に並ぶので、"
    "各カードの in_draft_set（true ならそのセットに収録）と set_codes（収録セット一覧）で必ず確かめてから選ぶこと。"
    "画像やうろ覚えから引いて候補が複数出たときは、名前の近さや並び順で決めず、in_draft_set と"
    "色・マナ・タイプを突き合わせて確定する（並びはリミテッドの強さ順ではない）。"
    "入れると 17Lands の統計と色の組み合わせ表がそのセットの分だけ同伴する（他セットは付かない）。"
    "構築（スタンダード・モダン等）の問いでは入れない。セットが分からないリミテの問いは空のまま＝最新セットの分が付く。"
    + _SETS_BLURB)
_mark_draft_set = _limited._mark_draft_set


def search_mtg_cards(query: str, format: str | None = None, top_k: int = 10, draft_set: str | None = None) -> str:
    """query: 検索語（空白区切りは AND）。format: legalities の鍵名。top_k: 1〜20。

    裁定「ルーター・絞り込みゲート・スコア補正部品の全撤廃」後の姿。旧実装は API(:8000) の
    ハイブリッド検索を叩いていたが、実運用でこの道具に来るのはほぼ名前引き
    （実運用ログの 7 件中 6 件）で、ルーター経由 6〜86 秒の待ちだけが残っていた。
    素の一致検索＝決定的・LLM ゼロ・1 秒未満に置き換える。"""
    _log_tool("search_mtg_cards", {"query": query, "format": format, "top_k": top_k, "draft_set": draft_set})
    q = query.strip()
    if not q:
        # この道具の答えは JSON なので error も JSON に揃える
        # （素の文字列と JSON が混ざるとクライアントが読み方を切り替えられない）。文言には
        # 「何が分からなかったか」と「次に何を試すか」の両方を入れる。
        return errors.err_json(
            errors.EMPTY_QUERY,
            "検索語が空です。query にカード名（日本語名・英語名どちらでも・部分一致可）か"
            "機能語（例:「飛行 吸血鬼」「draw two cards」）を入れて呼び直す")
    top_k = max(1, min(int(top_k), 20))
    fmt = (format or "").strip().lower()
    fmt, format_note, fmt_err = _check_format(fmt)
    if fmt_err:
        return fmt_err
    fmt_sql = " AND legalities->>%s = 'legal'" if fmt else ""
    if fmt not in ARENA_FORMATS:
        fmt_sql += " AND NOT digital"          # 既定は紙。Arena の形式を名指しされたときだけ Arena 専用カードも
    cols = ", ".join(_CARD_COLS)

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
    # 名前ヒットが top_k 件あれば本文ヒットは
    # 下の詰め合わせで 1 件も使われない（名前を先に詰めて top_k で止まる）＝引かない。出力は変わらない。
    # 公開サーバーの実引数 187 通りを VM で再生: 本文検索は 1 検索 339ms のうち平均 260ms（77%）で、実運用の
    # 大半は名前引き（top_k 1〜3）。p1 と p2 を 1 文に束ねても仕事量は減らないので、そちらは採らない。
    if len(name_rows) >= top_k:
        text_rows: list = []
    else:
        text_rows = _db(
            f"SELECT {cols} FROM mtg_cards_v2 WHERE " +
            " AND ".join([cond] * len(terms)) + fmt_sql +
            " ORDER BY edhrec_rank ASC NULLS LAST, card_name LIMIT %s", tuple(p2))

    keep = _CARD_COLS
    def _row(r: tuple) -> dict:
        """japanese_name だけは None でも落とさず明示する（クライアントは Helm of Obedience のような日本語版の無いカードに勝手な訳名を作ることがある。
        「無い」を返り値で言わないと、クライアントは無言を「自分で訳してよい」と読む）。"""
        d = {k: v for k, v in zip(keep, r) if v is not None}
        if d.get("japanese_name") is None:
            d["japanese_name"] = None
            d["name_note"] = ("日本語名未収録（Arena には日本語版あり）＝name_display をそのまま使う（訳名を作らない）" if d.get("digital")
                              else "日本語版なし＝name_display をそのまま使う（訳名を作らない）")
        # 面: 多面カードは faces を同伴し、裏面で当たったときはその面の完成形も返す（name_display は表面固定）
        enb = d.pop("name_en_back", None); jab = d.pop("name_ja_back", None)
        enf = d.pop("name_en_front", None); jaf = d.pop("name_ja_front", None)
        def _face_disp(en, ja):
            return face_display(en, ja, bool(d.get("digital")))
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
            d["digital_note"] = "Arena 専用のカード（アルケミー・A- リバランス等）＝紙には存在しない。紙の話では挙げない"
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
    #    実例: 「孤光のフェニックス」（正しくは弓へんの「弧光」・類似度 0.54）。打ち間違い耐性を SQL 一本で持たせる。
    route = "simple_match"
    if not cards:
        route = "fuzzy_name"
        # 索引で候補を絞ってから同じ条件で再チェックする形に。
        # similarity(a, b) > 0.3 の関数比較だけでは pg_trgm の GIN 索引に乗らず Seq Scan
        # （実測 Buffers 15,582）。演算子 % は索引に乗る（同 93＝167 分の 1）。
        # % の閾値は pg_trgm.similarity_threshold（既定 0.3）なので、後段の
        # greatest(similarity(...)) > 0.3 を残して集合を保証する（閾値が変えられても同じ答え）。
        # japanese_name は coalesce を外す＝式にすると索引が使えない。NULL は % が偽になるだけ。
        p3 = [q, q, q, q, 0.3] + ([fmt] if fmt else []) + [q, q, top_k]
        fuzzy_rows = _db(
            f"SELECT {cols} FROM mtg_cards_v2"
            " WHERE (card_name %% %s OR japanese_name %% %s)"
            "   AND greatest(similarity(card_name, %s),"
            "                similarity(coalesce(japanese_name,''), %s)) > %s"
            + fmt_sql +
            " ORDER BY greatest(similarity(card_name, %s),"
            "                   similarity(coalesce(japanese_name,''), %s)) DESC"
            " LIMIT %s", tuple(p3))
        cards = [_row(r) for r in fuzzy_rows]
    # 発売前のカード（先行収録）: 発売後に使えるフォーマットを候補ごとに書く
    # （統率者セットの新カードがスタンダードで使えるように読まれないため）。日本語名の無い面は表示も直す。
    apply_preview_to_cards(cards)
    if not cards:
        # 「該当なし」は入力の誤りでなく引き当てゼロ＝error_kind を分ける（no_match）。
        # 解釈し直した format は 0 件でも必ず言う（成功時と同じ format_note の鍵で）。
        return errors.err_json(
            errors.NO_MATCH,
            f"該当なし: {q}"
            "（語を減らす・言い換える・または query_mtg_database で SQL を書く。"
            "日本語名が分からないカードは英語名で引き直すこと＝訳名を推測しない）",
            **({"format_note": format_note} if format_note else {}))
    # 17Lands の同伴（速さの道具＝高確率で当たる分だけ付ける）:
    #   draft_set あり → そのセットだけ／不明な記号 → 数字は付けず一覧を返す／なし → 最新 N セット（既定 2）だけ。
    #   それ以外のセットにしか無いカードは、一行の道しるべ（limited_stats_elsewhere）に留める。
    # セット一覧の照会が落ちたときは「手持ちに無い」と言わない（照会できなかったと言う）
    sets_unavailable = False
    try:
        requested = _resolve_draft_set(draft_set, propagate_db_error=True) if draft_set else None
    except Exception:
        requested, sets_unavailable = None, True
    constructed = bool(fmt) and not requested and fmt.lower() not in ("limited", "draft", "sealed")
    if constructed:
        allowed: list[str] = []      # 構築の format 指定（modern 等）はドラフトの問いでない＝同伴なし（道しるべだけ）
    elif draft_set and not requested:
        allowed = []
    elif requested:
        allowed = [requested]
    else:
        allowed = [x["code"] for x in _limited_sets()[:_DRAFT_RECENT_N]]
    elsewhere, _stats_ok = _limited.attach_and_mark_limited(
        cards, allowed, requested_set=requested, _db=_db
    )
    archetypes = _limited_archetypes(sorted({s["set"] for c in cards for s in c.get("limited_stats", [])})) if allowed else {}
    out = {"route": route,
           "naming_rule": "name_display（完成形《日本語名/英語名》）を一字も変えずに使う。略称・通称・省略は冗長でも禁止（2 回目以降も）。「英語名（日本語版なし）」「英語名（日本語名未収録）」もそのまま（翻訳禁止・組み立て禁止）",
           "cards": cards}
    if format_note:
        out["format_note"] = format_note
    if sets_unavailable:
        out["draft_set_note"] = (f"セットの一覧の照会に失敗したため draft_set「{draft_set}」を確かめられず、"
                                 "in_draft_set と統計は付けていない（手持ちに無いと判定したわけではない）。少し待って呼び直す")
    elif draft_set and not requested:
        out["draft_set_note"] = (f"draft_set「{draft_set}」は手持ちに無いか、名前が複数のセットに当たって 1 つに決まらない"
                                 f"＝数字は付けていない。手持ち（新しい順・記号（名前））: {_sets_line()}。正しい記号で呼び直す")
    elif requested and not _stats_ok:
        out["draft_set_note"] = (f"draft_set「{requested}」の 17Lands 統計の照会に失敗したため、"
                                 "in_draft_set と統計は付けていない（ドラフト外と判定したわけではない）")
    if elsewhere:
        out["limited_stats_elsewhere"] = elsewhere
        out["limited_stats_elsewhere_note"] = ("これらのカードには別のセットの 17Lands 統計がある（記号のみ列挙）。"
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


# 以下は sisho.limited への薄い包み。mcp_server.py が m._limited_sets 等として再輸出し（test_draft_set が使う）、
# cards._db を差し替えた試験がそのまま効くよう _db を渡す＝消さない
_DRAFT_RECENT_N = _limited._DRAFT_RECENT_N
_sets_cache = _limited._sets_cache


def _attach_limited_stats(cards: list[dict], sets: list[str] | None = None) -> dict[str, list[str]]:
    return _limited._attach_limited_stats(cards, sets, _db=_db)


def _limited_sets() -> list[dict]:
    return _limited._limited_sets(_db=_db)


def _resolve_draft_set(s: str | None, *, propagate_db_error: bool = False) -> str | None:
    return _limited._resolve_draft_set(s, propagate_db_error=propagate_db_error, _db=_db)


def _sets_line() -> str:
    return _limited._sets_line(_db=_db)


def _archetype_lines(code: str) -> str:
    return _limited._archetype_lines(code, _db=_db)


def _limited_archetypes(sets: list[str]) -> dict:
    return _limited._limited_archetypes(sets, _db=_db)
