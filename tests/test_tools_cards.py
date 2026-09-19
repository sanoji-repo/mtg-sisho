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
    """英語名で引くと、そのカードが先頭に来て、返り値の外枠の鍵が揃う。"""
    d = _j("Lightning Bolt", None, 3)
    assert set(d) >= {"route", "naming_rule", "cards"}, f"外枠の鍵（{list(d)}）"
    assert d["route"] == "simple_match", "名前が当たる語は素の一致（曖昧名に落ちない）"
    assert d["cards"][0]["card_name"] == "Lightning Bolt", "完全一致が先頭"
    assert d["cards"][0]["name_display"] == "《稲妻/Lightning Bolt》", "完成形は《日本語名/英語名》"
    assert "name_display" in d["naming_rule"], "名前の掟が返り値に自己完結して載る"


def test_search_by_japanese_name():
    """日本語名でも同じカードに当たる（クライアントが日本語で引く経路）。"""
    d = _j("稲妻", None, 3)
    assert d["cards"][0]["card_name"] == "Lightning Bolt", "『稲妻』→ Lightning Bolt が先頭"
    assert all("card_name" in c for c in d["cards"]), "各カードに card_name"


def test_search_partial_and_multiword_and():
    """部分一致で拾い、空白区切りは AND（全語がどこかに載るカードだけ）。"""
    d = _j("Lotus", None, 5)
    assert len(d["cards"]) >= 1, "部分一致で 1 件以上"
    assert any("Lotus" in c["card_name"] for c in d["cards"]), "名前に部分一致したカードが混じる"

    d = _j("飛行 吸血鬼", None, 5)
    assert len(d["cards"]) >= 1, "『飛行 吸血鬼』は 1 件以上"
    for c in d["cards"]:
        blob = json.dumps(c, ensure_ascii=False)
        assert "飛行" in blob and "吸血鬼" in blob, f"全語が載る（AND）: {c['card_name']}"


def test_search_format_filter():
    """format 指定はそのフォーマットで合法なカードだけに絞る（不合法なカードは消える）。"""
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
    """日本語版が無いカードは「英語名（日本語版なし）」＋ japanese_name を null で明示。"""
    c = _j("Helm of Obedience", None, 3)["cards"][0]
    assert c["card_name"] == "Helm of Obedience"
    assert c["name_display"] == "Helm of Obedience（日本語版なし）", "完成形は日本語版なしの表記"
    assert "japanese_name" in c and c["japanese_name"] is None, (
        "不在を無言にしない＝鍵を残して None（クライアントが『無い』と読める）")
    assert "日本語版なし" in c["name_note"], "訳名を作らせない注記が付く"


def test_search_two_faced_card_faces():
    """両面・出来事のカードは faces を同伴し、裏面で当たったらその面の完成形も返す。"""
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
    """空の検索語と一致ゼロは、error＋error_kind の JSON で返る（Step 6 作業 3 で素の文字列から統一）。"""
    d = json.loads(f("   ", None, 5))
    assert d["error_kind"] == "empty_query" and d["error"].startswith("検索語が空です"), d
    assert "query" in d["error"] and "呼び直す" in d["error"], f"次に何を試すかを言う（{d['error']}）"
    assert "cards" not in d, "検索していない"

    d = json.loads(f("zzzqqqxxxyyy", None, 5))
    assert d["error_kind"] == "no_match", "『入力が不正』でなく『該当なし』（分けて名付ける）"
    assert d["error"].startswith("該当なし: zzzqqqxxxyyy"), d
    assert "query_mtg_database" in d["error"], "次の一手（SQL を書く）を返り値に載せる"


def test_search_error_kinds_are_registered():
    """search が返す error_kind は 4 種類とも sisho/errors.py の一覧にある名前。"""
    from sisho import errors
    kinds = {json.loads(f(*a))["error_kind"] for a in
             (("   ", None, 5), ("zzzqqqxxxyyy", None, 5),
              ("Lightning Bolt", "xyz", 3), ("Lightning Bolt", "standardbrwl", 3))}
    assert kinds == {"empty_query", "no_match", "unknown_format", "ambiguous_format"}, kinds
    assert kinds <= set(errors.KINDS), "一覧に無い名前を返さない"


