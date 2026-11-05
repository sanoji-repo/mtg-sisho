"""draft_pool の試験。

manacost・draftcode・poolcalc の純粋な部分は DB に触らない。道具を通す試験と DB 全体の照合は DB（読み取り）に触る。
"""
import json
import logging
import random

import pytest

from conftest import requires_db
from sisho import draftcode as dc
from sisho import errors
from sisho import manacost as mc
from sisho import poolcalc as pc


# ─── manacost ───

def test_mana_value_symbols():
    assert mc.mana_value("{2}{W}{U}") == 4
    assert mc.mana_value("{X}{X}{U}") == 1
    assert mc.mana_value("{2/W}{2/U}") == 4
    assert mc.mana_value("{1}{B/P}{B/P}") == 3
    assert mc.mana_value("{G/W/P}") == 1
    assert mc.mana_value("{C}{C/W}") == 2
    assert mc.mana_value("") == 0 and mc.mana_value(None) == 0
    assert mc.mana_value("{S}") is None, "氷雪は知らない記号＝判定できない"
    assert mc.mana_value("{2} {W}") is None, "記号の外の文字"


def test_payable_states():
    U = mc.parse_colors("U")
    assert mc.payable("{1}{U}", U)[0] == mc.TRUE
    st, why, _ = mc.payable("{1}{W}", U)
    assert st == mc.FALSE and "W" in why
    assert mc.payable("{W/U}", U)[0] == mc.TRUE
    assert mc.payable("{2/W}", U)[0] == mc.TRUE, "2 で払える"
    st, _, marks = mc.payable("{1}{B/P}", U)
    assert st == mc.TRUE and mc.MARK_PHYREXIAN in marks, "ファイレクシア・マナはその色が無くても払える"
    assert mc.payable("{G/W/P}", U)[0] == mc.TRUE, "混成のファイレクシアも打てる"
    assert mc.payable("{C}", U)[0] == mc.FALSE, "{C} は 6 番目の色"
    assert mc.payable("{C}", mc.parse_colors("UC"))[0] == mc.TRUE
    assert mc.payable("{C/W}", mc.parse_colors("C"))[0] == mc.TRUE
    assert mc.payable("{1}{U}", None)[0] == mc.NOT_JUDGED
    assert mc.payable("{S}", None)[0] == mc.UNKNOWN, "色が無くても知らない記号は打てるとみなさない"


def test_parse_colors_grammar():
    assert mc.parse_colors(None) is None
    assert mc.parse_colors("ur") == frozenset("UR")
    assert mc.parse_colors(" U R U ") == frozenset("UR")
    assert mc.parse_colors("C") == frozenset("C")
    for bad in ("", "   ", "UX", "青", 3):
        with pytest.raises(ValueError):
            mc.parse_colors(bad)


def test_colors_needed_counts_cards_not_symbols():
    assert mc.colors_needed("{G}{G}") == (["G"], [])
    assert mc.colors_needed("{1}{W/U}{W/U}") == ([], ["W か U"])
    assert mc.colors_needed("{2/W}{B/P}") == ([], []), "払い方の選択肢は必須と書かない"


@requires_db
def test_mana_value_matches_cmc_for_all_cards():
    """一つの面のカードは通常のコストの点数＝cmc。面のあるカードは表（分割は和）＝cmc（カード表 mtg_cards_v2 の全カードで食い違いは 0 件）。"""
    from sisho.db import _db
    rows = _db("SELECT layout, mana_cost, cmc, card_faces_json FROM mtg_cards_v2", ())
    bad, unknown = 0, set()
    for lay, cost, cmc, faces in rows:
        if isinstance(faces, list):
            vals = [mc.mana_value(f.get("mana_cost")) for f in faces]
            if None in vals:
                unknown |= {t for f in faces for t in (mc.tokens(f.get("mana_cost")) or ["?"]) if mc.classify_symbol(t) is None}
                continue
            v = sum(vals) if lay == "split" else vals[0]
        else:
            if not cost:
                continue
            v = mc.mana_value(cost)
            if v is None:
                unknown |= {t for t in (mc.tokens(cost) or ["?"]) if mc.classify_symbol(t) is None}
                continue
        bad += float(v) != float(cmc)
    assert bad == 0
    assert unknown <= {"S"}, f"判定できない記号は意図して未対応にした氷雪だけのはず（{unknown}）"


# ─── draftcode ───

def _names(n):
    return [f"Card {i:02d}" for i in range(1, n + 1)]


def test_code_round_trip_and_crc():
    code = dc.encode(dc.DRAFT, "FRA", _names(3))
    assert code.startswith("SD1|FRA|3|")
    assert dc.decode(code) == (dc.DRAFT, "FRA", _names(3))
    s = dc.encode(dc.SEALED, "FRA", ["B", "A", "B"])
    assert dc.decode(s) == (dc.SEALED, "FRA", ["A", "B", "B"])


def test_code_crc_covers_version_set_and_count():
    code = dc.encode(dc.DRAFT, "FRA", ["Alpha", "Beta"])
    head, crc = code.split("|")[:3], code.split("|")[3]
    assert crc == format(__import__("zlib").crc32("SD1|FRA|2|Alpha|Beta".encode()) & 0xFFFFFFFF, "08x")
    assert head == ["SD1", "FRA", "2"]


