#!/usr/bin/env python3
"""mtg_probability の castable・hand と SQL 関数 mtg_castable・mtg_hand_prob の試験。

黄金値は Python の独立実装: 引いた土地を 1 枚ずつ並べ、色の記号への割り当てを総当たりで探す（Hall の定理を使わない）。
SQL 側は Hall の条件と集合演算の数え上げ＝別の道筋で同じ値になること。DB が要る（無い環境では skip）。
走らせ方: pytest tests/test_manabase.py -v
"""
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

@requires_db
def test_no_sql_text_function_left():
    """生の条件式（text）を受け取る公開関数が残っていないこと。"""
    rows = m._db("SELECT p.proname FROM pg_proc p WHERE p.proname LIKE 'mtg%%'"
                 " AND 'text'::regtype = ANY(p.proargtypes::regtype[])", ())
    assert rows == [], f"text を受け取る mtg 関数: {rows}"


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
