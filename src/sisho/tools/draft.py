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
- DB の例外を不在に丸めない。統計取得が主目的なので、障害を「統計が無い」と誤報しない
  （sisho.limited の _resolve_draft_set は propagate_db_error=True で呼び、fetch_pack_stats は DB の例外を送出する）。
"""
import os

from sisho import errors
from sisho.db import LANE_LIGHT, _db
from sisho.limited import _archetype_lines, _resolve_draft_set, _sets_line, fetch_pack_stats
from sisho.names import _POOL_MIN_SCORE, _POOL_STRONG, _pool_score, name_rank, trgm_display_candidates
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
                f"手持ち（新しい順・記号（名前））: {_sets_line(_db=_db)}")
    code = _resolve_draft_set(set, allow_cube=True, propagate_db_error=True, _db=_db)
    if not code:
        return (f"set が不正: 「{set}」は手持ちに無いか、名前が複数のセットに当たって 1 つに決まらない。"
                f"手持ち（新しい順・記号（名前））: {_sets_line(_db=_db)}")

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
    exact: dict[str, list[dict]] = {}
    seen_en: dict[str, dict[str, bool]] = {}  # 注意: 引数名 set が組み込みの set() を隠す＝この関数の中で set() は呼べない

    def _take(row):
        ja, en, disp, rarity, mana, tline, enf, jaf, enb, jab = row
        card = {"card_name": en, "name_display": disp, "rarity": rarity,
                "mana_cost": mana, "type_line": tline}
        # 一致の種類を覚える。「そのカード自身の名前」で当たった候補を、「表面名」→「裏面名」の
        # 順で優先する（意図の推測ではなく、名前の種類の優先順位）。
        # 面名を対象にした副作用で、ありふれたカードが曖昧になるのを防ぐ。実例:
        # 「Lightning Bolt」は《稲妻/Lightning Bolt》と、裏面がその名前の
        # 《対立の名誉教授/Emeritus of Conflict》の 2 枚に当たる。
        # 順位の決まり（正式名 0 → 表面名 1 → 裏面名 2）は names.name_rank が持ち主（draft_pool と共有）
        for key in uniq:
            rank = name_rank(key, en, ja, enf, jaf, enb, jab)
            if rank is not None:
                if en in seen_en.setdefault(key, {}):
                    continue
                seen_en[key][en] = True
                exact.setdefault(key, []).append(dict(card, _own=rank == 0, _front=rank <= 1))

    for r in rows:
        _take(r)
    # name_ja_front だけ索引が無い＝正式名（_own）で確定していない非 ASCII 名が残っているときだけ引く（全件 Seq Scan を常用しない）
    # （第 1 段で別カードの裏面名 jab だけに当たった場合も name_ja_front 側を落とさない）
    rest = [
        k for k in uniq
        if not k.isascii() and not any(c.get("_own") for c in exact.get(k, []))
    ]
    if rest:
        for r in _db(f"SELECT {cols} FROM mtg_cards_v2 c WHERE c.name_ja_front = ANY(%s)",
                     (rest,)):
            _take(r)

    # 2) そのセットの 17Lands 統計（行の有無は「封入されたか」を証明しない＝断定しない）
    want = sorted({c["card_name"] for lst in exact.values() for c in lst})   # 内包表記なので set() を呼ばない
    stats = fetch_pack_stats(code, want, _db=_db)

    # 3) 完全一致しなかった名前だけ、プールに閉じた曖昧一致へ回す
    #    （実測: プールで絞ると候補がほぼ一意に落ちる。孤光→弧光のフェニックスは全体 30 候補→プール内 1）
    #    プールは一度だけ読んで Python で比べる（names._pool_score。名前のかけらも拾えるように）
    fuzzy: dict[str, list[str]] = {}
    elsewhere: dict[str, list[str]] = {}   # 指定セットの統計行は無いが DB には近い名前がある
    ties: dict[str, int] = {}              # 候補の上限で切った所に残った同点の数
    pool: list[tuple] | None = None
    for key in uniq:
        if key in exact:
            continue
        if pool is None:
            pool = _db(
                "SELECT DISTINCT c.name_display, c.card_name, c.japanese_name, c.name_en_front,"
                "       c.name_en_back, c.name_ja_front, c.name_ja_back"
                "  FROM mtg_cards_v2 c"
                "  JOIN limited_card_stats s"
                "    ON s.db_card_name = c.card_name AND s.expansion = %s AND s.event_type = 'PremierDraft'",
                (code,), lane=LANE_LIGHT)
        scored = sorted(((_pool_score(key, [x for x in r[1:] if x]), r[0]) for r in pool),
                        key=lambda t: (-t[0], t[1]))
        ok = [(sc, disp) for sc, disp in scored if sc >= _POOL_MIN_SCORE]
        cand = [disp for _, disp in ok[:_FUZZY_CANDIDATES]]
        if len(ok) > _FUZZY_CANDIDATES:
            # 上限で切った所に同点が残るなら、名前の順で機械的に選んだことを隠さない
            cut = ok[_FUZZY_CANDIDATES - 1][0]
            more = sum(1 for sc, _ in ok[_FUZZY_CANDIDATES:] if sc == cut)
            if more:
                ties[key] = more
        if cand and ok[0][0] >= _POOL_STRONG:
            fuzzy[key] = cand
            continue
        # セットの中に強い候補が無いときは DB 全体も見る（WOE で「Lightnig Bolt」が
        # セットの中の弱い《石断ちの稲妻》で止まり、DB 全体で一番近い《稲妻/Lightning Bolt》を伝えられなかった）。
        # 「このセットに居ない」と「DB のどこにも無い」は別の事実で、混ぜると実在するカードを「創作の疑い」と誤報する。
        wide = trgm_display_candidates(key, _FUZZY_CANDIDATES, _db=_db)
        in_pool = {r[0] for r in pool}
        fuzzy[key] = cand
        elsewhere[key] = [d for d in wide if d not in in_pool]

    # 4) 入力順のまま、1 入力につき 1 件の結果に振り分ける
    table, unverified, not_in_pool, ambiguous, small = [], [], [], [], []
    for key in raw:
        hits = exact.get(key)
        if not hits:
            cand = fuzzy.get(key) or []
            if cand:
                unverified.append((key, cand, elsewhere.get(key) or [], ties.get(key, 0)))
            elif elsewhere.get(key):
                not_in_pool.append((key, "・".join(elsewhere[key]), False))
            else:
                unverified.append((key, [], [], 0))
            continue
        own = [c for c in hits if c.get("_own")]
        if own:                         # 自分の名前で当たった候補があるならそちらだけを見る
            hits = own
        else:
            front = [c for c in hits if c.get("_front")]
            if front:                   # 表面名で当たった候補があるなら裏面一致より優先する
                hits = front
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
    out.append(_archetype_lines(code, _db=_db))
    return "\n".join(x for x in out if x is not None)


def _sections(unverified, not_in_pool, ambiguous, small, code) -> str:
    """表に載らなかった入力を、混ぜずに欄ごとに返す。黙って落とすと客が自分で埋める。"""
    out = []
    if unverified:
        out.append(f"完全一致しなかった名前 {len(unverified)} 件（そのまま答えに書かない）:")
        for key, cand, outside, more in unverified:
            if cand:
                line = f"  「{key}」 → 候補 {'・'.join(cand)}（曖昧一致・完全一致ではない）"
                if more:
                    line += f"。ほかに同じ近さが {more} 件＝この名前では絞り切れない"
                if outside:
                    line += f"。{code} の外の近い名前 {'・'.join(outside)}（{code} の 17Lands 統計行なし）"
                out.append(line)
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
