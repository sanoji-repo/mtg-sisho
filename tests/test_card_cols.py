"""test_card_cols.py — search_mtg_cards の列名一覧（_CARD_COLS）の単体試験（DB 不要）。

モジュール定数 _CARD_COLS の列名順序・完全一致・重複なし、および cards.py ソースコード内に
重複する列名リテラル文字列が残っていないことを検証する。
"""
from pathlib import Path
from sisho.tools import cards


EXPECTED_COLS_STR = (
    "card_name, japanese_name, type_line, mana_cost, power, toughness, rarity, "
    "oracle_text, japanese_oracle_text, edhrec_rank, name_display, digital, set_codes, "
    "name_en_front, name_en_back, name_ja_front, name_ja_back"
)


def test_card_cols_joined_string():
    """1. ', '.join(cards._CARD_COLS) が期待される SELECT 列の文字列と完全一致すること。"""
    assert ", ".join(cards._CARD_COLS) == EXPECTED_COLS_STR


def test_card_cols_count_and_uniqueness():
    """2. len(cards._CARD_COLS) == 17 で重複が無いこと（2026-09-16 に set_codes を追加）。"""
    assert len(cards._CARD_COLS) == 17
    assert len(set(cards._CARD_COLS)) == 17


def test_card_cols_head_and_tail():
    """3. 先頭が 'card_name' で末尾 4 つが面の列名であること（_row での pop 前提）。"""
    assert cards._CARD_COLS[0] == "card_name"
    assert cards._CARD_COLS[-4:] == (
        "name_en_front",
        "name_en_back",
        "name_ja_front",
        "name_ja_back",
    )


def test_no_inline_cols_literal_in_cards_py():
    """4. cards.py のソース内に 'card_name, japanese_name, type_line' リテラルが残っていないこと。"""
    source_path = Path(cards.__file__)
    content = source_path.read_text(encoding="utf-8")
    assert "card_name, japanese_name, type_line" not in content
