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
        ("Ob Nixilis, the Ascended", "frc", "Reality Fracture Commander", "2026-10-02", "commander")])
    n = preview.preview_notes(["Ob Nixilis, the Ascended"])["Ob Nixilis, the Ascended"]
    assert "発売前" in n and "2026-10-02 発売" in n and "今はどのフォーマットでも使えない" in n
    assert "スタンダード・パイオニア・モダンでは使えない" in n


def test_preview_notes_empty_skips_db(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("空の入力で DB を叩いた")
    monkeypatch.setattr(preview, "_db", boom)
    assert preview.preview_notes([]) == {}
