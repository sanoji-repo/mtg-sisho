#!/usr/bin/env python3
"""mtg_probability の castable・hand と SQL 関数 mtg_castable・mtg_hand_prob の試験。

黄金値は Python の独立実装: 引いた土地を 1 枚ずつ並べ、色の記号への割り当てを総当たりで探す（Hall の定理を使わない）。
SQL 側は Hall の条件と集合演算の数え上げ＝別の道筋で同じ値になること。DB が要る（無い環境では skip）。
走らせ方: pytest tests/test_manabase.py -v
"""
import hashlib
import itertools
import json
import os
import sys
from fractions import Fraction
from math import comb

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m
from conftest import requires_db
from sisho import manabase

f = m.mtg_probability.fn if hasattr(m.mtg_probability, "fn") else m.mtg_probability
W, U, B, R, G = 1, 2, 4, 8, 16


def call(**kw):
    return json.loads(f(**kw))


def brute(deck, cats, pips, generic, turn, on_play, mull=0):
    """cats=[(枚数, 色のマスク)]・pips=[色の記号のマスク]。割り当てを総当たりで探す黄金値。"""
    d = min(7 - mull + max(turn - (1 if on_play else 0), 0), deck)
    other = deck - sum(c for c, _ in cats)
    tot = Fraction(0)
    for xs in itertools.product(*[range(0, min(c, d) + 1) for c, _ in cats]):
        L = sum(xs)
        if d - L < 0 or d - L > other:
            continue
        drawn = [mk for (c, mk), x in zip(cats, xs) for _ in range(x)]
        ok = min(L, turn) >= generic + len(pips) and (
            not pips or any(all(drawn[p[j]] & pips[j] for j in range(len(pips)))
                            for p in itertools.permutations(range(len(drawn)), len(pips))))
        if ok:
            w = comb(other, d - L)
            for (c, _), x in zip(cats, xs):
                w *= comb(c, x)
            tot += w
    return float(tot / comb(deck, d))


def test_parse_cost():
    assert manabase.parse_cost("{2}{G}{G}")[:3] == ([G, G], 2, [])
    assert manabase.parse_cost("{G/W}{X}")[:2] == ([G | W], 0)
    assert manabase.parse_cost("{2/R}{C}")[:3] == ([32], 0, [R])
    assert manabase.parse_cost("{G/P}{1}")[:2] == ([], 1)
    with pytest.raises(ValueError):
        manabase.parse_cost("{S}{G}")


@requires_db
@pytest.mark.parametrize("case", [
    (40, [(9, G), (8, R)], [G], 1, 2, True),
    (40, [(9, G), (8, R)], [G, R], 0, 2, True),
    (40, [(6, G), (4, R), (1, U), (1, W), (1, B), (4, G | R)], [G, G, U], 1, 4, False),
    (40, [(7, G), (7, U), (3, G | U)], [G, G, U, U], 0, 4, True),
    (40, [(8, R), (8, G), (1, R | G)], [R | W, G], 1, 3, True),          # 混成
    (60, [(10, W), (8, U), (4, W | U)], [W, W, U], 1, 4, False),
])
def test_castable_sql_matches_brute(case):
    deck, cats, pips, generic, turn, on_play = case
    types = sorted(set(pips))
    need = [[pips.count(t) for t in types]]
    canpay = [sum(1 << i for i, t in enumerate(types) if mk & t) for _, mk in cats]
    got = m._db("SELECT mtg_castable(%s, %s, %s, %s, %s, %s, %s)",
                (deck, [c for c, _ in cats], canpay, need, [generic + len(pips)], turn, on_play))[0][0]
    assert abs(float(got) - brute(*case)) < 1e-6


@requires_db
def test_castable_single_color_equals_by_turn():
    """単色・記号 1 つ・土地が全部その色なら既存の by_turn と一致する。"""
    r = call(kind="castable", deck_size=40, lands=[{"name": "Forest", "count": 17}], mana_cost="{G}", turn=2)
    b = call(kind="by_turn", deck_size=40, copies=17, turn=2)
    assert abs(r["probability"] - b["probability"]) < 1e-4


