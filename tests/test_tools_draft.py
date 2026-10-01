#!/usr/bin/env python3
"""draft_pack_stats の名前の候補。

ChatGPT のクイックドラフト（WOE）で、画像から読んだ名前のかけらがセットの中で拾えなかった
（「いたずら屋」にセットの外の 2 枚を出し、WOE の《錠前破りのいたずら屋/Picklock Prankster》を出せない）。
セットの中の候補を先に出すこと・候補は未確認のまま置き換えないことを確かめる。
_pool_score は DB に触らない。draft_pack_stats の試験は DB（読み取り）に触る。
走らせ方: pytest tests/test_tools_draft.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from conftest import requires_db
from sisho.tools import draft as d


def test_pool_score_ranks():
    """空白と記号を落として同じ＞名前の一部＞かけらの割合＞似かた。関係の無い名前は下限 0.5 に届かない。"""
    greta = ["Greta, Sweettooth Scourge", "甘歯村の断罪人、グレタ"]
    assert d._pool_score("Greta Sweettooth Scourge", greta) == 1.0
    assert d._pool_score("いたずら屋", ["Picklock Prankster", "錠前破りのいたずら屋"]) == 0.9
    assert d._pool_score("錠前破りのいたずら屋です", ["錠前破りのいたずら屋"]) == 0.85
    assert d._pool_score("眠り いざない", ["Sleep-Cursed Faerie", "眠り呪いのフェアリー"]) >= 0.65
    assert d._pool_score("あいうえおかきくけこ", ["Picklock Prankster", "錠前破りのいたずら屋"]) < d._POOL_MIN_SCORE
    assert d._pool_score("", ["何か"]) == 0.0
    assert d._pool_score("x", []) == 0.0


def test_pool_score_short_and_word_boundaries():
    """2026-10-02 Codex のレビュー: 短い入力は名前の一部のときだけ弱い候補・英語は単語の切れ目で見る。"""
    assert d._pool_score("火花", ["Bitterblossom", "苦花"]) == 0.0, "1 字だけ共通の短い名前を拾わない"
    assert d._pool_score("火花", ["Sparkspitter", "火花吐き"]) == 0.6, "短い入力は名前の一部でも弱い候補どまり"
    assert d._pool_score("the", ["Hearth Elemental", "かまどの精"]) == 0.0, "単語の途中には当たらない"
    assert d._pool_score("the", ["The Irencrag", "アイレンクラッグ"]) == 0.6
    assert d._pool_score("Sweettooth Scourge", ["Greta, Sweettooth Scourge"]) == 0.9, "単語の並びとしての一部"
    assert d._pool_score("tooth Scourge", ["Greta, Sweettooth Scourge"]) < d._POOL_STRONG, "語の途中から始まる一部は強くない"
    assert d._pool_score("Lightnig Bolt", ["Stonesplitter Bolt", "石断ちの稲妻"]) < d._POOL_STRONG


@pytest.fixture(scope="module")
def pack():
    import mcp_server as m
    f = m.draft_pack_stats.fn if hasattr(m.draft_pack_stats, "fn") else m.draft_pack_stats
    return f


def _line_of(out: str, key: str) -> str:
    return next(ln for ln in out.splitlines() if f"「{key}」" in ln)


@requires_db
def test_draft_pack_stats_candidates_come_from_the_set(pack):
    out = pack(["甘歯村へようこそ", "いたずら屋", "Greta Sweettooth Scourge", "あいうえおかきくけこ"], "WOE")
    head = out.split("完全一致しなかった名前")[0]
    assert "《甘歯村へようこそ/Welcome to Sweettooth》 | uncommon" in head, "完全一致は表に載る"
    assert "《錠前破りのいたずら屋/Picklock Prankster》" not in head, "候補は表に載せない（置き換えない）"
    ln = _line_of(out, "いたずら屋")
    assert "候補 《錠前破りのいたずら屋/Picklock Prankster》" in ln, "セットの中の本物が先頭の候補"
    assert "いたずらっ子、モモ" not in ln and "Trickster Mage" not in ln, "セットの外のカードを出さない"
    assert "候補 《甘歯村の断罪人、グレタ/Greta, Sweettooth Scourge》" in _line_of(out, "Greta Sweettooth Scourge")
    assert "DB のどのカード名にも一致しない" in _line_of(out, "あいうえおかきくけこ"), "候補を無理に作らない"


@requires_db
def test_draft_pack_stats_weak_in_set_also_shows_outside(pack):
    """セットの中に強い候補が無ければ DB 全体も見て、セットの外の近い名前を別に添える。同点で切ったことは隠さない。"""
    out = pack(["Lightnig Bolt", "the", "火花"], "WOE")
    ln = _line_of(out, "Lightnig Bolt")
    assert "WOE の外の近い名前 《稲妻/Lightning Bolt》" in ln
    ln = _line_of(out, "the")
    assert "Hearth Elemental" not in ln and "絞り切れない" in ln
    ln = _line_of(out, "火花")
    assert "苦花" not in ln and "Bitterblossom" not in ln
    head = out.split("完全一致しなかった名前")[0]
    assert "Lightning Bolt" not in head, "セットの外の候補も表に載せない"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
