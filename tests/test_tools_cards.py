#!/usr/bin/env python3
"""search_mtg_cards の振る舞い（Step 4・2026-09-05）。

契約試験（tests/test_tool_contract.py）が縫うのは「名前・説明・署名」まで。
ここは**返り値の中身**を縫う＝この先の配置替えで道具の答えが静かに変わったら落ちる網。

黄金値の置き方: データは日々増えるので件数の完全一致では縫わず、
「この鍵がある」「この名前が先頭」「上限を超えない」といった**形**で縫う。
draft_set の解決そのものは tests/test_draft_set.py が縫っているので、ここでは重ねない
（ここで見るのは同伴した統計の鍵の形）。

走らせ方: pytest tests/test_tools_cards.py -v
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402
import sisho.tools.cards as cards  # noqa: E402  monkeypatch はこちら側（mcp_server の再輸出は束縛の写し）
from conftest import requires_db  # noqa: E402

pytestmark = requires_db

f = m.search_mtg_cards.fn if hasattr(m.search_mtg_cards, "fn") else m.search_mtg_cards


def _j(*a, **k):
    return json.loads(f(*a, **k))


def test_search_by_english_name():
    """英語名で引くと、その札が先頭に来て、返り値の外枠の鍵が揃う。"""
    d = _j("Lightning Bolt", None, 3)
    assert set(d) >= {"route", "naming_rule", "cards"}, f"外枠の鍵（{list(d)}）"
    assert d["route"] == "simple_match", "名前が当たる語は素の一致（曖昧名に落ちない）"
    assert d["cards"][0]["card_name"] == "Lightning Bolt", "完全一致が先頭"
    assert d["cards"][0]["name_display"] == "《稲妻/Lightning Bolt》", "完成形は《日本語名/英語名》"
    assert "name_display" in d["naming_rule"], "名前の掟が返り値に自己完結して載る"


def test_search_by_japanese_name():
    """日本語名でも同じ札に当たる（脳が日本語で引く経路）。"""
    d = _j("稲妻", None, 3)
    assert d["cards"][0]["card_name"] == "Lightning Bolt", "『稲妻』→ Lightning Bolt が先頭"
    assert all("card_name" in c for c in d["cards"]), "各札に card_name"


def test_search_partial_and_multiword_and():
    """部分一致で拾い、空白区切りは AND（全語がどこかに載る札だけ）。"""
    d = _j("Lotus", None, 5)
    assert len(d["cards"]) >= 1, "部分一致で 1 件以上"
    assert any("Lotus" in c["card_name"] for c in d["cards"]), "名前に部分一致した札が混じる"

    d = _j("飛行 吸血鬼", None, 5)
    assert len(d["cards"]) >= 1, "『飛行 吸血鬼』は 1 件以上"
    for c in d["cards"]:
        blob = json.dumps(c, ensure_ascii=False)
        assert "飛行" in blob and "吸血鬼" in blob, f"全語が載る（AND）: {c['card_name']}"


def test_search_format_filter():
    """format 指定はそのフォーマットで合法な札だけに絞る（不合法な札は消える）。"""
    assert "該当なし" in f("Ragavan, Nimble Pilferer", "standard", 5), (
        "Ragavan はスタンダード不合法＝絞ると残らない（曖昧名の救済も同じ絞りを通る）")
    d = _j("Ragavan, Nimble Pilferer", "modern", 5)
    assert d["cards"][0]["card_name"] == "Ragavan, Nimble Pilferer", "モダンでは合法＝出る"

    names = [c["card_name"] for c in _j("Sol Ring", "standard", 10)["cards"]]
    assert "Sol Ring" not in names, "Sol Ring 自身はスタンダード不合法＝本文一致の側にも出さない"


def test_search_top_k_bounds():
    """top_k は 1〜20 に丸める（上も下も）。"""
    assert len(_j("Island", None, 999)["cards"]) <= 20, "上限 20"
    assert len(_j("Island", None, 0)["cards"]) == 1, "0 は 1 に上げる"
    assert len(_j("Island", None, -5)["cards"]) == 1, "負も 1 に上げる"
    assert len(_j("Island", None, 3)["cards"]) <= 3, "指定どおりの本数以下"


def test_search_name_display_without_japanese():
    """日本語版が無い札は「英語名（日本語版なし）」＋ japanese_name を null で明示。"""
    c = _j("Helm of Obedience", None, 3)["cards"][0]
    assert c["card_name"] == "Helm of Obedience"
    assert c["name_display"] == "Helm of Obedience（日本語版なし）", "完成形は日本語版なしの表記"
    assert "japanese_name" in c and c["japanese_name"] is None, (
        "不在を無言にしない＝鍵を残して None（脳が『無い』と読める）")
    assert "日本語版なし" in c["name_note"], "訳名を作らせない注記が付く"


def test_search_two_faced_card_faces():
    """両面・出来事の札は faces を同伴し、裏面で当たったらその面の完成形も返す。"""
    c = _j("Petty Theft", None, 3)["cards"][0]
    assert c["card_name"] == "Brazen Borrower // Petty Theft"
    assert c["name_display"] == "《厚かましい借り手/Brazen Borrower》", "name_display は表面固定"
    assert [x["en"] for x in c["faces"]] == ["Brazen Borrower", "Petty Theft"], "faces は表→裏"
    assert [x["display"] for x in c["faces"]] == [
        "《厚かましい借り手/Brazen Borrower》", "《些細な盗み/Petty Theft》"], "面ごとの完成形（表の日本語名と裏の英語名を接がない・2026-08-31）"
    assert c["matched_face"] == "back" and c["face_display"] == "《些細な盗み/Petty Theft》", (
        "裏面で当たったことと、その面の完成形")

    front = _j("Brazen Borrower", None, 3)["cards"][0]
    assert "matched_face" not in front, "表面で当たったときは matched_face を出さない"
    assert len(front["faces"]) == 2, "faces は表で当たっても付く"


def test_search_empty_and_no_hit():
    """空の検索語と一致ゼロは、道を示す文字列（JSON でない）で返る。"""
    assert f("   ", None, 5) == "検索語が空です。"
    r = f("zzzqqqxxxyyy", None, 5)
    assert r.startswith("該当なし: zzzqqqxxxyyy"), "一致ゼロは該当なし"
    assert "query_mtg_database" in r, "次の一手（SQL を書く）を返り値に載せる"


def test_search_fuzzy_route_on_typo():
    """一致ゼロのときだけ曖昧名（pg_trgm）で救い、route でそれを申告する。"""
    d = _j("孤光のフェニックス", None, 3)      # 正しくは弧光
    assert d["route"] == "fuzzy_name", "救済経路は route に出る"
    assert d["cards"][0]["card_name"] == "Arclight Phoenix", "打ち間違いから正しい札"


def test_search_digital_only_with_arena_format():
    """既定は紙（digital は出ない）・Arena の形式を名指ししたときだけ Arena 専用札。"""
    names = [c["card_name"] for c in _j("A-Acererak the Archlich", None, 5)["cards"]]
    assert "A-Acererak the Archlich" not in names, "既定は WHERE NOT digital"

    d = _j("A-Acererak the Archlich", "historic", 5)
    c = d["cards"][0]
    assert c["card_name"] == "A-Acererak the Archlich", "historic なら Arena 専用札も出る"
    assert c["digital"] is True and "紙には存在しない" in c["digital_note"], "Arena 専用の札だと返り値で言う"


def test_search_limited_stats_shape():
    """draft_set を指定したときの 17Lands 同伴の鍵の形（値でなく形と出典）。"""
    d = _j("Vicious Rivalry", None, 1, "SOS")
    st = d["cards"][0]["limited_stats"]
    assert len(st) == 1 and st[0]["set"] == "SOS", "指定したセットの分だけ"
    assert set(st[0]) >= {"set", "event", "gih_games", "gih_wr", "oh_wr", "gd_wr", "alsa", "ata", "source"}, (
        f"統計の鍵（{list(st[0])}）")
    assert st[0]["source"] == "17Lands", "出典を行ごとに持つ"
    assert "17Lands" in d["limited_stats_note"], "読み方の注記が同伴する"

    a = d["limited_archetypes"]["SOS"]
    assert set(a) == {"baseline_wr", "color_pairs"}, f"色の組み合わせ表の鍵（{list(a)}）"
    assert set(a["color_pairs"][0]) >= {"colors", "games", "share_pct", "wr", "source"}
    assert len(a["color_pairs"][0]["colors"]) == 2, "2 色（タッチ無し）だけ"


def test_search_limited_stats_elsewhere():
    """指定外のセットにしか統計が無い札は、数字でなく道しるべ（記号だけ）で返す。"""
    d = _j("Lightning Bolt", None, 3)
    assert "limited_stats_elsewhere" in d, "別セットの統計は道しるべに留める"
    assert isinstance(d["limited_stats_elsewhere"], dict)
    assert all(isinstance(v, list) for v in d["limited_stats_elsewhere"].values()), "値は記号の一覧"
    assert "draft_set" in d["limited_stats_elsewhere_note"], "呼び直し方を書く"


def test_search_survives_missing_limited_tables(monkeypatch):
    """17Lands の表が無い環境（旧 VM 等）でも検索そのものは落ちない。"""
    real = cards._db

    def no_limited(sql, params):
        if "limited_" in sql:
            raise RuntimeError("relation does not exist")
        return real(sql, params)

    monkeypatch.setattr(cards, "_db", no_limited)      # 中身側を差し替える（再輸出では届かない）
    d = _j("Lightning Bolt", None, 2)
    assert d["cards"][0]["card_name"] == "Lightning Bolt", "統計が引けなくてもカードは返る"
    assert "limited_stats" not in d["cards"][0], "無い統計の鍵は出さない"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
