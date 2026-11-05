"""test_scrape_player_hook.py — 取得 3 本（MTGTop8・MTGO・Moxfield）がプレイヤー名を口に渡す所の試験。

外への取得は全部差し替える（ネットにも DB にも触らない）。偽の接続・偽のカーソルが
execute の SQL と引数を記録する。資料は全部架空の値（実在の名前・Moxfield の URL やデッキ ID は使わない）。
口を指すときの偽の resolver は、カーソルに印の行（RESOLVE）を書く＝呼ばれた回数・引数・INSERT との前後が分かる。
"""
import os
import re
import sys

import psycopg2.extras
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import player_hook  # noqa: E402
import scrape_moxfield  # noqa: E402
import scrape_mtgo  # noqa: E402
import scrape_mtgtop8  # noqa: E402

NAME_A = "Test Player A"
NAME_B = "Test Player B"

RESOLVER_SRC = '''
def resolve_player_id(cur, source, name):
    cur.execute("RESOLVE", (source, name))
    return 42
'''


# ─── 偽物 ───────────────────────────────────────────────────────────────

class FakeCursor:
    def __init__(self, fetch_results):
        self.executed = []
        self._fetch = list(fetch_results)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetch.pop(0)

    def close(self):
        pass

    # --- 見る側 ---
    def resolves(self):
        return [p for s, p in self.executed if s == "RESOLVE"]

    def inserts(self):
        return [(s, p) for s, p in self.executed if s.startswith("INSERT INTO deck_list")]


class FakeConn:
    def __init__(self, fetch_results):
        self.cur = FakeCursor(fetch_results)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def insert_columns(sql):
    m = re.match(r"INSERT INTO deck_list \((.*?)\) VALUES", sql, re.S)
    return [c.strip() for c in m.group(1).split(",")]


@pytest.fixture(autouse=True)
def _gate_clean(monkeypatch):
    monkeypatch.delenv("PLAYER_RESOLVER", raising=False)
    player_hook._reset_for_tests()
    # カード行の一括 INSERT は偽のカーソルでは動かないので差し替える
    monkeypatch.setattr(psycopg2.extras, "execute_values", lambda *a, **k: None)
    yield
    monkeypatch.delenv("PLAYER_RESOLVER", raising=False)
    player_hook._reset_for_tests()


@pytest.fixture
def gate_open(monkeypatch, tmp_path):
    f = tmp_path / "fake_resolver.py"
    f.write_text(RESOLVER_SRC)
    monkeypatch.setenv("PLAYER_RESOLVER", str(f))
    player_hook._reset_for_tests()


# ─── MTGTop8 get_deck_ids ────────────────────────────────────────────────

EVENT_HTML = """
<html><body>
<div class="event_title">Test Event Alpha</div>
<div>12 players - 30/08/26</div>
<table>
<tr><td><a href=?e=999001&d=700001&f=MO>Test Deck One</a></td>
    <td><a href=search?player=Test+Player+A class=player>Test Player A</a></td></tr>
<tr><td><a href="?e=999001&d=700002&f=MO">Test Deck Two</a></td>
    <td><a href="search?player=Test+Player+B" class="player">Test Player B</a></td></tr>
</table>
<div>Source: Test Article 01/01/20</div>
</body></html>
"""


def test_mtgtop8_get_deck_ids_returns_deck_and_player_in_order(monkeypatch):
    monkeypatch.setattr(scrape_mtgtop8, "fetch", lambda url, *a, **k: EVENT_HTML)
    name, date, decks = scrape_mtgtop8.get_deck_ids(999001)
    assert name == "Test Event Alpha"
    assert date == "2026-08-30"
    assert decks == [(700001, "Test Deck One", NAME_A), (700002, "Test Deck Two", NAME_B)]


HTML_NO_PLAYERS_LINE = """
<html><body>
<div class="event_title">Test League Beta</div>
<div>Modern 30/08/26 Source: Test Article 01/01/20</div>
<a href=?e=999002&d=700003&f=MO>Test Deck Three</a>
<a href=search?player=Test+Player+A class=player>Test Player A</a>
</body></html>
"""