@requires_db
def test_castable_by_name_and_resolution():
    r = call(kind="castable", deck_size=40, turn=3, on_play=False,
             lands=[{"name": "森", "count": 9}, {"name": "Mountain", "count": 8}], mana_cost="{1}{R}{G}")
    assert abs(r["probability"] - round(brute(40, [(9, G), (8, R)], [R, G], 1, 3, False), 4)) < 1e-4
    assert [x["produces"] for x in r["lands_resolved"]] == [["G"], ["R"]]
    assert set(r["by_turn"]) == {"1", "2", "3"} and r["by_turn"]["1"] == 0.0
    assert "アンタップ" in r["premise"]


@requires_db
def test_castable_dual_land_assignment():
    """2 色土地 1 枚では {G}{R} の両方は払えない（1 回に 1 色）。"""
    r = call(kind="castable", deck_size=40, turn=2, lands=[{"name": "Stomping Ground", "count": 17}], mana_cost="{R}{G}")
    exp = brute(40, [(17, G | R)], [R, G], 0, 2, True)
    assert abs(r["probability"] - round(exp, 4)) < 1e-4


@requires_db
def test_castable_fetch_needs_produces():
    r = call(kind="castable", lands=[{"name": "Evolving Wilds", "count": 4}, {"name": "Forest", "count": 13}],
             mana_cost="{G}", deck_size=40)
    assert r["error_kind"] == "out_of_range" and "produces" in r["error"]
    ok = call(kind="castable", lands=[{"name": "Evolving Wilds", "count": 4, "produces": ["G"]}, {"name": "Forest", "count": 13}],
              mana_cost="{G}", deck_size=40)
    assert ok["probability"] > 0


@requires_db
def test_castable_errors():
    assert call(kind="castable", lands=[{"name": "Lightning Bolt", "count": 4}], mana_cost="{R}")["error_kind"] == "out_of_range"
    assert call(kind="castable", lands=[{"name": "Forestt", "count": 4}], mana_cost="{G}")["error_kind"] == "no_match"
    assert call(kind="castable", lands=[{"name": "Forest", "count": 50}], mana_cost="{G}", deck_size=40)["error_kind"] == "out_of_range"
    assert call(kind="castable", lands=[{"name": "Forest", "count": 17}], deck_size=40)["error_kind"] == "empty_query"
    assert call(kind="castable", lands=[{"name": "Forest", "count": 17}], mana_cost="{S}{G}", deck_size=40)["error_kind"] == "out_of_range"


@requires_db
def test_castable_spell_name_uses_db_cost():
    r = call(kind="castable", deck_size=60, lands=[{"name": "Mountain", "count": 20}], spell="Lightning Bolt", turn=1)
    assert r["mana_cost"] == "{R}" and "Lightning Bolt" in r["spell"]


@requires_db
def test_hand_matches_hypergeom():
    r = call(kind="hand", deck_size=40, turn=0, groups=[{"label": "土地", "count": 17, "min": 2, "max": 4},
                                                         {"label": "2 マナ以下", "count": 7, "min": 1, "max": 7}])
    exp = sum(comb(17, a) * comb(7, b) * comb(16, 7 - a - b)
              for a in range(2, 5) for b in range(1, 8) if a + b <= 7) / comb(40, 7)
    assert abs(r["probability"] - round(exp, 4)) < 1e-4 and r["cards_seen"] == 7


@requires_db
def test_hand_errors():
    assert call(kind="hand", deck_size=40, groups=[])["error_kind"] == "empty_query"
    assert call(kind="hand", deck_size=40, groups=[{"count": 30, "min": 1, "max": 3}, {"count": 20, "min": 0, "max": 2}])["error_kind"] == "out_of_range"
    assert call(kind="hand", deck_size=40, groups=[{"count": 17, "min": 4, "max": 2}])["error_kind"] == "out_of_range"


# ─── 公開関数の安全と入力の読み違いを縫う ───

# 利用者から SQL の構文を渡せる入口を残さないための縫い方。「mtg 関数が text を受け取らないこと」で判定すると、
# 固定の SELECT に値として渡す text（mtg_card_partners のカード名）まで落とし、逆に varchar・domain に型を
# 変えた生の条件や、SQL の関数から別の動的な関数へ渡す形は通してしまう。そこで、拡張の物を除く自作の関数
# （名前の頭を問わない＝呼び先の helper も入る）の署名と定義の指紋を、下のレビュー済みの一覧と照らす。新しい関数・オーバーロード・定義の
# 変更は、レビューして一覧を直すまで落ちる（知らない物は通さない）。一覧に有って DB に無いのは落とさない
# （公開サーバーや clone した人の DB には開発側の関数が無い）。
# 一覧を直す手順: 定義（pg_get_functiondef）を読み、利用者の値が SQL の構文として解釈される道が無いことを
# 確かめる（EXECUTE があれば、構文を組む材料が利用者の文字列でないこと・値は USING で渡していること）。
# repo の sql/ の正本と DB の本体が一致することも確かめる。理由を一行書き、指紋はこの試験の失敗の出力から
# 写す（期待値を試験の中で DB から作らない）。PostgreSQL の版上げで定義の書式が変わったときも落ちる＝読み直す。
REMOVED_ENTRY_POINTS = [
    # 廃止した入口の本体: cond text を関数の中で SELECT として走らせられた（sql/prob_functions_mana.sql が DROP する）
    "public.mtg_multi_hypergeom(integer, integer[], integer[], integer[], integer, text)",
]

