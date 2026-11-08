#!/usr/bin/env python3
"""新しいセットの set_codes を足す段（fill_set_codes.py）の試験。

DB にも Scryfall にも繋がない。印刷の一覧は Scryfall の検索 API の返り値の形（name・digital・lang・released_at・
image_uris / card_faces）で作り、DB の行は (id, set_codes) の組で渡す。
確かめるのは「足すだけ」（消さない・NULL に戻さない・持っている行は触らない）と、紙の印刷だけ数えること。

走らせ方: pytest tests/test_fill_set_codes.py -v
"""
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import fill_set_codes as fsc  # noqa: E402


def _print(name, digital=False, lang="en", rel="2026-11-13", img=None, faces=None):
    c = {"name": name, "digital": digital, "lang": lang, "released_at": rel}
    if img:
        c["image_uris"] = {"normal": img}
    if faces:
        c["card_faces"] = faces
    return c


def test_add_only_never_removes_or_nulls():
    """NULL の行は [記号] に・値がある行は記号を足すだけ・既に持っている行は触らない。ほかの記号は消さない。"""
    prints = [_print("New Card"), _print("Old Reprint"), _print("Has It"), _print("Token Thing")]
    rows = {"New Card": [(1, None)], "Old Reprint": [(2, ["sld", "lea"])], "Has It": [(3, ["trk"])]}
    updates, stat = fsc.plan_set_codes("trk", prints, rows)
    assert updates == [(["trk"], 1), (["lea", "sld", "trk"], 2)]   # 名前の順・並びは sorted()
    assert stat == {"prints": 4, "names": 4, "not_in_db": 1, "null_to_value": 1, "added": 1, "already": 1}


def test_digital_prints_are_not_counted():
    """デジタル専用の印刷は set_codes に入れない（enrich_printings.py と違い、Arena 専用カードの行でも数えない）。"""
    updates, stat = fsc.plan_set_codes("trk", [_print("Arena Only", digital=True)], {"Arena Only": [(9, None)]})
    assert updates == [] and stat["names"] == 0


def test_same_name_two_rows_both_get_the_code():
    updates, _ = fsc.plan_set_codes("trk", [_print("Twin")], {"Twin": [(1, None), (2, ["afr"])]})
    assert updates == [(["trk"], 1), (["afr", "trk"], 2)]


def test_image_ja_fills_only_empty_and_takes_newest():
    """日本語の画像は空の行だけ。同じ名前の日本語の印刷が二つあれば発売日の新しい方・両面は表面の画像。"""
    prints_ja = [
        _print("A", lang="ja", rel="2020-01-01", img="old.jpg"),
        _print("A", lang="ja", rel="2026-11-13", img="new.jpg"),
        _print("B // C", lang="ja", faces=[{"image_uris": {"normal": "front.jpg"}}, {"image_uris": {"normal": "back.jpg"}}]),
        _print("D", lang="ja", img="d.jpg"),
        _print("E", lang="en", img="e.jpg"),          # 日本語でない印刷は使わない
    ]
    rows = {"A": [(1, None)], "B // C": [(2, None)], "D": [(3, "kept.jpg")], "E": [(4, None)]}
    assert fsc.plan_image_ja(prints_ja, rows) == [("new.jpg", 1), ("front.jpg", 2)]


def test_fetch_follows_pages_and_404_is_empty():
    pages = {
        "first": {"data": [{"name": "a"}], "has_more": True, "next_page": "second"},
        "second": {"data": [{"name": "b"}], "has_more": False},
    }
    seen = []

    def get(url):
        seen.append(url)
        return pages["first" if "q=" in url else url]

    assert [c["name"] for c in fsc.fetch_prints("trk", _get=get, _sleep=lambda s: None)] == ["a", "b"]
    assert "unique=prints" in seen[0] and "e%3Atrk" in seen[0] and seen[1] == "second"

    def not_found(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    assert fsc.fetch_prints("zzz", "ja", _get=not_found, _sleep=lambda s: None) == []

    def broken(url):
        raise urllib.error.HTTPError(url, 500, "Server Error", None, None)

    with pytest.raises(urllib.error.HTTPError):
        fsc.fetch_prints("trk", _get=broken, _sleep=lambda s: None)


def test_fetch_stops_at_page_limit():
    loop = {"data": [{"name": "x"}], "has_more": True, "next_page": "again"}
    with pytest.raises(RuntimeError):
        fsc.fetch_prints("trk", _get=lambda url: loop, _sleep=lambda s: None)


class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        self._rows = self.conn.rows if sql.lstrip().startswith("SELECT") else []

    def executemany(self, sql, seq):
        seq = list(seq)
        if seq:
            self.conn.writes.append((sql, seq))

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self.rows, self.sql, self.writes, self.commits, self.rollbacks = rows, [], [], 0, 0

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _fake_execute_batch(monkeypatch):
    import psycopg2.extras
    monkeypatch.setattr(psycopg2.extras, "execute_batch", lambda cur, sql, seq, page_size=100: cur.executemany(sql, seq))


def test_fill_dry_run_writes_nothing_and_one_failure_does_not_stop_the_rest(monkeypatch):
    _fake_execute_batch(monkeypatch)
    conn = _Conn([("New Card", 1, None, None)])

    def fetch(code, lang=None):
        if code == "bad":
            raise RuntimeError("scryfall down")
        return [] if lang else [_print("New Card")]

    logs = []
    res = fsc.fill(["bad", "trk"], dry_run=True, log=logs.append, _fetch=fetch, _connect=lambda: conn)
    assert "error" in res["bad"] and res["trk"]["null_to_value"] == 1
    assert conn.writes == [] and conn.commits == 0 and conn.rollbacks == 1
    assert any("試運転" in m for m in logs)

    res = fsc.fill(["trk"], log=logs.append, _fetch=fetch, _connect=lambda: conn)
    (sql, seq), = conn.writes
    assert "NOT (%s = ANY(coalesce(set_codes, '{}')))" in sql and seq == [(["trk"], 1, "trk")]
    assert conn.commits == 1
