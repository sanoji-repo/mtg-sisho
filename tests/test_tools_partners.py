#!/usr/bin/env python3
"""find_partner_cards（共起）の振る舞い（Step 4・2026-09-05）。

実デッキは毎晩増える＝数字は動く。黄金値は「行の書式」「scope ごとに空でない」
「不正な引数の断り方」「名前ゆれの吸収」の形で縫い、pct や lift の値では縫わない。

走らせ方: pytest tests/test_tools_partners.py -v
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402
import sisho.tools.partners as partners  # noqa: E402  monkeypatch はこちら側
from conftest import requires_db  # noqa: E402

pytestmark = requires_db

fp = m.find_partner_cards.fn if hasattr(m.find_partner_cards, "fn") else m.find_partner_cards

# 「《日本語名/英語名》: 12.3%（45 本同居・lift 6.7）」／日本語版なしの札は完成形が英語名（…）
ROW = re.compile(r"^(《[^》]+》|.+（(?:日本語版なし|日本語名未収録)）): [\d.]+%（\d+ 本同居・lift [\d.?]+）$")


def _rows(text):
    return [ln for ln in text.split("\n")[1:] if ln.strip()]


def test_name_variants_absorbs_two_faced_names():
    """両面・分割の表記ゆれを入口で吸収する（棚ごとにキーの持ち方が違うため）。"""
    assert partners._name_variants("Brazen Borrower") == [
        "Brazen Borrower", "Brazen Borrower // Petty Theft"], "表の名前 → 正式名を足す（DB 引き）"
    assert partners._name_variants("Brazen Borrower // Petty Theft") == [
        "Brazen Borrower // Petty Theft", "Brazen Borrower"], "正式名 → 表の名前を足す"
    assert partners._name_variants("Sol Ring") == ["Sol Ring"], "単面札は自分だけ"


def test_partners_edh_row_format():
    """既定 scope=edh の返り値は、見出し＋『完成形: pct%（n 本同居・lift x）』の行。"""
    r = fp("Sol Ring", "edh", 3)
    head, rows = r.split("\n")[0], _rows(r)
    assert "scope=edh・order_by=count" in head, "何で並べたかを見出しで言う"
    assert "完成形《日本語名/英語名》を一字も変えず" in head, "名前の掟が返り値に自己完結して載る"
    assert 1 <= len(rows) <= 3, f"limit どおり（{len(rows)} 行）"
    for ln in rows:
        assert ROW.match(ln), f"行の書式: {ln}"
    assert rows[0].startswith("《統率の塔/Command Tower》"), "EDH の Sol Ring の相方の筆頭は統率の塔（定番・順位が入れ替わったら気づく）"


def test_partners_constructed_and_other_scopes():
    """scope ごとに別の棚（deck_list.source）を見る＝構築でも空でない。"""
    for scope in ("constructed", "vintage", "precon", "commander"):
        r = fp("Sol Ring" if scope != "constructed" else "Lightning Bolt", scope, 2)
        assert "共起なし" not in r and "不正" not in r, f"scope={scope} で空でない"
        assert f"scope={scope}" in r.split("\n")[0], "見出しに scope を書く"
        for ln in _rows(r):
            assert ROW.match(ln), f"{scope}: {ln}"


def test_partners_rejects_bad_arguments():
    """不正な scope・order_by は、DB へ行かずに断りの文言で返す。"""
    assert fp("Sol Ring", "nope", 3) == "scope が不正: nope（edh/constructed/pauper/vintage/precon）"
    assert fp("Sol Ring", "edh", 3, False, "nope") == "order_by が不正: nope（count / lift）"


def test_partners_unknown_card():
    r = fp("Zzzqqq Xxxyyy", "edh", 3)
    assert r.startswith("共起なし: Zzzqqq Xxxyyy"), "無い札は共起なし"
    assert "英語の正式カード名" in r, "引き方を返り値に載せる"


def test_partners_limit_bounds():
    """limit は 1〜30 に丸める。"""
    assert len(_rows(fp("Sol Ring", "edh", 999))) == 30, "上限 30"
    assert len(_rows(fp("Sol Ring", "edh", 0))) == 1, "0 は 1 に上げる"


def test_partners_exclude_lands():
    """exclude_lands=True で土地を落とす（汎用フェッチが上位を占めるのを避ける口）。"""
    with_lands = fp("Lightning Bolt", "constructed", 5)
    no_lands = fp("Lightning Bolt", "constructed", 5, True)
    assert "《乾燥台地/Arid Mesa》" in with_lands, "既定では土地が上位に来る"
    for name in ("乾燥台地", "沸騰する小湖", "山/Mountain"):
        assert name not in no_lands, f"exclude_lands で土地が消える（{name}）"
    assert len(_rows(no_lands)) == 5, "土地を抜いても本数は埋まる"


def test_partners_order_by_lift():
    """order_by='lift' は lift の降順（同居数順とは別の並び）。"""
    r = fp("Sol Ring", "edh", 5, False, "lift")
    assert "order_by=lift" in r.split("\n")[0]
    lifts = [float(x) for x in re.findall(r"lift ([\d.]+)）", r)]
    assert lifts == sorted(lifts, reverse=True), f"lift の降順: {lifts}"
    assert _rows(r) != _rows(fp("Sol Ring", "edh", 5)), "同居数順とは並びが違う"


def test_partners_two_faced_card_name():
    """表の名前で呼んでも共起が引ける（EDH 棚は正式名キー・構築棚は表の名前キー）。"""
    for name in ("Brazen Borrower", "Brazen Borrower // Petty Theft"):
        for scope in ("edh", "constructed"):
            r = fp(name, scope, 2)
            assert "共起なし" not in r, f"{name} / {scope} が空振りしない（2026-08-13 の実測穴）"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
