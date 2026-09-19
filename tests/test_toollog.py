#!/usr/bin/env python3
"""道具ログの出口の行と計時の試験。

縫うのは 5 つ:
  1. 入口の行の形が変わっていないこと（既存の読み方＝2 列目が道具名を壊さない）。
  2. 出口の行が 1 呼び出しにつき 1 行出て、列が「時刻・end・道具名・outcome・所要秒・
     DB 回数・DB 秒」であること。**引数は繰り返さない**。
  3. outcome が ok・error_kind（JSON もテキストも）・exception で正しく分かれること。
  4. 1 秒（MCP_SLOW_SEC）超の道具と DB 呼び出しが journal に warning を出すこと
     （DB の warning に引数を載せないこと）。
  5. 包み越しでも道具の名前・docstring・署名が保たれ、登録済みの 10 本が全部包まれていること。

大半は DB を要らない（差し替えと純粋な検査）。走らせ方: pytest tests/test_toollog.py -v
"""
import inspect
import json
import logging
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402
import sisho.db as db  # noqa: E402
import sisho.toollog as toollog  # noqa: E402
from sisho import errors  # noqa: E402
from conftest import requires_db  # noqa: E402


def _lines() -> list[list[str]]:
    """いまの道具ログを列に割って返す（conftest が tmp_path へ逃がしている）。"""
    if not os.path.exists(toollog.TOOL_LOG):
        return []
    with open(toollog.TOOL_LOG, encoding="utf-8") as f:
        return [ln.rstrip("\n").split("\t") for ln in f if ln.strip()]


def _ends() -> list[list[str]]:
    return [c for c in _lines() if len(c) > 1 and c[1] == "end"]


# ─── 1・2. 行の形 ───────────────────────────────────────────────

def test_entry_line_shape_is_unchanged_and_exit_line_follows():
    """実在の道具（DB も網も要らない経路）で、入口 1 行＋出口 1 行が順に出る。"""
    out = m.find_combos([])                       # card_names が空＝DB にも外にも行かない
    assert json.loads(out)["error_kind"] == "empty_query", "返り値は変えていない"
    rows = _lines()
    assert len(rows) == 2, f"1 呼び出し＝入口 1 行＋出口 1 行（{rows}）"
    entry, end = rows
    # 入口: 時刻・道具名・引数の JSON・札（末尾に札を追加）
    assert len(entry) == 4 and entry[1] == "find_combos"
    assert json.loads(entry[2]) == {"n": 0, "commanders": None, "limit": 10}
    # 出口: 時刻・end・道具名・outcome・所要秒・DB 回数・DB 秒・札
    assert len(end) == 8, f"出口の列数（{end}）"
    assert end[1] == "end" and end[2] == "find_combos" and end[3] == "empty_query"
    assert float(end[4]) >= 0 and end[5] == "0" and float(end[6]) == 0.0
    assert len(end[0]) == len(entry[0]), "時刻の形は入口と同じ"


def test_exit_line_does_not_repeat_the_arguments():
    """出口の行に引数の中身を出さない（入口の行と時刻・名前・順序で対応づける）。"""
    m.query_mtg_database("DELETE FROM secret_table_name")     # 鞘が入口で断る＝DB へ行かない
    entry, end = _lines()
    assert "secret_table_name" in entry[2], "引数は入口の行にある"
    assert "secret_table_name" not in "\t".join(end), f"出口では繰り返さない（{end}）"
    assert "{" not in "\t".join(end), "出口の行に JSON を載せない"


def test_log_failure_does_not_kill_the_tool(monkeypatch):
    """ログが書けなくても道具は返る（入口の行と同じ約束）。"""
    monkeypatch.setattr(toollog, "TOOL_LOG", "/proc/one/does/not/exist/mcp_tools.log")
    assert json.loads(m.find_combos([]))["error_kind"] == "empty_query"


# ─── 3. outcome の分かれ方 ──────────────────────────────────────

