#!/usr/bin/env python3
"""find_combos の試験（2026-09-04）。

既定は偽の応答（ネットに出ない）。--live で Commander Spellbook を 1 回だけ実呼び。
走らせ方: pytest tests/test_find_combos.py -v [--live]
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m
import sisho.tools.combos as combos      # _spellbook_post の差し替え先（2026-09-05 Step 3 で
                                         # find_combos がこのモジュールへ移った。mcp_server の
                                         # 再輸出 m._spellbook_post を差し替えても道具には届かない）
from conftest import DB_AVAILABLE, requires_db

f = m.find_combos.fn if hasattr(m.find_combos, "fn") else m.find_combos

FAKE = {
    "results": {
        "included": [
            {
                "id": "742-1295",
                "uses": [{"card": {"name": "Demonic Consultation"}}, {"card": {"name": "Thassa's Oracle"}}],
                "produces": [{"feature": {"name": "Win the game"}}],
                "easyPrerequisites": "",
                "notablePrerequisites": "",
                "popularity": 148605,
                "bracketTag": "R",
            }
        ],
        "almostIncluded": [
            {
                "id": "1295-3093",
                "uses": [{"card": {"name": "Tainted Pact"}}, {"card": {"name": "Thassa's Oracle"}}],
                "produces": [{"feature": {"name": "Win the game"}}],
                "easyPrerequisites": "No two cards in library share a name.",
                "popularity": 131648,
                "bracketTag": "R",
            }
        ],
        "almostIncludedByAddingColors": [],
    }
}


def test_find_combos_error_kinds():
    """空・多すぎ・外部 API 不達で error_kind を分ける（Step 6 作業 3）。"""
    from sisho import errors
    d = json.loads(f([]))
    assert d["error_kind"] == "empty_query" and "呼び直す" in d["error"], f"次に何を試すかを言う（{d}）"
    d = json.loads(f(["Sol Ring"] * 121))
    assert d["error_kind"] == "out_of_range" and "121" in d["error"], f"何枚渡したかを言う（{d}）"

    def boom(payload):
        raise TimeoutError("timed out")
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(combos, "_spellbook_post", boom)
    try:
        d = json.loads(f(["Sol Ring"]))
    finally:
        mp.undo()
    assert d["error_kind"] == "upstream_unreachable" and "TimeoutError" in d["error"], (
        f"生の例外の型と文を素通し（{d}）")
    assert {"empty_query", "out_of_range", "upstream_unreachable"} <= set(errors.KINDS)


def test_find_combos_empty_error():
    """空のカード名指定は error"""
    d = json.loads(f([], None, 10))
    assert "error" in d, "空は error"


def test_find_combos_timeout_error(monkeypatch):
    """外部 API に届かないときは error（他ツールに影響なし）"""
    def boom(payload):
        raise TimeoutError("timed out")

    monkeypatch.setattr(combos, "_spellbook_post", boom)
    d = json.loads(f(["Thassa's Oracle"], None, 10))
    assert "届かない" in d.get("error", ""), "届かないときは error（他の道具に影響なし）"


@requires_db
def test_find_combos_mocked(monkeypatch):
    """モック応答による find_combos の機能検証（DB によるカード名解決と name_display 同伴が必要）"""
    captured = {}

    def fake_post(payload):
        captured["payload"] = payload
        return FAKE

    monkeypatch.setattr(combos, "_spellbook_post", fake_post)

    d = json.loads(f(["タッサの神託者", "Demonic Consultation"], None, 10))
    assert captured["payload"]["main"][0]["card"] == "Thassa's Oracle", (
        f"日本語名を英語名に直して送る（{captured['payload']['main'][0]['card']}）"
    )
    assert d["included"]["count_total"] == 1 and d["included"]["combos"][0]["id"] == "742-1295", (
        "included を id 付きで返す"
    )

    _nd = {
        r[0]: r[1]
        for r in m._db(
            "SELECT card_name, name_display FROM mtg_cards_v2 WHERE card_name = ANY(%s)",
            (["Demonic Consultation", "Thassa's Oracle"],),
        )
    }
    assert d["included"]["combos"][0]["uses_display"] == [_nd["Demonic Consultation"], _nd["Thassa's Oracle"]], (
        f"uses_display は DB の完成形そのまま（{d['included']['combos'][0]['uses_display']}）"
    )
    assert d["almostIncluded"]["combos"][0]["prerequisites"] == "No two cards in library share a name.", (
        "前提の原文をそのまま載せる"
    )
    assert d["included"]["combos"][0]["url"].endswith("/combo/742-1295/"), "出典 URL"
    assert "Commander Spellbook" in d["source"] and "前提" in d["note"], "出典と注記"


def test_find_combos_live(request):
    """--live オプション指定時のみ Commander Spellbook を 1 回だけ実呼び"""
    if not request.config.getoption("--live"):
        pytest.skip("--live オプション指定時のみ実呼び実行")
    if not DB_AVAILABLE:
        pytest.skip("DB が必要（カード名解決）")

    d = json.loads(f(["Thassa's Oracle", "Demonic Consultation"], None, 3))
    assert "error" not in d and d["included"]["count_total"] >= 1, (
        f"実呼び: included {d.get('included', {}).get('count_total')}・あと 1 枚 {d.get('almostIncluded', {}).get('count_total')}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