def test_search_fuzzy_route_on_typo():
    """一致ゼロのときだけ曖昧名（pg_trgm）で救い、route でそれを申告する。"""
    d = _j("孤光のフェニックス", None, 3)      # 正しくは弧光
    assert d["route"] == "fuzzy_name", "救済経路は route に出る"
    assert d["cards"][0]["card_name"] == "Arclight Phoenix", "打ち間違いから正しいカード"


def test_search_digital_only_with_arena_format():
    """既定は紙（digital は出ない）・Arena の形式を名指ししたときだけ Arena 専用カード。"""
    names = [c["card_name"] for c in _j("A-Acererak the Archlich", None, 5)["cards"]]
    assert "A-Acererak the Archlich" not in names, "既定は WHERE NOT digital"

    d = _j("A-Acererak the Archlich", "historic", 5)
    c = d["cards"][0]
    assert c["card_name"] == "A-Acererak the Archlich", "historic なら Arena 専用カードも出る"
    assert c["digital"] is True and "紙には存在しない" in c["digital_note"], "Arena 専用のカードだと返り値で言う"


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
    """指定外のセットにしか統計が無いカードは、数字でなく道しるべ（記号だけ）で返す。"""
    d = _j("Lightning Bolt", None, 3)
    assert "limited_stats_elsewhere" in d, "別セットの統計は道しるべに留める"
    assert isinstance(d["limited_stats_elsewhere"], dict)
    assert all(isinstance(v, list) for v in d["limited_stats_elsewhere"].values()), "値は記号の一覧"
    assert "draft_set" in d["limited_stats_elsewhere_note"], "呼び直し方を書く"


def test_search_valid_formats_covers_arena_formats():
    """legalities の鍵の一覧を実測で採り、Arena の形式の集合と突き合わせる。

    2026-09-05 実測: explorer だけ legalities に鍵が無く、ARENA_FORMATS に入ったままだった。
    2026-09-14 に集合から外した（_check_format が先に弾くので cards.py の ARENA_FORMATS の
    枝には到達しない＝死んだ語）。鍵が増える分には落とさず、ARENA_FORMATS に legalities へ
    無い語が足されたら落ちる置き方にする。
    """
    valid = cards._valid_formats()
    assert {"standard", "modern", "commander", "pauper"} <= set(valid), f"主要な鍵（{valid}）"
    # 全行展開をやめて先頭 100 行から採るようにしたので、一覧が痩せたら気づく
    assert len(valid) >= 20, f"鍵の一覧が痩せている（{len(valid)} 個・標本の行数が足りない?）"
    assert not (set(cards.ARENA_FORMATS) - set(valid)), (
        f"Arena の形式はすべて legalities の鍵にある（欠け: {set(cards.ARENA_FORMATS) - set(valid)}）")


def test_search_unknown_format_unique_candidate_is_corrected():
    """打ち間違いの format は、候補が一意なら直して検索し、直したことを返り値に必ず書く（Step 5 修正 4）。"""
    d = _j("Lightning Bolt", "modrn", 3)
    assert d["cards"][0]["card_name"] == "Lightning Bolt", "modern として検索できている"
    note = d["format_note"]
    assert "modrn" in note and "modern" in note, f"何をしたかを返り値に書く（{note}）"
    assert "legalities" in note and "standard" in note, "有効な鍵の一覧も添える"

    assert "format_note" not in _j("Lightning Bolt", "modern", 3), "正しい鍵のときは注記を出さない"
    assert "format_note" not in _j("Lightning Bolt", None, 3), "format 無しでも出さない"


def test_search_unknown_format_ambiguous_is_not_guessed():
    """近い鍵が複数あるときは検索せず選ばせる（誤って別のフォーマットで引くのが最悪）。"""
    d = _j("Lightning Bolt", "standardbrwl", 3)
    assert "cards" not in d, "推測して検索しない"
    assert "standardbrwl" in d["error"] and "standardbrawl" in d["error"] and "standard" in d["error"], (
        f"候補を並べて選ばせる（{d['error']}）")
    assert "modern" in d["valid_formats"] and "standard" in d["valid_formats"], "有効な鍵の一覧を返す"


