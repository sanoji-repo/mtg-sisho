#!/usr/bin/env python3
"""show_card_images（カード画像を画面に並べる道具）の試験。

DB には繋がない（偽の DB を images._db に差す）。サーバー越しの確かめ（道具の meta・画面の資源・CSP・HTML の中身）も
ここに置く。走らせ方: pytest tests/test_tools_images.py -v
"""
import asyncio
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402
import sisho.tools.images as images  # noqa: E402

EN = "https://cards.scryfall.io/normal/front/2/c/2cfd0000-0000-0000-0000-000000000001.jpg?1692939325"
JA = "https://cards.scryfall.io/normal/front/a/b/ab000000-0000-0000-0000-000000000002.jpg?1692939325"
EN2 = "https://cards.scryfall.io/normal/front/3/d/3d000000-0000-0000-0000-000000000003.jpg?1692939325"

# card_name → (japanese_name, 表示, image_url, image_url_ja)
SOL = "Sol Ring"
SOL_DISP = "《太陽の指輪/Sol Ring》"
PLAINS = "Plains"
PLAINS_DISP = "Plains（日本語版なし）"
NOIMG = "Mox Noimage"


def make_db(cards: dict, ambiguous_key: str | None = None, seen: list | None = None):
    """偽の DB。names.py の問い合わせ（名前の解決 2 段・表示の辞書・見つからないときの候補）と images.py の画像の問い合わせに答える。"""
    amb = {"A // Same": ("A", "Same"), "B // Same": ("B", "Same")}

    def fake_db(sql, params=None, lane=None):
        if seen is not None:
            seen.append((sql, params))
        if "image_url" in sql:
            want = params[0]
            return [(cn, v[2], v[3]) for cn, v in cards.items() if cn in want]
        if "japanese_name = ANY" in sql:      # resolve_names_to_en_ex の段 1
            want = set(params[0])
            rows = []
            for cn, v in cards.items():
                if cn in want or (v[0] in want):
                    rows.append((cn, v[0], cn, None, None, False))
            if ambiguous_key and ambiguous_key in want:
                rows += [(cn, None, f, b, None, False) for cn, (f, b) in amb.items()]
            return rows
        if "name_ja_front = ANY" in sql:
            return []
        if "name_display" in sql and "name_en_back = ANY" in sql:     # display_map_for_en_names
            want = set(params[0])
            rows = []
            for cn, v in cards.items():
                if cn in want:
                    rows.append((cn, cn, v[0], None, None, v[1], v[0], False, False))
            for cn, (f, b) in amb.items():
                if cn in want:
                    rows.append((cn, f, None, b, None, f"{f}（日本語版なし）", None, False, False))
            return rows
        if "card_name ILIKE" in sql:          # not_found_hint の候補
            return []
        raise AssertionError(f"想定外の SQL: {sql[:80]}")
    return fake_db


@pytest.fixture
def cards():
    return {SOL: ("太陽の指輪", SOL_DISP, EN, JA),
            PLAINS: (None, PLAINS_DISP, EN2, None),
            NOIMG: (None, f"{NOIMG}（日本語版なし）", None, None)}


def _text(res):
    return res.content[0].text


def test_two_cards_ja_and_en_only(monkeypatch, cards):
    monkeypatch.setattr(images, "_db", make_db(cards))
    res = images.show_card_images(["太陽の指輪", "Plains"])
    assert not isinstance(res, str), res
    assert res.structured_content is None, "structuredContent は使わない（本文と二重になる）"
    assert res.meta["cards"] == [
        {"display": SOL_DISP, "image": JA, "lang": "ja"},      # 日本語版があればそれ
        {"display": PLAINS_DISP, "image": EN2, "lang": "en"},   # 無ければ英語版
    ]
    assert res.meta["not_found"] == [] and res.meta["ambiguous"] == []
    t = _text(res)
    assert SOL_DISP in t and PLAINS_DISP in t, "完成形を一字も変えず本文に書く"
    assert f"日本語版の画像: {JA}" in t and f"英語版の画像: {EN2}" in t
    assert "著作権は Wizards of the Coast" in t and "隠したり切り抜いたりしない" in t
    assert EN not in t, "日本語版があるカードに英語版の URL は出さない"


def test_card_without_any_image(monkeypatch, cards):
    monkeypatch.setattr(images, "_db", make_db(cards))
    res = images.show_card_images([NOIMG])
    assert res.meta["cards"] == [{"display": f"{NOIMG}（日本語版なし）", "image": None, "lang": None}]
    assert "画像なし" in _text(res)


def test_not_found_is_listed_but_others_shown(monkeypatch, cards):
    monkeypatch.setattr(images, "_db", make_db(cards))
    res = images.show_card_images([SOL, "存在しないカード"])
    assert [c["display"] for c in res.meta["cards"]] == [SOL_DISP]
    assert res.meta["not_found"] == ["存在しないカード"]
    assert "見つからない名前" in _text(res) and "存在しないカード" in _text(res)


