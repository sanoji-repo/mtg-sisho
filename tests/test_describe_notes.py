"""test_describe_notes.py — describe_mtg_tables の表注記（_table_note_lines）の試験（DB 不要）。

_db_readonly を monkeypatch して偽のスキーマ情報を返し、_table_note_lines および
describe_mtg_tables の一覧出力・単表出力での注記と収録セット行の並び・SQL発行回数を検証する。
"""
import pytest
from sisho.tools import sql


@pytest.fixture
def fake_db(monkeypatch):
    calls = []

    def fake(query, max_rows):
        calls.append(query)
        if "pg_stat_user_tables" in query:
            cols = ["schemaname", "relname", "n_live_tup"]
            rows = [
                ("public", "mtg_cards_v2", 100),
                ("public", "limited_card_stats", 50),
                ("public", "deck_list", 10),
            ]
            return cols, rows
        elif "information_schema.columns" in query:
            cols = ["table_schema", "column_name", "data_type"]
            rows = [("public", "gih_wr", "numeric")]
            return cols, rows
        elif "FROM limited_card_stats" in query:
            cols = ["expansion", "event_type", "count"]
            rows = [("SOS", "PremierDraft", 300)]
            return cols, rows
        return [], []

    monkeypatch.setattr("sisho.tools.sql._db_readonly", fake)
    return calls


def test_table_note_lines_unknown():
    """1. _table_note_lines("nope") == []。"""
    assert sql._table_note_lines("nope") == []


def test_table_note_lines_single_row():
    """2. _table_note_lines("mtg_cards_v2") は 1 行で "- mtg_cards_v2: " + _TABLE_NOTES["mtg_cards_v2"]。"""
    lines = sql._table_note_lines("mtg_cards_v2")
    assert lines == [f"- mtg_cards_v2: {sql._TABLE_NOTES['mtg_cards_v2']}"]


def test_table_note_lines_limited_card_stats(fake_db, monkeypatch):
    """3. _table_note_lines("limited_card_stats") は 2 行。_limited_sets_note を "" にすると 1 行。"""
    lines = sql._table_note_lines("limited_card_stats")
    assert len(lines) == 2
    assert lines[0] == f"- limited_card_stats: {sql._TABLE_NOTES['limited_card_stats']}"
    assert lines[1].startswith("  収録セット")

    monkeypatch.setattr("sisho.tools.sql._limited_sets_note", lambda: "")
    lines_empty = sql._table_note_lines("limited_card_stats")
    assert len(lines_empty) == 1
    assert lines_empty[0] == f"- limited_card_stats: {sql._TABLE_NOTES['limited_card_stats']}"


def test_describe_tables_list_notes(fake_db):
    """4. describe_mtg_tables() 一覧で注記の並び、直後の収録セット行、- deck_list: が無いこと。"""
    out = sql.describe_mtg_tables()
    lines = out.splitlines()

    # - mtg_cards_v2: と - limited_card_stats: が存在
    assert any(line.startswith("- mtg_cards_v2:") for line in lines)
    assert any(line.startswith("- limited_card_stats:") for line in lines)
    # - deck_list: の行は無い
    assert not any(line.startswith("- deck_list:") for line in lines)

    # 後者の直後の行が `  収録セット` で始まる
    idx_lcs = next(i for i, line in enumerate(lines) if line.startswith("- limited_card_stats:"))
    assert lines[idx_lcs + 1].startswith("  収録セット")

    # 注記の並びは _TABLE_NOTES の定義順
    note_keys_in_lines = []
    for line in lines:
        if line.startswith("- "):
            key = line.split(":", 1)[0][2:]
            if key in sql._TABLE_NOTES:
                note_keys_in_lines.append(key)
    expected_order = [k for k in sql._TABLE_NOTES if k in ("mtg_cards_v2", "limited_card_stats")]
    assert note_keys_in_lines == expected_order


def test_describe_tables_single_table_notes(fake_db):
    """5. describe_mtg_tables("limited_card_stats") で列行の後に注記と収録セット。deck_list には無い。"""
    out_lcs = sql.describe_mtg_tables("limited_card_stats")
    lines_lcs = out_lcs.splitlines()

    assert any("gih_wr | numeric" in line for line in lines_lcs)
    idx_col = next(i for i, line in enumerate(lines_lcs) if "gih_wr | numeric" in line)
    idx_note = next(i for i, line in enumerate(lines_lcs) if line.startswith("- limited_card_stats:"))
    assert idx_col < idx_note
    assert lines_lcs[idx_note + 1].startswith("  収録セット")

    out_deck = sql.describe_mtg_tables("deck_list")
    assert not any(line.startswith("- deck_list:") for line in out_deck.splitlines())


def test_sql_query_count_for_limited_sets_note(fake_db):
    """6. fake が _limited_sets_note 用の SQL を受けた回数を数え、deck_list で 0 回、limited_card_stats で 1 回。"""
    calls = fake_db

    calls.clear()
    sql.describe_mtg_tables("deck_list")
    assert sum(1 for q in calls if "FROM limited_card_stats" in q) == 0

    calls.clear()
    sql.describe_mtg_tables("limited_card_stats")
    assert sum(1 for q in calls if "FROM limited_card_stats" in q) == 1