def test_search_unknown_format_without_candidate_lists_valid():
    """何にも似ていない format は検索せず一覧を返す（『鍵が無い』と『合法なカードが無い』を区別する）。"""
    d = _j("Lightning Bolt", "xyz", 3)
    assert "cards" not in d and "xyz" in d["error"]
    assert "standard" in d["valid_formats"]

    for other in ("edh", "explorer", "limited"):     # 別のフォーマットの名前は勝手に読み替えない
        d = _j("Lightning Bolt", other, 3)
        assert "error" in d and "cards" not in d, f"{other} を近い鍵（predh 等）に読み替えない: {d}"


def test_search_format_note_is_kept_when_zero_hits():
    """解釈し直した format は、結果が 0 件でも返り値に載せる（クライアントが『鍵が無い』と読めるように）。"""
    d = json.loads(f("Ragavan, Nimble Pilferer", "standrad", 3))
    assert d["error"].startswith("該当なし: ") and d["error_kind"] == "no_match", "standard 不合法なので 0 件"
    assert "standrad" in d["format_note"] and "standard" in d["format_note"], (
        f"0 件でも解釈し直したことを言う（{d.get('format_note')}）")


def test_search_format_check_is_skipped_when_list_unavailable(monkeypatch):
    """鍵の一覧が引けない環境でも検索は落とさない（素通り）。ただし黙っては通さない。

    元の意図（引けない環境で新しい失敗を作らない）はそのまま＝err は None・読み替えもしない。
    2026-09-14 に変えたのは「黙って」の部分だけ: 検査できなかったことを返り値の note に書く。
    黙って通していたせいで、綴り違いの format が legalities->>'…' に渡って全部 NULL になり、
    「鍵が無い」のか「合法なカードが無い」のかクライアントに区別がつかなかった（9/12 18:45 に公開サーバーで発生）。
    """
    monkeypatch.setitem(cards._FORMATS_CACHE, "keys", [])
    monkeypatch.setattr(cards, "_db", lambda sql, params, lane=None: (_ for _ in ()).throw(RuntimeError("boom"))
                        if "jsonb_object_keys" in sql else [])
    assert cards._valid_formats() == [], "引けなければ空"

    fmt, note, err = cards._check_format("modrn")

    assert (fmt, err) == ("modrn", None), "検索そのものは断らず、勝手な読み替えもしない"
    assert "検査できなかった" in note, f"黙って通さない（{note!r}）"


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



def test_draft_set_marks_which_cards_are_in_that_set():
    """draft_set を渡したら、どのカードがそのセットに入っているかを返り値に書く。

    由来（2026-09-16・ChatGPT での実地テスト）: 画像から読んだ名前が曖昧なとき、
    ChatGPT は「聖遺」のような部分文字列で引いていた。draft_set='LCI' は
    **検索を絞らない**（17Lands 統計を添えるセットの指定）ので、別セットのカードが
    EDHREC 人気順で並び、唯一の LCI 収録カードは 5 番目に沈んでいた。しかも返り値に
    収録セットが無いため、どれがそのセットかを**クライアントが判断できなかった**。
    誤同定の連鎖（誤読 → 部分一致 → 複数候補 → もっともらしい 1 枚を選ぶ）の入口。
    掟「MCP の返り値は自己完結させる」に従い、事実を足して判断材料を渡す。
    """
    import json
    d = json.loads(f("聖遺", top_k=10, draft_set="LCI"))
    cards = d["cards"]
    assert len(cards) >= 3, "複数候補が返る前提の試験"
    for c in cards:
        assert "in_draft_set" in c, f"{c['name_display']} に in_draft_set が無い"
    hit = [c for c in cards if c["in_draft_set"]]
    assert [c["name_display"] for c in hit] == ["《薄暮薔薇の聖遺/Dusk Rose Reliquary》"], (
        f"LCI 収録はこの 1 枚だけのはず（実際: {[c['name_display'] for c in hit]}）")
    # そのセットに入っていない候補も、どこ収録かが分かる
    other = [c for c in cards if not c["in_draft_set"]][0]
    assert other.get("set_codes"), f"{other['name_display']} に収録セットが無い"


def test_no_draft_set_means_no_in_draft_set_key():
    """draft_set を渡していないときは in_draft_set を足さない（毎回の税にしない）。"""
    import json
    d = json.loads(f("Lightning Bolt", top_k=1))
    assert "in_draft_set" not in d["cards"][0]
