#!/usr/bin/env python3
"""名前の解決（sisho/names.py）の試験（Step 6 作業 1・2026-09-05）。

「面の名前 → 正式名」の解決は 1 箇所（sisho.names.resolve_face_name）に置き、
相方検索（partners）と裁定検索（rules）の両方がそれを呼ぶ。ここで縫うのは
**解決の規則**（表面一致が先・一致なしは空・複数一致は推測せず全部返す）と、
**呼ぶ側が同じ関数を見ていること**（片方だけ直す事故の防止）。

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
    """partners と rules は同じ 1 つの解決関数を見ている（DB 不要の構造の網）。"""
    assert partners.resolve_face_name is names.resolve_face_name
    assert rules.resolve_face_name is names.resolve_face_name


@requires_db
def test_resolve_face_name_prefers_front():
    """裏面名でも正式名に解決する。表面一致が先＝本物のカードが勝つ（DESIGN 12）。"""
    assert names.resolve_face_name("Petty Theft") == ["Brazen Borrower // Petty Theft"], (
        "裏面（出来事の呪文側）の名前 → 正式名")
    assert names.resolve_face_name("Brazen Borrower")[0] == "Brazen Borrower // Petty Theft", (
        "表面の名前 → 正式名")

    # 裏面名が別の本物のカード名と同じ札（実測 21 枚）。本物のカード（表面一致）が先頭に来る。
    got = names.resolve_face_name("Ancestral Recall")
    assert got[0] == "Ancestral Recall", f"本物のカードが先頭（{got}）"
    assert "Emeritus of Ideation // Ancestral Recall" in got, f"裏面で当たる札も候補に残す（{got}）"

    assert names.resolve_face_name("Zzzqqq Xxxyyy") == [], "一致しなければ空（推測しない）"
    assert names.resolve_face_name("") == [] and names.resolve_face_name(None) == [], "空は DB へ行かない"
    assert names.resolve_face_name(" Petty Theft ") == ["Brazen Borrower // Petty Theft"], "前後の空白は落とす"
    assert names.resolve_face_name("petty theft") == [], "大文字小文字は区別する（従来どおり）"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
