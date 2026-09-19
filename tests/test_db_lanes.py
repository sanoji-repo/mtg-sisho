"""test_db_lanes.py — DB のスロット（重いレーン 4＋バイパス 1）と statement_timeout の単体試験（DB 不要）。

偽 psycopg2 を用いて以下を検証する:
1. 軽いレーン: _HEAVY_SLOTS は減らず _DB_SLOTS だけ減る、statement_timeout=1000
2. 重いレーン: 両方のスロットが減る、statement_timeout=10000
3. 並び直し: 軽いレーンで 57014 発生時に重いレーンで再実行（warning 1行、2回接続、スロット返却）
4. 重いレーンでの 57014 は並び直さず例外
5. 軽いレーンでの 57014 以外の例外は並び直さず例外
6. 予約スロット: 重いレーンが 4 スロット埋まっても軽いレーンは通る
7. query_mtg_database は lane="heavy" で呼ぶ、describe_mtg_tables は heavy でない
8. find_partner_cards 本体は lane="heavy" で呼ぶ
9. 既定は重いレーン（2026-09-14 反転）
10. 軽いレーンを宣言してよい形かの静的検査（大きな表・全行展開・Seq Scan 確定は不可）
"""
import ast
import logging
import os

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
    """1. 軽いレーン（lane='light' を明示）: _HEAVY_SLOTS は減らず _DB_SLOTS だけ 1 減る。options に statement_timeout=1000。

    2026-09-14: 既定を重いレーンへ反転したので、軽いレーンは明示したときだけ通る（宣言し忘れは安全側）。
    """
    slot_during = {}

    def on_connect(kwargs):
        slot_during["heavy"] = db._HEAVY_SLOTS._value
        slot_during["db"] = db._DB_SLOTS._value

    setup = fake_db_lanes
    calls, recorded, conns = setup(rows=[("ok",)], on_connect=on_connect)

    h_before = db._HEAVY_SLOTS._value
    d_before = db._DB_SLOTS._value

    res = db._db("SELECT 1", (), lane=db.LANE_LIGHT)

    assert res == [("ok",)]
    assert len(calls) == 1
    assert "statement_timeout=1000" in calls[0]["options"]
    assert slot_during["heavy"] == h_before, "軽いレーンでは _HEAVY_SLOTS は消費しない"
    assert slot_during["db"] == d_before - 1, "軽いレーンでは _DB_SLOTS を 1 消費する"
    assert db._HEAVY_SLOTS._value == h_before, "終了後に戻る"
    assert db._DB_SLOTS._value == d_before, "終了後に戻る"
    assert conns[0].closed is True


def test_heavy_lane_slots_and_options(fake_db_lanes):
    """2. 重いレーン（lane='heavy'）: 呼び出し中に両方 1 減る。options に statement_timeout=10000。"""
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
    assert slot_during["heavy"] == h_before - 1, "重いレーンでは _HEAVY_SLOTS を 1 消費する"
    assert slot_during["db"] == d_before - 1, "重いレーンでは _DB_SLOTS も 1 消費する"
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

    res = db._db("SELECT slow", (), lane=db.LANE_LIGHT)

    assert res == [("retry_ok",)]
    assert len(calls) == 2, "1回目軽いレーン、2回目重いレーンで計2回接続"
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
    """4. 重いレーンでの 57014 はそのまま例外（connect 1 回・並び直さない）。"""
    setup = fake_db_lanes
    calls, recorded, conns = setup(
        exc_sequence=[QueryCanceledError("heavy timeout")],
    )

    h_before = db._HEAVY_SLOTS._value
    d_before = db._DB_SLOTS._value

    with pytest.raises(QueryCanceledError, match="heavy timeout"):
        db._db("SELECT heavy_slow", (), lane=db.LANE_HEAVY)

    assert len(calls) == 1, "重いレーンでは再試行しない"
    assert conns[0].closed is True
    assert len(recorded) == 1
    assert db._HEAVY_SLOTS._value == h_before
    assert db._DB_SLOTS._value == d_before


def test_light_lane_other_exception_no_retry(fake_db_lanes):
    """5. 軽いレーンでの 57014 以外の例外はそのまま例外（並び直さない）。"""
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
    """6. 予約スロット: _HEAVY_SLOTS を 4 回埋めても軽いレーンの _db は通る。_DB_SLOTS も埋めると両方 DBBusy。"""
    monkeypatch.setattr(db, "_DB_WAIT_SEC", 0.05)
    setup = fake_db_lanes
    setup(rows=[("fast_ok",)])

    h_acquired = []
    d_acquired = []

    try:
        # 重いレーンのスロット（4スロット）を全部埋める
        for _ in range(4):
            assert db._HEAVY_SLOTS.acquire(timeout=0.01) is True
            h_acquired.append(db._HEAVY_SLOTS)

        assert db._HEAVY_SLOTS._value == 0

        # 重いレーンのクエリはスロットが取れず DBBusy
        with pytest.raises(db.DBBusy, match="混雑"):
            db._db("SELECT heavy", (), lane=db.LANE_HEAVY)

        # 軽いレーンのクエリは予約スロット（1スロット）があるので通る！
        res = db._db("SELECT fast", (), lane=db.LANE_LIGHT)
        assert res == [("fast_ok",)]

        # さらに _DB_SLOTS の残りスロットも埋める
        while db._DB_SLOTS.acquire(timeout=0.01):
            d_acquired.append(db._DB_SLOTS)

        assert db._DB_SLOTS._value == 0

        # 全スロット埋まると軽いレーンも DBBusy
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


