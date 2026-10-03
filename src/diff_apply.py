#!/usr/bin/env python3
"""diff_apply — 集計表の差分適用（共起などの洗い替えを TRUNCATE／DELETE→INSERT から差分へ）。

理由: 論理レプリケーションで公開サーバーに流れる行を「変わった行だけ」にする（毎晩 214 万行の流し直しをやめる）・
公開サーバーの表に穴を開けない（TRUNCATE も source 別 DELETE も、適用の間は行が消えている）・索引の膨張を止める
（VM の card_cooccurrence_pkey は葉の密度 23%）。掟「導出列は冪等・値が変わる行だけ UPDATE」の表版。

使い方:
    stats = diff_apply(conn, "card_population_deck_counts", keys=("population_id","card_id"), vals=("main_deck_count",),
                       select_sql=AGG_SELECT, params=(pid,), temp_name="_new_counts")
    # scope_where で表の一部（例: population_id = %s）だけを対象にする（共起は母集団ごとに洗う・build_cooccurrence.py）
呼び出し側が commit する。一時表は ON COMMIT DROP。
"""
import time


def _eq(keys, a="t", b="n"):
    return " AND ".join(f"{a}.{k} = {b}.{k}" for k in keys)


def diff_apply(conn, table: str, keys: tuple, vals: tuple, select_sql: str, params: tuple = (),
               scope_where: str | None = None, scope_params: tuple = (), work_mem: str | None = None,
               temp_name: str = "_diff_new") -> dict:
    """新しい集計（select_sql の結果）と表の差分だけを DELETE／UPDATE／INSERT する。返り値は件数と秒。

    1 つのトランザクションで何度も呼ぶ（分子と分母を一緒に確定する）ときは、呼ぶたびに temp_name を変える
    （一時表は ON COMMIT DROP なので、同じ名前だと 2 回目で衝突する）。
    """
    t0 = time.time()
    cols = tuple(keys) + tuple(vals)
    scope_t = f" AND ({scope_where.replace('{t}', 't')})" if scope_where else ""
    with conn.cursor() as cur:
        if work_mem:
            cur.execute(f"SET LOCAL work_mem = %s", (work_mem,))
        cur.execute(f"CREATE TEMP TABLE {temp_name} ON COMMIT DROP AS {select_sql}", params)
        n_new = cur.rowcount
        cur.execute(f"ANALYZE {temp_name}")
        cur.execute(f"DELETE FROM {table} t WHERE NOT EXISTS (SELECT 1 FROM {temp_name} n WHERE {_eq(keys)}){scope_t}",
                    scope_params)
        n_del = cur.rowcount
        set_sql = ", ".join(f"{v} = n.{v}" for v in vals)
        diff_sql = " OR ".join(f"t.{v} IS DISTINCT FROM n.{v}" for v in vals)
        cur.execute(f"UPDATE {table} t SET {set_sql} FROM {temp_name} n WHERE {_eq(keys)} AND ({diff_sql})")
        n_upd = cur.rowcount
        cur.execute(f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join('n.' + c for c in cols)} FROM {temp_name} n"
                    f" WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {_eq(keys)})")
        n_ins = cur.rowcount
    return {"new_rows": n_new, "deleted": n_del, "updated": n_upd, "inserted": n_ins, "seconds": round(time.time() - t0, 1)}


def verify(conn, table: str, keys: tuple, vals: tuple, select_sql: str, params: tuple = (),
           scope_where: str | None = None, scope_params: tuple = ()) -> dict:
    """差分適用の結果が『全部作り直した結果』と一致するか（両方向の EXCEPT が 0 行）。試験用。"""
    cols = ", ".join(tuple(keys) + tuple(vals))
    scope = f" WHERE {scope_where.replace('{t}', table)}" if scope_where else ""
    with conn.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE _verify_new ON COMMIT DROP AS {select_sql}", params)
        cur.execute(f"SELECT count(*) FROM (SELECT {cols} FROM {table}{scope} EXCEPT SELECT {cols} FROM _verify_new) x", scope_params)
        only_table = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM (SELECT {cols} FROM _verify_new EXCEPT SELECT {cols} FROM {table}{scope}) x", scope_params)
        only_new = cur.fetchone()[0]
    return {"only_in_table": only_table, "only_in_new": only_new, "ok": only_table == 0 and only_new == 0}