def test_mtgtop8_date_without_players_line(monkeypatch):
    monkeypatch.setattr(scrape_mtgtop8, "fetch", lambda url, *a, **k: HTML_NO_PLAYERS_LINE)
    name, date, decks = scrape_mtgtop8.get_deck_ids(999002)
    assert name == "Test League Beta"
    assert date == "2026-08-30"          # Source より後ろの 01/01/20 は拾わない
    assert decks == [(700003, "Test Deck Three", NAME_A)]


def test_mtgtop8_fewer_player_links_than_decks_gives_empty_name(monkeypatch):
    html = ("<div class=\"event_title\">Test Event Gamma</div>12 players - 01/02/26"
            "<a href=?e=1&d=11&f=MO>Test Deck A</a><a href=?e=1&d=12&f=MO>Test Deck B</a>"
            "<a href=search?player=Test+Player+A class=player>x</a>")
    monkeypatch.setattr(scrape_mtgtop8, "fetch", lambda url, *a, **k: html)
    _, _, decks = scrape_mtgtop8.get_deck_ids(1)
    assert decks == [(11, "Test Deck A", NAME_A), (12, "Test Deck B", "")]


def test_mtgtop8_arrow_only_deck_name_becomes_empty(monkeypatch):
    html = ("<div class=\"event_title\">Test Event Delta</div>5 players - 01/02/26"
            "<a href=?e=1&d=11&f=MO>&rarr;</a>"
            "<a href=search?player=Test+Player+A class=player>x</a>")
    monkeypatch.setattr(scrape_mtgtop8, "fetch", lambda url, *a, **k: html)
    _, _, decks = scrape_mtgtop8.get_deck_ids(1)
    assert decks == [(11, "", NAME_A)]


def test_mtgtop8_fetch_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(scrape_mtgtop8, "fetch", lambda url, *a, **k: None)
    assert scrape_mtgtop8.get_deck_ids(5) == ("Event 5", None, [])


# ─── MTGO extract_decks ──────────────────────────────────────────────────

MTGO_INFO = scrape_mtgo.parse_site_name("modern-challenge-32-2026-08-2199000001")

MTGO_DATA = {
    "description": "Test Challenge",
    "starttime": "2026-08-21T10:00:00",
    "final_rank": [{"loginid": "1001", "rank": "1"}, {"loginid": "1002", "rank": "2"}],
    "decklists": [
        {"loginid": "1001", "loginplayeventcourseid": "5001", "player": NAME_A,
         "main_deck": [{"qty": "4", "card_attributes": {"card_name": "Test Card One"}}],
         "sideboard_deck": [{"qty": "2", "card_attributes": {"card_name": "Test Card Two"}}]},
        {"loginid": "1002", "loginplayeventcourseid": "5002",
         "main_deck": [{"qty": "1", "card_attributes": {"card_name": "Test Card One"}}],
         "sideboard_deck": []},
        {"loginid": "1003", "player": NAME_B, "main_deck": [], "sideboard_deck": []},
    ],
}


def test_mtgo_extract_decks_carries_player():
    assert MTGO_INFO is not None
    name, date, decks = scrape_mtgo.extract_decks(MTGO_DATA, MTGO_INFO)
    assert name == "Test Challenge" and date == "2026-08-21"
    assert [d["player"] for d in decks] == [NAME_A, None]       # 名前の無い行は None・カード無しの行は落ちる
    assert decks[0]["placement"] == 1 and decks[1]["placement"] == 2
    assert decks[0]["cards"] == [("Test Card One", 4, "main"), ("Test Card Two", 2, "side")]


# ─── 保存 3 本 ───────────────────────────────────────────────────────────

def _mtgo_decks():
    _, _, decks = scrape_mtgo.extract_decks(MTGO_DATA, MTGO_INFO)
    return decks


def _save_mtgtop8(conn, source="mtgtop8"):
    return scrape_mtgtop8.save_deck(
        conn, 999001, "Test Event Alpha", "2026-08-30", 700001, "Test Deck One", NAME_A,
        "MO", [("Test Card One", 4, "main")], archetype="Test Archetype", source=source)


def _save_mtgo(conn, decks=None):
    return scrape_mtgo.save_event(conn, MTGO_INFO, "Test Challenge", "2026-08-21",
                                  decks if decks is not None else _mtgo_decks(), dry_run=False)


