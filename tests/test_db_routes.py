"""test_db_routes.py — sisho/db.py の _db と _db_readonly のルーティング試験（DB 不要）。

psycopg2.connect を monkeypatch で差し替え、引数・execute・fetch・close・record_db_call・席の返却を検証する。
"""
import pytest
from db_config import DB_CONFIG
from sisho import db


class FakeCursor:
    def __init__(self, description=None, rows=None, exc=None):
        self.description = description
        self._rows = rows if rows is not None else []
        self.executed = []
        self.fetchall_called = 0
        self.fetchmany_called = []
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args):
        self.executed.append(args)
        if self._exc:
            raise self._exc

    def fetchall(self):
        self.fetchall_called += 1
        return self._rows

    def fetchmany(self, size):
        self.fetchmany_called.append(size)
        return self._rows[:size]


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


@pytest.fixture
def fake_db(monkeypatch):
    calls = []
    recorded = []

    monkeypatch.setattr(db, "record_db_call", lambda elapsed, sql: recorded.append((elapsed, sql)))

    def setup(description=(("col1",), ("col2",)), rows=None, exc=None):
        if rows is None and exc is None:
            rows = [("r1c1", "r1c2"), ("r2c1", "r2c2")]
        cur = FakeCursor(description=description, rows=rows, exc=exc)
        conn = FakeConn(cur)

        def fake_connect(**kwargs):
            calls.append(kwargs)
            return conn

        monkeypatch.setattr("psycopg2.connect", fake_connect)
        return cur, conn

    return setup, calls, recorded


def test_db_route_success(fake_db):
    """1. _db は execute(sql, params) と fetchall() を呼び、connect 引数が DB_CONFIG と一致、返り値が fetchall の結果。"""
    setup, calls, recorded = fake_db
    rows = [("a", 1), ("b", 2)]
    cur, conn = setup(rows=rows)

    slot_before = db._DB_SLOTS._value
    res = db._db("SELECT name, count FROM cards WHERE set = %s", ("MH3",))
    slot_after = db._DB_SLOTS._value

    # 項目 1
    assert calls == [DB_CONFIG], "connect の引数が DB_CONFIG と一致"
    assert cur.executed == [("SELECT name, count FROM cards WHERE set = %s", ("MH3",))], "execute(sql, params) を呼ぶ"
    assert cur.fetchall_called == 1, "fetchall() を呼ぶ"
    assert res == rows, "返り値が fetchall の結果そのもの"

    # 項目 3 & 4
    assert conn.closed is True, "conn.close() が呼ばれる"
    assert len(recorded) == 1, "record_db_call が 1 回"
    assert recorded[0][1] == "SELECT name, count FROM cards WHERE set = %s"
    assert recorded[0][0] >= 0
    assert slot_after == slot_before, "席が返っている（_DB_SLOTS._value 不変）"


def test_db_readonly_route_success(fake_db, monkeypatch):
    """2. _db_readonly は user/password/options が付き、execute(sql) だけ（params 無し）、fetchmany(max_rows)。"""
    monkeypatch.setenv("DB_PASS_ROAI", "mock_roai_secret")
    setup, calls, recorded = fake_db
    rows = [("cardA",), ("cardB",)]
    cur, conn = setup(description=(("card_name",),), rows=rows)

    slot_before = db._DB_SLOTS._value
    cols, res = db._db_readonly("SELECT card_name FROM cards", 10)
    slot_after = db._DB_SLOTS._value

    # 項目 2
    assert len(calls) == 1
    cfg = calls[0]
    assert cfg["user"] == "readonly_ai"
    assert cfg["password"] == "mock_roai_secret"
    assert cfg["options"] == "-c statement_timeout=10000"
    for k, v in DB_CONFIG.items():
        if k not in ("user", "password", "options"):
            assert cfg[k] == v
    assert cur.executed == [("SELECT card_name FROM cards",)], "execute(sql) だけ（params を渡さない）"
    assert cur.fetchmany_called == [10], "fetchmany(max_rows) を呼ぶ"
    assert cols == ["card_name"]
    assert res == rows

    # 項目 3 & 4
    assert conn.closed is True, "conn.close() が呼ばれる"
    assert len(recorded) == 1, "record_db_call が 1 回"
    assert recorded[0][1] == "SELECT card_name FROM cards"
    assert slot_after == slot_before, "席が返っている"


def test_db_readonly_description_none(fake_db):
    """2b. cur.description が None なら cols が []。"""
    setup, calls, recorded = fake_db
    cur, conn = setup(description=None, rows=[])

    cols, res = db._db_readonly("SET foo = 1", 5)
    assert cols == [], "description が None なら空リスト"
    assert res == []


def test_db_exception_cleans_up(fake_db):
    """3. execute が例外を投げても close() と record_db_call が呼ばれ、例外はそのまま伝わり、席も返る。"""
    setup, calls, recorded = fake_db
    exc = ValueError("sql syntax error")
    cur, conn = setup(exc=exc)

    slot_before = db._DB_SLOTS._value
    with pytest.raises(ValueError, match="sql syntax error"):
        db._db("SELECT syntax error", ())
    slot_after = db._DB_SLOTS._value

    assert conn.closed is True, "例外時でも conn.close() が呼ばれる"
    assert len(recorded) == 1, "例外時でも record_db_call が呼ばれる"
    assert recorded[0][1] == "SELECT syntax error"
    assert slot_after == slot_before, "例外時でも席が返る"


def test_db_readonly_exception_cleans_up(fake_db):
    """3b. _db_readonly で例外発生時も close() と record_db_call が呼ばれ、例外伝播、席返却。"""
    setup, calls, recorded = fake_db
    exc = RuntimeError("readonly error")
    cur, conn = setup(exc=exc)

    slot_before = db._DB_SLOTS._value
    with pytest.raises(RuntimeError, match="readonly error"):
        db._db_readonly("SELECT error", 10)
    slot_after = db._DB_SLOTS._value

    assert conn.closed is True, "例外時でも conn.close() が呼ばれる"
    assert len(recorded) == 1, "例外時でも record_db_call が呼ばれる"
    assert recorded[0][1] == "SELECT error"
    assert slot_after == slot_before, "例外時でも席が返る"