def test_code_every_single_char_change_is_rejected():
    code = dc.encode(dc.DRAFT, "FRA", ["Alpha Card", "Beta Card"])
    for i, ch in enumerate(code):
        alt = "Z" if ch != "Z" else "Y"
        with pytest.raises(dc.CodeError):
            dc.decode(code[:i] + alt + code[i + 1:])


def test_code_rejections():
    good = dc.encode(dc.DRAFT, "FRA", ["Alpha", "Beta"])
    sw = good.split("|")
    swapped = "|".join(sw[:4] + [sw[5], sw[4]])
    with pytest.raises(dc.CodeError) as e:
        dc.decode(swapped)
    assert e.value.reason == dc.CHECKSUM_MISMATCH, "違う名前の入れ替え"
    for bad in (good[:-2], good + "|", good.replace("|2|", "|02|"), "SD2|FRA|1|00000000|A", "XX|FRA|1|0|A",
                good + "\n", "", None, 5):
        with pytest.raises(dc.CodeError):
            dc.decode(bad)
    # 正しい CRC でも枚数が範囲の外なら止める
    names43 = ["Dismember"] * 43
    head = ["SD1", "FRA", "43"]
    code43 = "|".join(head + [dc._crc(head + names43)] + names43)
    with pytest.raises(dc.CodeError) as e:
        dc.decode(code43)
    assert e.value.reason == dc.COUNT_OUT_OF_RANGE
    # 並びが正規でない SS1
    head = ["SS1", "FRA", "2"]
    ss = "|".join(head + [dc._crc(head + ["B", "A"])] + ["B", "A"])
    with pytest.raises(dc.CodeError) as e:
        dc.decode(ss)
    assert e.value.reason == dc.NOT_CANONICAL


def test_code_length_limit_on_output():
    with pytest.raises(dc.CodeError):
        dc.encode(dc.DRAFT, "FRA", ["x" * 190] * 42)


def test_apply_picks_rules():
    old = _names(15)
    r = dc.apply_picks(old, [(3, old[2]), (16, "New 16"), (17, "New 17")], False)
    assert r["resent"] == [3] and r["added"] == [16, 17] and len(r["names"]) == 17, "再送＋続きの追加"
    r = dc.apply_picks(old, [(17, "x"), (16, "y")], False)
    assert r["names"][15:] == ["y", "x"], "入力の順を変えても同じ"
    with pytest.raises(dc.PositionError):
        dc.apply_picks(old, [(3, "other")], False)
    with pytest.raises(dc.PositionError):
        dc.apply_picks(old, [(17, "x")], False)          # 16 が抜けている
    with pytest.raises(dc.PositionError):
        dc.apply_picks(old, [(16, "x"), (16, "y")], False)
    r = dc.apply_picks(old, [(16, "x"), (16, "x")], False)
    assert r["added"] == [16], "同じ位置・同じ名前は 1 件"
    r = dc.apply_picks(old, [(3, "c3"), (10, "c10"), (5, old[4])], True)
    assert [c["position"] for c in r["corrected"]] == [3, 10] and r["unchanged"] == [5]
    assert len(r["names"]) == 15, "訂正は枚数を変えない"
    with pytest.raises(dc.PositionError):
        dc.apply_picks(old, [(3, "c3"), (16, "x")], True), "訂正と追加の混在"
    with pytest.raises(dc.PositionError):
        dc.apply_picks([], [(1, "x")], True)
    full = _names(42)
    assert dc.apply_picks(full, [(42, full[41])], False)["resent"] == [42]
    with pytest.raises(dc.PositionError):
        dc.apply_picks(full, [(43, "x")], False)


def test_position_boundaries():
    assert dc.position(1, 14) == 14 and dc.position(2, 1) == 15 and dc.position(3, 14) == 42
    assert dc.pack_pick(15) == {"pack": 2, "pick": 1}


# ─── poolcalc（合成の行） ───

def _row(name, layout="normal", cost=None, tl="Creature — Test", faces=None, oracle=None, produced=None, **kw):
    d = {"card_name": name, "name_display": f"《{name}》", "layout": layout, "mana_cost": cost, "type_line": tl,
         "card_faces": faces, "oracle_text": oracle, "produced_mana": produced}
    d.update(kw)
    return d


def _face(cost, tl, oracle=None):
    return {"mana_cost": cost, "type_line": tl, "oracle_text": oracle}


def test_split_and_adventure_take_smaller_castable():
    fire_ice = _row("Fire // Ice", "split", None, "Instant // Instant",
                    [_face("{1}{R}", "Instant"), _face("{1}{U}", "Instant")])
    r = pc.classify(fire_ice, None)
    assert r["bucket"] == pc.CURVE and r["counted_form"] == "左" and pc.MARK_CHANGES_WITH_COLORS in r["marks"]
    assert r["types"] == ["Instant"], "Fire // Ice 1 枚はインスタント 1"
    assert pc.classify(fire_ice, mc.parse_colors("U"))["counted_form"] == "右"
    assert pc.classify(fire_ice, mc.parse_colors("G"))["bucket"] == pc.NOT_CASTABLE
    giant = _row("Giant // Stomp", "adventure", None, "Creature — Giant // Instant — Adventure",
                 [_face("{2}{R}{R}", "Creature — Giant"), _face("{1}{R}", "Instant — Adventure")])
    r = pc.classify(giant, mc.parse_colors("R"))
    assert r["counted_form"] == "出来事" and r["mana_value"] == 2 and r["types"] == ["Creature", "Instant"]
    assert r["other_faces"][0]["mana_value"] == 4, "数えなかった形も返す"