def test_ambiguous_is_not_guessed(monkeypatch, cards):
    monkeypatch.setattr(images, "_db", make_db(cards, ambiguous_key="Same"))
    res = images.show_card_images([SOL, "Same"])
    assert [c["display"] for c in res.meta["cards"]] == [SOL_DISP], "曖昧な名前を 1 枚に決めて画像を出さない"
    assert res.meta["ambiguous"] == [{"name": "Same", "candidates": ["A（日本語版なし）", "B（日本語版なし）"]}]
    assert "1 枚に決まらない名前" in _text(res) and "A（日本語版なし）" in _text(res)


def test_eleven_names_cut_to_ten(monkeypatch):
    table = {f"Card {i:02d}": (None, f"Card {i:02d}（日本語版なし）", f"https://cards.scryfall.io/normal/front/{i}.jpg", None)
             for i in range(11)}
    seen: list = []
    monkeypatch.setattr(images, "_db", make_db(table, seen=seen))
    res = images.show_card_images(list(table))
    assert len(res.meta["cards"]) == 10
    assert "Card 10（日本語版なし）" not in [c["display"] for c in res.meta["cards"]]
    assert "最初の 10 枚だけ表示した" in _text(res)
    assert all("Card 10" not in json.dumps(p, ensure_ascii=False) for _s, p in seen), "11 枚目は DB にも渡さない"
    ten = images.show_card_images(list(table)[:10])
    assert "最初の 10 枚だけ" not in _text(ten), "ちょうど 10 枚なら切った注記は出さない"


def test_same_card_by_two_names_once(monkeypatch, cards):
    monkeypatch.setattr(images, "_db", make_db(cards))
    res = images.show_card_images(["Sol Ring", "太陽の指輪"])
    assert len(res.meta["cards"]) == 1


def test_errors_are_strings(monkeypatch, cards):
    monkeypatch.setattr(images, "_db", make_db(cards))
    from sisho import errors
    d = json.loads(images.show_card_images([]))
    assert d["error_kind"] == "empty_query" and "呼び直す" in d["error"]
    d = json.loads(images.show_card_images(["  ", ""]))
    assert d["error_kind"] == "empty_query"
    s = images.show_card_images(["存在しない A", "存在しない B"])
    assert isinstance(s, str)
    d = json.loads(s)
    assert d["error_kind"] == "no_match" and "存在しない A" in d["error"], d
    assert errors.outcome_of(s) == "no_match", "道具ログの outcome がエラーになる文頭"
    monkeypatch.setattr(images, "_db", make_db(cards, ambiguous_key="Same"))
    d = json.loads(images.show_card_images(["Same"]))
    assert d["error_kind"] == "no_match" and d["ambiguous_names"] == {"Same": ["A // Same", "B // Same"]}


# ─── サーバー越し ─────────────────────────────────────────────

URI = "ui://sisho/card-images.html"


def test_server_tool_meta_and_resource():
    tools = {t.name: t for t in asyncio.run(m.server.list_tools())}
    t = tools["show_card_images"]
    assert t.meta == {"ui": {"resourceUri": URI}, "ui/resourceUri": URI}
    assert t.output_schema is None
    assert list(tools)[-1] == "show_card_images", "道具は一覧の最後"
    res = {str(r.uri): r for r in asyncio.run(m.server.list_resources())}
    assert URI in res, f"画面の資源が登録されている（{list(res)}）"
    r = res[URI]
    assert r.mime_type == "text/html;profile=mcp-app"
    assert r.meta["ui"]["csp"]["resourceDomains"] == ["https://cards.scryfall.io"]
    body = list(asyncio.run(m.server.read_resource(URI)))
    assert body and "ui/initialize" in body[0].content, "資源の本文は画面の HTML"


def test_server_call_success_and_error(monkeypatch, cards):
    monkeypatch.setattr(images, "_db", make_db(cards))
    res = asyncio.run(m.server.call_tool("show_card_images", {"names": ["Plains"]}))
    assert res.structured_content is None and not res.is_error
    assert res.meta["cards"][0]["display"] == PLAINS_DISP
    assert PLAINS_DISP in res.content[0].text
    err = asyncio.run(m.server.call_tool("show_card_images", {"names": []}))
    assert json.loads(err.content[0].text)["error_kind"] == "empty_query"
    assert err.meta is None and err.structured_content is None


def test_html_is_self_contained():
    h = images.HTML
    for bad in ("<script src", "<link", "http://", "unpkg", "jsdelivr", "@import", "<iframe"):
        assert bad not in h.lower(), f"画面の HTML に {bad} がある"
    assert len(re.findall(r"<script\b", h)) == 1 and '<script type="module">' in h
    assert re.findall(r"https://[^\s\"')]+", h) == ["https://cards.scryfall.io/"], "外の住所は Scryfall の画像だけ"
