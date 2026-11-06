#!/usr/bin/env python3
"""名前の解決（sisho/names.py）の試験。

相方検索（partners）と裁定検索（rules）が同じ名前解決関数（sisho.names.resolve_card）を共有していること
（片方だけ直す事故の防止）と、面の名前の解決規則（DESIGN 12: 表面一致が先・一致なしは空・
複数一致は推測せず全部返す）を検証する。

走らせ方: pytest tests/test_names.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import sisho.names as names  # noqa: E402
import sisho.tools.partners as partners  # noqa: E402
import sisho.tools.rules as rules  # noqa: E402
from conftest import requires_db  # noqa: E402


def test_name_resolution_is_shared():
    """partners と rules は同じ解決関数（resolve_card_ex / resolve_card）を見ている（DB 不要の構造の網）。"""
    # 両方とも「入力を 1 枚のカードに決める」resolve_card_ex / resolve_card を使う
    # partners が独自の分岐を持たず、resolve_card_ex まで同一関数であることも検証する
    assert partners.resolve_card is names.resolve_card
    assert rules.resolve_card is names.resolve_card
    assert partners.resolve_card_ex is names.resolve_card_ex
    assert rules.resolve_card_ex is names.resolve_card_ex
    assert partners._resolve is names.resolve_card_ex


def test_no_cross_tool_private_imports():
    """tools/*.py 同士が互いの関数・変数を直接 import しない（共有知識は sisho/ 直下のモジュールに置く）。"""
    import ast
    from pathlib import Path

    tools_dir = Path(__file__).resolve().parent.parent / "src" / "sisho" / "tools"
    violations = []
    for py in sorted(tools_dir.glob("*.py")):
        if py.name == "__init__.py":
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sisho.tools"):
                violations.append(f"{py.name}:{node.lineno} imports from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("sisho.tools"):
                        violations.append(f"{py.name}:{node.lineno} imports {alias.name}")
    assert not violations, "tools/ 間の横 import が残っている:\n" + "\n".join(violations)


@requires_db
def test_resolve_face_name_prefers_front():
    """裏面名でも正式名に解決する。表面一致が先＝本物のカードが勝つ（DESIGN 12）。"""
    assert names.resolve_face_name("Petty Theft") == ["Brazen Borrower // Petty Theft"], (
        "裏面（出来事の呪文側）の名前 → 正式名")
    assert names.resolve_face_name("Brazen Borrower")[0] == "Brazen Borrower // Petty Theft", (
        "表面の名前 → 正式名")

    # 裏面名が別の本物のカード名と同じカード（実測 21 枚）。本物のカード（表面一致）が先頭に来る。
    got = names.resolve_face_name("Ancestral Recall")
    assert got[0] == "Ancestral Recall", f"本物のカードが先頭（{got}）"
    assert "Emeritus of Ideation // Ancestral Recall" in got, f"裏面で当たるカードも候補に残す（{got}）"

    assert names.resolve_face_name("Zzzqqq Xxxyyy") == [], "一致しなければ空（推測しない）"
    assert names.resolve_face_name("") == [] and names.resolve_face_name(None) == [], "空は DB へ行かない"
    assert names.resolve_face_name(" Petty Theft ") == ["Brazen Borrower // Petty Theft"], "前後の空白は落とす"
    assert names.resolve_face_name("petty theft") == [], "大文字小文字は区別する（従来どおり）"


@requires_db
def test_single_slash_resolved_in_both_partners_and_rules():
    """斜線 1 本（Fire / Ice）は partners と rules の両方で Fire // Ice に解決され、後半が違う偽名は弾かれる。"""
    c, amb = names.resolve_card_ex("Fire / Ice")
    assert c and c["card_name"] == "Fire // Ice" and not amb
    assert names.resolve_card_ex("Fire / Totally Wrong") == (None, [])

    r_rule = rules.get_card_rulings("Fire / Ice", limit=2)
    assert r_rule.startswith("Fire // Ice（"), f"rules でも Fire // Ice の裁定が引ける: {r_rule[:80]}"
    r_bad = rules.get_card_rulings("Fire / Totally Wrong", limit=2)
    assert r_bad.startswith("裁定なし: Fire / Totally Wrong"), f"後半が違う偽名は前半で通さない: {r_bad[:80]}"


@requires_db
def test_display_map_and_resolve_names_handle_front_face():
    """combos / sql 共通の名前解決と完成形辞書が両面カードの表面英語名（name_en_front）も扱える。"""
    disp, _ = names.display_map_for_en_names(["Brazen Borrower", "Unknown Card Xyz"], fallback_suffix="（DB 未収録）")
    assert "（DB 未収録）" not in disp["Brazen Borrower"] and "Brazen Borrower" in disp["Brazen Borrower"]
    assert disp["Unknown Card Xyz"] == "Unknown Card Xyz（DB 未収録）"

    resolved = names.resolve_names_to_en(["Brazen Borrower", "厚かましい借り手", "タッサの神託者"])
    assert resolved["Brazen Borrower"] == "Brazen Borrower // Petty Theft"
    assert resolved["厚かましい借り手"] == "Brazen Borrower // Petty Theft"
    assert resolved["タッサの神託者"] == "Thassa's Oracle"


def test_display_map_and_resolve_names_do_not_pick_arbitrary_on_tie():
    """同順位で複数カードに当たる名前を ORDER BY id の 1 枚目で勝手に確定させない。"""
    def fake_resolve_db(sql, params=None, lane=None):
        assert "name_ja_front = ANY" not in sql, "正式名（日本語名）で当たった名前に第 2 段は要らない"
        # 「緊急脱出」が Bail Out と Eject の 2 枚に同順位（日本語の正式名＝順位 0）で当たる模擬
        # 列: card_name, japanese_name, name_en_front, name_en_back, name_ja_back, digital
        return [
            ("Bail Out", "緊急脱出", "Bail Out", None, None, False),
            ("Eject", "緊急脱出", "Eject", None, None, False),
        ]

    resolved, amb = names.resolve_names_to_en_ex(["緊急脱出"], _db=fake_resolve_db)
    assert "緊急脱出" not in resolved
    assert amb == {"緊急脱出": ["Bail Out", "Eject"]}
    assert names.resolve_names_to_en(["緊急脱出"], _db=fake_resolve_db) == {}

    def fake_display_db(sql, params=None, lane=None):
        assert "s.released_at" not in sql, "先行収録でない行で状態を照会しない"
        # 裏面名 Same を持つ紙のカードが 2 枚（同順位 2）
        # 列: card_name, name_en_front, name_ja_front, name_en_back, name_ja_back, name_display, japanese_name, digital, is_prev
        return [
            ("A // Same", "A", "表A", "Same", "訳A", "《表A/A》", "表A // 訳A", False, False),
            ("B // Same", "B", "表B", "Same", "訳B", "《表B/B》", "表B // 訳B", False, False),
        ]

    disp, _ = names.display_map_for_en_names(["Same"], back_as_face=True, _db=fake_display_db)
    assert "Same" not in disp
    disp, _ = names.display_map_for_en_names(["Same"], fallback_suffix="（DB 未収録）", _db=fake_display_db)
    assert disp["Same"] == "Same（複数カードに該当: A // Same・B // Same）"


def test_lookup_card_with_face_stage2_front_beats_stage1_back():
    """第 1 段で裏面一致（順位 2）があっても、第 2 段の name_ja_front（順位 1）を引いて表面一致を優先する。"""
    cols = "card_name, type_line, name_en_front, name_ja_front, name_en_back, name_ja_back, digital"
    calls = []

    def fake_db(sql, params=None, lane=None):
        calls.append(sql)
        if "WHERE name_ja_front = %s" in sql:
            # 第 2 段: 表面の日本語名が一致する本命の両面カード（片面だけ訳あり＝japanese_name は NULL）
            return [("True Front // Back", "Creature", "True Front", "テスト面", "Back", None, False, 1)]
        if "name_ja_back = %s" in sql:
            # 第 1 段: 裏面の日本語名だけが一致する別カード
            return [("Other // Test", "Creature", "Other", "他カード", "Test", "テスト面", False, 2)]
        raise AssertionError(f"unexpected SQL: {sql}")

    r, face, amb = names.lookup_card_with_face("テスト面", cols, _db=fake_db)
    assert len(calls) == 2, "裏面一致だけで第 2 段を省いてはいけない"
    assert amb == []
    assert r[0] == "True Front // Back"
    assert face == 0


def test_display_map_propagates_db_and_preserves_partial_dfc_front(monkeypatch):
    """monkeypatch した names._db が内部の preview_status まで伝播し、片面翻訳の表面名を消さず、
    反映待ちを『日本語版なし』と誤記しない。"""
    from datetime import date as _d

    from sisho import preview

    def fake_db(sql, params=None, lane=None):
        if "s.released_at" in sql:
            return [
                ("Front // Back", "fra", "Reality Fracture", _d(2099, 1, 1), "expansion"),
                ("Past Card", "fra", "Reality Fracture", _d(2000, 1, 1), "expansion"),
            ]
        # 列: card_name, name_en_front, name_ja_front, name_en_back, name_ja_back, name_display, japanese_name, digital, is_prev
        return [
            ("Front // Back", "Front", "表訳", "Back", None, "《表訳/Front》", None, False, True),
            ("Past Card", "Past Card", None, None, None, "Past Card（日本語版なし）", None, False, True),
        ]

    stray = []

    def must_not_be_called(*a, **k):
        # 例外だけだと display_map の「状態の照会の失敗は注記にする」try に飲まれる＝呼ばれた記録を残して後で確かめる
        stray.append(a)
        raise AssertionError("preview._db に流れた＝注入した DB が状態照会まで届いていない")

    monkeypatch.setattr(names, "_db", fake_db)
    monkeypatch.setattr(preview, "_db", must_not_be_called)
    disp, pnotes = names.display_map_for_en_names(["Front // Back", "Past Card"])
    assert stray == [], "preview._db に流れた"
    assert set(pnotes) == {"Front // Back", "Past Card"}
    assert not any(v.startswith(names.PREVIEW_UNAVAILABLE) for v in pnotes.values()), pnotes
    assert disp["Front // Back"] == "《表訳/Front》"
    assert disp["Past Card"] == "Past Card（日本語名未収録）"


def test_preview_lookup_failure_keeps_known_translations_at_consumers(monkeypatch):
    """発売状態の補助照会だけが落ちても、sql と combos の入口で取れた訳を保ち、「DB 未収録」「発売前」「日本語版なし」と
    書かない。失敗は display_map の注記で返る。"""
    import sisho.tools.combos as combos
    import sisho.tools.sql as sql

    def fake_db(sql_text, params=None, lane=None):
        if "s.released_at" in sql_text:
            raise RuntimeError("preview db timeout")
        # 列: card_name, name_en_front, name_ja_front, name_en_back, name_ja_back, name_display, japanese_name, digital, is_prev
        return [
            ("Known", "Known", "既知", None, None, "《既知/Known》", "既知", False, False),
            ("Future", "Future", None, None, None, "Future（日本語名は未収録・発売前）", None, False, True),
        ]

    disp, pnotes = names.display_map_for_en_names(["Known", "Future"], _db=fake_db)
    assert disp == {"Known": "《既知/Known》", "Future": "Future（日本語名未収録）"}
    assert set(pnotes) == {"Future"} and "照会に失敗" in pnotes["Future"]

    monkeypatch.setattr(combos, "_db", fake_db)
    assert combos._display_map(["Known", "Future"]) == {"Known": "《既知/Known》", "Future": "Future（日本語名未収録）"}

    monkeypatch.setattr(sql, "_db", fake_db)
    cols, rows, note = sql._attach_japanese_names(["card_name"], [("Known",), ("Future",)])
    assert cols == ["card_name", "card_name_display"]
    assert [r[1] for r in rows] == ["《既知/Known》", "Future（日本語名未収録）"]
    assert "発売状態の照会に失敗" in note and "Future" in note, note     # 失敗は返り値の注記まで届く


def test_unknown_release_date_is_neutral_at_display_map():
    """発売日の分からない先行収録の行（UNKNOWN）は、sql と combos の入口でも「日本語名未収録」に倒し、注記も
    「発売前のカード」と言わない（cards の apply_preview_to_cards と同じ扱い）。"""
    def fake_db(sql_text, params=None, lane=None):
        if "s.released_at" in sql_text:
            return [("Future", "zzz", None, None, None)]     # mtg_sets に行が無い＝発売日が分からない
        # 列: card_name, name_en_front, name_ja_front, name_en_back, name_ja_back, name_display, japanese_name, digital, is_prev
        return [("Future", "Future", None, None, None, "Future（日本語名は未収録・発売前）", None, False, True)]

    disp, pnotes = names.display_map_for_en_names(["Future"], _db=fake_db)
    assert disp == {"Future": "Future（日本語名未収録）"}
    assert pnotes["Future"].startswith("zzz のカード（発売日未確認）") and "発売前のカード" not in pnotes["Future"]


def test_find_combos_reports_preview_lookup_failure(monkeypatch):
    """コンボ検索の返り値（JSON）にも、発売状態の照会に失敗したことが載る（注記を捨てない）。"""
    import json

    import sisho.tools.combos as combos

    def fake_db(sql_text, params=None, lane=None):
        if "s.released_at" in sql_text:
            raise RuntimeError("preview db timeout")
        if "name_display" in sql_text:
            # 列: card_name, name_en_front, name_ja_front, name_en_back, name_ja_back, name_display, japanese_name, digital, is_prev
            return [("Demonic Consultation", "Demonic Consultation", "悪魔との契約", None, None,
                     "《悪魔との契約/Demonic Consultation》", "悪魔との契約", False, False),
                    ("Thassa's Oracle", "Thassa's Oracle", None, None, None,
                     "Thassa's Oracle（日本語名は未収録・発売前）", None, False, True)]
        return []

    fake = {"results": {"included": [{"id": "1-2", "uses": [{"card": {"name": "Demonic Consultation"}},
                                                            {"card": {"name": "Thassa's Oracle"}}]}],
                        "almostIncluded": [], "almostIncludedByAddingColors": []}}
    monkeypatch.setattr(combos, "_db", fake_db)
    monkeypatch.setattr(combos, "_spellbook_post", lambda payload: fake)
    d = json.loads(combos.find_combos(["Demonic Consultation", "Thassa's Oracle"], None, 10))
    assert d["included"]["combos"][0]["uses_display"] == [
        "《悪魔との契約/Demonic Consultation》", "Thassa's Oracle（日本語名未収録）"]
    assert "発売状態の照会に失敗" in d["display_note"] and "Thassa's Oracle" in d["display_note"], d.get("display_note")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
