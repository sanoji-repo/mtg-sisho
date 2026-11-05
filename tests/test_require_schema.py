"""require_columns / require_indexes の試験（DB に触らない・判定と DDL の口を差し替える）。

通常の実行では DDL を打たない。足りない列は止める（黙って進むと列の無いまま書いて事故になる）。
足りない索引は警告だけで続ける（答えは変わらず速さだけの問題）。--migrate のときだけ DDL を打つ。
夜間の取り込みの切り替えに --migrate を混ぜない、の前提をここで縫う。
"""
from __future__ import annotations

import contextlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import db_config  # noqa: E402


class _DDLCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, args=None):
        self.log.append(sql)


@pytest.fixture
def ddl_log(monkeypatch):
    log: list[str] = []

    @contextlib.contextmanager
    def fake_ddl_cursor(conn, timeout=None):
        yield _DDLCursor(log)

    monkeypatch.setattr(db_config, "ddl_cursor", fake_ddl_cursor)
    return log


def _missing(monkeypatch, cols=(), idx=()):
    monkeypatch.setattr(db_config, "missing_columns", lambda conn, table, columns: list(cols))
    monkeypatch.setattr(db_config, "missing_indexes", lambda conn, table, names: list(idx))


def test_columns_present_do_nothing(monkeypatch, ddl_log):
    _missing(monkeypatch)
    assert db_config.require_columns(None, "deck_list", ("bracket",), "ALTER …") is False
    assert ddl_log == []


def test_missing_column_stops_without_migrate(monkeypatch, ddl_log):
    _missing(monkeypatch, cols=["bracket"])
    with pytest.raises(SystemExit) as e:
        db_config.require_columns(None, "deck_list", ("bracket",), "ALTER …", migrate=False)
    assert "bracket" in str(e.value) and "--migrate" in str(e.value)
    assert ddl_log == [], "通常の実行で DDL を打ってはいけない"


def test_missing_column_adds_with_migrate(monkeypatch, ddl_log):
    _missing(monkeypatch, cols=["bracket"])
    assert db_config.require_columns(None, "deck_list", ("bracket",), "ALTER TABLE x ADD COLUMN bracket INTEGER;",
                                     migrate=True) is True
    assert ddl_log == ["ALTER TABLE x ADD COLUMN bracket INTEGER;"]


def test_missing_index_warns_and_continues_without_migrate(monkeypatch, ddl_log, capsys):
    _missing(monkeypatch, idx=["deck_list_tournament_event_id_idx"])
    assert db_config.require_indexes(None, "deck_list", ("deck_list_tournament_event_id_idx",), "CREATE INDEX …",
                                     migrate=False) is False
    assert "警告" in capsys.readouterr().out
    assert ddl_log == [], "通常の実行で索引を作ってはいけない"


def test_missing_index_created_with_migrate(monkeypatch, ddl_log):
    _missing(monkeypatch, idx=["deck_list_tournament_event_id_idx"])
    assert db_config.require_indexes(None, "deck_list", ("deck_list_tournament_event_id_idx",), "CREATE INDEX i …",
                                     migrate=True) is True
    assert ddl_log == ["CREATE INDEX i …"]


def test_migrate_requested_only_with_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["scrape_x.py", "--limit", "10"])
    assert not db_config.migrate_requested()
    monkeypatch.setattr(sys, "argv", ["scrape_x.py", "--migrate"])
    assert db_config.migrate_requested()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", *sys.argv[1:]]))
