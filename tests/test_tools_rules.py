#!/usr/bin/env python3
"""lookup_mtg_rule・get_card_rulings の振る舞い。

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
    assert "該当なし" not in r and _numbers(r), "AND 不発なら OR で拾う（クライアントのバグ報告）"


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
    """面の名前（出来事の呪文側）で引いても、正式名の裁定に辿り着く。

    解決は names.resolve_card_ex に一本化（partners と同じ関数・正式名 → 表面名 → 裏面名）。
    """
    r = gr("Petty Theft", 3)
    assert "裁定なし" not in r, "裏面の名前でも空で帰さない"
    assert r.startswith("Brazen Borrower // Petty Theft（"), f"正式名に解決して引く: {r[:60]}"


def test_rulings_partial_match_fallback():
    """完全一致も面の名前も外れたら、カード表の部分一致で救う。候補がちょうど 1 枚のときだけ、その 1 枚だと明示して引く。
    2 枚以上なら選ばせる（以前は裁定の表を部分一致で引き、「Ragavan」で当たった最初のカードの裁定を返していた＝
    《ラシュミとラガバン/Rashmi and Ragavan》もあるのに 1 枚に決めつけていた）。"""
    # 「Nimble Pilferer」は別の本物のカード《敏捷なこそ泥/Nimble Pilferer》の名前ちょうど＝部分一致ではなくそのカードになる
    r = gr("Ragavan, Nimble", 2)
    assert r.startswith("「Ragavan, Nimble」は 《") and "Ragavan, Nimble Pilferer（" in r, r[:200]
    r = gr("Ragavan", 2)
    assert r.startswith("裁定なし: Ragavan（この名前ちょうどのカードは無い。名前に含むカード: "), r[:200]


def test_rulings_never_slide_to_another_cards_rulings():
    """名前ちょうどのカードが在るなら、裁定が 0 件でも別カードの裁定へ滑らない。
    「Fog」は以前《目つぶしの霧/Blinding Fog》の裁定を黙って返していた（道具ログは ok＝見張りにも出なかった）。
    LIKE の記号（_ と %）は字として扱う＝「R_gavan」「Ragavan%」で Ragavan の裁定を返さない。"""
    r = gr("Fog", 3)
    assert r.startswith("裁定なし: 《") and "/Fog》" in r and "Blinding" not in r, r[:200]
    for name in ("R_gavan", "Ragavan%"):
        r = gr(name, 3)
        assert r.startswith(f"裁定なし: {name}（カードが見つかりません"), r[:200]


def test_rulings_back_face_partial_names_the_official_name():
    """裏面の一部で当たったときは、表面の完成形だけでなく正式名も見せる。"""
    r = gr("Petty The", 1)
    assert "（正式名 Brazen Borrower // Petty Theft）" in r and "Brazen Borrower // Petty Theft（" in r, r[:200]


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


def test_empty_query_is_refused_not_answered_with_anything():
    """空の検索語に「それらしい行」を返さない（別モデルのレビューで指摘）。

    最終フォールバックが ILIKE '%%' になるため、空文字でも任意の条文・裁定が
    正常な結果として返っていた。利用者から見ると「何を引いたか分からない答え」で、
    掟「不在は NULL・番兵禁止」と同じ筋（無いものを無いと言う）。
    """
    for bad in ("", "   "):
        r = lr(bad)
        assert "[用語集" not in r and "[規則" not in r, f"空の検索語に条文を返した: {r[:90]}"
        assert "検索語" in r, f"何が足りないかを言うべき: {r[:90]}"
        r2 = gr(bad)
        assert "（20" not in r2, f"空の検索語に裁定を返した: {r2[:90]}"
        assert "カード名" in r2, f"何が足りないかを言うべき: {r2[:90]}"


def test_rulings_known_card_without_rulings_is_not_blamed_on_the_name():
    """裁定が 0 件でもカードが在るなら、名前を疑わせず『収録済み・公式裁定がまだ無い』を返す。
    実例は Shang-Chi（実デッキ 869 本・裁定 0 件）。今その状態のカードを DB から 1 枚選ぶ。"""
    row = rules._db(
        "SELECT m.card_name FROM mtg_cards_v2 m WHERE NOT m.digital AND m.card_name NOT LIKE '%%//%%'"
        " AND NOT EXISTS (SELECT 1 FROM card_rulings r WHERE r.card_name ILIKE '%%' || m.card_name || '%%')"
        " ORDER BY m.id LIMIT 1", ())
    assert row, "裁定の無いカードが 1 枚はある"
    r = gr(row[0][0], 5)
    assert r.startswith("裁定なし: "), r
    assert "収録済み" in r and "lookup_mtg_rule" in r, r
    assert "英語の正式カード名" not in r, "在るカードの名前を疑わせない"



def test_rulings_partial_name_lists_candidates():
    """「Shang-Chi」のような一部の名前は、候補を完成形で並べる。"""
    r = gr("Shang-Chi", 5)
    assert r.startswith("裁定なし: Shang-Chi（この名前ちょうどのカードは無い。名前に含むカード: 《"), r
    assert "Shang-Chi, Master of Kung Fu》" in r and "Shang-Chi, Martial Mentor》" in r, r



def test_rulings_names_only_in_the_rulings_table_still_work():
    """裁定の表を入力そのままで先に引く。裁定の表の名前のうち 732 種（次元・アンセット等）は
    カード表に無い＝カード表を先に見ると「見つかりません」や別カードへ滑った（The Maelstrom →《アラーラへの侵攻》の裁定）。"""
    for name in ("Rules Lawyer", "The Maelstrom", "Raven's Run"):
        r = gr(name, 1)
        assert r.startswith(name + "（"), r[:150]
    r = gr("rules lawyer", 1)
    assert r.startswith("「rules lawyer」は Rules Lawyer として引いた:") and "Rules Lawyer（" in r, r[:150]


def test_rulings_case_only_difference_resolves():
    """大文字小文字だけ違う正式名は、補正したと明示して引く（「brainstorm」は裏面名 Brainstorm の別カードと
    部分一致で並んで候補の列挙に落ちていた）。"""
    r = gr("brainstorm", 1)
    assert r.startswith("「brainstorm」は 《") and "\nBrainstorm（" in r.replace("\n\n", "\n"), r[:200]


def test_rulings_ambiguous_back_face_is_not_decided_by_id():
    """裏面の名前が複数のカードで重なるときは 1 枚に決めない（「Vicious Verse」は 3 枚）。
    《稲妻/Lightning Bolt》は裁定が 0 件でも、裏面が Lightning Bolt の別カードの裁定へ滑らない（以前の版は滑っていた）。"""
    r = gr("Vicious Verse", 1)
    assert r.startswith("裁定なし: Vicious Verse（この名前は複数のカードの面の名前で、1 枚に決まらない: "), r[:200]
    assert r.count(" // Vicious Verse") >= 2, r
    r = gr("Lightning Bolt", 1)
    assert "Emeritus" not in r, r[:200]


def test_rulings_digital_only_candidate_is_labeled():
    """デジタル専用の候補には印を付ける。"""
    r = gr("A-Falcon Abominatio", 1)
    assert "（デジタル専用）" in r, r[:200]


def test_rulings_partial_names_include_names_only_in_the_rulings_table():
    """部分一致の候補は裁定の表にだけある名前（次元・アンセット等）も含める。
    カード表だけを見ると「Rules Law」が見つからず、「Talon Gat」は別カード《マダラの鉤爪門》の裁定に決まっていた。
    候補が 2 つ以上なら決めずに並べる。大文字小文字を直したデジタルカードも印を残す。"""
    r = gr("Rules Law", 1)
    assert r.startswith("「Rules Law」は Rules Lawyer として引いた:") and "Rules Lawyer（" in r, r[:150]
    r = gr("Talon Gat", 1)
    assert r.startswith("裁定なし: Talon Gat（この名前ちょうどのカードは無い。") and "Talon Gates＝" in r, r[:200]
    assert "（デジタル専用）" in gr("a-falcon abomination", 1)


def test_rulings_only_names_are_not_hidden_behind_many_card_candidates():
    """カード表の候補が上限（5 件）以上あっても、裁定の表にだけある名前を分けて案内する。
    「Gavon」はカード表に Gavony ○○ が 7 件あり、裁定の表にだけある Gavony（次元）が消えていた。
    「Goldmeado」はカード表がちょうど 5 件で、足した Goldmeadow が 6 番目として表示から隠れていた。"""
    for name, only in (("Gavon", "Gavony"), ("Goldmeado", "Goldmeadow")):
        r = gr(name, 1)
        assert "裁定の表にだけある名前（次元・アンセット等）: " + only + "＝" in r, r[-200:]
