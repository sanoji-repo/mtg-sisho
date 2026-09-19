#!/usr/bin/env python3
"""mtg_probability と SQL 関数 mtg_* の試験。

黄金値は math.comb の独立実装（LLM でも DB でもない第三の計算）。
注意: mcp_server.mtg_probability は内部で PostgreSQL の mtg_* 関数を _db() で
実行するため、計算結果の検証には DB 接続が必要です。DB 未接続環境では計算テストは
skip され、引数検証（test_probability_errors）のみ実行されます。
走らせ方: pytest tests/test_probability.py -v
"""
import json
import os
import sys
from math import comb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m
from conftest import requires_db


def atleast(n, k, d, mm):
    """黄金値計算用: 超幾何分布で d 枚引いて k 枚中 mm 枚以上引く確率"""
    return sum(comb(k, i) * comb(n - k, d - i) for i in range(mm, min(k, d) + 1)) / comb(n, d)


f = m.mtg_probability.fn if hasattr(m.mtg_probability, "fn") else m.mtg_probability


def call(**kw):
    return json.loads(f(**kw))


@requires_db
def test_probability_at_least():
    r = call(kind="at_least", deck_size=60, copies=4, draws=7, at_least=1)
    assert abs(r["probability"] - round(atleast(60, 4, 7, 1), 4)) < 1e-4 and r["percent"] == "40.0%", (
        f"at_least 60/4/7/≥1 = {r['probability']}（黄金 0.3995）"
    )


@requires_db
def test_probability_by_turn():
    # 先手 3T
    r = call(kind="by_turn", deck_size=60, copies=4, turn=3, on_play=True)
    assert r["cards_seen"] == 9 and abs(r["probability"] - round(atleast(60, 4, 9, 1), 4)) < 1e-4, (
        f"by_turn 先手 3T: 見る枚数 9・{r['probability']}"
    )

    # 後手は 1 枚多く見る＝確率が上がる
    r2 = call(kind="by_turn", deck_size=60, copies=4, turn=3, on_play=False)
    assert r2["cards_seen"] == 10 and r2["probability"] > r["probability"], "後手は 1 枚多く見る＝確率が上がる"

    # マリガン 1 回は見る枚数 −1
    r3 = call(kind="by_turn", deck_size=60, copies=4, turn=3, on_play=True, mulligans=1)
    assert r3["cards_seen"] == 8 and r3["probability"] < r["probability"], "マリガン 1 回は見る枚数 −1"


@requires_db
def test_probability_land_drops():
    # 先手 60/24 4T
    r = call(kind="land_drops", deck_size=60, copies=24, turn=4, on_play=True)
    assert abs(r["probability"] - round(atleast(60, 24, 10, 4), 4)) < 1e-4, (
        f"land_drops 60/24 先手 4T = {r['probability']}（黄金 {round(atleast(60,24,10,4),4)}）"
    )

    # 後手 40/17 3T
    r = call(kind="land_drops", deck_size=40, copies=17, turn=3, on_play=False)
    assert abs(r["probability"] - round(atleast(40, 17, 10, 3), 4)) < 1e-4, (
        f"land_drops 40/17 後手 3T = {r['probability']}"
    )


@requires_db
def test_probability_combo_by_turn():
    r = call(kind="combo_by_turn", deck_size=60, copies=4, copies_b=4, turn=4, on_play=True)
    gold = 1 - (comb(56, 10) + comb(56, 10) - comb(52, 10)) / comb(60, 10)
    assert abs(r["probability"] - round(gold, 4)) < 1e-4, f"combo 4+4 先手 4T = {r['probability']}（黄金 {round(gold,4)}）"


@requires_db
def test_probability_color_sources():
    r = call(kind="by_turn", deck_size=60, copies=12, turn=2, on_play=True, at_least=2)
    assert abs(r["probability"] - round(atleast(60, 12, 8, 2), 4)) < 1e-4, (
        f"色ソース 12 枚で 2T に 2 つ = {r['probability']}"
    )


def test_probability_errors():
    assert "error" in call(kind="foo"), "不明な kind は error"
    assert "error" in call(kind="at_least", deck_size=60, copies=70), "範囲外は error"
    # error_kind: 「一覧に無い値」と「範囲外」を名前で分ける
    from sisho import errors
    assert call(kind="foo")["error_kind"] == "unknown_option"
    assert call(kind="at_least", deck_size=60, copies=70)["error_kind"] == "out_of_range"
    assert {"unknown_option", "out_of_range"} <= set(errors.KINDS), "一覧に無い名前を返さない"
    assert "error" in call(kind="combo_by_turn", deck_size=60, copies=40, copies_b=30, turn=2), (
        "A+B > deck は定義不能の error"
    )


@requires_db
def test_probability_return_schema():
    r = call(kind="by_turn")
    assert all(k in r for k in ("formula", "premise", "cards_seen", "percent", "note")), (
        "返り値は式・前提・見る枚数・％・注記を持つ"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
