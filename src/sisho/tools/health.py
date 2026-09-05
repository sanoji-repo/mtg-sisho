"""health.py — 健全性確認の道具 mtg_rag_health（2026-09-05 Step 3 で mcp_server.py から切り出し）。

登録（server.tool）は mcp_server.py 側。ここは DESCRIPTION と素の関数だけを持つ。
"""
import json

from sisho.db import DBBusy, _db
from sisho.toollog import _log_tool


DESCRIPTION = "データ層の健全性を確認する（DB 実疎通・主要テーブルの行数と鮮度）。"


def mtg_rag_health(deep: bool = False) -> str:
    """deep は旧 API 時代の名残で互換のため受けるが、常に DB 実疎通を見る。"""
    _log_tool("mtg_rag_health", {"deep": deep})
    import time
    t0 = time.time()
    try:
        rows = _db(
            "SELECT (SELECT COUNT(*) FROM mtg_cards_v2),"
            "       (SELECT COUNT(*) FROM mtg_rules),"
            "       (SELECT COUNT(*) FROM card_rulings),"
            "       (SELECT COUNT(*) FROM deck_list),"
            "       (SELECT MAX(tournament_date)::text FROM deck_list)", ())
        n_card, n_rule, n_rul, n_deck, latest = rows[0]
        # ドラフト統計（17Lands 集計・limited_card_stats）は表が無い環境もあるので別口で・失敗は 0
        try:
            n_l17 = _db("SELECT COUNT(DISTINCT expansion) FROM limited_card_stats", ())[0][0]
        except Exception:
            n_l17 = 0
        return json.dumps({
            "status": "ok",
            "db_latency_ms": int((time.time() - t0) * 1000),
            "cards": n_card, "rules": n_rule, "rulings": n_rul,
            "decks": n_deck, "latest_deck": latest,
            "draft_stat_sets": n_l17,
            "draft_stat_note": "17Lands 集計・表 limited_card_stats・セット一覧は describe_mtg_tables"},
            ensure_ascii=False)
    except DBBusy as e:
        return str(e)          # 混雑は DB の故障でない＝「health 失敗」に丸めない（errors.BUSY）
    except Exception as e:
        return f"health 失敗: {e}"        # 想定外は素通し（errors.DB_ERROR）
