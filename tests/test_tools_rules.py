#!/usr/bin/env python3
"""lookup_mtg_rule・get_card_rulings の振る舞い（Step 4・2026-09-05）。

総合ルールと公式裁定は追記されていく（版が上がる・裁定が増える）ので、
黄金値は「この条番号が入る／入らない」「タグの形」「件数の上限」で縫い、
件数の完全一致では縫わない。

走らせ方: pytest tests/test_tools_rules.py -v
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402
import sisho.tools.rules as rules  # noqa: E402  monkeypatch はこちら側
from conftest import requires_db  # noqa: E402

pytestmark = requires_db

lr = m.lookup_mtg_rule.fn if hasattr(m.lookup_mtg_rule, "fn") else m.lookup_mtg_rule
gr = m.get_card_rulings.fn if hasattr(m.get_card_rulings, "fn") else m.get_card_rulings


def _numbers(text):
    return re.findall(r"^\[(?:条文|用語集) ([^\]]+)\]", text, re.M)


def test_lookup_rule_by_number_takes_self_and_direct_children():
    """条番号は「自身と直下の枝」だけ。前方一致の巻き添え（702.19 → 702.190）を出さない。"""
    nums = _numbers(lr("702.19", 30))
    assert nums[0] == "702.19", "自身が先頭"
    assert "702.19a" in nums and "702.19b" in nums, "直下の英字枝は入る"
    assert not any(n.startswith("702.190") or n.startswith("702.191") for n in nums), (
        f"別条文（702.190 以降）を巻き込まない: {nums}")


def test_lookup_rule_by_number_leaf_and_trailing_dot():
    """枝そのもの（601.2b）は 1 件・末尾のピリオドは無視する。"""
    assert _numbers(lr("601.2b", 10)) == ["601.2b"], "枝を名指ししたら 1 件だけ"
    assert _numbers(lr("601.2b.", 10)) == ["601.2b"], "末尾のピリオドを剥がして同じ結果"
    nums = _numbers(lr("601.2", 40))
    assert nums[0] == "601.2" and "601.2a" in nums, "親を引くと自身＋枝"


def test_lookup_rule_by_word_and_glossary_tag():
    """語で引くと条文が当たり、用語集は [用語集 …] のタグで区別できる。"""
    r = lr("trample", 10)
    assert "[条文 702.19" in r, "trample はトランプルの条文に当たる"
    assert re.search(r"^\[条文 ", r, re.M), "条文のタグ"

    g = lr("Deathtouch", 10)
    assert "[用語集 Deathtouch]" in g, "用語集の見出しは語そのものが条番号の位置に入る"


def test_lookup_rule_multiword_falls_back_to_or():
    """概念の羅列（全語 AND が不発になる問い）でも空で帰さない（OR に降りる）。"""
    r = lr("dies trigger simultaneous", 5)
    assert "該当なし" not in r and _numbers(r), "AND 不発なら OR で拾う（2026-08-11 のクライアントのバグ報告）"


def test_lookup_rule_limit_bounds():
    """limit は 1〜30 に丸める。"""
    assert len(_numbers(lr("702", 999))) == 30, "上限 30"
    assert len(_numbers(lr("trample", 0))) == 1, "0 は 1 に上げる"
    assert len(_numbers(lr("702.19", 2))) == 2, "指定どおり"


def test_lookup_rule_not_found():
    r = lr("zzzqqqxxx", 5)
    assert r.startswith("該当なし: zzzqqqxxx"), "無い語は該当なし"
    assert "条番号または英語キーワード" in r, "引き方を返り値に載せる"


def test_rulings_exact_name():
    """英語の正式名で引くと、そのカードの裁定が「名前（日付）: 本文」で並ぶ。"""
    r = gr("Ragavan, Nimble Pilferer", 3)
    lines = r.split("\n\n")
    assert len(lines) == 3, "limit どおり"
    for ln in lines:
        assert ln.startswith("Ragavan, Nimble Pilferer（"), f"行頭は card_name（日付）: {ln[:40]}"
        assert re.match(r"^[^（]+（\d{4}-\d{2}-\d{2}）: ", ln), f"日付の書式: {ln[:60]}"


def test_rulings_face_name_resolves_to_official_name():
    """面の名前（出来事の呪文側）で引いても、正式名の裁定に辿り着く（2026-08-31 R3-4）。

    注意: この解決は partners.py の _name_variants ではなく、rules.py が
    name_en_front／name_en_back を直接引いてやっている（実装を見て確認・重複した経路）。
    """
    r = gr("Petty Theft", 3)
    assert "裁定なし" not in r, "裏面の名前でも空で帰さない"
    assert r.startswith("Brazen Borrower // Petty Theft（"), f"正式名に解決して引く: {r[:60]}"


def test_rulings_partial_match_fallback():
    """完全一致も面の名前も外れたら部分一致で救う。"""
    r = gr("Ragavan", 2)
    assert "裁定なし" not in r and "Ragavan" in r, "部分一致の救済"


def test_rulings_limit_bounds():
    """limit は 1〜40 に丸める。"""
    assert len(gr("Ragavan, Nimble Pilferer", 0).split("\n\n")) == 1, "0 は 1 に上げる"
    assert len(gr("Ragavan, Nimble Pilferer", 999).split("\n\n")) <= 40, "上限 40"


def test_rulings_not_found():
    r = gr("zzzqqqxxx", 5)
    assert r.startswith("裁定なし: zzzqqqxxx"), "無いカードは裁定なし"
    assert "英語の正式カード名" in r, "引き方を返り値に載せる"


def test_rules_tools_do_not_write(monkeypatch):
    """2 本とも読み取りだけ（SELECT 以外の SQL を投げない）。"""
    seen = []
    real = rules._db

    def spy(sql, params, lane=None):
        seen.append(sql.strip().split(None, 1)[0].upper())
        return real(sql, params) if lane is None else real(sql, params, lane=lane)

    monkeypatch.setattr(rules, "_db", spy)
    lr("trample", 3)
    gr("Petty Theft", 3)
    assert seen and set(seen) == {"SELECT"}, f"投げた SQL の先頭語: {set(seen)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
