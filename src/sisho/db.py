"""db.py — DB 直結の席取りと問い合わせ（2026-09-05 Step 2 で mcp_server.py から切り出し）。

接続設定は src/db_config.py（sh/ からも使われるので動かさない）。コネクションプールは
置かない（2026-09-05 裁定: 実測負荷は IP 別 8 回/分・localhost の接続 1 回は数ミリ秒・
箱は 2 コアで readonly_ai の上限 6 と席取り 5 に重ねると管理が二重になる）。

線（2026-09-07 本人裁定）:
DB の席は「重い線 4＋バイパス 1」に分ける。軽い線（バイパス）は statement_timeout 1 秒で、
切られたら（57014）席を返して重い線（10 秒）に並び直す。重い線は 4 席までなので軽い線には
常に 1 席予約が残る。既定は軽い線で、重い線は呼び出し側（query_mtg_database と
find_partner_cards 本体）が明示する。
"""
import os
import time

from sisho.toollog import record_db_call

# ─── DB の席取り（2026-08-29・本人「6 本で弾くより順番待ち」）───
# readonly_ai の接続上限（6）にぶつかると「too many connections」で即失敗する。代わりに
# 同時に DB へ行ける道具を MCP_DB_SLOTS（既定 5・1 本は健全性確認と手動 psql 用に残す）に
# 絞り、空きが無ければ MCP_DB_WAIT_SEC（既定 20 秒＝statement_timeout 10 秒 × 2）まで待つ。
# 待ちきれなければ「混雑」を返す（失敗でなく待たせるのが目的）。MCP は 1 プロセスなので
# プロセス内セマフォで足りる。DB を叩く物が MCP 以外に増えたら pgbouncer に格上げ。
#
# 2026-09-07 本人裁定: 席を「重い線 4＋バイパス 1」に分ける。
# 重い線は _HEAVY_SLOTS と _DB_SLOTS の両方を取り、軽い線は _DB_SLOTS のみを取る。
# 重い線が 4 席埋まっても、軽い線には必ず 1 席予約が残る。
import threading as _threading

_DB_SLOTS_N = int(os.environ.get("MCP_DB_SLOTS", "5"))
_DB_SLOTS = _threading.BoundedSemaphore(_DB_SLOTS_N)

_HEAVY_SLOTS_N = int(os.environ.get("MCP_DB_SLOTS_HEAVY", "4"))
if _HEAVY_SLOTS_N >= _DB_SLOTS_N:
    # MCP_DB_SLOTS_HEAVY >= MCP_DB_SLOTS の場合は予約席が必ず 1 つ残るよう MCP_DB_SLOTS - 1 に丸める
    _HEAVY_SLOTS_N = max(1, _DB_SLOTS_N - 1)
_HEAVY_SLOTS = _threading.BoundedSemaphore(_HEAVY_SLOTS_N)

BYPASS_TIMEOUT_MS = int(os.environ.get("MCP_DB_BYPASS_TIMEOUT_MS", "1000"))
HEAVY_TIMEOUT_MS = int(os.environ.get("MCP_DB_HEAVY_TIMEOUT_MS", "10000"))

LANE_LIGHT = "light"
LANE_HEAVY = "heavy"

_DB_WAIT_SEC = float(os.environ.get("MCP_DB_WAIT_SEC", "20"))


class DBBusy(RuntimeError):
    """DB の席が空かなかった＝混雑（2026-09-05 Step 6 作業 3）。

    RuntimeError の子なので、これを知らない受け手は従来どおり例外として扱える。
    知っている道具（query_mtg_database・describe_mtg_tables・mtg_rag_health）は
    「SQL エラー」「health 失敗」に丸めず、混雑としてそのまま返す
    （失敗ではなく待てば通る＝次の一手が違う・errors.BUSY）。"""