# (スキーマ, 名前, 入力の署名): (定義の sha256 の頭 16 字, レビューの理由)。11 本とも sql/ の正本と本体の一致を確かめてある
REVIEWED_FUNCTIONS = {
    ("public", "mtg__int_array_ok", "a integer[], n integer"):
        ("f59482abf0153dbe", "LANGUAGE sql・整数の配列の検査だけ"),
    ("public", "mtg_card_partners", "p_format text, p_card text, p_days integer, p_board text, p_min_with integer"):
        ("5c6a334cf05b28b7", "LANGUAGE sql の固定の SELECT・text は比較の値として使うだけ（開発側の分析用・公開サーバーには置かない）"),
    ("public", "mtg_cards_seen", "turn integer, on_play boolean, mull integer"):
        ("3de3a1d8bd0ddf79", "LANGUAGE sql・整数の計算だけ"),
    ("public", "mtg_castable", "deck integer, counts integer[], canpay integer[], need integer[], mv integer[], turn integer, on_play boolean, mull integer"):
        ("63aa3773233bdbf6", "EXECUTE の文は整数と整数の配列から format で組む・表は USING で渡す・文字列の引数なし"),
    ("public", "mtg_comb", "n integer, k integer"):
        ("001854385bac9b6b", "plpgsql・整数の計算だけ"),
    ("public", "mtg_combo_by_turn", "deck integer, a integer, b integer, turn integer, on_play boolean, mull integer"):
        ("32475a2bed15253a", "LANGUAGE sql・整数の計算だけ"),
    ("public", "mtg_hand_prob", "deck integer, counts integer[], lo integer[], hi integer[], turn integer, on_play boolean, mull integer"):
        ("ac7863edd058abe9", "EXECUTE の文は整数と整数の配列から format で組む・表は USING で渡す・文字列の引数なし"),
    ("public", "mtg_hypergeom_atleast", "n integer, k integer, d integer, m integer"):
        ("909babefbc34eddb", "LANGUAGE sql・整数の計算だけ"),
    ("public", "mtg_hypergeom_exact", "n integer, k integer, d integer, m integer"):
        ("2797075345214581", "LANGUAGE sql・整数の計算だけ"),
    ("public", "mtg_land_drops", "deck integer, lands integer, turn integer, on_play boolean, mull integer"):
        ("2f48f39472a9bbeb", "LANGUAGE sql・整数の計算だけ"),
    ("public", "mtg_prob_by_turn", "deck integer, copies integer, turn integer, on_play boolean, m integer, mull integer"):
        ("8238e8dccf057f58", "LANGUAGE sql・整数の計算だけ"),
}

# 拡張の物を除く自作の関数と手続き（集約・窓は pg_get_functiondef が受けないので数えない）。% は書かない（_db の書式と混ざる）
_OWN_FUNCTIONS_SQL = (
    "SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), pg_get_functiondef(p.oid)"
    " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
    " WHERE p.prokind IN ('f', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname !~ '^pg_'"
    " AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')")


def _fingerprint(definition):
    return hashlib.sha256(definition.encode("utf-8")).hexdigest()[:16]


def unreviewed_functions(rows, reviewed):
    """rows=[(スキーマ, 名前, 入力の署名, 定義)]。一覧に無い関数と、指紋が一覧と違う関数を (鍵, 今の指紋) で返す。"""
    out = []
    for nsp, name, args, definition in rows:
        key, fp = (nsp, name, args), _fingerprint(definition)
        if key not in reviewed or reviewed[key][0] != fp:
            out.append((key, fp))
    return sorted(out)