def test_lands_first():
    pathway = _row("A Pathway // B Pathway", "modal_dfc", None, "Land // Land",
                   [_face(None, "Land"), _face(None, "Land")], produced=["U", "R"])
    r = pc.classify(pathway, None)
    assert r["bucket"] == pc.LANDS and r["produces"] == ["U", "R"] and pc.MARK_BOTH_LAND in r["marks"]
    spell_land = _row("Spell // Land", "modal_dfc", None, "Sorcery // Land",
                      [_face("{2}{G}", "Sorcery"), _face(None, "Land")])
    r = pc.classify(spell_land, None)
    assert r["bucket"] == pc.CURVE and pc.MARK_BACK_LAND in r["marks"], "裏が土地の両面は呪文の面で数え、土地に数えない"
    town = _row("Town // Spell", "adventure", None, "Land — Town // Sorcery — Adventure",
                [_face(None, "Land — Town"), _face("{2}{B}", "Sorcery — Adventure")], produced=["B"])
    r = pc.classify(town, mc.parse_colors("B"))
    assert r["bucket"] == pc.LANDS and pc.MARK_LAND_ADVENTURE in r["marks"], "FIN の町の土地は土地"
    assert r["other_faces"][0]["form"] == "出来事" and r["other_faces"][0]["mana_value"] == 3
    assert r["other_faces"][0]["castable"] == mc.TRUE
    odd = _row("Land // Spell", "modal_dfc", None, "Land // Sorcery",
               [_face(None, "Land"), _face("{2}{B}", "Sorcery")])
    assert pc.classify(odd, None)["bucket"] == pc.UNCLASSIFIED, "ほかの表が土地の組み合わせは未分類のまま"


def test_prepare_transform_meld():
    prep = _row("Angel // Suture", "prepare", None, "Creature — Angel // Sorcery",
                [_face("{3}{W}", "Creature — Angel"), _face("{G/W}", "Sorcery")])
    r = pc.classify(prep, None)
    assert r["counted_form"] == "クリーチャー面" and r["mana_value"] == 4 and r["types"] == ["Creature"]
    assert pc.MARK_CHANGES_WITH_COLORS not in r["marks"], "候補は 1 つ"
    tr = _row("Front // Back", "transform", None, "Creature — X // Creature — Y",
              [_face("{1}{G}", "Creature — X"), _face(None, "Creature — Y")])
    r = pc.classify(tr, None)
    assert r["counted_form"] == "表" and r["other_faces"][0]["note"]
    meld_land = _row("Meld Land", "meld", None, "Land")
    assert pc.classify(meld_land, None)["bucket"] == pc.LANDS


def test_prototype_follows_castability():
    goliath = _row("Rust Goliath", "prototype", "{10}", "Artifact Creature — Construct",
                   oracle="Prototype {3}{G}{G} — 3/5 (You may cast this spell with different mana cost...)")
    assert pc.classify(goliath, None)["mana_value"] == 5, "色が無ければ試作"
    assert pc.classify(goliath, mc.parse_colors("G"))["mana_value"] == 5
    assert pc.classify(goliath, mc.parse_colors("U"))["mana_value"] == 10, "試作で打てなければ通常"
    unread = _row("Odd Proto", "prototype", "{7}", "Artifact Creature", oracle="Something else")
    r = pc.classify(unread, None)
    assert r["mana_value"] == 7 and pc.MARK_PROTOTYPE_UNREAD in r["marks"]


def test_suspend_and_no_cost():
    le = _row("Living End", "normal", None, "Sorcery",
              oracle="Suspend 3—{2}{B}{B} (Rather than cast this card from your hand...)\nEach player...")
    r = pc.classify(le, None)
    assert r["counted_form"] == "待機" and r["mana_value"] == 4 and "待機 3" in r["marks"], "マナ・コストの無い待機呪文は待機コストで数える"
    assert pc.classify(le, mc.parse_colors("G"))["bucket"] == pc.NOT_CASTABLE
    odd = _row("No Cost", "normal", None, "Sorcery", oracle="Do something.")
    assert pc.classify(odd, None)["bucket"] == pc.UNCLASSIFIED


def test_unknown_layout_and_symbol():
    assert pc.classify(_row("Flip", "flip", "{1}{R}"), None)["bucket"] == pc.UNCLASSIFIED
    assert pc.classify(_row("Snow", "normal", "{S}{G}"), None)["bucket"] == pc.UNCLASSIFIED


def test_summary_conservation_and_roles():
    rows = [_row("Divination", "normal", "{2}{U}", "Sorcery", draw_count=2),
            _row("Divination", "normal", "{2}{U}", "Sorcery", draw_count=2),
            _row("Bolt", "normal", "{R}", "Instant", removal=[{"type": "damage"}]),
            _row("Island", "normal", None, "Basic Land — Island", produced=["U"]),
            _row("Flip", "flip", "{1}{R}")]
    deck = mc.parse_colors("U")
    pairs = [(r, pc.classify(r, deck)) for r in rows]
    s = pc.summarize(pairs)
    b = s["buckets"]
    assert b["curve"] + b["lands"] + b["not_castable"] + b["unclassified"] == b["total"] == 5
    assert s["roles"]["draw"]["count"] == 2, "《Divination》2 枚はドローのカード 2 枚（引く枚数の和にしない）"
    assert s["roles"]["removal"]["count"] == 1, "役割はプール全体（色で絞らない）"
    assert s["colors"]["U"]["count"] == 2


