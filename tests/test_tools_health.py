#!/usr/bin/env python3
"""mtg_rag_health の振る舞い（Step 4・2026-09-05）。

行数は毎晩増えるので値では縫わず、「鍵が揃う」「数字が下限を割らない」で縫う。
失敗経路（DB が落ちている）は monkeypatch で作るので DB 無しでも走る。

走らせ方: pytest tests/test_tools_health.py -v
"""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402
import sisho.tools.health as health  # noqa: E402  monkeypatch はこちら側
from conftest import requires_db  # noqa: E402

h = m.mtg_rag_health.fn if hasattr(m.mtg_rag_health, "fn") else m.mtg_rag_health

KEYS = {"status", "db_latency_ms", "cards", "rules", "rulings", "decks",
        "latest_deck", "draft_stat_sets", "draft_stat_note",
        "started_at", "uptime_hours", "code_version"}


@requires_db
@pytest.mark.parametrize("deep", [False, True])
def test_health_keys_and_lower_bounds(deep):
    """deep は旧 API 時代の名残＝どちらでも同じ形・同じ実測を返す。"""
    d = json.loads(h(deep))
    assert set(d) == KEYS, f"返り値の鍵（{sorted(d)}）"
    assert d["status"] == "ok"
    assert isinstance(d["db_latency_ms"], int) and d["db_latency_ms"] >= 0, "実測の往復時間"
    # 下限は「これを割ったら搬入か接続がおかしい」水準（データは増える一方なので上限は縫わない）
    assert d["cards"] > 30000, f"カード 3 万枚超（{d['cards']}）"
    assert d["rules"] > 3000, f"総合ルール条文（{d['rules']}）"
    assert d["rulings"] > 70000, f"公式裁定 7 万件超（{d['rulings']}）"
    assert d["decks"] > 100000, f"実デッキ 10 万本超（{d['decks']}）"
    assert d["draft_stat_sets"] >= 1, "17Lands のセット数"
    assert len(d["latest_deck"]) == 10 and d["latest_deck"][4] == "-", (
        f"最新デッキの日付は YYYY-MM-DD（{d['latest_deck']}）")
    assert "limited_card_stats" in d["draft_stat_note"], "セット一覧の在り処を返り値に載せる"


@requires_db
def test_health_deep_matches_shallow():
    a, b = json.loads(h(False)), json.loads(h(True))
    for k in KEYS - {"db_latency_ms", "uptime_hours"}:   # この 2 つは呼ぶたびに進む
        assert a[k] == b[k], f"deep で答えが変わらない（{k}）"


@requires_db
def test_health_reports_start_time_and_code_version():
    """再起動や配備のたびに「今動いているのはいつのコードか」を返り値だけで言えること（#814）。

    2026-09-14 に追加。箱へは rsync で配ぶので .git が無く、VERSION が無ければ src の
    .py の最終更新を返す（値は日々変わるので形だけを縫う）。
    """
    d = json.loads(h(False))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", d["started_at"]), (
        f"起動時刻は YYYY-MM-DD HH:MM:SS（{d['started_at']}）")
    assert isinstance(d["uptime_hours"], float) and d["uptime_hours"] >= 0, (
        f"稼働時間は 0 以上の時間（{d['uptime_hours']}）")
    assert d["code_version"] and "不明" not in d["code_version"], (
        f"コードの版が読めている（{d['code_version']}）")
    assert d["started_at"] >= d["code_version"][-19:], (
        f"起動はコードの更新より後（起動 {d['started_at']}・版 {d['code_version']}）")


def test_health_survives_missing_limited_table(monkeypatch):
    """17Lands の表が無い環境では draft_stat_sets=0 で、他の数字は返る。"""
    def fake(sql, params):
        if "limited_card_stats" in sql:
            raise RuntimeError("relation does not exist")
        return [(1, 2, 3, 4, "2026-09-05")]

    monkeypatch.setattr(health, "_db", fake)
    d = json.loads(h(False))
    assert d["status"] == "ok" and d["draft_stat_sets"] == 0, "表が無くても health は ok"
    assert d["cards"] == 1 and d["latest_deck"] == "2026-09-05"


def test_health_reports_db_failure_as_text(monkeypatch):
    """DB へ行けないときは例外でなく『health 失敗: …』の文字列（他の道具に波及させない）。"""
    monkeypatch.setattr(health, "_db", lambda sql, params: (_ for _ in ()).throw(RuntimeError("boom")))
    r = h(False)
    assert r.startswith("health 失敗: ") and "boom" in r, f"失敗の文言（{r}）"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