@pytest.mark.parametrize("result,expected", [
    ("普通の答え（テキスト）", "ok"),
    ('{"status": "ok", "cards": 32730}', "ok"),
    ("0 行（クエリは成功）。", "ok"),
    (errors.err_json(errors.NO_MATCH, "該当なし: x"), "no_match"),
    (errors.err_json(errors.OUT_OF_RANGE, "引数の範囲外: copies は 0〜deck_size"), "out_of_range"),
    ('{"error": "error_kind を載せ忘れた"}', "error"),
    ("拒否: 複文（; 区切り）は実行できません。1 文だけにしてください。", "sql_rejected"),
    ("SQL エラー: column zzz does not exist", "db_error"),
    ("エラー: いろいろ", "db_error"),
    ("health 失敗: いろいろ", "db_error"),
    ("混雑: DB の順番待ちが 20 秒を超えました。", "busy"),
    ("該当なし: zzz（条番号または英語キーワードで検索してください）", "no_match"),
    ("裁定なし: zzz（英語の正式カード名で検索してください）", "no_match"),
    ("共起なし: zzz（scope=edh・英語の正式カード名で指定してください）", "no_match"),
    ("テーブルなし: zzz（describe_mtg_tables() を引数なしで呼ぶと一覧が出る）", "unknown_table"),
    ("表名に使えない文字: 「deck-list」（英数字と _ のみ）。", "invalid_identifier"),
    ("スキーマ名に使えない文字: 「pub-lic」（英数字と _ のみ）。", "invalid_identifier"),
    ("order_by が不正: zzz（count / lift）", "unknown_option"),
    ("scope が不正: zzz（edh/constructed/pauper/vintage/precon）", "unknown_option"),
])
def test_outcome_of_reads_the_result_without_touching_it(result, expected):
    assert errors.outcome_of(result) == expected, f"{result[:30]} → {expected}"


def test_real_text_tools_are_not_logged_as_ok(monkeypatch):
    """**実物の道具**の error が ok に化けないこと（対応表は文言の写しなので、道具側の文言を
    変えると黙って ok に戻る＝そのずれをここで捕まえる。DB の要らない経路だけ）。"""
    assert m.query_mtg_database("DELETE FROM x").startswith("拒否: ")
    assert m.describe_mtg_tables("deck-list").startswith("表名に使えない")
    assert m.describe_mtg_tables("pub-lic.mtg_rules").startswith("スキーマ名に使えない")
    assert m.find_partner_cards("Sol Ring", order_by="zzz").startswith("order_by が不正")
    # scope の検査は名前の解決（DB）より後なので、この試験では扱わない（下の DB あり側で見る）

    def raise_busy(*a, **k):
        raise db.DBBusy("混雑: DB の順番待ちが 20 秒を超えました。")
    monkeypatch.setattr("sisho.tools.sql._db_readonly", raise_busy)
    monkeypatch.setattr("sisho.tools.health._db", raise_busy)
    m.query_mtg_database("SELECT 1")            # 混雑（busy）
    m.mtg_rag_health()                          # 混雑（busy）
    got = [(e[2], e[3]) for e in _ends()]
    assert got == [("query_mtg_database", "sql_rejected"),
                   ("describe_mtg_tables", "invalid_identifier"),
                   ("describe_mtg_tables", "invalid_identifier"),
                   ("find_partner_cards", "unknown_option"),
                   ("query_mtg_database", "busy"),
                   ("mtg_rag_health", "busy")], got


@requires_db
def test_real_text_tools_no_match_and_unknown_table():
    """DB が要る側の文言（scope が不正・該当なし・裁定なし・共起なし・テーブルなし）も ok に化けない。"""
    m.find_partner_cards("Sol Ring", scope="zzz")
    m.lookup_mtg_rule("zzzqqqxxx")
    m.get_card_rulings("zzzqqqxxx")
    m.find_partner_cards("zzzqqqxxx")
    m.describe_mtg_tables("no_such_table_zzz")
    assert [(e[2], e[3]) for e in _ends()] == [
        ("find_partner_cards", "unknown_option"),
        ("lookup_mtg_rule", "no_match"), ("get_card_rulings", "no_match"),
        ("find_partner_cards", "no_match"), ("describe_mtg_tables", "unknown_table")]


def test_text_prefixes_use_the_registered_vocabulary():
    """テキストの対応表が error_kind の語彙（KINDS）から外れない＝語彙は 1 箇所。"""
    for prefix, kind in errors.TEXT_PREFIXES:
        assert kind in errors.KINDS, f"未登録の名前: {kind}（{prefix}）"
        assert prefix.strip(), "文頭が空"