# ─── 道具を通す（DB を読む） ───

def _call(**kw):
    from sisho.tools.pool import draft_pool
    return json.loads(draft_pool(**kw))


@pytest.fixture(scope="module")
def fra():
    from sisho import limited
    return limited.fetch_set_pool("FRA")


def _picks(names, start=1):
    return [dict(dc.pack_pick(i), card=n) for i, n in enumerate(names, start)]


@requires_db
def test_tool_round_trip_three_ways(fra):
    random.seed(7)
    names = [random.choice(fra)["card_name"] for _ in range(42)]
    once = _call(set="FRA", picks=_picks(names))["draft_code"]
    code = None
    for i, n in enumerate(names, 1):
        code = _call(set="FRA", picks=_picks([n], i), **({"code": code} if code else {}))["draft_code"]
    assert code == once
    code = None
    for j in range(0, 42, 5):
        chunk = names[j:j + 5]
        code = _call(set="FRA", picks=_picks(chunk, j + 1), **({"code": code} if code else {}))["draft_code"]
    assert code == once
    r = _call(code=once)
    assert r["picks"] == {"total": 42, "expected": 42, "next": None}
    assert sum(v for k, v in r["buckets"].items() if k != "total") == r["buckets"]["total"] == 42


@requires_db
def test_tool_rerun_vs_resend(fra):
    """同じ古いコード＋同じ picks の再実行は同じ added。返ってきた新しいコード＋同じ picks なら resent。"""
    a, b = fra[0]["card_name"], fra[1]["card_name"]
    c1 = _call(set="FRA", picks=_picks([a]))["draft_code"]
    r1 = _call(code=c1, picks=_picks([b], 2))
    r2 = _call(code=c1, picks=_picks([b], 2))
    assert r1["draft_code"] == r2["draft_code"] and r1["this_call"]["added"] == r2["this_call"]["added"] != []
    r3 = _call(code=r1["draft_code"], picks=_picks([b], 2))
    assert r3["draft_code"] == r1["draft_code"] and r3["this_call"]["resent"] == [{"pack": 1, "pick": 2}]


@requires_db
def test_tool_japanese_and_english_same_card(fra):
    row = next(r for r in fra if r.get("japanese_name") and " // " not in r["card_name"])
    c = _call(set="FRA", picks=_picks([row["card_name"]]))["draft_code"]
    r = _call(code=c, picks=[{"pack": 1, "pick": 1, "card": row["japanese_name"]}])
    assert r["this_call"]["resent"] == [{"pack": 1, "pick": 1}], "日本語名で再送しても同じカード"


@requires_db
def test_tool_rejections(fra):
    a = fra[0]["card_name"]
    c = _call(set="FRA", picks=_picks([a]))["draft_code"]
    assert _call(set="FRA")["error_kind"] == errors.EMPTY_QUERY
    assert _call(set="FRA", picks=_picks([a], 2))["error_kind"] == errors.OUT_OF_RANGE, "コード無しで途中から"
    assert _call(code=c, cards=[a])["error_kind"] == errors.OUT_OF_RANGE
    assert _call(code=c, correct=True)["error_kind"] == errors.OUT_OF_RANGE
    assert _call(set="SOS", code=c)["error_kind"] == errors.OUT_OF_RANGE, "set とコードのセットが違う"
    assert _call(set="Dismember", picks=_picks([a]))["error_kind"] == errors.UNKNOWN_OPTION
    assert _call(set="FRA", picks=_picks([a]), colors="")["error_kind"] == errors.OUT_OF_RANGE
    bad = _call(code=c[:-1] + ("0" if c[-1] != "0" else "1"))
    assert bad["error_kind"] == errors.INVALID_CODE and bad["reason"]
    for p in ({"pack": "1", "pick": 1, "card": a}, {"pack": 1, "pick": 1.0, "card": a},
              {"pack": True, "pick": 1, "card": a}, {"pack": 1, "pick": 15, "card": a},
              {"pack": 1, "pick": 1, "card": a, "extra": 1}):
        assert _call(set="FRA", picks=[p])["error_kind"] == errors.OUT_OF_RANGE
    r = _call(set="FRA", picks=_picks([a, "存在しない名前のカード"]))
    assert r["error_kind"] == errors.NO_MATCH and "draft_code" not in r, "全体を作らない"


@requires_db
def test_tool_catalog_mismatch_and_correct(fra):
    """形は正しいが名前が一覧に無いコードは破損と言わず、correct で置き換えて救える。"""
    head = ["SD1", "FRA", "2"]
    names = [fra[0]["card_name"], "Renamed Card Not In List"]
    code = "|".join(head + [dc._crc(head + names)] + names)
    r = _call(code=code)
    assert r["error_kind"] == errors.NO_MATCH and r["reason"] == "catalog_mismatch"
    r = _call(code=code, picks=_picks([fra[1]["card_name"]], 2), correct=True)
    assert r["picks"]["total"] == 2 and r["this_call"]["corrected"]


