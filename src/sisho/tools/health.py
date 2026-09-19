"""health.py — 健全性確認の道具 mtg_rag_health（2026-09-05 Step 3 で mcp_server.py から切り出し）。

登録（server.tool）は mcp_server.py 側。ここは DESCRIPTION と素の関数だけを持つ。
"""
import json
import os
import time

from sisho.db import DBBusy, LANE_LIGHT, _db
from sisho.toollog import _log_tool


DESCRIPTION = "データ層の健全性を確認する（DB 実疎通・主要テーブルの行数と鮮度）。"

_STARTED = time.time()
_SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # …/src
_ROOT = os.path.dirname(_SRC)                                                         # 配備先の直下


def _code_stamp() -> str:
    """今動いているコードの版。VERSION があればその中身、無ければ src の .py の最終更新。

    公開サーバーへは rsync で配ぶので .git が無く、版を示す物がファイルの日付しかない（2026-09-14 実測）。
    起動時に 1 回だけ数えて使い回す（health を呼ぶたびに walk しない）。
    """
    try:
        with open(os.path.join(_ROOT, "VERSION"), encoding="utf-8") as f:
            v = f.read().strip()
        if v:
            return v
    except OSError:
        pass
    newest = 0.0
    for dirpath, _dirs, files in os.walk(_SRC):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if fn.endswith(".py"):
                try:
                    newest = max(newest, os.stat(os.path.join(dirpath, fn)).st_mtime)
                except OSError:
                    pass
    if not newest:
        return "不明（VERSION も src の .py も読めない）"
    return time.strftime("src の最終更新 %Y-%m-%d %H:%M:%S", time.localtime(newest))


_CODE = _code_stamp()


def mtg_rag_health(deep: bool = False) -> str:
    """deep は旧 API 時代の名残で互換のため受けるが、常に DB 実疎通を見る。"""
    _log_tool("mtg_rag_health", {"deep": deep})
    t0 = time.time()
    try:
        # deck_list を 2 回走査していた（COUNT と MAX が別の副問い合わせ・実測 6,515 頁）。
        # 1 回の走査で両方採ると 3,420 頁（実測）。索引で逃げる手は無く、
        # deck_list_format_date_idx は (format_name, tournament_date) の複合なので
        # MAX 単独だと索引を丸ごと読んで 12,278 頁とかえって重い。
        # 時間キャッシュも試したが、呼び出しは 10 分に 1 回でほとんど当たらない一方、
        # プロセス内に状態が残って試験の順序依存を作ったのでやめた（継ぎ足しはフレームのサイン）。
        rows = _db(
            "SELECT (SELECT COUNT(*) FROM mtg_cards_v2),"
            "       (SELECT COUNT(*) FROM mtg_rules),"
            "       (SELECT COUNT(*) FROM card_rulings),"
            "       d.n, d.latest"
            "  FROM (SELECT count(*) AS n, max(tournament_date)::text AS latest FROM deck_list) d", ())
        n_card, n_rule, n_rul, n_deck, latest = rows[0]
        # ドラフト統計（17Lands 集計・limited_card_stats）は表が無い環境もあるので別口で・失敗は 0
        # 表が無い（未搬入）と、照会できない（障害・権限・timeout）を分ける。
        # 以前はどちらも 0 にして status: ok を返していたので、「本当に 0 件」と
        # 「引けなかった」が区別できなかった（掟「不在は NULL・番兵禁止」）。
        n_l17, l17_note = None, None
        try:
            n_l17 = _db("SELECT COUNT(DISTINCT expansion) FROM limited_card_stats", (), lane=LANE_LIGHT)[0][0]
        except Exception as exc:
            if "does not exist" in str(exc) or "UndefinedTable" in type(exc).__name__:
                n_l17, l17_note = 0, "limited_card_stats が無い環境（17Lands 未搬入）"
            else:
                l17_note = f"17Lands の収録セット数を照会できなかった（{type(exc).__name__}）＝0 件という意味ではない"
        return json.dumps({
            "status": "ok",
            "db_latency_ms": int((time.time() - t0) * 1000),
            "cards": n_card, "rules": n_rule, "rulings": n_rul,
            "decks": n_deck, "latest_deck": latest,
            "draft_stat_sets": n_l17,          # 照会できなかったときは null（0 と区別する）
            "draft_stat_note": l17_note or "17Lands 集計・表 limited_card_stats・セット一覧は describe_mtg_tables",
            # 再起動や配備のたびに「今動いているのはいつのコードか」を返り値だけで言えるように
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_STARTED)),
            "uptime_hours": round((time.time() - _STARTED) / 3600, 1),
            "code_version": _CODE},
            ensure_ascii=False)
    except DBBusy as e:
        return str(e)          # 混雑は DB の故障でない＝「health 失敗」に丸めない（errors.BUSY）
    except Exception as e:
        return f"health 失敗: {e}"        # 想定外は素通し（errors.DB_ERROR）
