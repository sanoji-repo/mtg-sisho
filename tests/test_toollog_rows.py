"""test_toollog_rows.py — toollog.py の行書き込み内部処理（_append_row）の試験（DB 不要）。

入口行・出口行のフォーマット、TOOL_LOG_MAX による切り詰め、追記順序、および書き込み不可時の例外抑制を検証する。
"""
import re
from sisho import toollog


_TS_RE = re.compile(r"^\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def test_log_tool_row_format(tmp_path, monkeypatch):
    """1. _log_tool は 1 行・タブ区切り 3 列・1 列目は時刻・3 列目は引数 JSON（非 ASCII 維持）。"""
    log_file = tmp_path / "t.log"
    monkeypatch.setattr("sisho.toollog.TOOL_LOG", str(log_file))

    toollog._log_tool("search_mtg_cards", {"query": "稲妻"})

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    cols = lines[0].split("\t")
    assert len(cols) == 4
    assert _TS_RE.match(cols[0]), f"時刻の形式が不正: {cols[0]}"
    assert cols[1] == "search_mtg_cards"
    assert cols[2] == '{"query": "稲妻"}'
    assert cols[3] == ""


def test_log_tool_max_truncation(tmp_path, monkeypatch):
    """2. TOOL_LOG_MAX を monkeypatch で 10 にすると 3 列目が 10 字で切れる。"""
    log_file = tmp_path / "t.log"
    monkeypatch.setattr("sisho.toollog.TOOL_LOG", str(log_file))
    monkeypatch.setattr("sisho.toollog.TOOL_LOG_MAX", 10)

    toollog._log_tool("search_mtg_cards", {"query": "とても長いクエリの文字列"})

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    cols = lines[0].split("\t")
    assert len(cols) == 4
    assert len(cols[2]) == 10
    assert cols[2] == '{"query": '
    assert cols[3] == ""


def test_log_tool_end_row_format(tmp_path, monkeypatch):
    """3. _log_tool_end は 1 行・タブ 8 列・end・道具名・outcome・秒・DB回数・DB秒・札。"""
    log_file = tmp_path / "t.log"
    monkeypatch.setattr("sisho.toollog.TOOL_LOG", str(log_file))

    toollog._log_tool_end("x", "ok", 0.12345, 2, 0.5)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    cols = lines[0].split("\t")
    assert len(cols) == 8
    assert _TS_RE.match(cols[0]), f"時刻の形式が不正: {cols[0]}"
    assert cols[1] == "end"
    assert cols[2] == "x"
    assert cols[3] == "ok"
    assert cols[4] == "0.123"
    assert cols[5] == "2"
    assert cols[6] == "0.500"
    assert cols[7] == ""


def test_append_order(tmp_path, monkeypatch):
    """4. 入口 → 出口の順に書くと 2 行で順序どおり追記される。"""
    log_file = tmp_path / "t.log"
    monkeypatch.setattr("sisho.toollog.TOOL_LOG", str(log_file))

    toollog._log_tool("lookup_mtg_rule", {"rule": "100.1"})
    toollog._log_tool_end("lookup_mtg_rule", "ok", 0.05, 1, 0.04)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    entry_cols = lines[0].split("\t")
    exit_cols = lines[1].split("\t")

    assert len(entry_cols) == 4
    assert len(exit_cols) == 8
    assert entry_cols[1] == "lookup_mtg_rule"
    assert exit_cols[1] == "end"
    assert exit_cols[2] == "lookup_mtg_rule"


def test_unwritable_path_does_not_raise(tmp_path, monkeypatch):
    """5. TOOL_LOG が書き込めない場所でも例外を出さずに戻る。"""
    blocker = tmp_path / "blocker_file"
    blocker.write_text("not a directory", encoding="utf-8")
    unwritable = blocker / "sub" / "t.log"

    monkeypatch.setattr("sisho.toollog.TOOL_LOG", str(unwritable))

    toollog._log_tool("test_tool", {"arg": 1})
    toollog._log_tool_end("test_tool", "ok", 0.01, 0, 0.0)


def test_unserializable_args_do_not_raise(tmp_path, monkeypatch):
    """6. 引数が JSON にできなくても（整形の失敗）例外を外に出さず、行も書かない（共通化前の try の範囲を保つ）。"""
    log_file = tmp_path / "t.log"
    monkeypatch.setattr("sisho.toollog.TOOL_LOG", str(log_file))

    toollog._log_tool("x", {"obj": object()})
    toollog._log_tool_end("x", "ok", "not-a-number", 1, 0.0)

    assert not log_file.exists()


def test_log_tool_with_current_fuda(tmp_path, monkeypatch):
    """7. CURRENT_FUDA が設定されているときは末尾列にその値が入る。"""
    from sisho.context import CURRENT_FUDA

    log_file = tmp_path / "t.log"
    monkeypatch.setattr("sisho.toollog.TOOL_LOG", str(log_file))

    token = CURRENT_FUDA.set("abc12345")
    try:
        toollog._log_tool("search_mtg_cards", {"query": "test"})
        toollog._log_tool_end("search_mtg_cards", "ok", 0.05, 1, 0.01)
    finally:
        CURRENT_FUDA.reset(token)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].split("\t")[3] == "abc12345"
    assert lines[1].split("\t")[7] == "abc12345"