@requires_db
def test_tool_sealed(fra):
    a, b = fra[0]["card_name"], fra[1]["card_name"]
    r1 = _call(set="FRA", cards=[b, a, b])
    r2 = _call(set="FRA", cards=[a, b, b])
    assert r1["pool_code"] == r2["pool_code"] and r1["cards_total"] == 3
    assert {p["count"] for p in r1["pool"]} == {1, 2}
    r3 = _call(code=r1["pool_code"], colors="W")
    assert r3["pool_code"] == r1["pool_code"]
    assert _call(code=r1["pool_code"], picks=_picks([a]))["error_kind"] == errors.OUT_OF_RANGE


# ─── ログと SQL の境界 ───

_SENTINEL_NAME = "Zzqx Sentinel Name"


@requires_db
def test_sql_receives_only_set_and_event(fra, monkeypatch):
    from sisho import db as dbmod
    from sisho.tools import pool
    seen = []
    real = dbmod._db

    def spy(sql, params, lane=dbmod.LANE_HEAVY):
        seen.append((sql, params))
        return real(sql, params, lane=lane)

    monkeypatch.setattr(pool, "_db", spy)
    a = fra[0]["card_name"]
    pool.draft_pool(set="FRA", picks=_picks([a, _SENTINEL_NAME]))
    pool.draft_pool(set=_SENTINEL_NAME, picks=_picks([a]))
    pool.draft_pool(code=f"SD1|{_SENTINEL_NAME}|1|00000000|x")
    assert any("limited_card_stats" in s and "mtg_cards_v2" in s for s, _ in seen), "一覧の SQL を捕まえている"
    flat = json.dumps([p for _, p in seen], ensure_ascii=False)
    assert _SENTINEL_NAME not in flat and a not in flat
    assert all(set(p) <= {"FRA", "PremierDraft"} or p == () for _, p in seen), seen


@requires_db
def test_logs_carry_no_names(fra, tmp_path, monkeypatch, caplog):
    from sisho import toollog
    from sisho.tools import pool
    log = tmp_path / "tools.log"
    monkeypatch.setattr(toollog, "TOOL_LOG", str(log))
    caplog.set_level(logging.DEBUG)
    a = fra[0]["card_name"]
    wrapped = toollog.observed(pool.draft_pool)
    c = json.loads(wrapped(set="FRA", picks=_picks([a])))["draft_code"]
    wrapped(set="FRA", picks=_picks([_SENTINEL_NAME]))
    wrapped(code=c + "x")

    def boom(*_a, **_k):
        raise RuntimeError(f"broken while handling {_SENTINEL_NAME}")

    monkeypatch.setattr(pool, "fetch_set_pool", boom)
    out = json.loads(wrapped(set="FRA", picks=_picks([a])))
    assert out["error_kind"] == errors.DB_ERROR and _SENTINEL_NAME not in out["error"]
    # getMessage() だけでは exc_info（トレースバック）を見落とす＝整形した caplog.text で見る
    text = log.read_text(encoding="utf-8") + caplog.text
    for bad in (_SENTINEL_NAME, a, c, "SD1|"):
        assert bad not in text
    assert "draft_pool\t{}" in text, "入口の行は引数を書かない"


@requires_db
def test_sdk_path_and_backstage_keep_names_out_of_logs(fra, tmp_path, monkeypatch, caplog):
    """SDK を通した呼び出し（引数検証で弾かれる回を含む）と backstage の幕でも、名前が記録に出ない。"""
    import asyncio

    import mcp_server as m
    from sisho import backstage, toollog
    log = tmp_path / "tools.log"
    monkeypatch.setattr(toollog, "TOOL_LOG", str(log))
    caplog.set_level(logging.DEBUG)
    a = fra[0]["card_name"]
    texts = []

    async def main():
        tools = await m.server.list_tools()
        t = next(x for x in tools if x.name == "draft_pool")
        assert t.input_schema["properties"]["correct"].get("type") == "boolean"
        r = await m.server.call_tool("draft_pool", {"set": "FRA", "picks": _picks([a])})
        assert json.loads(r.content[0].text)["picks"]["total"] == 1
        for bad in ({"set": "FRA", "picks": _SENTINEL_NAME},                       # 配列でない
                    {"set": "FRA", "picks": _picks([a]), "correct": _SENTINEL_NAME},  # 真偽でない
                    {"set": "FRA", "picks": [_SENTINEL_NAME]}):                     # 要素が辞書でない
            try:
                await m.server.call_tool("draft_pool", bad)
            except Exception as e:                       # SDK の引数検証（本文に入力の値が入る）
                texts.append(str(e))
            else:
                raise AssertionError("SDK の引数検証で弾かれるはず")
    asyncio.run(main())
    assert any(_SENTINEL_NAME in x for x in texts), "検証の本文には値が入る＝幕が要る"
    for x in texts:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": x if x.startswith("Error executing tool") else f"Error executing tool draft_pool: {x}"}],
            "isError": True}}).encode()
        out = backstage._rewrite_logged(body)
        assert _SENTINEL_NAME.encode() not in out, "客に返す本文からも値を落とす"
    text = log.read_text(encoding="utf-8") + caplog.text
    assert _SENTINEL_NAME not in text and a not in text


