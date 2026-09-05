#!/usr/bin/env python3
"""verify_answer（答案検査）の振る舞い（Step 4・2026-09-05）。

この道具は「脳が最後に一回通す門」＝答えの文字列そのものを書き換える。
だから縫うのは**修正版の全文**（部分一致でなく等値）と、未確認として挙がる名前。
カード名の対（日本語/英語）は DB の生成列から来るので、名前が変わったら落ちてよい。

走らせ方: pytest tests/test_tools_verify.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402
import sisho.tools.verify as verify  # noqa: E402  monkeypatch はこちら側
from conftest import requires_db  # noqa: E402

pytestmark = requires_db

v = m.verify_answer.fn if hasattr(m.verify_answer, "fn") else m.verify_answer

MARK = "---- 修正版（未確認ゼロならこのまま使う） ----\n"


def _fixed(text):
    """返り値のうち『修正版』の全文。"""
    return v(text).split(MARK, 1)[1]


def _unknown(text):
    """未確認として挙がった名前と候補の行。"""
    head = v(text).split(MARK, 1)[0]
    return [ln.strip()[2:] for ln in head.split("\n") if ln.startswith("  - ")]


def test_verify_bracketed_english_becomes_full_form():
    """《英語名》→ 完成形《日本語名/英語名》。"""
    assert _fixed("《Lightning Bolt》は強い。") == "《稲妻/Lightning Bolt》は強い。"
    assert "未確認の名前: なし" in v("《Lightning Bolt》は強い。"), "DB にある名前は未確認に挙げない"


def test_verify_bracketed_japanese_becomes_full_form():
    """《日本語名》→ 完成形。"""
    assert _fixed("《稲妻》は強い。") == "《稲妻/Lightning Bolt》は強い。"


def test_verify_bare_english_name():
    """裸の英語名（日本語名あり）→ 完成形。前後の語は壊さない。"""
    assert _fixed("I like Lightning Bolt a lot.") == "I like 《稲妻/Lightning Bolt》 a lot."


def test_verify_bare_japanese_name():
    """裸の日本語名（4 文字以上）→ 完成形。"""
    assert _fixed("太陽の指輪を入れる。") == "《太陽の指輪/Sol Ring》を入れる。"


def test_verify_full_form_is_left_alone():
    """すでに完成形なら 1 文字も触らない（何度通しても同じ＝冪等）。"""
    s = "《稲妻/Lightning Bolt》は強い。"
    assert _fixed(s) == s
    assert _fixed(_fixed(s)) == s, "二度通しても増殖しない"
    assert "機械修正 0 箇所" in v(s)


def test_verify_unknown_name_is_listed_with_candidates():
    """DB に無い名前は未確認として挙げ、修正版では書き換えない（脳に引き直させる）。"""
    unknown = _unknown("《精鋼の魔女》は強い。")
    assert unknown == ["《精鋼の魔女》 → 候補: （近い名前なし・日本語版なしなら英語名のまま）"], unknown
    assert _fixed("《精鋼の魔女》は強い。") == "《精鋼の魔女》は強い。", "勝手に別のカードへ直さない"

    unknown = _unknown("《いなずま/Lightning Bolt》")
    assert unknown == ["《いなずま/Lightning Bolt》 → 候補: 《稲妻/Lightning Bolt》"], (
        "英語半分が正しければ、正しい完成形が第一候補")


def test_verify_wrong_face_pairing_is_caught():
    """2026-08-31 の実例（面の対の誤接合）が再発しないこと。

    《厚かましい借り手/Petty Theft》は「表面の日本語名 ＋ 裏面の英語名」の接合＝誤り。
    正しい対は《些細な盗み/Petty Theft》。
    """
    bad = "《厚かましい借り手/Petty Theft》で戻す。"
    assert _unknown(bad) == ["《厚かましい借り手/Petty Theft》 → 候補: 《些細な盗み/Petty Theft》"]
    assert _fixed(bad) == bad, "未確認は機械では直さない（脳に直させる）"

    assert _fixed("《Petty Theft》で戻す。") == "《些細な盗み/Petty Theft》で戻す。", (
        "裏面の名前は、その面の対で完成形にする（表の日本語名と接がない）")
    assert _fixed("《Brazen Borrower》で殴る。") == "《厚かましい借り手/Brazen Borrower》で殴る。", (
        "表面の名前は表面の対")


def test_verify_card_without_japanese_stays_english():
    """日本語版が無い札は英語名のまま（訳名を作らない・中の語も拾わない）。"""
    assert _fixed("Helm of Obedience is good.") == "Helm of Obedience is good."
    assert _fixed("Volcanic Island を置く。") == "Volcanic Island を置く。", (
        "日本語版なしの名前は保護域＝中の Island を拾わない")
    assert _fixed("Volcanic Island と Island。") == "Volcanic Island と 《島/Island》。", (
        "保護は名前の内側だけ＝外の Island は直す")


def test_verify_protects_italics_and_parentheses():
    """*斜体*（アーキタイプ名）の中と（）の中は触らない。"""
    assert _fixed("*太陽の指輪デッキ* は速い。太陽の指輪を入れる。") == (
        "*太陽の指輪デッキ* は速い。《太陽の指輪/Sol Ring》を入れる。")
    assert _fixed("（太陽の指輪）と Lightning Bolt。") == "（太陽の指輪）と 《稲妻/Lightning Bolt》。"
    assert _fixed("*Lightning Bolt deck* は速い。Lightning Bolt を入れる。") == (
        "*Lightning Bolt deck* は速い。《稲妻/Lightning Bolt》 を入れる。")


def test_verify_english_with_japanese_gloss():
    """『英語名（日本語名）』型（英語主・日本語添え）は完成形に畳む。"""
    assert _fixed("Black Lotus（ブラック・ロータス）は強い。") == "《ブラック・ロータス/Black Lotus》は強い。"


def test_verify_removes_duplicate_english_gloss():
    """完成形の直後に残った（英語名）の重複は削る。"""
    assert _fixed("《稲妻/Lightning Bolt》（Lightning Bolt）は強い。") == "《稲妻/Lightning Bolt》は強い。"


def test_verify_collapses_double_brackets():
    """《《X》》の二重囲みは 1 重に戻す。

    注意（Step 4 で気づいた実装の癖・直していない）: 二重囲みは《》の中身の抽出で
    『《稲妻』（開き括弧つき）として拾われるため、同じ答案が「未確認 1 件」としても
    報告される。ここでは修正版が 1 重に戻ることだけを縫う。
    """
    assert _fixed("《《稲妻》》は強い。") == "《稲妻》は強い。"


def test_verify_report_shape():
    """返り値の骨格（未確認の行・機械修正の件数・修正版の札）は自己完結している。"""
    r = v("《Lightning Bolt》と《精鋼の魔女》。")
    assert "未確認の名前 1 件" in r and "search_mtg_cards で引いて正式名に直して" in r, "直し方まで返り値に載せる"
    assert "機械修正 " in r and MARK in r, "件数と修正版の札"


def test_verify_no_cards_is_passthrough():
    """カード名が 1 つも無い文は素通り（余計な書き換えをしない）。"""
    s = "今日は天気がいいですね。"
    assert _fixed(s) == s
    assert "未確認の名前: なし" in v(s)


def test_verify_name_cache_is_reused(monkeypatch):
    """名前表は 1 時間キャッシュ＝答案ごとに 3 万行を引き直さない。"""
    verify._names()                     # 温めておく
    calls = []
    real = verify._db

    def spy(sql, params):
        calls.append(sql)
        return real(sql, params)

    monkeypatch.setattr(verify, "_db", spy)
    v("《稲妻》は強い。")
    assert not any("FROM mtg_cards_v2" in c and "similarity" not in c for c in calls), (
        f"名前表の再取得が走っている: {calls}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
