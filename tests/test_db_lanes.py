"""test_db_lanes.py — DB の席（重い線 4＋バイパス 1）と statement_timeout の単体試験（DB 不要）。

偽 psycopg2 を用いて以下を検証する:
1. 軽い線: _HEAVY_SLOTS は減らず _DB_SLOTS だけ減る、statement_timeout=1000
2. 重い線: 両方のスロットが減る、statement_timeout=10000
3. 並び直し: 軽い線で 57014 発生時に重い線で再実行（warning 1行、2回接続、スロット返却）
4. 重い線での 57014 は並び直さず例外
5. 軽い線での 57014 以外の例外は並び直さず例外
6. 予約席: 重い線が 4 席埋まっても軽い線は通る
7. query_mtg_database は lane="heavy" で呼ぶ、describe_mtg_tables は heavy でない
8. find_partner_cards 本体は lane="heavy" で呼ぶ
"""
import logging
import pytest
from sisho import db


class QueryCanceledError(Exception):
    pgcode = "57014"


class OtherDBError(Exception):
    pgcode = "42601"


class FakeCursor:
    def __init__(self, description=None, rows=None, exc_sequence=None):
        self.description = description
        self._rows = rows if rows is not None else []
        self.executed = []
        self._exc_sequence = exc_sequence if exc_sequence is not None else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args):
        self.executed.append(args)
        if self._exc_sequence:
            exc = self._exc_sequence.pop(0)
            if exc:
                raise exc

    def fetchall(self):
        return self._rows

    def fetchmany(self, size):
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
def fake_db_lanes(monkeypatch):
    calls = []
    recorded = []
    conns = []

    monkeypatch.setattr(db, "record_db_call", lambda elapsed, sql: recorded.append((elapsed, sql)))

    def setup(description=(("col1",),), rows=None, exc_sequence=None, on_connect=None):
        if rows is None:
            rows = [("r1",)]
        shared_exc = list(exc_sequence) if exc_sequence is not None else []

        def fake_connect(**kwargs):
            calls.append(kwargs)
            if on_connect:
                on_connect(kwargs)
            cur = FakeCursor(description=description, rows=rows, exc_sequence=shared_exc)
            conn = FakeConn(cur)
            conns.append(conn)
            return conn

        monkeypatch.setattr("psycopg2.connect", fake_connect)
        return calls, recorded, conns

    return setup


def test_light_lane_slots_and_options(fake_db_lanes):
    """1. 軽い線: 呼び出し中に _HEAVY_SLOTS._value は減らず _DB_SLOTS._value だけ 1 減る。options に statement_timeout=1000。"""
    slot_during = {}

    def on_connect(kwargs):
        slot_during["heavy"] = db._HEAVY_SLOTS._value
        slot_during["db"] = db._DB_SLOTS._value

    setup = fake_db_lanes
    calls, recorded, conns = setup(rows=[("ok",)], on_connect=on_connect)

    h_before = db._HEAVY_SLOTS._value
    d_before = db._DB_SLOTS._value

    res = db._db("SELECT 1", ())

    assert res == [("ok",)]
    assert len(calls) == 1
    assert "statement_timeout=1000" in calls[0]["options"]
    assert slot_during["heavy"] == h_before, "軽い線では _HEAVY_SLOTS は消費しない"
    assert slot_during["db"] == d_before - 1, "軽い線では _DB_SLOTS を 1 消費する"
    assert db._HEAVY_SLOTS._value == h_before, "終了後に戻る"
    assert db._DB_SLOTS._value == d_before, "終了後に戻る"
    assert conns[0].closed is True


def test_heavy_lane_slots_and_options(fake_db_lanes):
    """2. 重い線（lane='heavy'）: 呼び出し中に両方 1 減る。options に statement_timeout=10000。"""
    slot_during = {}

    def on_connect(kwargs):
        slot_during["heavy"] = db._HEAVY_SLOTS._value
        slot_during["db"] = db._DB_SLOTS._value

    setup = fake_db_lanes
    calls, recorded, conns = setup(rows=[("heavy_ok",)], on_connect=on_connect)

    h_before = db._HEAVY_SLOTS._value
    d_before = db._DB_SLOTS._value

    res = db._db("SELECT heavy", (), lane=db.LANE_HEAVY)

    assert res == [("heavy_ok",)]
    assert len(calls) == 1
    assert "statement_timeout=10000" in calls[0]["options"]
    assert slot_during["heavy"] == h_before - 1, "重い線では _HEAVY_SLOTS を 1 消費する"
    assert slot_during["db"] == d_before - 1, "重い線では _DB_SLOTS も 1 消費する"
    assert db._HEAVY_SLOTS._value == h_before, "終了後に戻る"
    assert db._DB_SLOTS._value == d_before, "終了後に戻る"
    assert conns[0].closed is True


def test_light_lane_retries_in_heavy_lane_on_57014(fake_db_lanes, caplog):
    """3. 並び直し: 1 回目 57014 → 2 回目成功。返り値は 2 回目の結果、connect 2 回、warning 1 行、両スロット復帰。"""
    caplog.set_level(logging.WARNING)
    setup = fake_db_lanes
    calls, recorded, conns = setup(
        rows=[("retry_ok",)],
        exc_sequence=[QueryCanceledError("statement timeout"), None],
    )

    h_before = db._HEAVY_SLOTS._value
    d_before = db._DB_SLOTS._value

    res = db._db("SELECT slow", ())

    assert res == [("retry_ok",)]
    assert len(calls) == 2, "1回目軽い線、2回目重い線で計2回接続"
    assert "statement_timeout=1000" in calls[0]["options"]
    assert "statement_timeout=10000" in calls[1]["options"]
    assert conns[0].closed is True, "1回目の接続も close されている"
    assert conns[1].closed is True, "2回目の接続も close されている"
    assert len(recorded) == 2, "record_db_call は 2 回"

    # warning の確認
    lane_logs = [rec for rec in caplog.records if "[lane]" in rec.message]
    assert len(lane_logs) == 1
    assert "bypass timeout(1000ms) -> heavy" in lane_logs[0].message
    assert "SELECT slow" in lane_logs[0].message

    assert db._HEAVY_SLOTS._value == h_before
    assert db._DB_SLOTS._value == d_before