MOX_JSON = {
    "publicId": "test-0001", "name": "Test Moxdeck", "format": "commander",
    "publicUrl": "https://example.invalid/decks/test-0001",
    "createdByUser": {"userName": NAME_A},
    "mainboard": {"Test Card One": {"quantity": 1}},
    "commanders": {"Test Card Two": {"quantity": 1}},
}


def _save_mox(conn):
    return scrape_moxfield.save_deck(conn, MOX_JSON, bracket=3)


def test_mtgtop8_save_gate_empty():
    conn = FakeConn([(1,)])
    assert _save_mtgtop8(conn) is True
    cur = conn.cur
    (sql, params), = cur.inserts()
    assert "player_id" not in insert_columns(sql)
    assert len(insert_columns(sql)) == len(params)
    assert cur.resolves() == []
    assert NAME_A not in repr(cur.executed)          # 名前はどこにも残らない
    assert conn.commits == 1


def test_mtgtop8_save_gate_open_passes_source_and_name(gate_open):
    conn = FakeConn([(1,)])
    assert _save_mtgtop8(conn, source="mtgtop8_edh") is True
    cur = conn.cur
    assert cur.resolves() == [("mtgtop8_edh", NAME_A)]
    (sql, params), = cur.inserts()
    cols = insert_columns(sql)
    assert cols[-1] == "player_id" and params[-1] == 42
    assert len(cols) == len(params)
    assert NAME_A not in params                      # 名前でなく ID だけが列に入る


def test_mtgtop8_save_duplicate_resolves_before_insert(gate_open):
    conn = FakeConn([None])
    assert _save_mtgtop8(conn) is False
    sqls = [s for s, _ in conn.cur.executed]
    assert sqls[0] == "RESOLVE" and sqls[1].startswith("INSERT INTO deck_list")
    assert conn.rollbacks == 1 and conn.commits == 0


def test_mtgo_save_gate_empty():
    conn = FakeConn([(1,), (2,)])
    assert _save_mtgo(conn) == (2, 0)
    cur = conn.cur
    ins = cur.inserts()
    assert len(ins) == 2
    for sql, params in ins:
        assert "player_id" not in insert_columns(sql)
        assert len(insert_columns(sql)) == len(params)
    assert cur.resolves() == []
    assert NAME_A not in repr(cur.executed)


def test_mtgo_save_gate_open_calls_per_deck(gate_open):
    conn = FakeConn([(1,), (2,)])
    assert _save_mtgo(conn) == (2, 0)
    cur = conn.cur
    # 名前の無い行も（source, None）で呼ばれる＝捨てるか決めるのは resolver 側
    assert cur.resolves() == [("mtgo", NAME_A), ("mtgo", None)]
    for sql, params in cur.inserts():
        cols = insert_columns(sql)
        assert cols[-1] == "player_id" and params[-1] == 42
        assert len(cols) == len(params)


def test_mtgo_save_duplicate_resolves_before_insert(gate_open):
    conn = FakeConn([None, (2,)])
    assert _save_mtgo(conn) == (1, 1)
    sqls = [s for s, _ in conn.cur.executed]
    assert sqls[0] == "RESOLVE" and sqls[1].startswith("INSERT INTO deck_list")


def test_mtgo_save_dry_run_touches_nothing(gate_open):
    conn = FakeConn([])
    assert scrape_mtgo.save_event(conn, MTGO_INFO, "Test Challenge", "2026-08-21",
                                  _mtgo_decks(), dry_run=True) == (2, 0)
    assert conn.cur.executed == []


def test_moxfield_save_gate_empty():
    conn = FakeConn([(1,)])
    assert _save_mox(conn) is True
    cur = conn.cur
    (sql, params), = cur.inserts()
    assert "player_id" not in insert_columns(sql)
    assert len(insert_columns(sql)) == len(params)
    assert cur.resolves() == []
    assert NAME_A not in repr(cur.executed)


def test_moxfield_save_gate_open_passes_source_and_name(gate_open):
    conn = FakeConn([(1,)])
    assert _save_mox(conn) is True
    cur = conn.cur
    assert cur.resolves() == [("moxfield_edh", NAME_A)]
    (sql, params), = cur.inserts()
    cols = insert_columns(sql)
    assert cols[-1] == "player_id" and params[-1] == 42
    assert len(cols) == len(params)
    assert NAME_A not in params