class _db_slot:
    """with _db_slot(lane=...): の間だけ DB の席を占有する。"""

    def __init__(self, lane: str = LANE_LIGHT):
        self.lane = lane
        self._acquired = []

    def __enter__(self):
        t0 = time.monotonic()
        timeout = _DB_WAIT_SEC

        if self.lane == LANE_HEAVY:
            # 重い線: _HEAVY_SLOTS を取ってから _DB_SLOTS を取る（この順・逆にしない）
            if not _HEAVY_SLOTS.acquire(timeout=timeout):
                raise DBBusy(
                    f"混雑: DB の順番待ちが {_DB_WAIT_SEC:.0f} 秒を超えました。少し待ってからもう一度呼んでください。")
            self._acquired.append(_HEAVY_SLOTS)

            elapsed = time.monotonic() - t0
            rem_timeout = max(0.0, timeout - elapsed)

            if not _DB_SLOTS.acquire(timeout=rem_timeout):
                # 2 つ目が取れなければ 1 つ目を返して DBBusy
                self._acquired.pop().release()
                raise DBBusy(
                    f"混雑: DB の順番待ちが {_DB_WAIT_SEC:.0f} 秒を超えました。少し待ってからもう一度呼んでください。")
            self._acquired.append(_DB_SLOTS)
        else:
            # 軽い線: _DB_SLOTS だけ取る（＝5 席のどれでも座れる）
            if not _DB_SLOTS.acquire(timeout=timeout):
                raise DBBusy(
                    f"混雑: DB の順番待ちが {_DB_WAIT_SEC:.0f} 秒を超えました。少し待ってからもう一度呼んでください。")
            self._acquired.append(_DB_SLOTS)

        return self

    def __exit__(self, *exc):
        # 取った順の逆に返す
        while self._acquired:
            sem = self._acquired.pop()
            sem.release()
        return False


def _run(cfg: dict, sql: str, params: tuple | None, fetch, lane: str = LANE_HEAVY):
    import logging
    import psycopg2
    from sisho.toollog import JOURNAL

    cfg_run = dict(cfg)
    timeout_ms = HEAVY_TIMEOUT_MS if lane == LANE_HEAVY else BYPASS_TIMEOUT_MS
    opt = f"-c statement_timeout={timeout_ms}"
    if cfg_run.get("options"):
        cfg_run["options"] = f"{cfg_run['options']} {opt}"
    else:
        cfg_run["options"] = opt

    try:
        with _db_slot(lane=lane):
            # 計時は席を取った後から（順番待ちは DB の仕事でない＝道具の所要秒と DB 累計秒の
            # 差として見える・2026-09-06 Step 7）。接続・実行・取得の全部を 1 回として数える。
            t0 = time.perf_counter()
            conn = psycopg2.connect(**cfg_run)
            try:
                with conn.cursor() as cur:
                    if params is None:
                        cur.execute(sql)
                    else:
                        cur.execute(sql, params)
                    return fetch(cur)
            finally:
                conn.close()
                record_db_call(time.perf_counter() - t0, sql)
    except Exception as e:
        if lane == LANE_LIGHT and getattr(e, "pgcode", None) == "57014":
            sql_summary = " ".join((sql or "").split())[:120]
            logging.getLogger(JOURNAL).warning(
                "[lane] bypass timeout(%dms) -> heavy sql=%s", BYPASS_TIMEOUT_MS, sql_summary)
            return _run(cfg, sql, params, fetch, lane=LANE_HEAVY)
        raise


def _db(sql: str, params: tuple, lane: str = LANE_HEAVY) -> list[tuple]:
    from db_config import DB_CONFIG
    return _run(DB_CONFIG, sql, params, lambda cur: cur.fetchall(), lane=lane)


# ─── 自由 SQL の口（2026-08-11・本人発案「エージェント自身が SQL を叩く路線」）───
# 鞘は三重: (1) readonly_ai ロール（GRANT SELECT のみ＝書き込みは権限層で不可能・
# 実証済み） (2) statement_timeout 10 秒 (3) 入口で SELECT/WITH 以外と複文を拒否＋
# 行数・セル長の上限で応答を制限（コンテキスト爆発防止）。

def _db_readonly(sql: str, max_rows: int, lane: str = LANE_HEAVY) -> tuple[list[str], list[tuple]]:
    from db_config import DB_CONFIG
    cfg = dict(DB_CONFIG)
    cfg["user"] = "readonly_ai"
    cfg["password"] = os.environ.get("DB_PASS_ROAI") or ""
    # options は _run が lane に応じて付与する（軽い線 1s・重い線 10s）

    def _fetch(cur):
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, cur.fetchmany(max_rows)

    return _run(cfg, sql, None, _fetch, lane=lane)