def test_heavy_lane_57014_no_retry(fake_db_lanes):
    """4. 重い線での 57014 はそのまま例外（connect 1 回・並び直さない）。"""
    setup = fake_db_lanes
    calls, recorded, conns = setup(
        exc_sequence=[QueryCanceledError("heavy timeout")],
    )

    h_before = db._HEAVY_SLOTS._value
    d_before = db._DB_SLOTS._value

    with pytest.raises(QueryCanceledError, match="heavy timeout"):
        db._db("SELECT heavy_slow", (), lane=db.LANE_HEAVY)

    assert len(calls) == 1, "重い線では再試行しない"
    assert conns[0].closed is True
    assert len(recorded) == 1
    assert db._HEAVY_SLOTS._value == h_before
    assert db._DB_SLOTS._value == d_before


def test_light_lane_other_exception_no_retry(fake_db_lanes):
    """5. 軽い線での 57014 以外の例外はそのまま例外（並び直さない）。"""
    setup = fake_db_lanes
    calls, recorded, conns = setup(
        exc_sequence=[OtherDBError("syntax error")],
    )

    h_before = db._HEAVY_SLOTS._value
    d_before = db._DB_SLOTS._value

    with pytest.raises(OtherDBError, match="syntax error"):
        db._db("SELECT syntax_error", ())

    assert len(calls) == 1, "57014 以外は並び直さない"
    assert conns[0].closed is True
    assert len(recorded) == 1
    assert db._HEAVY_SLOTS._value == h_before
    assert db._DB_SLOTS._value == d_before


def test_reserved_slot_for_light_lane(fake_db_lanes, monkeypatch):
    """6. 予約席: _HEAVY_SLOTS を 4 回埋めても軽い線の _db は通る。_DB_SLOTS も埋めると両方 DBBusy。"""
    monkeypatch.setattr(db, "_DB_WAIT_SEC", 0.05)
    setup = fake_db_lanes
    setup(rows=[("fast_ok",)])

    h_acquired = []
    d_acquired = []

    try:
        # 重い線の席（4席）を全部埋める
        for _ in range(4):
            assert db._HEAVY_SLOTS.acquire(timeout=0.01) is True
            h_acquired.append(db._HEAVY_SLOTS)

        assert db._HEAVY_SLOTS._value == 0

        # 重い線のクエリは席が取れず DBBusy
        with pytest.raises(db.DBBusy, match="混雑"):
            db._db("SELECT heavy", (), lane=db.LANE_HEAVY)

        # 軽い線のクエリは予約席（1席）があるので通る！
        res = db._db("SELECT fast", (), lane=db.LANE_LIGHT)
        assert res == [("fast_ok",)]

        # さらに _DB_SLOTS の残り席も埋める
        while db._DB_SLOTS.acquire(timeout=0.01):
            d_acquired.append(db._DB_SLOTS)

        assert db._DB_SLOTS._value == 0

        # 全席埋まると軽い線も DBBusy
        with pytest.raises(db.DBBusy, match="混雑"):
            db._db("SELECT fast", (), lane=db.LANE_LIGHT)

    finally:
        while d_acquired:
            d_acquired.pop().release()
        while h_acquired:
            h_acquired.pop().release()


def test_query_mtg_database_uses_heavy_lane(monkeypatch):
    """7. query_mtg_database は _db_readonly を lane='heavy' で呼ぶ。describe_mtg_tables は heavy でない。"""
    from sisho.tools import sql

    calls = []

    def mock_db_readonly(q, max_rows, lane=db.LANE_LIGHT):
        calls.append({"q": q, "max_rows": max_rows, "lane": lane})
        return ["col"], [("row",)]

    monkeypatch.setattr(sql, "_db_readonly", mock_db_readonly)

    # 1) query_mtg_database
    sql.query_mtg_database("SELECT 1")
    assert len(calls) == 1
    assert calls[0]["lane"] == db.LANE_HEAVY

    # 2) describe_mtg_tables
    calls.clear()
    sql.describe_mtg_tables()
    assert len(calls) == 1
    assert calls[0]["lane"] != db.LANE_HEAVY
    assert calls[0]["lane"] == db.LANE_LIGHT


def test_find_partner_cards_uses_heavy_lane(monkeypatch):
    """8. find_partner_cards の本体は _db を lane='heavy' で呼ぶ。"""
    from sisho.tools import partners

    calls = []

    monkeypatch.setattr(partners, "_name_variants", lambda n: [n])

    def mock_db(query, params, lane=db.LANE_LIGHT):
        calls.append({"query": query, "params": params, "lane": lane})
        return []

    monkeypatch.setattr(partners, "_db", mock_db)

    res = partners.find_partner_cards("Sol Ring")
    assert "共起なし" in res
    assert len(calls) == 1
    assert calls[0]["lane"] == db.LANE_HEAVY
