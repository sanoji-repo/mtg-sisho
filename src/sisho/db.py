"""db.py — DB 直結の席取りと問い合わせ（2026-09-05 Step 2 で mcp_server.py から切り出し）。

接続設定は src/db_config.py（sh/ からも使われるので動かさない）。コネクションプールは
置かない（2026-09-05 裁定: 実測負荷は IP 別 8 回/分・localhost の接続 1 回は数ミリ秒・
箱は 2 コアで readonly_ai の上限 6 と席取り 5 に重ねると管理が二重になる）。
"""
import os

# ─── DB の席取り（2026-08-29・本人「6 本で弾くより順番待ち」）───
# readonly_ai の接続上限（6）にぶつかると「too many connections」で即失敗する。代わりに
# 同時に DB へ行ける道具を MCP_DB_SLOTS（既定 5・1 本は健全性確認と手動 psql 用に残す）に
# 絞り、空きが無ければ MCP_DB_WAIT_SEC（既定 20 秒＝statement_timeout 10 秒 × 2）まで待つ。
# 待ちきれなければ「混雑」を返す（失敗でなく待たせるのが目的）。MCP は 1 プロセスなので
# プロセス内セマフォで足りる。DB を叩く物が MCP 以外に増えたら pgbouncer に格上げ。
import threading as _threading
_DB_SLOTS = _threading.BoundedSemaphore(int(os.environ.get("MCP_DB_SLOTS", "5")))
_DB_WAIT_SEC = float(os.environ.get("MCP_DB_WAIT_SEC", "20"))


class DBBusy(RuntimeError):
    """DB の席が空かなかった＝混雑（2026-09-05 Step 6 作業 3）。

    RuntimeError の子なので、これを知らない受け手は従来どおり例外として扱える。
    知っている道具（query_mtg_database・describe_mtg_tables・mtg_rag_health）は
    「SQL エラー」「health 失敗」に丸めず、混雑としてそのまま返す
    （失敗ではなく待てば通る＝次の一手が違う・errors.BUSY）。"""


class _db_slot:
    """with _db_slot(): の間だけ DB の席を 1 つ占有する。"""

    def __enter__(self):
        if not _DB_SLOTS.acquire(timeout=_DB_WAIT_SEC):
            raise DBBusy(
                f"混雑: DB の順番待ちが {_DB_WAIT_SEC:.0f} 秒を超えました。少し待ってからもう一度呼んでください。")
        return self

    def __exit__(self, *exc):
        _DB_SLOTS.release()
        return False


def _db(sql: str, params: tuple) -> list[tuple]:
    import psycopg2
    from db_config import DB_CONFIG
    with _db_slot():
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        finally:
            conn.close()


# ─── 自由 SQL の口（2026-08-11・本人発案「エージェント自身が SQL を叩く路線」）───
# 鞘は三重: (1) readonly_ai ロール（GRANT SELECT のみ＝書き込みは権限層で不可能・
# 実証済み） (2) statement_timeout 10 秒 (3) 入口で SELECT/WITH 以外と複文を拒否＋
# 行数・セル長の上限で応答を制限（コンテキスト爆発防止）。

def _db_readonly(sql: str, max_rows: int) -> tuple[list[str], list[tuple]]:
    import os
    import psycopg2
    from db_config import DB_CONFIG
    cfg = dict(DB_CONFIG)
    cfg["user"] = "readonly_ai"
    cfg["password"] = os.environ.get("DB_PASS_ROAI") or ""
    cfg["options"] = "-c statement_timeout=10000"
    with _db_slot():
        conn = psycopg2.connect(**cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description] if cur.description else []
                return cols, cur.fetchmany(max_rows)
        finally:
            conn.close()
