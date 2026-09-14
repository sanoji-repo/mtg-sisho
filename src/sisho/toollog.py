"""toollog.py — 道具の呼び出し履歴と計時（2026-09-05 Step 2 で mcp_server.py から切り出し・
2026-09-06 Step 7 で出口の行と DB の計時を追加）。

出力先は環境変数 MCP_TOOL_LOG（既定はリポジトリ直下の logs/mcp_tools.log・2026-09-05 Step 3 で
作者の開発環境の絶対パス /mnt/mtg_rag/logs/mcp_tools.log から置き換え。公開サーバーは .env で明示している）。

行は 2 種類（どちらもタブ区切り・1 列目は必ず時刻）:

  入口（道具の本体が先頭で書く・Step 2 から不変）
    MM-DD HH:MM:SS \t <道具名> \t <引数の JSON（先頭 TOOL_LOG_MAX 字）>
  出口（Step 7・包み observed() が書く）
    MM-DD HH:MM:SS \t end \t <道具名> \t <outcome> \t <所要秒> \t <DB 呼び出し回数> \t <DB 累計秒>

出口では引数を繰り返さない（入口の行と時刻・名前・順序で対応づける）。2 列目が道具名か
`end` かで見分ける＝既存の「2 列目＝道具名」の読み方（awk -F'\\t' '$2=="search_mtg_cards"' など）は
そのまま生きる。outcome は `ok` か error_kind（sisho/errors.py の名前）か `exception`。

1 秒（環境変数 MCP_SLOW_SEC・既定 1.0）を超えた道具と DB 呼び出しは journal（logger
"uvicorn.error"＝systemd の journal に出る系統）に warning を 1 行。DB の警告に載せるのは
SQL の先頭 120 字だけで、引数（利用者の入力）は載せない。
"""
import contextvars
import datetime
import functools
import json
import logging
import os
import time

from sisho import errors
from sisho.context import CURRENT_FUDA
from sisho.paths import repo_path


TOOL_LOG = os.environ.get(
    "MCP_TOOL_LOG", repo_path("logs", "mcp_tools.log"))
TOOL_LOG_MAX = int(os.environ.get("MCP_TOOL_LOG_MAX", "200"))   # 引数の記録の上限（ベンチは 2000 にして SQL の表名まで採る・2026-09-03）

#: 「遅い」の物差し（秒）。超えたら journal に warning（2026-09-06 Step 7・方針「1 秒以上かかった
#: クエリに警告」）。公開サーバーの PostgreSQL 側も readonly_ai の log_min_duration_statement=1s で同じ線。
SLOW_SEC = float(os.environ.get("MCP_SLOW_SEC", "1.0"))

#: warning の宛先。uvicorn の系統に乗せると systemd の journal でそのまま読める（HTTP 版）。
#: stdio 版では stderr に出る＝どちらでも人が読める場所に落ちる。
JOURNAL = "uvicorn.error"

# DB 呼び出しの集計器。道具 1 回ぶんをコンテキストに貯める（スレッドローカルと
# 非同期コルーチンの両方で分離される＝MCP の同期道具・将来の async 道具の両対応）。
# 2026-09-06 Antigravity の監査で threading.local から移行。今日の道具は同期関数で、mcp 2.0 は
# anyio.to_thread.run_sync で別スレッドに投げ、anyio は context を複製して渡す＝動きは同じ。
_db_stats: contextvars.ContextVar[dict] = contextvars.ContextVar("_db_stats", default=None)


def reset_db_stats() -> None:
    """道具 1 回ぶんの集計を 0 に戻す（包み observed() が本体を呼ぶ直前に）。"""
    _db_stats.set({"calls": 0, "seconds": 0.0})


def db_stats() -> tuple[int, float]:
    """いまのコンテキストの (DB 呼び出し回数, DB 累計秒)。まだ数えていなければ (0, 0.0)。"""
    st = _db_stats.get()
    if st is None:
        return 0, 0.0
    return st["calls"], st["seconds"]


