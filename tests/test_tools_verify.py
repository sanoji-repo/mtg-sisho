#!/usr/bin/env python3
"""verify_answer（答案検査）の振る舞い（Step 4・2026-09-05）。

この道具は「クライアントが最後に一回通す検査」＝答えの文字列そのものを書き換える。
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
    """裸の日本語名は **報告だけ**（2026-09-15 に振る舞いを変えた）。

    以前は 4 文字以上の日本語カード名 31,011 語を無条件に完成形へ書き換えていたが、
    カード名と同形の一般語（漢字 4〜5 字だけで 1,169 語）まで書き換えて
    「存在しないカードに言及した答案」を作る事故が実戦で出た。機械では決められないので
    判断を答案側に返す。同じ答案が《》でカードとして言及済みなら直す（下の試験）。
    """
    r = v("太陽の指輪を入れる。")
    assert "裸のカード名 1 件" in r and "《太陽の指輪/Sol Ring》" in r
    assert _fixed("太陽の指輪を入れる。") == "太陽の指輪を入れる。", "本文は書き換えない"


def test_verify_full_form_is_left_alone():
    """すでに完成形なら 1 文字も触らない（何度通しても同じ＝冪等）。"""
    s = "《稲妻/Lightning Bolt》は強い。"
    assert _fixed(s) == s
    assert _fixed(_fixed(s)) == s, "二度通しても増殖しない"
    assert "機械修正 0 箇所" in v(s)


def test_verify_unknown_name_is_listed_with_candidates():
    """DB に無い名前は未確認として挙げ、修正版では書き換えない（クライアントに引き直させる）。"""
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
    assert _fixed(bad) == bad, "未確認は機械では直さない（クライアントに直させる）"

    assert _fixed("《Petty Theft》で戻す。") == "《些細な盗み/Petty Theft》で戻す。", (
        "裏面の名前は、その面の対で完成形にする（表の日本語名と接がない）")
    assert _fixed("《Brazen Borrower》で殴る。") == "《厚かましい借り手/Brazen Borrower》で殴る。", (
        "表面の名前は表面の対")


def test_verify_card_without_japanese_stays_english():
    """日本語版が無いカードは英語名のまま（訳名を作らない・中の語も拾わない）。"""
    assert _fixed("Helm of Obedience is good.") == "Helm of Obedience is good."
    assert _fixed("Volcanic Island を置く。") == "Volcanic Island を置く。", (
        "日本語版なしの名前は保護域＝中の Island を拾わない")
    assert _fixed("Volcanic Island と Island。") == "Volcanic Island と 《島/Island》。", (
        "保護は名前の内側だけ＝外の Island は直す")


def test_verify_protects_italics_and_parentheses():
    """*斜体*（アーキタイプ名）の中と（）の中は触らない——書き換えでも報告でも。"""
    assert _fixed("（太陽の指輪）と Lightning Bolt。") == "（太陽の指輪）と 《稲妻/Lightning Bolt》。"
    assert _fixed("*Lightning Bolt deck* は速い。Lightning Bolt を入れる。") == (
        "*Lightning Bolt deck* は速い。《稲妻/Lightning Bolt》 を入れる。")
    # 既出の日本語名を直すときも保護域は避ける（2 度目の言及だけが完成形になる）
    assert _fixed("《Sol Ring》。*太陽の指輪デッキ* は速い。太陽の指輪を入れる。") == (
        "《太陽の指輪/Sol Ring》。*太陽の指輪デッキ* は速い。《太陽の指輪/Sol Ring》を入れる。")
    # 保護域の中にしか無い語は報告もしない（斜体のアーキタイプ名を毎回指摘しない）
    assert "裸のカード名" not in v("*太陽の指輪デッキ* が速い。")


def test_verify_english_with_japanese_gloss():
    """『英語名（日本語名）』型（英語主・日本語添え）は完成形に畳む。"""
    assert _fixed("Black Lotus（ブラック・ロータス）は強い。") == "《ブラック・ロータス/Black Lotus》は強い。"


def test_verify_removes_duplicate_english_gloss():
    """完成形の直後に残った（英語名）の重複は削る。"""
    assert _fixed("《稲妻/Lightning Bolt》（Lightning Bolt）は強い。") == "《稲妻/Lightning Bolt》は強い。"


def test_verify_collapses_double_brackets():
    """《《X》》の二重囲みは照合の前に 1 重へ畳み、完成形まで上げる（Step 5 修正 1）。

    Step 4 まで: 抽出の正規表現 [^》]+ が開き括弧を中身に含めて『《稲妻』を拾い、
    (1)「未確認 1 件」と誤報し (2) 修正版も《稲妻》止まりで完成形に上がらなかった。
    ここで縫うのは「未確認ゼロ・完成形に上がる・畳んだことを返り値で言う」の三点。
    """
    r = v("《《稲妻》》は強い。")
    assert r.split(MARK, 1)[1] == "《稲妻/Lightning Bolt》は強い。", "二重囲みでも完成形まで上がる"
    assert "未確認の名前: なし" in r, "二重囲みを未確認として誤報しない"
    assert "二重の囲み《《…》》を 1 箇所" in r, "何をしたか（畳んだこと）を返り値に書く"

    r = v("《《Lightning Bolt》》は強い。")
    assert r.split(MARK, 1)[1] == "《稲妻/Lightning Bolt》は強い。", "英語名の二重囲みも同じ"
    assert "未確認の名前: なし" in r


def test_verify_collapses_triple_brackets():
    """三重《《《X》》》も畳んで完成形へ（畳みは収束するまで回す）。"""
    r = v("《《《稲妻》》》は強い。")
    assert r.split(MARK, 1)[1] == "《稲妻/Lightning Bolt》は強い。"
    assert "未確認の名前: なし" in r
    assert "二重の囲み《《…》》を 2 箇所" in r, "三重は 2 箇所ぶん畳む"


def test_verify_double_bracketed_unknown_name_is_reported_singly():
    """DB に無い名前が二重囲みでも、未確認の行は 1 重の名前で挙げる（開き括弧を混ぜない）。"""
    assert _unknown("《《精鋼の魔女》》は強い。") == [
        "《精鋼の魔女》 → 候補: （近い名前なし・日本語版なしなら英語名のまま）"]
    assert _fixed("《《精鋼の魔女》》は強い。") == "《精鋼の魔女》は強い。", (
        "未確認は畳むだけで、別のカードへ直さない（誤発動＝有害）")


def test_verify_stray_open_bracket_does_not_swallow_the_name():
    """迷子の《（対になっていない開き括弧）が、後ろの名前を飲み込まない（Step 5 修正 1 の抽出側）。

    抽出が `《([^》]+)》` だと『の話。《稲妻』（前の迷子の《から）を 1 つの名前として拾い、
    未確認 1 件と誤報したうえ完成形にも上げない。`[^《》]+` にすると直前の《から拾う。
    """
    r = v("記号《の話。《稲妻》は強い。")
    assert r.split(MARK, 1)[1] == "記号《の話。《稲妻/Lightning Bolt》は強い。"
    assert "未確認の名前: なし" in r


def test_verify_report_shape():
    """返り値の骨格（未確認の行・機械修正の件数・修正版のカード）は自己完結している。"""
    r = v("《Lightning Bolt》と《精鋼の魔女》。")
    assert "未確認の名前 1 件" in r and "search_mtg_cards で引いて正式名に直して" in r, "直し方まで返り値に載せる"
    assert "機械修正 " in r and MARK in r, "件数と修正版のカード"


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


# ─── マナ・コストの照合（2026-09-12・claude.ai のドラフト補助で 3 マナを 2 マナと言った事故を止める検査）────

def _head(text):
    return v(text).split(MARK, 1)[0]


def test_mana_mismatch_is_listed_and_text_untouched():
    """《…》の近くの「N マナ」が DB の cmc と違えば食い違いに挙げる。修正版の本文は変えない。"""
    t = "《Kutzil, Malamet Exemplar》は 2 マナで軽い。"
    head = _head(t)
    assert "マナ・コストの食い違い 1 件" in head
    assert "{1}{G}{W}" in head and "3 マナ" in head and "答案は 2 マナ" in head
    assert _fixed(t) == "《マラメトの模範、クチル/Kutzil, Malamet Exemplar》は 2 マナで軽い。"


def test_mana_match_is_counted_not_listed():
    t = "《Kutzil, Malamet Exemplar》は 3 マナ。"
    head = _head(t)
    assert "食い違い" not in head and "マナ・コストの照合: 1 件一致" in head


def test_mana_reduction_clause_is_note_not_mismatch():
    """軽減条項のあるカードは判定せず条項を添える（Quicksand Whirlpool {5}{W}・タップ状態を対象なら {3} 軽減）。"""
    t = "《Quicksand Whirlpool》は軽減後 4 マナで撃てる。"
    head = _head(t)
    assert "食い違い" not in head
    assert "マナ・コストの注意 1 件" in head and "less to cast" in head and "{5}{W}" in head


def test_mana_symbols_are_compared():
    t = "《Kutzil, Malamet Exemplar》のコストは {1}{G}{W}。"
    assert "マナ・コストの照合: 1 件一致" in _head(t)
    assert "食い違い 1 件" in _head("《Kutzil, Malamet Exemplar》のコストは {G}{W}。")


def test_mana_x_spell_is_not_judged():
    assert "マナ・コスト" not in _head("《Walking Ballista》は 2 マナ。")


def test_no_mana_claim_adds_nothing():
    assert "マナ・コスト" not in _head("《Lightning Bolt》は強い。")


def test_mana_claim_attributes_to_nearest_name():
    """2 枚が同じ文にあっても、数字は近い方の名前に付く。"""
    t = "《Lightning Bolt》は 1 マナ、《Kutzil, Malamet Exemplar》は 3 マナ。"
    head = _head(t)
    assert "食い違い" not in head and "2 件一致" in head


# ─── 誤検知の修理（2026-09-15・別セッションのクイックドラフト実戦で 7 パターンが挙がった）────
# どれも「本文の数字は正しいのに照合の紐付けだけが誤る」型＝答案側が直しようのない誤報なので、
# 掟「誤発動＝有害・取り逃し＝無害」に従って誤発動をゼロにする側へ倒す。

def test_mana_claim_does_not_cross_a_newline():
    """(a) 箇条書きの直後の行にある数字を、リスト最終行のカードに紐付けない。

    実戦の形: 17Lands の一覧を出した直後に「4 マナのインスタントで…」と書くと、
    最終行のカードと照合されて食い違いが出ていた（右窓が改行を越えていた）。
    """
    t = "候補:\n- 《Lightning Bolt》: GIH WR 58.2%\n4 マナのインスタントなら色を足す価値がある。"
    assert "マナ・コスト" not in _head(t)


def test_mana_curve_table_does_not_borrow_the_next_label():
    """(b) カーブ表でラベルとカード名が交互に並ぶとき、次の行のラベルを拾わない。

    実戦では 1 つの答案で 4 件まとめて出た型。ラベル→カード名→ラベル…の構造上必ず踏む。
    """
    t = "1 マナ: 《Lightning Bolt》\n3 マナ: 《Kutzil, Malamet Exemplar》"
    head = _head(t)
    assert "食い違い" not in head and "2 件一致" in head


def test_mana_range_matches_either_end():
    """(d) 「N から M マナ」の範囲表記は、どちらかに一致すれば食い違いにしない。"""
    assert "食い違い" not in _head("《Lightning Bolt》は 1 から 2 マナの帯。")
    assert "食い違い" not in _head("《Lightning Bolt》は 0 〜 1 マナで撃てる。")
    # 範囲のどちらにも当たらなければ従来どおり食い違い
    assert "食い違い 1 件" in _head("《Lightning Bolt》は 4 から 5 マナ。")


def test_mana_symbol_respects_reduction_clause():
    """(2) 記号 {N} 側にも軽減の判定を効かせる（マナ側にはあったが記号側に無かった）。

    実戦の形: 軽減量を波括弧で書くと、それをカードのマナ・コストとして照合していた。
    同じ答案に「軽減条項があるので判定しない」という注意も出て、判定側と注意側が
    同じ数値を別々に解釈している状態だった。
    """
    head = _head("《Quicksand Whirlpool》は条件を満たすとコストが {3} 少なくなる。")
    assert "食い違い" not in head
    assert "マナ・コストの注意 1 件" in head


def test_bare_japanese_word_is_not_turned_into_a_card():
    """(4) 答案に一度も出ていないカード名と同じ一般語を、勝手に完成形へ書き換えない。

    実戦の形: 報告書の見出しに使った普通の名詞が《…/…》に化けた。誤検知の中で最も危険で、
    気づかずに修正版を採用すると「存在しないカードに言及した答案」が出来上がる。
    対象の裸の日本語名は 31,011 語あり、漢字 4〜5 字の一般語だけで 1,169 語が入っていた。
    """
    for word in ("決定的瞬間", "環境科学者", "応用幾何学"):
        t = f"## {word}\n\nこの節では{word}について述べる。"
        assert _fixed(t) == t, f"「{word}」がカード名に化けた"


def test_bare_japanese_name_is_fixed_when_already_quoted():
    """裸の日本語名の修正そのものは残す＝同じ答案に完成形で出ているカードの 2 度目の言及は直す。

    掟「2 回目以降の言及も毎回完成形」に対応する。答案側が《》で書いた＝カードとして
    言及した宣言があるので、ここで直すのは誤発動にならない。
    """
    t = "《Lightning Bolt》は軽い。稲妻をもう一枚積みたい。"
    assert _fixed(t) == "《稲妻/Lightning Bolt》は軽い。《稲妻/Lightning Bolt》をもう一枚積みたい。"


def test_unknown_name_candidates_are_not_noise():
    """(5) 候補の類似度が低すぎると、ひな形の語に実在カードを勧めてしまう。

    実戦では説明用に書いた《カード名》に「カー砦」が候補として出た（類似度 0.286）。
    正当な誤字は「氷巻きの偵察」→「水巻きの偵察」0.400・「太陽の指輪」→「太陽の指環」0.500
    なので、その間（0.35）に線を引く。候補が消えても未確認の報告は残る。
    """
    r = v("書き方の例: 《カード名》のように書く。")
    assert "未確認の名前 1 件" in r, "DB に無い名前の報告自体は残す"
    assert "カー砦" not in r, "類似度の低い候補は勧めない"
    # 1 文字違いの本物の誤字には候補が出る
    assert "水巻きの偵察" in v("《氷巻きの偵察》を取った。")
