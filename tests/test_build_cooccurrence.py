#!/usr/bin/env python3
"""build_cooccurrence の検算（CHECK_SQL）が壊れた集計を止めること。

一時表（_cz_pd・_cz_counts・_cz_pairs）に小さな入力を置いて check() を呼ぶ。一時表はこの接続の中だけで、
本物の表には触らない。落ちるときの意味: 検算が「分母の欠落」や「成り立たない交差」を通してしまい、
壊れた集計が公開サーバーまで流れる。

走らせ方: pytest tests/test_build_cooccurrence.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import build_cooccurrence as b  # noqa: E402
from conftest import requires_db  # noqa: E402

pytestmark = requires_db


def run(decks: int, counts: list, pairs: list) -> dict:
    """母集団 1 つ（pid=1）の入力を一時表に置き、check() の結果を返す。"""
    import psycopg2
    from db_config import get_db_config
    conn = psycopg2.connect(**get_db_config())
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE _cz_pd (population_id int, deck_id int, source text, has_unresolved bool)")
            cur.execute("CREATE TEMP TABLE _cz_counts (population_id int, card_id int, main_deck_count int, side_deck_count int)")
            cur.execute('CREATE TEMP TABLE _cz_pairs (population_id int, relation "char", card_id_a int, card_id_b int,'
                        ' cooccurrence_count int)')
            cur.executemany("INSERT INTO _cz_pd VALUES (1, %s, 'x', false)", [(i,) for i in range(decks)])
            cur.executemany("INSERT INTO _cz_counts VALUES (1, %s, %s, %s)", counts)
            cur.executemany("INSERT INTO _cz_pairs VALUES (1, %s, %s, %s, %s)", pairs)
        conn.commit()          # 一時表はセッションの間残る（本番の stage と同じ形）
        return b.check(conn, 1)
    finally:
        conn.close()


def test_correct_population_passes():
    """対照: N=5・A はメイン 4・B はメイン 3・サイド 2・メイン同居 2・サイド同居 1 は通る。"""
    r = run(5, [(10, 4, 1), (20, 3, 2)], [("m", 10, 20, 2), ("s", 10, 20, 1)])
    assert r["ok"], r


@pytest.mark.parametrize("label,decks,counts,pairs,field", [
    ("B の分母の行が無い組（同居 4 は A の M=2 も超える）", 5, [(10, 2, 0)], [("s", 10, 20, 4)], "orphan_pairs"),
    ("上限は守るが交差が成り立たない（4+4−1=7 > N=5）", 5, [(10, 4, 0), (20, 0, 4)], [("s", 10, 20, 1)], "bad_pairs"),
    ("メイン同士の同居が B の分母を超える", 5, [(10, 4, None), (20, 3, None)], [("m", 10, 20, 4)], "bad_pairs"),
    ("分母が N を超える", 3, [(10, 4, None)], [], "bad_counts"),
    ("A の分母の行が無い組", 5, [(20, 3, None)], [("m", 10, 20, 1)], "orphan_pairs"),
])
def test_broken_population_is_stopped(label, decks, counts, pairs, field):
    r = run(decks, counts, pairs)
    assert not r["ok"] and r[field] == 1, (label, r)
