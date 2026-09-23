"""draft.py — ドラフトのパックにある複数のカードの 17Lands 統計をまとめて引く道具。

名前が分かっている 1〜20 枚を名指しで引く口。search_mtg_cards は名前や本文から探す道具で、
14 枚分の名前を並べると該当なしになり、そのエラー文が利用者を生 SQL へ送っていた。
そこで生まれる手探り（スキーマ調べ・列名と型の誤り）を構造で塞ぐ。

設計の要点:
- 対象は limited_card_stats.expansion で絞る。mtg_cards_v2 のセット列（単数 set_code も
  配列 set_codes も）は条件に使わない。ボーナス・シート（STX の Mystical Archive 等）と
  Arena 専用の形式（PIO・SIR・HBG）が落ちるため。
- ただし「統計行が無い＝ブースターに入らない」とは断定しない。統計は game_data と draft_data の
  外部結合で作られており、行の不在は元データに列が無かったことしか示さない。逆に行が在っても
  遊ばれた証拠ではない（FIN の Wastes は行が在って seen_packs も gp_games も 0）。
  返すのは「指定セットの 17Lands 統計行の有無」という実測できる事実だけにする。
- 統計は db_card_name で結ぶ。生の card_name で救済すると、名前の対応が未確認の行を
  検証済みの事実として返してしまう。
- DB の例外を不在に丸めない。統計取得が主目的なので、障害を「統計が無い」と誤報しない。
"""
import os

from sisho import errors
from sisho.db import LANE_LIGHT, _db
from sisho.toollog import _log_tool

MAX_CARDS = int(os.environ.get("MCP_DRAFT_PACK_MAX", "20"))
_FUZZY_CANDIDATES = 3          # 完全一致しなかった名前に添える候補の上限
_SMALL_SAMPLE = 500            # これ未満の母数は数字がぶれる（実測: 標準偏差が 4.5 倍）

_COLS = ("name_display", "rarity", "mana_cost", "type_line",
         "gih_wr", "gih_games", "alsa", "gp_wr", "oh_wr", "gd_wr", "ata")

DESCRIPTION = (
    "ドラフトのパックなど、名前が分かっている複数のカード（1〜20 件）の 17Lands 統計を"
    "1 回でまとめて引く。カードごとに search_mtg_cards を呼ばずに済む。"
    "cards に名前を並べ（日本語名・英語名どちらでも）、set にセット記号を渡す（必須）。"
    "返るのは表形式で、gih_wr（手札に来たゲームの勝率）・alsa（最後に見えたピック番号の平均・"
    "小さいほど早く消える）・ata（取られたピック番号の平均）・gih_games（母数）ほか。"
    "完全一致しなかった名前は別の欄に『未確認』として返る＝そのまま答えに書かない。"
    "カード名は返り値の name_display を一字も変えずに使う。"
    "セット横断の集計や他の列は query_mtg_database で。"
)