@requires_db
def test_adding_calls_stay_small_and_query_returns_details(fra):
    """picks を渡す呼び出しは大きさが変わらない物だけ。詳細はコードだけの照会で返る。"""
    random.seed(3)
    names = [random.choice(fra)["card_name"] for _ in range(42)]
    code, sizes = None, []
    for i, n in enumerate(names, 1):
        s = __import__("sisho.tools.pool", fromlist=["draft_pool"]).draft_pool(
            picks=_picks([n], i), **({"code": code} if code else {"set": "FRA"}))
        o = json.loads(s)
        code = o["draft_code"]
        sizes.append(len(s) - len(code))
        assert "pool" not in o and set(o["curve"]) == {"0", "1", "2", "3", "4", "5", "6", "7+"}
        assert o["this_call"]["added"][0]["pack"] == dc.pack_pick(i)["pack"]
    assert max(sizes) - min(sizes) < 200, "コードの長さを除けば、ピックが増えても大きさがほぼ変わらない"
    full = _call(code=code)
    assert len(full["pool"]) == 42 and "roles" in full and "colors" in full and "details" not in full


# ─── 入力の形・数えなかった面・コードの読み戻し・ログの水準の縫い留め ───

@requires_db
def test_presence_is_not_none_and_codes_always_round_trip(fra):
    """空の配列も「渡した」と見る。成功したコードはどれも読み戻せる。"""
    a = fra[0]["card_name"]
    sd = _call(set="FRA", picks=_picks([a]))["draft_code"]
    ss = _call(set="FRA", cards=[a])["pool_code"]
    for kw in ({"code": sd, "cards": []}, {"code": ss, "cards": []}, {"code": ss, "picks": []},
               {"set": "FRA", "cards": []}, {"set": "FRA", "picks": []}, {"code": sd, "picks": []}):
        r = _call(**kw)
        assert r.get("error_kind") == errors.OUT_OF_RANGE, (kw, r)
    for code in (sd, ss, _call(code=sd)["draft_code"], _call(code=ss)["pool_code"]):
        dc.decode(code)
    with pytest.raises(dc.CodeError):
        dc.encode(dc.SEALED, "FRA", [])


def test_fetch_set_pool_refuses_unmatched_and_duplicate_rows():
    """カード表と結べない行・同じカードの二行は、黙って消さず SetPoolError。引数はセット記号とイベントだけ。"""
    from sisho import limited
    good = ("Alpha",) + (None,) * (len(limited.SET_POOL_KEYS) - 1)
    unmatched = (None,) * len(limited.SET_POOL_KEYS)
    seen = []

    def fake(rows):
        def _db(sql, params, lane=None):
            seen.append(params)
            return rows
        return _db

    assert len(limited.fetch_set_pool("FRA", _db=fake([good]))) == 1
    for rows in ([good, unmatched], [unmatched], [good, good]):
        with pytest.raises(limited.SetPoolError) as e:
            limited.fetch_set_pool("FRA", _db=fake(rows))
        assert "Alpha" not in str(e.value)
    assert all(p == ("FRA", "PremierDraft") for p in seen)
    assert "LEFT JOIN" in limited.SET_POOL_SQL


def test_not_castable_keeps_other_faces():
    """区分がカーブの外でも、数えなかった面（準備呪文の呪文の面）を返す。"""
    prep = _row("Angel // Suture", "prepare", None, "Creature — Angel // Sorcery",
                [_face("{3}{W}", "Creature — Angel"), _face("{G/W}", "Sorcery")])
    r = pc.classify(prep, mc.parse_colors("G"))
    assert r["bucket"] == pc.NOT_CASTABLE
    assert r["other_faces"][0]["form"] == "呪文の面" and r["other_faces"][0]["castable"] == mc.TRUE
    assert r["other_faces"][0]["mana_value"] == 1


def test_prototype_missing_normal_cost_is_not_zero():
    """試作の通常のコストが空でも、0 マナの形にしない。"""
    both_missing = _row("P", "prototype", None, "Artifact Creature", oracle="x")
    assert pc.classify(both_missing, None)["bucket"] == pc.UNCLASSIFIED
    proto_only = _row("Q", "prototype", None, "Artifact Creature", oracle="Prototype {1}{G} — 2/2 (…)")
    r = pc.classify(proto_only, mc.parse_colors("U"))
    assert r["bucket"] == pc.UNCLASSIFIED, "試作で打てず通常のコストは欠け＝打てないと断定しない"
    assert r["other_faces"][0]["castable"] == mc.UNKNOWN
    r = pc.classify(proto_only, None)
    assert r["mana_value"] == 2 and pc.MARK_PROTOTYPE_NO_NORMAL in r["marks"]
    assert pc.classify(proto_only, mc.parse_colors("G"))["bucket"] == pc.CURVE, "試作で打てるならカーブ"
    with_normal = dict(proto_only, mana_cost="{10}")
    assert pc.classify(with_normal, mc.parse_colors("U"))["mana_value"] == 10, "対照: 通常のコストがあれば通常でカーブ"