def test_unreviewed_functions_judging():
    """照らし方そのもの（DB 不要）: 知っている定義は通し、新しい名前・オーバーロード・定義の変更は落とす。"""
    known = ("public", "f_ok", "a integer")
    reviewed = {known: (_fingerprint("def ok"), "試験")}
    assert unreviewed_functions([("public", "f_ok", "a integer", "def ok")], reviewed) == []
    assert unreviewed_functions([], reviewed) == []                                   # 一覧に有って DB に無いのは落とさない
    new = unreviewed_functions([("public", "f_new", "q varchar", "def x")], reviewed)
    assert [k for k, _ in new] == [("public", "f_new", "q varchar")]                   # 型を varchar に変えた新顔も一覧に無ければ落ちる
    over = unreviewed_functions([("public", "f_ok", "a integer, cond text", "def ok")], reviewed)
    assert [k for k, _ in over] == [("public", "f_ok", "a integer, cond text")]        # 同じ名前のオーバーロード
    changed = unreviewed_functions([("public", "f_ok", "a integer", "def ok -- EXECUTE cond")], reviewed)
    assert changed == [(known, _fingerprint("def ok -- EXECUTE cond"))]                # 同じ署名で定義が変わった
    other_schema = unreviewed_functions([("tools", "f_ok", "a integer", "def ok")], reviewed)
    assert [k for k, _ in other_schema] == [("tools", "f_ok", "a integer")]            # 別のスキーマの同じ名前


@requires_db
def test_removed_entry_points_absent():
    """廃止した入口（cond text を受け取る mtg_multi_hypergeom）が DB に無いこと。"""
    for sig in REMOVED_ENTRY_POINTS:
        assert m._db("SELECT to_regprocedure(%s)::text", (sig,)) == [(None,)], f"廃止した入口が残っている: {sig}"


@requires_db
def test_own_functions_match_reviewed_list():
    """自作の関数の署名と定義が、レビュー済みの一覧と一致すること。"""
    bad = unreviewed_functions(m._db(_OWN_FUNCTIONS_SQL, ()), REVIEWED_FUNCTIONS)
    assert bad == [], "レビューしていない関数か、定義が変わった関数（読んでから REVIEWED_FUNCTIONS を直す）:\n" + "\n".join(
        f"  {k}: 今の指紋 {fp}" for k, fp in bad)


@requires_db
@pytest.mark.parametrize("sql,params", [
    ("SELECT mtg_castable(1000000, %s, %s, %s, %s, 1, true)", ([], [], [[0]], [0])),        # deck が上限超え
    ("SELECT mtg_castable(40, %s, %s, %s, %s, 30, true)", ([17], [1], [[1]], [1])),          # turn が上限超え
    ("SELECT mtg_castable(40, %s, %s, %s, %s, 2, true)", ([17, 3], [1], [[1]], [1])),        # 配列の長さ不一致
    ("SELECT mtg_castable(40, %s, %s, %s, %s, 2, true)", ([-1], [1], [[1]], [1])),           # 負の枚数
    ("SELECT mtg_castable(40, %s, %s, %s, %s, 2, true)", ([30, 20], [1, 0], [[1]], [1])),    # 合計がデッキ超え
    ("SELECT mtg_castable(500, %s, %s, %s, %s, 20, false)", ([60] * 8, [1, 2, 4, 8, 16, 32, 3, 5], [[1] * 6], [6])),  # 数え上げ超え
    ("SELECT mtg_hand_prob(500, %s, %s, %s, 20, true)", ([40] * 6, [0] * 6, [40] * 6)),    # hand の数え上げ超え
    ("SELECT mtg_hand_prob(40, %s, %s, %s, 0, true)", ([17], [4], [2])),                    # min > max
])
def test_sql_guards_return_null(sql, params):
    """SQL 関数を直接呼んでも上限の外は計算せず NULL（入口の Python を迂回されても膨らまない）。"""
    assert m._db(sql, params)[0][0] is None


@requires_db
def test_spell_without_mana_cost_is_error():
    """マナ・コストを持たないカードを 0 マナ（100%）と答えない。"""
    r = call(kind="castable", deck_size=60, lands=[{"name": "Forest", "count": 20}], spell="Living End", turn=1)
    assert r["error_kind"] == "out_of_range" and "マナ・コストを持たない" in r["error"]


@pytest.mark.parametrize("bad", ["garbage", "{1}{G}trailing", "{BAD/P}", "", "{G}{Q}"])
def test_parse_cost_rejects_garbage(bad):
    """記号以外の文字・不明な記号を黙って捨てない。"""
    with pytest.raises(ValueError):
        manabase.parse_cost(bad)