def draft_pack_stats(cards: list[str], set: str | None = None) -> str:
    """cards: カード名の一覧（1〜20）。set: セット記号（必須・例 LCI）。"""
    _log_tool("draft_pack_stats", {"cards": cards, "set": set})
    # 循環輸入を避けるため関数の中で読む（cards.py はこの道具を輸入しない）
    from sisho.tools.cards import _archetype_lines, _resolve_draft_set, _sets_line

    if isinstance(cards, str):          # 1 枚を素の文字列で渡されたときも受ける
        cards = [cards]
    if not isinstance(cards, list) or not cards:
        return ("入力が空: cards が空です。"
                "ドラフトのパックにあるカード名を 1 件以上並べてください（日本語名・英語名どちらでも）。")
    if len(cards) > MAX_CARDS:          # 受け取った要素数で数える（空白を除いた後ではない）
        return (f"件数が範囲外: cards は {MAX_CARDS} 件までです（受け取った件数 {len(cards)}）。"
                "1 パック分ずつに分けて呼んでください。")

    raw = [("" if c is None else str(c)).strip() for c in cards]
    if any(not c for c in raw):
        return ("入力が空: cards に空の要素があります。"
                "空文字や空白だけの要素を外してから呼び直してください。")

    if not set or not str(set).strip():
        return ("セットの指定が必要です: set にドラフトのセット記号を渡してください（例 set=\"LCI\"）。"
                f"手持ち（新しい順・記号（名前））: {_sets_line()}")
    code = _resolve_draft_set(set)
    if not code:
        # _limited_sets() は Cube を除くが、統計は実在する（Cube_-_Powered は 538 行）。
        # この道具の有効値は「統計表に在る expansion」＝そちらで引き直す。
        want_code = str(set).strip().lstrip("#").strip()
        hit = _db("SELECT expansion FROM limited_card_stats"
                  " WHERE lower(expansion) = lower(%s) LIMIT 1", (want_code,), lane=LANE_LIGHT)
        code = hit[0][0] if hit else None
    if not code:
        return (f"set が不正: 「{set}」は手持ちに無いセットです。"
                f"手持ち（新しい順・記号（名前））: {_sets_line()}")

    uniq = list(dict.fromkeys(raw))     # 問い合わせだけ重複を除く（返却は入力順を保つ）

    # 1) 完全一致（英語の正式名・日本語名の両方）。1 つの入力が複数のカードを指すことがある
    #    （実測: 日本語名「緊急脱出」が Bail Out と Eject の 2 枚に当たる・全 32,730 枚でこの 1 件）。
    #    書き方の急所（実測）: lower(card_name) = ANY(...) と OR を使うと索引が死んで
    #    Seq Scan 57.4ms・15,666 バッファになる。ILIKE ANY と = ANY なら両方の pg_trgm 索引に
    #    BitmapOr で乗り、0.65ms・30 バッファ（88 倍）。関数で包まない・OR でも索引に乗る形を選ぶ。
    #    両面カードの card_name は「表 // 裏」なので、客が読む表面名では当たらない（LCI だけで 36 行）。
    #    面の列も一致の対象にする。索引の型で書き方を変える: card_name と japanese_name は
    #    pg_trgm（ILIKE / = が乗る）・name_en_front/back と name_ja_back は btree（= のみ・
    #    ILIKE では乗らず Seq Scan 86ms になる）。name_ja_front は索引が無いので下の 2 本目で拾う。
    cols = ("c.japanese_name, c.card_name, c.name_display, c.rarity, c.mana_cost, c.type_line,"
            " c.name_en_front, c.name_ja_front, c.name_en_back, c.name_ja_back")
    rows = _db(
        f"SELECT {cols} FROM mtg_cards_v2 c"
        " WHERE c.card_name ILIKE ANY(%s) OR c.japanese_name = ANY(%s)"
        "    OR c.name_en_front = ANY(%s) OR c.name_en_back = ANY(%s)"
        "    OR c.name_ja_back = ANY(%s)",
        (uniq, uniq, uniq, uniq, uniq), lane=LANE_LIGHT)
    matched: dict[str, bool] = {}   # 注意: 引数名 set が組み込みの set() を隠す＝この関数の中で set() は呼べない
    exact: dict[str, list[dict]] = {}

    def _take(row):
        ja, en, disp, rarity, mana, tline, enf, jaf, enb, jab = row
        card = {"card_name": en, "name_display": disp, "rarity": rarity,
                "mana_cost": mana, "type_line": tline}
        # 一致の種類を覚える。「そのカード自身の名前」で当たった候補を、「面の名前」で
        # 当たった候補より優先する（意図の推測ではなく、名前の種類の優先順位）。
        # 面名を対象にした副作用で、ありふれたカードが曖昧になるのを防ぐ。実例:
        # 「Lightning Bolt」は《稲妻/Lightning Bolt》と、裏面がその名前の
        # 《対立の名誉教授/Emeritus of Conflict》の 2 枚に当たる。
        for key in uniq:
            own = (key.lower() == (en or "").lower()) or (ja is not None and key == ja)
            face = (any(key.lower() == x.lower() for x in (enf, enb) if x)
                    or any(key == x for x in (jaf, jab) if x))
            if own or face:
                exact.setdefault(key, []).append(dict(card, _own=own))
                matched[key] = True

    for r in rows:
        _take(r)
    # name_ja_front だけ索引が無い＝外れた名前が残っているときだけ引く（全件 Seq Scan を常用しない）
    rest = [k for k in uniq if k not in matched]
    if rest:
        for r in _db(f"SELECT {cols} FROM mtg_cards_v2 c WHERE c.name_ja_front = ANY(%s)",
                     (rest,)):
            _take(r)

    # 2) そのセットの 17Lands 統計（行の有無は「封入されたか」を証明しない＝断定しない）
    want = sorted({c["card_name"] for lst in exact.values() for c in lst})   # 内包表記なので set() を呼ばない
    stats: dict[str, tuple] = {}
    if want:
        for r in _db(
            "SELECT db_card_name, gih_wr, gih_games, alsa, gp_wr, oh_wr, gd_wr, ata"
            "  FROM limited_card_stats"
            " WHERE expansion = %s AND event_type = 'PremierDraft' AND db_card_name = ANY(%s)",
                (code, want), lane=LANE_LIGHT):
            stats[r[0]] = r[1:]

    # 3) 完全一致しなかった名前だけ、プールに閉じた曖昧一致へ回す
    #    （実測: プールで絞ると候補がほぼ一意に落ちる。孤光→弧光のフェニックスは全体 30 候補→プール内 1）
    fuzzy: dict[str, list[str]] = {}
    elsewhere: dict[str, list[str]] = {}   # 指定セットの統計行は無いが DB には近い名前がある
    for key in uniq:
        if key in exact:
            continue
        cand = _db(
            "SELECT c.name_display"
            "  FROM mtg_cards_v2 c"
            "  JOIN limited_card_stats s"
            "    ON s.db_card_name = c.card_name AND s.expansion = %s AND s.event_type = 'PremierDraft'"
            " WHERE (c.card_name %% %s OR c.japanese_name %% %s)"
            "   AND greatest(similarity(c.card_name, %s),"
            "                similarity(coalesce(c.japanese_name, ''), %s)) > 0.3"
            " ORDER BY greatest(similarity(c.card_name, %s),"
            "                   similarity(coalesce(c.japanese_name, ''), %s)) DESC"
            " LIMIT %s",
            # similarity( は関数比較で pg_trgm 索引に乗らない＝重い線で走らせる（既定）
            (code, key, key, key, key, key, key, _FUZZY_CANDIDATES))
        if cand:
            fuzzy[key] = [r[0] for r in cand]
            continue
        # プールで当たらなかったときだけ DB 全体を見る。「このセットに居ない」と
        # 「DB のどこにも無い」は別の事実で、混ぜると実在するカードを「創作の疑い」と誤報する。
        wide = _db(
            "SELECT c.name_display FROM mtg_cards_v2 c"
            " WHERE (c.card_name %% %s OR c.japanese_name %% %s)"
            "   AND greatest(similarity(c.card_name, %s),"
            "                similarity(coalesce(c.japanese_name, ''), %s)) > 0.3"
            " ORDER BY greatest(similarity(c.card_name, %s),"
            "                   similarity(coalesce(c.japanese_name, ''), %s)) DESC"
            " LIMIT %s",
            (key, key, key, key, key, key, _FUZZY_CANDIDATES))
        fuzzy[key] = []
        elsewhere[key] = [r[0] for r in wide]

    # 4) 入力順のまま、1 入力につき 1 件の結果に振り分ける
    table, unverified, not_in_pool, ambiguous, small = [], [], [], [], []
    for key in raw:
        hits = exact.get(key)
        if not hits:
            cand = fuzzy.get(key) or []
            if cand:
                unverified.append((key, cand))
            elif elsewhere.get(key):
                not_in_pool.append((key, "・".join(elsewhere[key]), False))
            else:
                unverified.append((key, []))
            continue
        own = [c for c in hits if c.get("_own")]
        if own:                         # 自分の名前で当たった候補があるならそちらだけを見る
            hits = own
        if len(hits) > 1:               # それでも複数なら自動で選ばない（客にしか分からない）
            ambiguous.append((key, [(c["name_display"], c["card_name"] in stats) for c in hits]))
            continue
        c = hits[0]
        st = stats.get(c["card_name"])
        if st is None:
            not_in_pool.append((key, c["name_display"], True))
            continue
        gih_wr, gih_games, alsa, gp_wr, oh_wr, gd_wr, ata = st
        if gih_games is not None and gih_games < _SMALL_SAMPLE:
            small.append((c["name_display"], gih_games))
        table.append([c["name_display"], c["rarity"], c["mana_cost"], c["type_line"],
                      gih_wr, gih_games, alsa, gp_wr, oh_wr, gd_wr, ata])

    if not table and not ambiguous:
        return ("該当なし: 渡された名前のどれも "
                f"{code} の 17Lands 統計行に辿り着けませんでした。\n"
                + _sections(unverified, not_in_pool, ambiguous, small, code))

    def cell(v):
        # 不在は NULL と書く（空欄にすると「値が無い」と「空の値がある」を区別できない）
        return "NULL" if v is None else str(v)

    out = [f"セット: {code}／出典: 17Lands PremierDraft", ""]
    if table:                      # 行が無いのに見出しだけ出すと「空の表」に見える
        out.append(" | ".join(_COLS))
        out += [" | ".join(cell(v) for v in r) for r in table]
    else:
        out.append(f"統計を引けた行はありません（{code}）。")
    tail = _sections(unverified, not_in_pool, ambiguous, small, code)
    if tail:
        out.append("")
        out.append(tail)
    out.append("")
    out.append(_LEGEND)
    out.append(_archetype_lines(code))
    return "\n".join(x for x in out if x is not None)


