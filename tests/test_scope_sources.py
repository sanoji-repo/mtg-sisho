"""test_scope_sources.py — find_partner_cards の scope→デッキ source 定数の単体試験（DB 不要）。

モジュール定数 _SCOPE_SOURCES のキー・値・順序、引数チェックによる DB 非到達、
および partners.py 内に辞書リテラルが重複して残っていないことを検証する。
"""
from pathlib import Path
import pytest
from sisho.tools import partners


def test_scope_sources_keys_and_order():
    """1. list(partners._SCOPE_SOURCES) が指定の順序と完全に一致すること。"""
    expected_keys = ["edh", "commander", "constructed", "pauper", "vintage", "precon"]
    assert list(partners._SCOPE_SOURCES) == expected_keys


def test_scope_sources_values():
    """2. 各 scope に対応するデッキ source リストが正しく定義されていること。"""
    assert partners._SCOPE_SOURCES["edh"] == ["moxfield_edh", "mtgtop8_edh"]
    assert partners._SCOPE_SOURCES["commander"] == ["moxfield_edh", "mtgtop8_edh"]
    assert partners._SCOPE_SOURCES["constructed"] == ["mtgtop8", "mtgo", "mtgo_other"]
    assert partners._SCOPE_SOURCES["pauper"] == ["mtgtop8_pauper", "mtgo_pauper"]
    assert partners._SCOPE_SOURCES["vintage"] == ["mtgtop8_vintage", "mtgo_vintage"]
    assert partners._SCOPE_SOURCES["precon"] == ["mtgjson_precon"]


def test_reject_bad_scope_without_db(monkeypatch):
    """3. 不正な scope は DB に触らずに断りの文言を返すこと。"""
    def _fail_db(*args, **kwargs):
        raise AssertionError("DB access should not occur for invalid scope")

    monkeypatch.setattr("sisho.tools.partners._name_variants", lambda n: [n])
    monkeypatch.setattr("sisho.tools.partners._db", _fail_db)

    result = partners.find_partner_cards("Lightning Bolt", scope="modern")
    assert result == "scope が不正: modern（edh/constructed/pauper/vintage/precon）"


def test_reject_bad_order_by_without_db(monkeypatch):
    """4. 不正な order_by は DB に触らずに断りの文言を返すこと。"""
    def _fail_db(*args, **kwargs):
        raise AssertionError("DB access should not occur for invalid order_by")

    monkeypatch.setattr("sisho.tools.partners._name_variants", lambda n: [n])
    monkeypatch.setattr("sisho.tools.partners._db", _fail_db)

    result = partners.find_partner_cards("Lightning Bolt", scope="edh", order_by="foo")
    assert result == "order_by が不正: foo（count / lift）"


def test_no_duplicate_dictionary_literal_in_partners_py():
    """5. partners.py ソース内の 'mtgjson_precon' 出現が 1 回だけであること。"""
    source_path = Path(partners.__file__)
    content = source_path.read_text(encoding="utf-8")
    assert content.count("mtgjson_precon") == 1