def test_outcome_is_written_for_ok_and_for_error_kind():
    """包み越しの実物: ok と error_kind が出口の行に載る。"""
    def fake_ok():
        return "普通の答え"

    def fake_busy():
        return errors.err_json(errors.BUSY, "混雑: …")

    toollog.observed(fake_ok)()
    toollog.observed(fake_busy)()
    assert [(e[2], e[3]) for e in _ends()] == [("fake_ok", "ok"), ("fake_busy", "busy")]


def test_exception_is_recorded_and_re_raised(caplog):
    """例外は出口の行に exception として残し、そのまま再送出する（握りつぶさない）。"""
    def boom():
        raise db.DBBusy("混雑: …")
    boom.__name__ = "fake_boom"    # 中身の名前が出口の行に出る（包みは fn.__name__ を読む）
    with caplog.at_level(logging.WARNING, logger=toollog.JOURNAL):
        with pytest.raises(db.DBBusy):
            toollog.observed(boom)()
    end = _ends()[-1]
    assert end[2:4] == ["fake_boom", "exception"], f"出口の行（{end}）"
    assert any("[tool-error]" in r.getMessage() and "DBBusy" in r.getMessage()
               for r in caplog.records), f"journal に型を残す（{caplog.text}）"


# ─── 4. 遅いものは journal に warning ───────────────────────────

def test_slow_tool_warns_to_the_journal(monkeypatch, caplog):
    monkeypatch.setattr(toollog, "SLOW_SEC", 0.05)

    def slow():
        time.sleep(0.08)
        return "できた"
    slow.__name__ = "fake_slow"
    with caplog.at_level(logging.WARNING, logger=toollog.JOURNAL):
        toollog.observed(slow)()
    msg = [r.getMessage() for r in caplog.records if "[slow] tool=" in r.getMessage()]
    assert msg, f"1 秒（この試験では 0.05 秒）超で warning（{caplog.text}）"
    assert "tool=fake_slow" in msg[0] and "db_calls=0" in msg[0], msg[0]


def test_fast_tool_does_not_warn(caplog):
    def fake_fast():
        return "すぐ"

    with caplog.at_level(logging.WARNING, logger=toollog.JOURNAL):
        toollog.observed(fake_fast)()
    assert not [r for r in caplog.records if "[slow]" in r.getMessage()], caplog.text


def test_slow_db_call_warns_with_truncated_sql_and_no_parameters(monkeypatch, caplog):
    """DB の warning に載せるのは SQL の先頭 120 字だけ（引数＝利用者の入力は載せない）。"""
    monkeypatch.setattr(toollog, "SLOW_SEC", 0.5)
    sql = "SELECT card_name,\n   japanese_name FROM mtg_cards_v2 WHERE card_name = %s AND " + "x" * 200
    with caplog.at_level(logging.WARNING, logger=toollog.JOURNAL):
        toollog.record_db_call(2.0, sql)
    msg = [r.getMessage() for r in caplog.records if "[slow] db" in r.getMessage()]
    assert msg, caplog.text
    body = msg[0].split("sql=", 1)[1]
    assert len(body) == 120, f"先頭 120 字（{len(body)} 字）"
    assert "\n" not in body, "改行は畳む"
    assert "%s" in msg[0] and "x" * 130 not in msg[0]


def test_db_stats_are_counted_and_reset_per_tool():
    toollog.reset_db_stats()
    assert toollog.db_stats() == (0, 0.0)
    toollog.record_db_call(0.01, "SELECT 1")
    toollog.record_db_call(0.02, "SELECT 2")
    calls, seconds = toollog.db_stats()
    assert calls == 2 and 0.029 < seconds < 0.031

    def uses_db():
        toollog.record_db_call(0.5, "SELECT 3")
        return "できた"
    uses_db.__name__ = "fake_db"
    toollog.observed(uses_db)()
    end = _ends()[-1]
    assert end[5] == "1" and float(end[6]) >= 0.5, f"包みが数え直す（{end}）"


