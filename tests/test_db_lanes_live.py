"""test_db_lanes_live.py — DB 実接続での軽いレーンタイムアウト→重いレーン並び直し試験（requires_db）。

VM で走らせる live 試験:
9. 軽いレーンで 1 秒超の重いクエリを実行 → 1 秒で statement_timeout (57014) により切られ、
   重いレーンに並び直して完走する（返り値 [(25000000,)]、所要 1 秒超、caplog に [lane] warning）。
   重いクエリは CPU だけで時間を稼ぐ直積（5000×5000・VM 実測 3.2 秒）にする。1 本の
   generate_series(1, 2 億) は関数スキャンが一時ファイルに落ちて devuser でも 10 秒を越え、
   readonly_ai では temp_file_limit(256MB) に当たる（2026-09-07 実測）。
10. 軽いクエリ SELECT 1 → [(1,)]、[lane] warning なし。
"""
import logging
import time
from conftest import requires_db
from sisho import db


@requires_db
def test_live_bypass_timeout_retries_in_heavy_lane(caplog):
    """9. 軽いレーンで 1 秒超のクエリが 57014 で切られ、重いレーンで完走する。"""
    caplog.set_level(logging.WARNING)
    t0 = time.perf_counter()
    rows = db._db("SELECT count(*) FROM generate_series(1, 5000) a CROSS JOIN generate_series(1, 5000) b", (),
                  lane=db.LANE_LIGHT)
    elapsed = time.perf_counter() - t0

    assert rows == [(25000000,)]
    assert elapsed >= 1.0, f"1秒で切られて重いレーンで完走するため1秒超（実測 {elapsed:.3f}秒）"
    assert any("[lane] bypass timeout" in rec.message for rec in caplog.records)


@requires_db
def test_live_light_query_does_not_retry(caplog):
    """10. 軽いクエリは切られず、[lane] warning も出ない。"""
    caplog.set_level(logging.WARNING)
    rows = db._db("SELECT 1", ())
    assert rows == [(1,)]
    assert not any("[lane]" in rec.message for rec in caplog.records)