def record_db_call(elapsed: float, sql: str) -> None:
    """DB 呼び出し 1 回を数える（sisho/db.py から呼ばれる）。1 秒超は journal に warning。

    warning に載せるのは SQL の先頭 120 字（空白は 1 個に潰す）だけ＝引数は載せない
    （ログに利用者の入力を残しすぎない）。
    """
    st = _db_stats.get()
    if st is None:
        st = {"calls": 0, "seconds": 0.0}
        _db_stats.set(st)
    st["calls"] += 1
    st["seconds"] += elapsed
    if elapsed > SLOW_SEC:
        logging.getLogger(JOURNAL).warning(
            "[slow] db elapsed=%.3f sql=%s", elapsed, " ".join((sql or "").split())[:120])


def _append_row(*fields: str) -> None:
    """道具ログに 1 行追記する（先頭に時刻を付与・タブ区切り・失敗は黙る）。"""
    try:
        os.makedirs(os.path.dirname(TOOL_LOG), exist_ok=True)
        with open(TOOL_LOG, "a", encoding="utf-8") as f:
            ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S")
            f.write(f"{ts}\t" + "\t".join(fields) + "\n")
    except Exception:
        pass                     # ログ失敗で道具を殺さない


def _log_tool(name: str, args: dict) -> None:
    """道具の呼び出し履歴（2026-08-11・「彼はどう MCP を使ったか」に query_log だけでは
    答えられなかった観測穴の修理）。search 以外はローカル DB 直結で足跡が無かった。"""
    try:
        arg_s = json.dumps(args, ensure_ascii=False)[:TOOL_LOG_MAX]
    except Exception:
        return                   # 整形の失敗も道具を殺さない（共通化前は try の中にあった）
    fuda = CURRENT_FUDA.get() or ""
    _append_row(name, arg_s, fuda)


def _log_tool_end(name: str, outcome: str, elapsed: float,
                  db_calls: int, db_seconds: float) -> None:
    """道具の出口の 1 行（2026-09-06 Step 7）。引数は繰り返さない（入口の行にある）。"""
    try:
        fuda = CURRENT_FUDA.get() or ""
        row = ("end", name, outcome, f"{elapsed:.3f}", str(db_calls), f"{db_seconds:.3f}", fuda)
    except Exception:
        return                   # 整形の失敗も道具を殺さない（共通化前は try の中にあった）
    _append_row(*row)


def observed(fn):
    """道具を計時して出口の行を残す薄い包み（2026-09-06 Step 7）。

    返り値・例外は素通し＝道具の契約は一切変えない。名前・docstring・注釈は
    functools.wraps で引き継ぎ、inspect.signature は __wrapped__ を辿って**元の署名**を返す
    （MCP SDK が道具の JSON Schema を作るのも inspect.signature なので、包んでも
    引数の一覧は変わらない＝契約試験 tests/test_tool_contract.py がそれを縫っている）。

    outcome の決め方は sisho/errors.py の outcome_of（返り値の error_kind か文頭で対応づけ）。
    例外は `exception` として記録してから**そのまま再送出**する。
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        reset_db_stats()
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except BaseException as e:
            elapsed = time.perf_counter() - t0
            calls, seconds = db_stats()
            _log_tool_end(fn.__name__, "exception", elapsed, calls, seconds)
            logging.getLogger(JOURNAL).warning(
                "[tool-error] tool=%s exc=%s elapsed=%.3f db_calls=%d db_s=%.3f",
                fn.__name__, type(e).__name__, elapsed, calls, seconds)
            raise
        elapsed = time.perf_counter() - t0
        calls, seconds = db_stats()
        _log_tool_end(fn.__name__, errors.outcome_of(result), elapsed, calls, seconds)
        if elapsed > SLOW_SEC:
            logging.getLogger(JOURNAL).warning(
                "[slow] tool=%s elapsed=%.3f db_calls=%d db_s=%.3f",
                fn.__name__, elapsed, calls, seconds)
        return result
    return wrapper