def _sections(unverified, not_in_pool, ambiguous, small, code) -> str:
    """表に載らなかった入力を、混ぜずに欄ごとに返す。黙って落とすと客が自分で埋める。"""
    out = []
    if unverified:
        out.append(f"完全一致しなかった名前 {len(unverified)} 件（そのまま答えに書かない）:")
        for key, cand in unverified:
            if cand:
                out.append(f"  「{key}」 → 候補 {'・'.join(cand)}（曖昧一致・完全一致ではない）")
            else:
                out.append(f"  「{key}」 → DB のどのカード名にも一致しない"
                           "（未収録 または 創作の疑い）")
    if ambiguous:
        out.append(f"名前が複数のカードを指す {len(ambiguous)} 件（どれを指すか指定して呼び直す）:")
        for key, cands in ambiguous:
            s = "・".join(f"{d}（{code} の統計行{'在り' if inp else '無し'}）" for d, inp in cands)
            out.append(f"  「{key}」 → {s}")
    if not_in_pool:
        out.append(f"{code} の 17Lands 統計行が無い {len(not_in_pool)} 件"
                   "（カードは DB に在るが、指定セットの 17Lands 統計行が無い）:")
        for key, disp, exact_hit in not_in_pool:
            if exact_hit:
                out.append(f"  「{key}」 → {disp}（このセットの 17Lands 統計行なし）")
            else:
                out.append(f"  「{key}」 → 完全一致せず。DB の近い名前 {disp} は"
                           f"どれも {code} の 17Lands 統計行を持たない")
    if small:
        out.append(f"母数注意（gih_games < {_SMALL_SAMPLE}・数字がぶれる）: "
                   + "、".join(f"{d}={g}" for d, g in small))
    return "\n".join(out)


_LEGEND = (
    "列の意味: gih_wr=そのカードが手札にあったゲームの勝率／oh_wr=開幕手札にあった場合／"
    "gd_wr=ゲーム中に引いた場合／gp_wr=デッキに入れて遊ばれた場合。勝率は 0〜1 の値。"
    "alsa=そのカードが最後に見えたピック番号の平均・ata=実際に取られたピック番号の平均"
    "（どちらも小さいほど早く消える）。gih_games=母数。空欄は NULL（値が無い）。"
    "カード名は name_display を一字も変えずに使う。"
    "集計元は Premier Draft（人間対面・Bo1）だが Bo1 ドラフト一般の物差しとして使ってよい"
    "（alsa・ata の流れ方だけはボット相手の Quick Draft とずれる）。答えに出典「17Lands」を添える。"
)
