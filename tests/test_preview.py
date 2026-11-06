"""test_preview.py — 発売前のカード（先行収録）の注記と表示の単体試験（DB 不要）。

統率者セットの新カードがスタンダードで使えるように読まれないことを縫う: 統率者セットの注記がスタンダードで使えると読めないこと・未確認のセットで
スタンダードを約束しないこと・日本語名の無い発売前の面が「日本語版なし」と言わないこと。
"""
from sisho import preview
from sisho.names import face_display


def test_frc_is_not_standard():
    s = preview._legality("frc", "commander")
    assert "スタンダード・パイオニア・モダンでは使えない" in s
    assert "統率者・レガシー・ヴィンテージ" in s


def test_unknown_commander_set_is_not_standard():
    s = preview._legality("trc", "commander")
    assert "スタンダード・パイオニア・モダンでは使えない" in s


def test_unknown_expansion_does_not_promise_standard():
    s = preview._legality("trk", "expansion")
    assert "未確認" in s and "決めつけない" in s


def test_fra_all_formats():
    assert "全フォーマット" in preview._legality("fra", "expansion")


def test_face_display_preview_without_japanese():
    assert face_display("Ob Nixilis, the Ascended", None, False, True) == "Ob Nixilis, the Ascended（日本語名は未収録・発売前）"


def test_face_display_preview_with_japanese():
    assert face_display("Peer Review", "一瞥査読", False, True) == "《一瞥査読/Peer Review》"


def test_preview_notes_text(monkeypatch):
    monkeypatch.setattr(preview, "_db", lambda sql, params, lane=None: [
        ("Ob Nixilis, the Ascended", "frc", "Reality Fracture Commander", __import__("datetime").date(2099, 10, 2), "commander")])
    n = preview.preview_notes(["Ob Nixilis, the Ascended"])["Ob Nixilis, the Ascended"]
    assert "発売前" in n and "2099-10-02 発売" in n and "今はどのフォーマットでも使えない" in n
    assert "スタンダード・パイオニア・モダンでは使えない" in n


def test_preview_notes_empty_skips_db(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("空の入力で DB を叩いた")
    monkeypatch.setattr(preview, "_db", boom)
    assert preview.preview_notes([]) == {}


def test_apply_preview_to_cards_awaiting_upstream_does_not_say_upcoming(monkeypatch):
    """発売日を過ぎた先行収録カード（AWAITING_UPSTREAM）は、cards 表示でも『発売前』と言わない。"""
    from datetime import date as _d

    monkeypatch.setattr(
        preview,
        "_db",
        lambda sql, params, lane=None: [("X", "fra", "Reality Fracture", _d(2000, 1, 1), "expansion")],
    )
    cards = [
        {
            "card_name": "X",
            "japanese_name": None,
            "name_display": "X（日本語名は未収録・発売前）",
            "faces": [{"en": "X", "ja": None, "display": "X（日本語版なし）"}],
        }
    ]
    preview.apply_preview_to_cards(cards, today=_d(2026, 10, 30))
    assert "発売前" not in cards[0]["preview_note"] and "反映待ち" in cards[0]["preview_note"]
    assert "発売前" not in cards[0]["name_display"] and "日本語版なし" not in cards[0]["name_display"]
    assert cards[0]["name_display"] == "X（日本語名未収録）"
    assert cards[0]["faces"][0]["display"] == "X（日本語名未収録）"
    assert "発売前" not in cards[0]["name_note"] and "反映待ち" in cards[0]["name_note"]


def test_release_state_has_unknown_for_missing_date():
    """発売日が分からない先行収録の行は UNKNOWN（発売前とも反映待ちとも決めない）。発売日当日は反映待ち。"""
    from datetime import date as _d

    assert preview.classify_release(None) == preview.ReleaseState.UNKNOWN
    assert preview.classify_release(_d(2099, 1, 1), today=_d(2026, 11, 6)) == preview.ReleaseState.UPCOMING
    assert preview.classify_release(_d(2026, 11, 6), today=_d(2026, 11, 6)) == preview.ReleaseState.AWAITING_UPSTREAM


def test_apply_preview_to_cards_unknown_date_is_neutral(monkeypatch):
    """mtg_sets と結べず発売日が分からない先行収録カードは、注記・表示とも「発売前」「日本語版なし」と断定しない。"""
    from datetime import date as _d

    # 列: card_name, set_code, set_name, released_at, set_type（LEFT JOIN が結べなかった形）
    monkeypatch.setattr(preview, "_db", lambda sql, params, lane=None: [("X", None, None, None, None)])
    cards = [
        {
            "card_name": "X",
            "japanese_name": None,
            "name_display": "X（日本語名は未収録・発売前）",
            "faces": [{"en": "X", "ja": None, "display": "X（日本語版なし）"}],
        }
    ]
    preview.apply_preview_to_cards(cards, today=_d(2026, 11, 6))
    n = cards[0]["preview_note"]
    assert n.startswith("セット未確認 のカード（発売日未確認）") and "決められない" in n, n
    assert "発売前のカード" not in n and "今はどのフォーマットでも使えない" not in n
    assert cards[0]["name_display"] == "X（日本語名未収録）"
    assert cards[0]["faces"][0]["display"] == "X（日本語名未収録）"
    assert cards[0]["name_note"] == "日本語名は未収録（発売日が確かめられない）＝name_display をそのまま使う（訳名を作らない）"


def test_apply_preview_to_cards_preserves_partial_dfc_japanese_front(monkeypatch):
    """片面（表面）だけ日本語名がある両面カード（japanese_name=None, faces[0]['ja'] あり）の表面日本語名を消さない。"""
    from datetime import date as _d

    monkeypatch.setattr(
        preview,
        "_db",
        lambda sql, params, lane=None: [("Front // Back", "fra", "Reality Fracture", _d(2099, 1, 1), "expansion")],
    )
    cards = [
        {
            "card_name": "Front // Back",
            "japanese_name": None,
            "name_display": "《表訳/Front》",
            "faces": [
                {"en": "Front", "ja": "表訳", "display": "《表訳/Front》"},
                {"en": "Back", "ja": None, "display": "Back（日本語版なし）"},
            ],
        }
    ]
    preview.apply_preview_to_cards(cards, today=_d(2026, 10, 30))
    assert cards[0]["name_display"] == "《表訳/Front》"
    assert cards[0]["faces"][0]["display"] == "《表訳/Front》"
    assert cards[0]["faces"][1]["display"] == "Back（日本語名は未収録・発売前）"
    # 表面の訳はあるので「日本語名は未収録」と全体を言わない。訳の無い裏面だけを名指しする（訳名を作らせない）
    assert cards[0]["name_note"].startswith("裏面の日本語名は未収録（発売前）")