# ─── 9・10: 既定の向きと、軽いレーンを宣言してよい形（設計判断で既定を反転）───
# なぜ反転したか: 宣言し忘れの害が非対称だから。重いものを軽いレーンに入れると 1 秒を捨てて
# やり直し、失敗もする（_valid_formats は 11 秒待って落ち、format の検査が黙って
# 止まった）。軽いものを重いレーンに入れる害は「4 スロットの順番待ちに加わる」だけ。掟「誤発動＝有害・
# 取り逃し＝無害」に合わせ、忘れたら遅くなるだけの側へ倒す。
# 副産物として、危ないのは「軽い」と宣言する側だけになり、検査対象が有限になる（＝下の 10）。

def test_default_lane_is_heavy(fake_db_lanes):
    """9. 既定は重いレーン。宣言し忘れが安全側に倒れることを縫う。"""
    setup = fake_db_lanes
    calls, recorded, conns = setup(rows=[("ok",)])

    db._db("SELECT 1", ())

    assert len(calls) == 1
    assert "statement_timeout=10000" in calls[0]["options"], (
        f"既定は重いレーン＝10 秒（実際: {calls[0]['options']}）")


#: 軽いレーン（予約スロット）に混ぜてはいけない形。表の大きさ・全行展開・索引に乗らない関数比較。
LIGHT_LANE_FORBIDDEN = (
    ("deck_cards", "1,376 万行の表"),
    ("deck_list", "43 万行の表"),
    ("jsonb_object_keys", "全行の jsonb 展開（9/12 に公開サーバーで 10 秒 timeout した形）"),
    ("similarity(", "関数比較は pg_trgm 索引に乗らない（#850）"),
    ("COUNT(*) FROM mtg_cards_v2", "全表 COUNT（公開サーバーでは冷えると桁が変わる）"),
)


def _light_lane_calls(path):
    """そのファイルで lane=LANE_LIGHT を明示している _db 呼び出しの (SQL, 行番号)。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if name not in ("_db", "_db_readonly"):
            continue
        light = any(k.arg == "lane" and getattr(k.value, "attr", getattr(k.value, "id", "")) == "LANE_LIGHT"
                    for k in node.keywords)
        if not light or not node.args:
            continue
        sql = "".join(sub.value for sub in ast.walk(node.args[0])
                      if isinstance(sub, ast.Constant) and isinstance(sub.value, str))
        out.append((sql, node.lineno))
    return out


def test_light_lane_only_for_safe_shapes():
    """10. 軽いレーンを宣言してよいのは索引で点を引く形だけ（危ない形が混ざったら落とす）。"""
    src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "src", "sisho"))
    checked, bad = 0, []
    for dirpath, _dirs, files in os.walk(src):
        if "__pycache__" in dirpath:
            continue
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            for sql, lineno in _light_lane_calls(path):
                checked += 1
                for pat, why in LIGHT_LANE_FORBIDDEN:
                    if pat in sql:
                        bad.append(f"{os.path.relpath(path, src)}:{lineno} に「{pat}」（{why}）: {sql[:70]}")
    assert checked >= 15, f"軽いレーンの宣言が急に減った（{checked} 箇所）＝既定の反転が戻されていないか"
    assert not bad, "軽いレーンに重い形が混ざっている:\n" + "\n".join(bad)


def test_light_lane_shape_check_catches_bad(tmp_path):
    """10 の検査が実際に危ない形を捕まえること（検査そのものの反証・偽のソースで確かめる）。"""
    f = tmp_path / "fake_tool.py"
    f.write_text(
        "from sisho.db import LANE_LIGHT, _db\n"
        "def x():\n"
        "    return _db(\"SELECT count(*) FROM deck_cards\", (), lane=LANE_LIGHT)\n",
        encoding="utf-8")

    found = _light_lane_calls(str(f))

    assert len(found) == 1, "lane=LANE_LIGHT の呼び出しを 1 つ見つける"
    assert any(pat in found[0][0] for pat, _ in LIGHT_LANE_FORBIDDEN), (
        f"危ない形（deck_cards）を検知できる: {found[0][0]!r}")
