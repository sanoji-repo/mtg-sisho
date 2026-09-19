#!/usr/bin/env python3
"""error の語彙（sisho/errors.py）と混雑の扱いの試験。

縫うのは 3 つ:
  1. error_kind の語彙が 1 箇所にあり、そこに無い名前を返り値に載せられないこと。
  2. 番号でなく短い英語の名前であること。
  3. 「混雑」（DB のスロット取り待ち＝待てば通る）が「SQL エラー」「health 失敗」に丸められないこと。

DB は要らない（すべて差し替えと純粋な検査）。走らせ方: pytest tests/test_errors.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import sisho.db as db  # noqa: E402
import sisho.tools.health as health  # noqa: E402
import sisho.tools.sql as sqltool  # noqa: E402
from sisho import errors  # noqa: E402


def test_error_kinds_are_short_names_not_numbers():
    """名前は短い英字と _ だけ（番号は使わない・一覧は 10 個前後に収める）。"""
    assert 8 <= len(errors.KINDS) <= 16, f"語彙が増えすぎ・減りすぎ（{len(errors.KINDS)} 個）"
    for kind, meaning in errors.KINDS.items():
        assert kind.replace("_", "").isalpha() and kind.islower(), f"短い英語の名前（{kind}）"
        assert not any(ch.isdigit() for ch in kind), f"番号を使わない（{kind}）"
        assert meaning, f"意味を書く（{kind}）"


def test_every_constant_is_in_the_listing():
    """モジュールの定数と一覧（KINDS）がずれない＝一覧が正本。"""
    consts = {v for k, v in vars(errors).items()
              if k.isupper() and k != "KINDS" and isinstance(v, str)}
    assert consts == set(errors.KINDS), f"定数と一覧の差: {consts ^ set(errors.KINDS)}"


def test_err_json_shape_and_unknown_kind_is_refused():
    import json
    d = json.loads(errors.err_json(errors.NO_MATCH, "該当なし: x", valid_formats=["modern"]))
    assert list(d) == ["error", "error_kind", "valid_formats"], f"鍵の順（{list(d)}）"
    assert d["error_kind"] == "no_match" and d["valid_formats"] == ["modern"]
    with pytest.raises(AssertionError):
        errors.err_json("mystery_kind", "…")     # 一覧に無い名前は載せられない


def test_db_slot_raises_busy(monkeypatch):
    """スロットが空かないときの例外は DBBusy（RuntimeError の子＝知らない受け手は従来どおり）。"""
    monkeypatch.setattr(db, "_DB_WAIT_SEC", 0.05)     # 既定 20 秒を待たない（待ち時間そのものは別の関心）
    slots = db._DB_SLOTS
    held = 0
    while slots.acquire(timeout=0.01):
        held += 1
    try:
        with pytest.raises(db.DBBusy) as ei:
            with db._db_slot():
                pass
    finally:
        for _ in range(held):
            slots.release()
    assert isinstance(ei.value, RuntimeError), "RuntimeError の子であること（後方互換）"
    assert "混雑" in str(ei.value) and "少し待って" in str(ei.value), f"文言は不変（{ei.value}）"


def test_busy_is_not_reported_as_sql_error(monkeypatch):
    """混雑は SQL の誤り・DB の故障と別（丸めるとクライアントが次の一手を間違える）。"""
    busy = db.DBBusy("混雑: DB の順番待ちが 20 秒を超えました。少し待ってからもう一度呼んでください。")

    def raise_busy(*a, **k):
        raise busy

    monkeypatch.setattr(sqltool, "_db_readonly", raise_busy)
    q = sqltool.query_mtg_database
    r = q("SELECT 1")
    assert r.startswith("混雑: ") and "SQL エラー" not in r, f"query（{r}）"
    r = sqltool.describe_mtg_tables("mtg_cards_v2")
    assert r.startswith("混雑: ") and not r.startswith("エラー: "), f"describe（{r}）"

    monkeypatch.setattr(health, "_db", raise_busy)
    r = health.mtg_rag_health()
    assert r.startswith("混雑: ") and "health 失敗" not in r, f"health（{r}）"


def test_unexpected_exception_is_passed_through(monkeypatch):
    """想定外の例外は『失敗しました』に丸めず、生の例外文を素通しする。"""
    def boom(*a, **k):
        raise RuntimeError("column zzz does not exist")

    monkeypatch.setattr(sqltool, "_db_readonly", boom)
    assert "column zzz does not exist" in sqltool.query_mtg_database("SELECT 1")
    assert "column zzz does not exist" in sqltool.describe_mtg_tables()
    monkeypatch.setattr(health, "_db", boom)
    assert "column zzz does not exist" in health.mtg_rag_health()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
