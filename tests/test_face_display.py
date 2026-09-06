"""test_face_display.py — sisho/names.py の face_display の単体試験（DB 不要）。

面の完成形（日本語あり・なし・digital）の書式生成と、src/sisho/tools/ 配下から旧式のインライン式が根絶されたことを検証する。
"""
from pathlib import Path
from sisho.names import face_display


def test_face_display_with_japanese():
    """1. 日本語名がある場合は《日本語名/英語名》。"""
    assert face_display("Petty Theft", "ちょっとした盗み") == "《ちょっとした盗み/Petty Theft》"


def test_face_display_without_japanese():
    """2. 日本語名が無い（None）かつ digital=False の場合は「英語名（日本語版なし）」。"""
    assert face_display("Helm of Obedience", None) == "Helm of Obedience（日本語版なし）"


def test_face_display_digital_without_japanese():
    """3. 日本語名が無く digital=True の場合は「英語名（日本語名未収録）」。"""
    assert face_display("Advanced Floral Invocations", None, True) == "Advanced Floral Invocations（日本語名未収録）"


def test_face_display_empty_japanese():
    """4. 日本語名が空文字の場合は無しと同じ扱い（truthy 判定）。"""
    assert face_display("X", "", False) == "X（日本語版なし）"


def test_face_display_japanese_overrides_digital():
    """5. 日本語名があれば digital=True でも《日本語名/英語名》となる。"""
    assert face_display("X", "日本語", True) == "《日本語/X》"


def test_no_inline_face_display_in_tools():
    """6. src/sisho/tools/ 配下のソースに旧式の '日本語名未収録' if が残っていないこと。"""
    repo_root = Path(__file__).resolve().parent.parent
    tools_dir = repo_root / "src" / "sisho" / "tools"

    target_files = [
        tools_dir / "cards.py",
        tools_dir / "sql.py",
        tools_dir / "verify.py",
    ]

    for p in target_files:
        assert p.exists(), f"{p} が存在しない"
        content = p.read_text(encoding="utf-8")
        assert "'日本語名未収録' if" not in content, f"{p.name} に旧式の '日本語名未収録' if が残っている"
        assert '"日本語名未収録" if' not in content, f"{p.name} に旧式の \"日本語名未収録\" if が残っている"