def test_counters_do_not_mix_between_concurrent_tools():
    """集計器はスレッドローカル（MCP は同期道具を 1 呼び出し＝1 ワーカースレッドで走らせる:
    mcp/server/mcpserver/utilities/func_metadata.py の anyio.to_thread.run_sync）。
    同時に走った 2 本の DB 回数が混ざらないこと。"""
    import threading

    def many():
        for _ in range(5):
            toollog.record_db_call(0.01, "SELECT many")   # 合計 0.050 秒
            time.sleep(0.005)
        return "できた"

    def few():
        toollog.record_db_call(0.007, "SELECT few")       # 合計 0.007 秒
        return "できた"

    many.__name__, few.__name__ = "fake_many", "fake_few"
    ts = [threading.Thread(target=toollog.observed(many)),
          threading.Thread(target=toollog.observed(few))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # 回数だけだと「割り込みで 0 に戻され、残りを数え直して同じ数になる」偶然を見逃すので秒も見る
    got = {e[2]: (e[5], e[6]) for e in _ends()}
    assert got == {"fake_many": ("5", "0.050"), "fake_few": ("1", "0.007")}, got


def test_counters_do_not_mix_between_concurrent_coroutines():
    """集計器は contextvars＝同一スレッドで交互に進む二つのコルーチンでも混ざらない
    （asyncio の Task は生成時に context を複製する）。今日の道具は同期関数でスレッド分離だが、
    async 道具を足しても同じ器で数えられることの担保。threading.local だとこの試験は落ちる
    （外部レビューの指摘で、死んだコードを削って実際にコルーチンを走らせる形に）。"""
    import asyncio

    async def amany():
        toollog.reset_db_stats()
        for _ in range(5):
            toollog.record_db_call(0.01, "SELECT many")
            await asyncio.sleep(0.001)
        return toollog.db_stats()

    async def afew():
        toollog.reset_db_stats()
        await asyncio.sleep(0.002)
        toollog.record_db_call(0.007, "SELECT few")
        return toollog.db_stats()

    async def main():
        return await asyncio.gather(amany(), afew())

    res_many, res_few = asyncio.run(main())
    assert res_many[0] == 5 and abs(res_many[1] - 0.05) < 1e-9, res_many
    assert res_few[0] == 1 and abs(res_few[1] - 0.007) < 1e-9, res_few


@requires_db
def test_real_db_call_is_timed_and_counted():
    """実物の _db／_db_readonly が回数と秒を積む（1 回以上・0 秒より大きい）。"""
    toollog.reset_db_stats()
    db._db("SELECT 1", ())
    calls, seconds = toollog.db_stats()
    assert calls == 1 and seconds > 0, (calls, seconds)
    db._db_readonly("SELECT 1", 1)
    calls, seconds = toollog.db_stats()
    assert calls == 2 and seconds > 0, (calls, seconds)


@requires_db
def test_real_tool_records_its_db_calls():
    m.describe_mtg_tables()
    end = _ends()[-1]
    assert end[2] == "describe_mtg_tables" and end[3] == "ok", end
    assert int(end[5]) >= 1 and float(end[6]) > 0, f"DB 回数と秒（{end}）"
    assert float(end[4]) >= float(end[6]), "道具の所要秒は DB 累計秒以上"


# ─── 5. 包んでも契約が変わらない ────────────────────────────────

def test_every_registered_tool_is_wrapped():
    """10 本すべてが包まれている（道具を足したときに包み忘れる事故の網）。"""
    tools = m.server._tool_manager.list_tools()
    unwrapped = [t.name for t in tools if not hasattr(t.fn, "__wrapped__")]
    assert not unwrapped, f"包まれていない道具: {unwrapped}"
    assert len(tools) == 10


@pytest.mark.parametrize("name", [
    "search_mtg_cards", "mtg_probability", "find_combos", "lookup_mtg_rule",
    "get_card_rulings", "find_partner_cards", "query_mtg_database", "verify_answer",
    "describe_mtg_tables", "mtg_rag_health"])
def test_wrapper_keeps_name_doc_and_signature(name):
    """契約試験と同じ物差しを包みの側から: 名前・docstring・署名は元のまま。"""
    tool = {t.name: t for t in m.server._tool_manager.list_tools()}[name]
    wrapped = tool.fn
    original = wrapped.__wrapped__
    assert wrapped.__name__ == original.__name__ == name
    assert wrapped.__doc__ == original.__doc__
    assert inspect.signature(wrapped) == inspect.signature(original)
    # JSON Schema（相手側に見える引数）も元の関数から作ったものと一致する
    assert "self" not in tool.parameters.get("properties", {})
    assert set(tool.parameters.get("properties", {})) == {
        p for p in inspect.signature(original).parameters}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