def test_one_face_unknown_and_broken_faces():
    """三値の片方だけ unknown・面の配列の欠け。"""
    odd = _row("Snow // Plain", "split", None, "Instant // Instant",
               [_face("{S}", "Instant"), _face("{2}{U}", "Instant")])
    r = pc.classify(odd, None)
    assert r["bucket"] == pc.CURVE and r["mana_value"] == 3 and pc.MARK_OTHER_UNKNOWN in r["marks"]
    for faces in (None, [], [{"mana_cost": "{1}"}], ["x", "y"]):
        r = pc.classify(_row("Broken", "adventure", None, "Creature // Sorcery", faces), None)
        assert r["bucket"] == pc.UNCLASSIFIED and "データ欠け" in r["reason"]


@requires_db
def test_sealed_catalog_mismatch_guides_reimport(fra):
    """シールドの catalog_mismatch は correct ではなく、完全な cards の渡し直しを案内し、それで通る。"""
    names = sorted([fra[0]["card_name"], "Zzz Old Name"])
    head = ["SS1", "FRA", "2"]
    code = "|".join(head + [dc._crc(head + names)] + names)
    r = _call(code=code)
    assert r["reason"] == "catalog_mismatch" and "cards を code 無しで" in r["error"] and "correct" not in r["error"]
    assert _call(set="FRA", cards=[fra[0]["card_name"], fra[1]["card_name"]])["cards_total"] == 2


def test_header_fields_reject_trailing_newline():
    """欄は fullmatch。CRC を付け直しても、枚数・セット・版の末尾の改行は止める。"""
    for head in (["SD1", "FRA", "1\n"], ["SD1", "FRA\n", "1"], ["SD1\n", "FRA", "1"]):
        code = "|".join(head + [dc._crc(head + ["Alpha"]), "Alpha"])
        with pytest.raises(dc.CodeError):
            dc.decode(code)
    head = ["SD1", "FRA", "1"]
    code = "|".join(head + [dc._crc(head + ["Alpha"]) + "\n", "Alpha"])
    with pytest.raises(dc.CodeError):
        dc.decode(code)


def test_code_count_and_length_boundaries():
    names101 = ["A"] * 101
    head = ["SS1", "FRA", "101"]
    with pytest.raises(dc.CodeError) as e:
        dc.decode("|".join(head + [dc._crc(head + names101)] + names101))
    assert e.value.reason == dc.COUNT_OUT_OF_RANGE
    head0 = ["SD1", "FRA", "0"]
    with pytest.raises(dc.CodeError):
        dc.decode("|".join(head0 + [dc._crc(head0)]))
    with pytest.raises(dc.CodeError):
        dc.encode(dc.DRAFT, "FRA", ["x" * 201])
    near = ["n" * 141] * 42                      # 4+42 欄で 6,000 字の直前
    code = dc.encode(dc.DRAFT, "FRA", near)
    assert len(code) <= dc.MAX_CODE_LEN and dc.decode(code)[2] == near
    with pytest.raises(dc.CodeError):
        dc.encode(dc.DRAFT, "FRA", ["n" * 142] * 42)


def test_server_refuses_low_log_level():
    """INFO より下の水準では起動しない。"""
    from mcp_server import _uvicorn_kwargs
    assert _uvicorn_kwargs(8765, "info")["log_level"] == "info"
    for lv in ("debug", "trace", "DEBUG"):
        with pytest.raises(SystemExit):
            _uvicorn_kwargs(8765, lv)


@requires_db
def test_two_pools_interleaved_do_not_mix(fra):
    """状態を持たない: 二つのドラフトを交互に進めても混ざらない。"""
    a = [r["card_name"] for r in fra[:4]]
    b = [r["card_name"] for r in fra[10:14]]
    ca = cb = None
    for i in range(4):
        ra = _call(picks=_picks([a[i]], i + 1), **({"code": ca} if ca else {"set": "FRA"}))
        rb = _call(picks=_picks([b[i]], i + 1), **({"code": cb} if cb else {"set": "FRA"}))
        ca, cb = ra["draft_code"], rb["draft_code"]
    assert dc.decode(ca)[2] == a and dc.decode(cb)[2] == b