def test_parse_cost_zero_and_phyrexian():
    assert manabase.parse_cost("{0}")[:3] == ([], 0, [])
    assert manabase.parse_cost("{G/U/P}{1}")[:2] == ([], 1)


def test_mask_duplicate_letters():
    """同じ色を 2 回書いてもビットが繰り上がらない（G+G が C にならない）。"""
    assert manabase._mask(["G", "G"]) == manabase.BIT["G"]


@requires_db
def test_produces_duplicate_still_green():
    r = call(kind="castable", deck_size=40, turn=2, mana_cost="{G}",
             lands=[{"name": "Forest", "count": 17, "produces": ["G", "G"]}])
    b = call(kind="by_turn", deck_size=40, copies=17, turn=2)
    assert abs(r["probability"] - b["probability"]) < 1e-4


@requires_db
def test_hand_work_limit_at_entrance():
    """hand にも入口の計算量上限がある。"""
    r = call(kind="hand", deck_size=500, turn=20, groups=[{"label": f"g{i}", "count": 40, "min": 0, "max": 40} for i in range(6)])
    assert r["error_kind"] == "out_of_range" and "計算量" in r["error"]


def test_preview_after_release_says_pending(monkeypatch):
    """発売日を過ぎても legalities が切り替わっていない行は『発売前』と言わない。"""
    from datetime import date as _d
    from sisho import preview
    monkeypatch.setattr(preview, "_db", lambda sql, params, lane=None: [
        ("X", "fra", "Reality Fracture", _d(2000, 1, 1), "expansion")])
    n = preview.preview_notes(["X"])["X"]
    assert "発売前" not in n and "反映待ち" in n


@requires_db
def test_ambiguous_back_face_spell_is_rejected():
    """裏面名が複数のカードで重なる名前（Vicious Verse 等）を渡したとき、LIMIT 1 で 1 枚に潰さず曖昧エラーを返す。"""
    r = call(kind="castable", deck_size=60, lands=[{"name": "Mountain", "count": 24}], spell="Vicious Verse", turn=2)
    assert r["error_kind"] == "out_of_range"
    assert "複数のカードの面の名前で、1 枚に決まらない" in r["error"]
    assert len(r.get("candidates", [])) >= 2


@requires_db
def test_pathway_face_name_counts_as_dual_land():
    """両面とも土地の modal_dfc（Pathway）は、面の名前で渡してもデュアルランドと同じ扱い＝カード全体の色
    （デッキ作りの数え方ではデュアルランドと同じ扱いにする）。面の色に絞る直しを入れるとここが落ちる。"""
    lands, err = manabase._resolve_lands([{"name": "Riverglide Pathway", "count": 24}])
    assert err is None, err
    assert lands[0]["produces"] == ["U", "R"] and lands[0]["source"] == "DB", lands
    for face in ("Riverglide Pathway", "Lavaglide Pathway"):
        for cost in ("{U}", "{R}"):
            r = call(kind="castable", deck_size=60, lands=[{"name": face, "count": 24}], mana_cost=cost, turn=1)
            assert r.get("probability", 0) > 0.9, f"{face} で {cost}: {r}"


@requires_db
def test_lands_reject_transform_back_land_and_mdfc_spell_face():
    """変身後の裏面だけ土地のカード（Search for Azcanta / Azcanta, the Sunken Ruin）や MDFC の呪文面名指し（Bala Ged Recovery）は土地枠で拒否し、MDFC の土地面（Bala Ged Sanctuary）や全体名（Bala Ged Recovery // Bala Ged Sanctuary）は通す。"""
    for bad_land in ("Search for Azcanta", "Azcanta, the Sunken Ruin", "Bala Ged Recovery"):
        r = call(kind="castable", deck_size=60, lands=[{"name": bad_land, "count": 24}], spell="Opt", turn=1)
        assert r["error_kind"] == "out_of_range", f"{bad_land} が土地として通ってしまった: {r}"

    for ok_land in ("Bala Ged Sanctuary", "Bala Ged Recovery // Bala Ged Sanctuary"):
        r = call(kind="castable", deck_size=60, lands=[{"name": ok_land, "count": 24, "produces": ["G"]}], mana_cost="{G}", turn=1)
        assert "probability" in r, f"{ok_land} が土地として通らなかった: {r}"
