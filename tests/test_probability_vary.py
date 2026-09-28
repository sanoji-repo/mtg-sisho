#!/usr/bin/env python3
"""mtg_probability の vary（数字を 1〜3 つ振って 1 回で比べる）の試験。

試験の範囲: 1〜3 変数。
DB が無くても走る試験（偽の _db で形を見る）と、DB に繋いで数値を照合する試験（requires_db）を分ける
＝DB の無い環境で数値の試験だけが飛ばされて 0 件で通るのを避ける。
走らせ方: pytest tests/test_probability_vary.py -v
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402
import sisho.probvary as pv  # noqa: E402
import sisho.tools.probability as pr  # noqa: E402
from conftest import requires_db  # noqa: E402

f = m.mtg_probability.fn if hasattr(m.mtg_probability, "fn") else m.mtg_probability
GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "probability_single.json")


def call(**kw):
    return json.loads(f(**kw))


def err(**kw):
    r = call(**kw)
    assert "error" in r, f"エラーになるはず: {kw} → {r}"
    return r["error"]


# ─── 偽の DB（常に走る）───────────────────────────────────────────

class FakeDB:
    """数字ごとの値の配列（params の末尾の d 個）の全組み合わせを返す偽物。どの軸の取り違えも見つかるように、
    確率＝（組み合わせの通し番号＋1）/100（全部の値で違う）・見る枚数＝5＋（値の和 mod 7）（組み合わせごとに変わる）。
    null_at の値（1 つ目の数字）だけ確率を None にする（null_at="all" なら全部）。"""

    def __init__(self, null_at=None):
        self.calls = []
        self.null_at = null_at

    def __call__(self, sql, params, lane=None):
        import itertools
        self.calls.append((sql, params, lane))
        d = sql.count("unnest(")
        lists = params[-d:]
        out = []
        for idx, combo in enumerate(itertools.product(*lists)):
            null = self.null_at == "all" or combo[0] == self.null_at
            out.append((*combo, None if null else (idx + 1) / 100, 5 + sum(combo) % 7))
        return out


@pytest.fixture
def fake(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(pv, "_db", db)
    return db


def test_one_select_in_heavy_lane(fake):
    """試験 8: DB の問い合わせは 1 本・重いレーン。"""
    from sisho.db import LANE_HEAVY
    call(kind="land_drops", deck_size=40, turn=4, vary={"copies": [15, 16, 17, 18]})
    assert len(fake.calls) == 1, f"問い合わせが {len(fake.calls)} 本"
    sql, params, lane = fake.calls[0]
    assert lane == LANE_HEAVY
    assert "unnest(%s::int[]) WITH ORDINALITY" in sql and params[-1] == [15, 16, 17, 18]


def test_fifty_values_pass_and_fifty_one_are_refused(fake):
    """上限は全部で 50 通り。50 個は通り、51 個は絞るよう返す。"""
    r = call(kind="by_turn", deck_size=250, copies=4, vary={"turn": list(range(1, 51))})
    assert "error" not in r and len(r["rows"]) == 50
    msg = err(kind="by_turn", deck_size=250, copies=4, vary={"turn": list(range(1, 52))})
    assert "50 通りまで" in msg and "絞って" in msg


def test_order_is_kept_and_duplicates_merge(fake):
    """試験 4: [18, 16, 18, 17] は 18・16・17 の順（rows と table の両方）。"""
    r = call(kind="land_drops", deck_size=40, turn=4, vary={"copies": [18, 16, 18, 17]})
    assert [x["copies"] for x in r["rows"]] == [18, 16, 17]
    body = [ln for ln in r["table"].split("\n") if ln.startswith("| ") and not ln.startswith("| 土地")]
    assert [ln.split(" | ")[0][2:] for ln in body] == ["18", "16", "17"]


def test_table_matches_rows_and_has_caveat(fake):
    """試験 5: 前提の行に計算値の断り・区切りの行・行の数と順と百分率が rows と同じ。"""
    r = call(kind="by_turn", deck_size=60, copies=4, turn=3, vary={"at_least": [1, 2, 3]})
    lines = r["table"].split("\n")
    assert lines[0].startswith("前提: ") and "超幾何分布の計算値・実戦の記録ではない" in lines[0]
    sep = next(ln for ln in lines if ln.startswith("|---|"))
    body = lines[lines.index(sep) + 1:]
    assert [ln.strip("|").split("|")[-1].strip() for ln in body] == [x["percent"] for x in r["rows"]]


@pytest.mark.parametrize("kind,var,vals,base", [
    ("land_drops", "deck_size", [40, 60], dict(copies=17, turn=4)),
    ("at_least", "draws", [7, 8], dict(deck_size=60, copies=4)),
])
def test_caption_does_not_carry_the_varied_value(fake, kind, var, vals, base):
    """試験 5: deck_size・draws を振っても、前提の行に振った値（基準の値）が紛れない。"""
    r = call(kind=kind, vary={var: vals}, **base)
    cap = r["table"].split("\n")[0]
    if var == "deck_size":
        assert "枚のデッキ" not in cap, cap
    else:
        assert "枚引く" not in cap, cap
    assert var not in r["fixed"]


def test_turn_moves_premise_to_rows_and_left_column_has_cards_seen(fake):
    """試験 5: turn を振ると premise が各行へ・左の列に見る枚数。"""
    r = call(kind="land_drops", deck_size=40, copies=17, vary={"turn": [3, 4]})
    assert "premise" not in r and all("premise" in x for x in r["rows"])
    first = r["rows"][0]
    assert f"| 3 ターン目（{first['cards_seen']} 枚） |" in r["table"]


def test_mulligans_caveat_in_caption_and_note(fake):
    """試験 5: mulligans を振ると前提と note の両方に注意。"""
    r = call(kind="by_turn", deck_size=60, copies=4, turn=3, vary={"mulligans": [0, 1]})
    assert pv._MULL_CAVEAT in r["table"].split("\n")[0] and pv._MULL_CAVEAT in r["note"]


def test_fixed_has_only_args_used_by_the_kind(fake):
    """試験 5: fixed にその kind の計算に使わない数字が入らない。"""
    r = call(kind="land_drops", deck_size=40, turn=4, draws=9, at_least=3, copies_b=5, vary={"copies": [16, 17]})
    assert set(r["fixed"]) == {"deck_size", "turn", "mulligans", "on_play"}


def test_unused_args_are_range_checked_like_the_single_call(fake):
    """計算に使わない引数も 1 回の計算と同じく範囲検査する（draws=99 は vary でも弾く）。"""
    single = call(kind="land_drops", deck_size=40, turn=4, draws=99, copies=16)
    assert single.get("error_kind") == "out_of_range" and "draws" in single["error"]
    msg = err(kind="land_drops", deck_size=40, turn=4, draws=99, vary={"copies": [16, 17]})
    assert "draws は 0〜deck_size" in msg and fake.calls == []


def test_same_name_plain_arg_is_ignored_even_out_of_range(fake):
    """試験 5: 同じ名前の普通の引数は使わない・範囲外でも検査にかからない。"""
    r = call(kind="land_drops", deck_size=40, turn=4, copies=600, vary={"copies": [15, 16]})
    assert "error" not in r and [x["copies"] for x in r["rows"]] == [15, 16]
    assert "600" not in r["table"]


@pytest.mark.parametrize("kw,needle", [
    (dict(kind="land_drops", vary={}), "の形"),
    (dict(kind="by_turn", vary={"copies": [1, 2], "turn": [1, 2], "at_least": [1, 2], "mulligans": [0, 1]}), "3 つまで"),
    (dict(kind="land_drops", vary={"copies": [16, 17], "turn": [3, 3]}), "2 個以上"),
    (dict(kind="land_drops", vary={"copies": list(range(10, 18)), "turn": list(range(1, 8))}), "8×7＝56 通り"),
    (dict(kind="castable", vary={"turn": [2, 3]}), "vary を外して"),
    (dict(kind="hand", vary={"turn": [0, 1]}), "vary を外して"),
    (dict(kind="land_drops", vary={"draws": [7, 8]}), "deck_size・copies・turn・mulligans"),
    (dict(kind="land_drops", vary={"on_play": [1, 0]}), "振れる数字は"),
    (dict(kind="land_drops", vary={"copies": 17}), "整数の配列"),
    (dict(kind="land_drops", vary={"copies": {"nested": 1}}), "整数の配列"),
    (dict(kind="land_drops", vary={"copies": list(range(0, 41)) + list(range(0, 10))}), "50 通りまで"),
    (dict(kind="land_drops", vary={"copies": ["17", 18]}), "整数だけ"),
    (dict(kind="land_drops", vary={"copies": [True, 18]}), "整数だけ"),
    (dict(kind="land_drops", vary={"copies": [17.0, 18]}), "整数だけ"),
    (dict(kind="land_drops", vary={"copies": [None, 18]}), "整数だけ"),
    (dict(kind="land_drops", vary={"copies": [17, 17]}), "2 個以上"),
])
def test_input_errors(fake, kw, needle):
    """試験 6: 入力エラー 1〜7（DB に行かない）。"""
    msg = err(deck_size=40, **kw)
    assert needle in msg, msg
    assert fake.calls == [], "入力エラーで DB に行った"


def test_out_of_range_lists_all_broken_values_and_returns_no_rows(fake):
    """試験 6: 範囲外が 1 つでもあれば表を返さず、破れた値を全部並べる。"""
    msg = err(kind="land_drops", deck_size=40, turn=4, vary={"copies": [15, 41, 45]})
    assert "copies=41" in msg and "copies=45" in msg and fake.calls == []


def test_combo_sum_constraint_per_value(fake):
    """試験 6: combo は copies＋copies_b ≤ deck_size を値ごとに見る。"""
    msg = err(kind="combo_by_turn", copies=20, copies_b=20, vary={"deck_size": [60, 30]})
    assert "deck_size=30" in msg and "deck_size=60" not in msg


def test_null_row_fails_the_whole_call(monkeypatch):
    """試験 7: 途中の 1 行だけ NULL なら行を 1 つも返さない。"""
    monkeypatch.setattr(pv, "_db", FakeDB(null_at=16))
    r = call(kind="land_drops", deck_size=40, turn=4, vary={"copies": [15, 16, 17]})
    assert "error" in r and "rows" not in r and "copies=16" in r["error"]


def test_log_puts_vary_first_only_when_given(monkeypatch, fake):
    """試験 9: vary を渡したときだけ記録の先頭に vary。省いた呼び出しに vary を書かない。50 個でも JSON として読み直せる（幅 2000）。"""
    seen = []
    monkeypatch.setattr(pr, "_log_tool", lambda name, args: seen.append(args))
    vals = list(range(451, 501))
    f(kind="at_least", deck_size=500, copies=250, draws=250, vary={"copies": vals})
    assert list(seen[-1])[0] == "vary" and seen[-1]["vary"] == {"copies": vals}
    assert len(json.dumps(seen[-1], ensure_ascii=False)) <= 2000
    monkeypatch.setattr(pv, "_db", fake)
    try:
        f(kind="zzz")
    except Exception:
        pass
    assert "vary" not in seen[-1]


def test_sdk_schema_and_type_checks_reach_the_body(fake):
    """試験 6・10: 引数の形に説明が載る・"17"／true／17.0 は本体まで元の型で届く・vary=17 と [] は SDK が弾く。"""
    async def main():
        tools = await m.server.list_tools()
        t = next(x for x in tools if x.name == "mtg_probability")
        schema = t.input_schema["properties"]["vary"]
        assert schema.get("description") == pv.VARY_DESCRIPTION
        assert {"type": "object", "additionalProperties": True} in schema["anyOf"]
        outs = []
        for bad in ({"copies": ["17", 18]}, {"copies": [True, 18]}, {"copies": [17.0, 18]}):
            r = await m.server.call_tool("mtg_probability", {"kind": "land_drops", "deck_size": 40, "vary": bad})
            outs.append(json.loads(r.content[0].text)["error"])
        assert all("整数だけ" in o for o in outs), outs
        for bad in (17, []):
            with pytest.raises(Exception):
                await m.server.call_tool("mtg_probability", {"kind": "land_drops", "deck_size": 40, "vary": bad})
    asyncio.run(main())


def _check_matrix(table, rows, names, lists):
    """行×列の表（3 変数なら 3 つ目ごとの表）の全マスを、rows の同じ組み合わせの百分率と突き合わせる。"""
    import re
    pct = {tuple(r[n] for n in names): r["percent"] for r in rows}
    seen = {tuple(r[n] for n in names): r["cards_seen"] for r in rows}
    blocks = [table] if len(names) == 2 else table.split("\n\n")[1:]
    thirds = [None] if len(names) == 2 else lists[2]
    tables = []
    if len(names) == 2:
        tables = [table]
    else:
        # 3 変数: 「見出し: 値」の段落の後に表が続く
        parts = table.split("\n\n")[1:]
        tables = [parts[i + 1] for i in range(0, len(parts), 2)]
    assert len(tables) == len(thirds)
    for vc, t in zip(thirds, tables):
        body = [ln for ln in t.split("\n") if ln.startswith("| ") and "＼" not in ln]
        assert len(body) == len(lists[0])
        for va, ln in zip(lists[0], body):
            cells = [c.strip() for c in ln.strip("|").split("|")][1:]
            assert len(cells) == len(lists[1])
            for vb, c in zip(lists[1], cells):
                key = (va, vb) if vc is None else (va, vb, vc)
                m = re.fullmatch(r"(\d+\.\d%)(?:（(\d+) 枚）)?", c)
                assert m and m.group(1) == pct[key], (key, c, pct[key])
                if m.group(2) is not None:
                    assert int(m.group(2)) == seen[key], (key, c, seen[key])


def test_sql_binds_fixed_values_in_order(fake):
    """SQL の %s の並びが確率の式 → 見る枚数の式 → 配列の順（固定の値を全部違う数にして確かめる）。"""
    call(kind="combo_by_turn", deck_size=61, copies=5, copies_b=3, turn=4, on_play=False, mulligans=2,
         vary={"copies_b": [2, 3], "turn": [3, 5]})
    sql, params, _ = fake.calls[0]
    assert "mtg_combo_by_turn(%s, %s, t0.v, t1.v, %s, %s)" in sql
    assert "least(mtg_cards_seen(t1.v, %s, %s), %s)" in sql
    assert params == (61, 5, False, 2, False, 2, 61, [2, 3], [3, 5])


def test_three_vars_fifty_pass_and_seventy_five_are_refused(fake):
    """3 変数でも全部で 50 通り（5×5×2＝50 は通る・5×5×3＝75 は形を添えて断る）。"""
    ok = call(kind="by_turn", deck_size=60, vary={"copies": [1, 2, 3, 4, 5], "turn": [1, 2, 3, 4, 5], "mulligans": [0, 1]})
    assert "error" not in ok and len(ok["rows"]) == 50
    _check_matrix(ok["table"], ok["rows"], ("copies", "turn", "mulligans"), ([1, 2, 3, 4, 5], [1, 2, 3, 4, 5], [0, 1]))
    msg = err(kind="by_turn", deck_size=60, vary={"copies": [1, 2, 3, 4, 5], "turn": [1, 2, 3, 4, 5], "mulligans": [0, 1, 2]})
    assert "5×5×3＝75 通り" in msg


def test_many_broken_combinations_say_how_many_more(fake):
    """範囲外の組み合わせが 10 を超えたら「ほか N 通り」。"""
    msg = err(kind="land_drops", deck_size=40, vary={"copies": [41, 42, 43, 44], "turn": [1, 2, 3]})
    assert "ほか 2 通り" in msg


def test_many_null_rows_say_how_many_more(monkeypatch):
    """計算できない行が 10 を超えたら「ほか N 通り」（1 行でも NULL なら表は返さない）。"""
    monkeypatch.setattr(pv, "_db", FakeDB(null_at="all"))
    msg = err(kind="land_drops", deck_size=40, turn=4, vary={"copies": list(range(1, 13))})
    assert "ほか 2 通り" in msg


def fake_db_single(sql, params, lane=None):
    """変更前の 1 回の計算を DB 無しで固定するための偽物（tests/golden/probability_single_fakedb.json を作ったのと同じ式）。"""
    s = sum(int(x) for x in params if isinstance(x, (int, bool)))
    p = (s % 997) / 997
    return [(p, 7 + (s % 5))] if "mtg_cards_seen" in sql else [(p,)]


def test_single_call_outputs_are_unchanged_without_db(monkeypatch):
    """試験 2（常に走る）: vary なし（省略と null）の返り値が、変更前の probability.py を同じ偽 DB で動かした出力と 1 文字も違わない。
    castable と hand は vary の分岐より前に抜ける別の道なので、DB に繋いだ試験（下）で照合する。"""
    monkeypatch.setattr(pr, "_db", fake_db_single)
    g = json.load(open(os.path.join(os.path.dirname(__file__), "golden", "probability_single_fakedb.json"), encoding="utf-8"))
    for name, kw in g["cases"].items():
        assert f(**kw) == g["outputs"][name], f"{name}: 1 回の計算の返り値が変わった"
        assert f(**kw, vary=None) == g["outputs"][name], f"{name}: vary=None で返り値が変わった"


def test_two_vars_make_a_matrix(fake):
    """2 変数は行（1 つ目）×列（2 つ目）の表・rows は全組み合わせ・DB は 1 本。"""
    r = call(kind="land_drops", deck_size=40, vary={"copies": [16, 17, 18], "turn": [3, 4, 5]})
    assert r["vary"] == ["copies", "turn"] and len(r["rows"]) == 9 and len(fake.calls) == 1
    assert [(x["copies"], x["turn"]) for x in r["rows"]][:4] == [(16, 3), (16, 4), (16, 5), (17, 3)]
    lines = r["table"].split("\n")
    assert lines[2].startswith("| 土地の枚数 ＼ ターン | 3 ターン目") and lines[3] == "|---|---|---|---|"
    assert len(lines) == 2 + 2 + 3
    _check_matrix(r["table"], r["rows"], ("copies", "turn"), ([16, 17, 18], [3, 4, 5]))
    assert set(r["fixed"]) == {"deck_size", "mulligans", "on_play"}


def test_matrix_is_self_contained_about_cards_seen(fake):
    """表だけ貼って伝わる: 『rows を見る』とは書かない。
    ターンの見出しで見る枚数が分からないマス（ターンとマリガンを同時に振る）は、マスの中に「xx.x%（N 枚）」。"""
    r = call(kind="by_turn", deck_size=60, copies=4, vary={"turn": [2, 3], "mulligans": [0, 1]})
    assert "rows" not in r["table"]
    body = [ln for ln in r["table"].split("\n") if ln.startswith("| ") and "＼" not in ln]
    assert all("枚）" in c for ln in body for c in ln.strip("|").split("|")[1:]), r["table"]
    _check_matrix(r["table"], r["rows"], ("turn", "mulligans"), ([2, 3], [0, 1]))


def test_three_vars_split_tables_by_the_third(fake):
    """3 変数は 3 つ目の値ごとに行×列の表を分ける。"""
    r = call(kind="by_turn", deck_size=60, copies=4, vary={"turn": [2, 3], "mulligans": [0, 1], "at_least": [1, 2]})
    assert len(r["rows"]) == 8
    assert r["table"].count("| ターン ＼ マリガン回数 |") == 2
    assert "必要な枚数: 1\n" in r["table"] and "必要な枚数: 2\n" in r["table"]
    assert pv._MULL_CAVEAT in r["note"]
    _check_matrix(r["table"], r["rows"], ("turn", "mulligans", "at_least"), ([2, 3], [0, 1], [1, 2]))


def test_combo_constraint_across_two_vars(fake):
    """combo の copies＋copies_b ≤ deck_size を全組み合わせで見る（2 変数）。"""
    msg = err(kind="combo_by_turn", deck_size=40, vary={"copies": [10, 30], "copies_b": [5, 15]})
    assert "copies=30・copies_b=15" in msg and "copies=10" not in msg and fake.calls == []


# ─── DB に繋いだ数値の照合 ────────────────────────────────────────

@requires_db
def test_single_call_outputs_are_unchanged():
    """試験 2: vary なし（省略と null）の返り値は変更前の出力（tests/golden/probability_single.json）と 1 文字も違わない。"""
    g = json.load(open(GOLDEN, encoding="utf-8"))
    for name, kw in g["cases"].items():
        assert f(**kw) == g["outputs"][name], f"{name}: 1 回の計算の返り値が変わった"
        assert f(**kw, vary=None) == g["outputs"][name], f"{name}: vary=None で返り値が変わった"


_BASE = dict(deck_size=60, copies=8, copies_b=4, draws=7, turn=4, on_play=True, at_least=1, mulligans=0)
_SWEEP = {"deck_size": [40, 60, 99], "copies": [3, 4, 5], "copies_b": [2, 3], "draws": [7, 8, 9],
          "turn": [2, 3, 5], "at_least": [1, 2], "mulligans": [0, 1, 2]}


@requires_db
@pytest.mark.parametrize("kind,var", [(k, v) for k, vs in pv.VARY_ARGS.items() for v in vs])
def test_each_row_matches_the_single_call(kind, var):
    """試験 3: 4 種類 × 振れる数字の全部で、各行の確率と百分率が同じ条件の 1 回の計算と一致。
    見る枚数と式は vary 側の約束（計算に実際に使った枚数・数字を入れた式）で、明示の値と照合する。"""
    r = call(kind=kind, vary={var: _SWEEP[var]}, **_BASE)
    assert "error" not in r, r
    for row in r["rows"]:
        p = dict(_BASE, **{var: row[var]})
        one = call(kind=kind, **p)
        assert row["probability"] == one["probability"] and row["percent"] == one["percent"], (kind, var, row, one)
        seen = p["draws"] if kind == "at_least" else min(one["cards_seen"], p["deck_size"])
        assert row["cards_seen"] == seen
        assert row["formula"] == pv._formula(kind, p, seen)
        if "premise" in row:
            assert row["premise"] == one["premise"]


@requires_db
@pytest.mark.parametrize("kind,vary", [
    ("land_drops", {"copies": [16, 17, 18], "turn": [3, 4, 5]}),
    ("by_turn", {"turn": [2, 4], "mulligans": [0, 2], "at_least": [1, 2]}),
    ("combo_by_turn", {"copies": [3, 4], "copies_b": [2, 4]}),
    ("at_least", {"draws": [7, 9], "copies": [4, 8]}),
])
def test_multi_var_rows_match_single_calls(kind, vary):
    """多変数でも、各行の確率・百分率・前提が同じ条件の 1 回の計算と一致。"""
    r = call(kind=kind, vary=vary, **_BASE)
    assert "error" not in r, r
    for row in r["rows"]:
        p = dict(_BASE, **{k: row[k] for k in vary})
        one = call(kind=kind, **p)
        assert row["probability"] == one["probability"] and row["percent"] == one["percent"], (kind, row, one)
        if "premise" in row:
            assert row["premise"] == one["premise"]


@requires_db
def test_contract_example_numbers():
    """契約の冒頭の例: 40 枚・4 ターン目・土地 15〜18 枚＝56.8・64.0・70.7・76.7%。"""
    r = call(kind="land_drops", deck_size=40, turn=4, on_play=True, vary={"copies": [15, 16, 17, 18]})
    assert [x["percent"] for x in r["rows"]] == ["56.8%", "64.0%", "70.7%", "76.7%"]
    assert r["table"].split("\n")[0] == ("前提: 40 枚のデッキ・先手・マリガン 0 回・見る 10 枚・4 ターン目まで毎ターン土地を置ける確率"
                                         "（超幾何分布の計算値・実戦の記録ではない）")


@requires_db
def test_turn_headers_carry_cards_seen_without_extra_sentence():
    """見出しで見る枚数が分かるとき（土地 × ターン）は、マスに枚数を添えず『rows を見る』とも書かない。"""
    r = call(kind="land_drops", deck_size=40, vary={"copies": [16, 17, 18], "turn": [3, 4, 5]})
    body = [ln for ln in r["table"].split("\n") if ln.startswith("| ") and "＼" not in ln]
    assert "rows" not in r["table"] and not any("枚）" in ln for ln in body)
    assert "| 土地の枚数 ＼ ターン | 3 ターン目（9 枚） | 4 ターン目（10 枚） | 5 ターン目（11 枚） |" in r["table"]
    assert "| 17 | 84.5% | 70.7% | 54.6% |" in r["table"]


@requires_db
def test_capped_cards_seen_show_in_the_table():
    """8 枚のデッキの 3 ターン目は 8 枚しか見ない＝表に見る枚数の列が出る。"""
    r = call(kind="by_turn", copies=2, at_least=2, turn=3, vary={"deck_size": [8, 10]})
    assert [x["cards_seen"] for x in r["rows"]] == [8, 9]
    assert "| 8 | 8 枚 | 100.0% |" in r["table"] and "| 10 | 9 枚 | 80.0% |" in r["table"]


@requires_db
@pytest.mark.parametrize("kind,vary", [
    ("land_drops", {"copies": [4, 6], "deck_size": [8, 40]}),
    ("by_turn", {"deck_size": [9, 60], "turn": [3, 5], "mulligans": [0, 2]}),
])
def test_multi_var_cards_seen_and_formula_match(kind, vary):
    """多変数でも各行の見る枚数（頭打ちを含む）と式が、1 回の計算の条件から出した値と一致。"""
    base = dict(_BASE, copies=4, copies_b=2, at_least=1)
    r = call(kind=kind, vary=vary, **base)
    assert "error" not in r, r
    for row in r["rows"]:
        p = dict(base, **{k: row[k] for k in vary})
        one = call(kind=kind, **p)
        seen = min(one["cards_seen"], p["deck_size"])
        assert row["cards_seen"] == seen and row["formula"] == pv._formula(kind, p, seen)
        assert row["percent"] == one["percent"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