def test_moxfield_save_duplicate_resolves_before_insert(gate_open):
    conn = FakeConn([None])
    assert _save_mox(conn) is False
    sqls = [s for s, _ in conn.cur.executed]
    assert sqls[0] == "RESOLVE" and sqls[1].startswith("INSERT INTO deck_list")
    assert conn.rollbacks == 1


def test_moxfield_save_without_author_passes_none(gate_open):
    deck = dict(MOX_JSON)
    deck.pop("createdByUser")
    conn = FakeConn([(1,)])
    assert scrape_moxfield.save_deck(conn, deck, bracket=None) is True
    assert conn.cur.resolves() == [("moxfield_edh", None)]


def test_names_never_printed(gate_open, capsys):
    """取得 3 本の保存は、名前をログ（標準出力・標準エラー）に出さない。"""
    _save_mtgtop8(FakeConn([(1,)]))
    _save_mtgo(FakeConn([(1,), (2,)]))
    _save_mox(FakeConn([(1,)]))
    out = capsys.readouterr()
    assert NAME_A not in out.out + out.err and NAME_B not in out.out + out.err


def test_closed_gate_stops_when_resolver_unreadable(monkeypatch, tmp_path):
    """口が壊れているときは保存の前に止まる（名前を黙って捨て続けない）。"""
    monkeypatch.setenv("PLAYER_RESOLVER", str(tmp_path / "no_such_resolver.py"))
    player_hook._reset_for_tests()
    conn = FakeConn([(1,)])
    with pytest.raises(SystemExit):
        _save_mtgtop8(conn)
    assert conn.cur.inserts() == []


# ─── 引き当てが失敗したら保存に進まない・名前を外へ出さない──────────

FAILING_RESOLVER_SRC = '''
def resolve_player_id(cur, source, name):
    cur.execute("RESOLVE", (source, name))
    raise RuntimeError(f"DETAIL: Key (source, player_name)=({source}, {name}) already exists.")
'''


@pytest.fixture
def gate_failing(monkeypatch, tmp_path):
    f = tmp_path / "failing_resolver.py"
    f.write_text(FAILING_RESOLVER_SRC)
    monkeypatch.setenv("PLAYER_RESOLVER", str(f))
    player_hook._reset_for_tests()


@pytest.mark.parametrize("save", [_save_mtgtop8, _save_mtgo, _save_mox], ids=["mtgtop8", "mtgo", "moxfield"])
def test_resolver_failure_stops_before_insert(gate_failing, save):
    conn = FakeConn([(1,)])
    with pytest.raises(player_hook.PlayerResolveError) as ei:
        save(conn)
    assert NAME_A not in str(ei.value) and ei.value.__context__ is None
    assert conn.cur.inserts() == [], "deck_list の INSERT に進まない"
    assert conn.commits == 0


# ─── 行まるごとの照らし─────────────────────────────
# 列の数と player_id だけでなく、INSERT の列と値の対応を一行まるごと期待値と比べる。
# カード行の一括 INSERT も記録して中身を見る。名前の口を開けても player_id 以外は一字も変わらないこと。

@pytest.fixture
def card_rows(monkeypatch):
    calls = []
    monkeypatch.setattr(psycopg2.extras, "execute_values",
                        lambda cur, sql, rows, *a, **k: calls.append(list(rows)))
    return calls


def _rows(conn):
    return [dict(zip(insert_columns(s), p)) for s, p in conn.cur.inserts()]


def _open_gate(monkeypatch, tmp_path):
    f = tmp_path / "fake_resolver_row.py"
    f.write_text(RESOLVER_SRC)
    monkeypatch.setenv("PLAYER_RESOLVER", str(f))
    player_hook._reset_for_tests()


EXPECT_TOP8 = {
    "deck_name": "mtgtop8_999001_700001", "set_code": "MO", "source": "mtgtop8",
    "tournament_name": "Test Event Alpha", "tournament_date": "2026-08-30",
    "format_name": scrape_mtgtop8.FORMAT_NAMES.get("MO", "MO"),
    "source_url": f"{scrape_mtgtop8.BASE_URL}/event?e=999001&d=700001&f=MO",
    "tournament_event_id": 999001, "archetype": "Test Archetype",
}