@requires_db
@pytest.mark.anyio
async def test_live_http_path_keeps_names_out_of_logs(fra, tmp_path, monkeypatch):
    """本物の HTTP（uvicorn・SDK の runner・backstage の幕）を通しても、記録にも客への本文にも番兵が出ない。

    本番に近い記録の作り: 根の水準は INFO（MCPServer の __init__ の basicConfig と同じ）・uvicorn は既定の log_config
    （uvicorn と uvicorn.access は自前の handler を持ち propagate しない）・見張りの handler は NOTSET で、
    根と uvicorn の記録係の両方に付ける（記録係が個別に DEBUG なら、根が INFO でも記録は作られる＝handler で落とさない）。
    呼び出しごとに、狙った経路に届いたこと（error_kind・幕の文・状態コード・本体の失敗の印）も確かめる。
    DEBUG にすると sse_starlette が幕の前の生の SSE の塊を記録して入力の値が残る（実測）＝起動時に水準を検査する理由。
    """
    import socket
    import threading
    import time

    import httpx
    import uvicorn
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server.transport_security import TransportSecuritySettings

    import mcp_server
    from sisho import toollog
    from sisho.backstage import BackstageASGI
    from sisho.tools import pool

    class _Keep(logging.Handler):
        def __init__(self):
            super().__init__(logging.NOTSET)
            self.lines = []
            self.fmt = logging.Formatter("%(name)s %(levelname)s %(message)s")

        def emit(self, record):
            self.lines.append(self.fmt.format(record))     # exc_info（トレースバック）も文字にする

    log = tmp_path / "tools.log"
    monkeypatch.setattr(toollog, "TOOL_LOG", str(log))
    root = logging.getLogger()
    old_root = root.level
    root.setLevel(logging.INFO)
    keep = _Keep()
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    app = BackstageASGI(mcp_server.server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False), host="127.0.0.1"))
    srv = uvicorn.Server(uvicorn.Config(app, **mcp_server._uvicorn_kwargs(port, "info")))   # 既定の log_config
    watched = [root, logging.getLogger("uvicorn"), logging.getLogger("uvicorn.access")]
    for lg in watched:
        lg.addHandler(keep)
    mcp_server._check_logger_levels()                       # 本番の起動と同じ条件で走らせる
    reached = []

    def boom(*_a, **_k):
        reached.append("body")
        raise RuntimeError(_SENTINEL_NAME)

    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)
    a = fra[0]["card_name"]
    bodies = []
    try:
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (r, w, *_):
            async with ClientSession(r, w) as s:
                await s.initialize()
                ok = await s.call_tool("draft_pool", {"set": "FRA", "picks": _picks([a])})
                code = json.loads(ok.content[0].text)["draft_code"]
                for bad in ({"set": "FRA", "picks": _SENTINEL_NAME},                        # SDK の引数検証
                            {"set": "FRA", "picks": _picks([a]), "correct": _SENTINEL_NAME},
                            {"set": "FRA", "picks": [_SENTINEL_NAME]}):
                    res = await s.call_tool("draft_pool", bad)
                    t = res.content[0].text
                    bodies.append(t)
                    assert res.is_error and t.startswith("入力が不正"), t[:80]   # 幕を通った（backstage の文）
                res = await s.call_tool("draft_pool", {"set": _SENTINEL_NAME, "picks": _picks([a])})
                bodies.append(res.content[0].text)
                assert json.loads(res.content[0].text)["error_kind"] == errors.UNKNOWN_OPTION
                res = await s.call_tool("draft_pool", {"code": code + _SENTINEL_NAME})
                bodies.append(res.content[0].text)
                assert json.loads(res.content[0].text)["error_kind"] == errors.INVALID_CODE
                monkeypatch.setattr(pool, "fetch_set_pool", boom)
                res = await s.call_tool("draft_pool", {"set": "FRA", "picks": _picks([a])})
                bodies.append(res.content[0].text)
                assert json.loads(res.content[0].text)["error_kind"] == errors.DB_ERROR and reached == ["body"]
        async with httpx.AsyncClient() as hc:              # 壊れた JSON
            resp = await hc.post(f"http://127.0.0.1:{port}/mcp",
                                 content=b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"draft_pool","arguments":{"set":"'
                                 + _SENTINEL_NAME.encode() + b'"',
                                 headers={"content-type": "application/json", "accept": "application/json, text/event-stream"})
            bodies.append(resp.text)
            assert resp.status_code == 400
    finally:
        srv.should_exit = True
        th.join(timeout=10)
        for lg in watched:
            lg.removeHandler(keep)
        root.setLevel(old_root)
    text = log.read_text(encoding="utf-8") + "\n".join(keep.lines)
    assert any(" INFO " in x for x in keep.lines), "見張りの handler に記録が届いている"
    for bad in (_SENTINEL_NAME, a, code):
        assert bad not in text, f"記録に {bad[:20]} が出た"
    assert all(_SENTINEL_NAME not in b for b in bodies), "客への本文にも入力の値を繰り返さない"
    assert "draft_pool\t{}" in text

def test_land_front_transform_and_town_lands_in_summary():
    """表が土地の変身も裏の面を返す。町の土地は集計でも土地のまま、出来事はカーブに足さない。"""
    tr = _row("Land // Creature", "transform", None, "Land // Creature — X",
              [_face(None, "Land"), _face(None, "Creature — X")], produced=["G"])
    r = pc.classify(tr, None)
    assert r["bucket"] == pc.LANDS and r["other_faces"][0]["form"] == "裏"
    town = _row("Town // Spell", "adventure", None, "Land — Town // Sorcery — Adventure",
                [_face(None, "Land — Town"), _face("{3}{W}{W}", "Sorcery — Adventure")], produced=["W"])
    bolt = _row("Bolt", "normal", "{R}", "Instant")
    pairs = [(x, pc.classify(x, None)) for x in (town, town, bolt, tr)]
    s = pc.summarize(pairs)
    assert s["buckets"] == {"curve": 1, "lands": 3, "not_castable": 0, "unclassified": 0, "total": 4}
    assert s["curve"]["5"] == 0 and s["curve"]["1"] == 1, "出来事の 5 マナはカーブに入れない"
    assert all(c.get("other_faces") for c in s["lands"]["cards"])


def test_logger_levels_are_checked_at_startup():
    """根が INFO でも、見張る記録係のどれかが DEBUG なら起動しない。"""
    import mcp_server
    mcp_server._check_logger_levels()
    lg = logging.getLogger("sse_starlette")
    old = lg.level
    try:
        lg.setLevel(logging.DEBUG)
        with pytest.raises(SystemExit):
            mcp_server._check_logger_levels()
    finally:
        lg.setLevel(old)

