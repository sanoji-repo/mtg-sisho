#!/usr/bin/env python3
"""query_mtg_database・describe_mtg_tables の振る舞い（Step 4・2026-09-05）。

SELECT 以外の拒否・複文・先頭コメントの札は tests/test_draft_set.py が縫っているので
ここでは重ねない。ここで縫うのは **応答の抑制（行数・セル長）と日本語名の同伴、
スキーマの窓の形**。行数概算や列の並びは環境で動くので、値でなく形で縫う。

走らせ方: pytest tests/test_tools_sql.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402
import sisho.tools.sql as sqltool  # noqa: E402  monkeypatch はこちら側
from conftest import requires_db  # noqa: E402

pytestmark = requires_db

q = m.query_mtg_database.fn if hasattr(m.query_mtg_database, "fn") else m.query_mtg_database
dt = m.describe_mtg_tables.fn if hasattr(m.describe_mtg_tables, "fn") else m.describe_mtg_tables


def _body(text):
    """見出し行から『（N 行返却…）』の手前までの本文行。"""
    return [ln for ln in text.split("\n")[1:] if ln and not ln.startswith("（")]


def test_query_max_rows_bounds():
    """max_rows は 1〜50 に丸める（コンテキスト爆発を入口で止める）。"""
    r = q("SELECT generate_series(1,100) AS n", 999)
    assert len(_body(r)) == 50, "上限 50"
    assert "（50 行返却・上限 50）" in r, "何行返したかを返り値で言う"

    r = q("SELECT generate_series(1,100) AS n", 0)
    assert len(_body(r)) == 1 and "上限 1）" in r, "0 は 1 に上げる"

    r = q("SELECT generate_series(1,10) AS n", 3)
    assert _body(r) == ["1", "2", "3"], "指定どおりの行数"


def test_query_cell_truncation():
    """長いセルは 160 字で切って … を付ける（列は消さない）。"""
    r = q("SELECT repeat('x', 300) AS s", 1)
    cell = _body(r)[0]
    assert len(cell) == 158 and cell.endswith("…"), f"157 字＋…（{len(cell)} 字）"
    short = q("SELECT repeat('x', 160) AS s", 1)
    assert len(_body(short)[0]) == 160 and "…" not in _body(short)[0], "160 字ちょうどは切らない"


def test_query_attaches_display_next_to_name_column():
    """カード名の列の右隣に <列>_display（完成形）を同伴する（列名でなく値で判定）。"""
    r = q("SELECT card_name AS pname FROM mtg_cards_v2"
          " WHERE card_name IN ('Sol Ring','Lightning Bolt') ORDER BY 1", 5)
    assert r.split("\n")[0] == "pname | pname_display", "別名の列でも右隣に付く（値で判定）"
    assert "Lightning Bolt | 《稲妻/Lightning Bolt》" in r, "完成形をそのまま同伴"
    assert "自分で訳さない・組み立てない・略さない" in r, "使い方の注記が返り値に載る"


def test_query_display_not_attached_twice():
    """すでに完成形の列（*_display）があるときは同伴しない。"""
    r = q("SELECT card_name, name_display FROM mtg_cards_v2 WHERE card_name = 'Sol Ring'", 5)
    assert r.split("\n")[0] == "card_name | name_display", "列を増やさない"
    assert "card_name_display" not in r


def test_query_no_display_for_non_name_columns():
    """カード名でない文字列列には付けない（過半がカード名の列だけ名前列と見なす）。"""
    r = q("SELECT 'not a card' AS s, 'also not' AS t", 5)
    assert r.split("\n")[0] == "s | t", "雑多な文字列列は素通り"

    cols, rows, note = sqltool._attach_japanese_names(
        ["x"], [("Sol Ring",), ("notacard",), ("alsonot",)])
    assert cols == ["x"] and note == "", "過半に満たなければ名前列と見なさない（1/3）"
    cols, _, note = sqltool._attach_japanese_names(
        ["x"], [("Sol Ring",), ("Lightning Bolt",), ("notacard",)])
    assert cols == ["x", "x_display"] and note, "過半（2/3）なら名前列"


def test_query_zero_rows_and_error():
    assert q("SELECT 1 WHERE false") == "0 行（クエリは成功）。", "0 行は成功だと言う"
    r = q("SELECT nosuchcol FROM mtg_cards_v2 LIMIT 1")
    assert r.startswith("SQL エラー: ") and "nosuchcol" in r, "DB のエラーは文字列で返す（例外を投げない）"
    assert len(r) <= 400 + len("SQL エラー: "), "エラー本文は 400 字で切る"


def test_describe_all_tables():
    """table_name 省略で一覧＋行数概算＋主要な表の注記。"""
    r = dt()
    assert r.split("\n")[0] == "テーブル | 行数概算", "見出し"
    names = [ln.split(" | ")[0] for ln in r.split("\n")[1:] if " | " in ln]
    for t in ("mtg_cards_v2", "mtg_rules", "card_rulings", "deck_list"):
        assert t in names, f"主要な表が一覧に出る（{t}）"
    assert "- mtg_cards_v2: " in r, "表ごとの注記（返り値の自己完結）"
    assert "収録セット（expansion 列の値" in r, "limited_card_stats には実測の収録セットを添える"


def test_describe_one_table():
    """table_name 指定でその表の列名・型。注記も添う。"""
    r = dt("mtg_cards_v2")
    assert r.split("\n")[0] == "mtg_cards_v2 の列:", "見出し"
    cols = {ln.split(" | ")[0] for ln in r.split("\n")[1:] if " | " in ln}
    for c in ("card_name", "japanese_name", "name_display", "name_en_front", "name_en_back"):
        assert c in cols, f"名前の正本の列が出る（{c}）"
    assert "- mtg_cards_v2: " in r, "注記も添う"

    assert dt("public.mtg_rules").split("\n")[0] == "mtg_rules の列:", (
        "スキーマ付きでも受ける（public はそのまま表名で返す）")


def test_describe_unknown_table():
    assert dt("no_such_table_xyz") == "テーブルなし: no_such_table_xyz"


def test_describe_identifier_is_sanitized():
    """識別子は英数字と _ だけ残す（_db_readonly はプレースホルダを受けないので文字種で守る）。"""
    assert sqltool._ident("mtg_cards_v2") == "mtg_cards_v2"
    assert sqltool._ident("a;b'c d-1") == "abcd1", "記号・空白は落ちる（_ だけ残る）"
    assert sqltool._ident("limited_card_stats") == "limited_card_stats"
    r = dt("mtg_rules'; DROP TABLE x; --")
    assert r.startswith("テーブルなし:"), f"注入は識別子の無害化で落ちる（{r[:60]}）"
    assert sqltool._db_readonly("SELECT to_regclass('public.mtg_rules') IS NOT NULL", 1)[1][0][0] is True, (
        "mtg_rules は消えていない")


def test_describe_survives_db_error(monkeypatch):
    """DB が answers を返さないときも例外を投げず文字列で返す。"""
    monkeypatch.setattr(sqltool, "_db_readonly", lambda sql, n: (_ for _ in ()).throw(RuntimeError("boom")))
    r = dt()
    assert r.startswith("エラー: ") and "boom" in r, "例外は文字列に畳む"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