def _expect_mtgo(deck_key, placement):
    return {
        "deck_name": f"mtgo_{MTGO_INFO['site_name']}_{deck_key}", "set_code": MTGO_INFO["format_key"][:10],
        "source": MTGO_INFO["source"], "tournament_name": "Test Challenge", "tournament_date": "2026-08-21",
        "placement": placement, "format_name": MTGO_INFO["format_name"],
        "source_url": f"{scrape_mtgo.BASE_URL}/decklist/{MTGO_INFO['site_name']}",
        "tournament_event_id": MTGO_INFO["event_id"], "archetype": None,
    }


EXPECT_MOX = {
    "deck_name": "moxfield_test-0001", "set_code": scrape_moxfield.FORMAT_NAMES.get("commander", "commander"),
    "source": scrape_moxfield.SOURCE, "tournament_name": None, "tournament_date": None,
    "format_name": scrape_moxfield.FORMAT_NAMES.get("commander", "commander"),
    "source_url": "https://example.invalid/decks/test-0001", "tournament_event_id": None,
    "archetype": None, "bracket": 3,
}


def test_mtgtop8_full_row_and_cards(card_rows):
    conn = FakeConn([(7,)])
    assert _save_mtgtop8(conn) is True
    assert _rows(conn) == [EXPECT_TOP8]
    assert card_rows == [[(7, "Test Card One", 4, "main")]]


def test_mtgo_full_rows_and_cards(card_rows):
    conn = FakeConn([(7,), (8,)])
    assert _save_mtgo(conn) == (2, 0)
    assert _rows(conn) == [_expect_mtgo("5001", 1), _expect_mtgo("5002", 2)]
    assert card_rows == [[(7, "Test Card One", 4, "main"), (7, "Test Card Two", 2, "side")],
                         [(8, "Test Card One", 1, "main")]]


def test_moxfield_full_row_and_cards(card_rows):
    conn = FakeConn([(7,)])
    assert _save_mox(conn) is True
    assert _rows(conn) == [EXPECT_MOX]
    assert card_rows == [[(7, n, c, b) for n, c, b in scrape_moxfield._extract_cards(MOX_JSON)]]
    assert card_rows[0], "カード行が空ではない"


@pytest.mark.parametrize("save, n_fetch", [(_save_mtgtop8, 1), (_save_mtgo, 2), (_save_mox, 1)],
                         ids=["mtgtop8", "mtgo", "moxfield"])
def test_gate_changes_only_player_id(monkeypatch, tmp_path, card_rows, save, n_fetch):
    closed = FakeConn([(7,)] * n_fetch)
    save(closed)
    cards_closed = list(card_rows)
    card_rows.clear()
    _open_gate(monkeypatch, tmp_path)
    opened = FakeConn([(7,)] * n_fetch)
    save(opened)
    rows_open = _rows(opened)
    assert [r.pop("player_id") for r in rows_open] == [42] * n_fetch
    assert rows_open == _rows(closed), "名前の口で player_id 以外の列が変わらない"
    assert card_rows == cards_closed, "名前の口でカード行が変わらない"


# ─── 取得の脚本の verify_schema を --migrate の有り無しで呼ぶ──────

@pytest.mark.parametrize("mod, needed", [(scrape_moxfield, ["bracket"]), (scrape_mtgtop8, ["tournament_name"])],
                         ids=["moxfield", "mtgtop8"])
@pytest.mark.parametrize("migrate", [False, True], ids=["通常", "migrate"])
def test_verify_schema_follows_migrate_flag(monkeypatch, mod, needed, migrate):
    import contextlib
    import db_config
    ddl = []

    class _C:
        def execute(self, sql, args=None):
            ddl.append(sql)

    @contextlib.contextmanager
    def fake_ddl_cursor(conn, timeout=None):
        yield _C()

    monkeypatch.setattr(db_config, "missing_columns", lambda conn, table, cols: list(needed))
    monkeypatch.setattr(db_config, "missing_indexes", lambda conn, table, names: [])
    monkeypatch.setattr(db_config, "ddl_cursor", fake_ddl_cursor)
    monkeypatch.setattr(sys, "argv", ["scrape_x.py", "--limit", "5"] + (["--migrate"] if migrate else []))
    if migrate:
        mod.verify_schema(None)
        assert len(ddl) == 1 and "deck_list" in ddl[0], "--migrate のときだけ DDL を一回打つ"
    else:
        with pytest.raises(SystemExit):
            mod.verify_schema(None)
        assert ddl == [], "通常の実行では DDL を打たない"
