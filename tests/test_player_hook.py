"""test_player_hook.py — プレイヤー名の引き当ての口（src/player_hook.py）の試験。

口が空／指す／指した先が無い／関数が無い、の 4 通り。DB には触らない（偽のカーソルを使う）。
資料は全部架空の値。環境変数 PLAYER_RESOLVER は試験ごとに戻し、読み込みを捨てる（他の試験に漏らさない）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import player_hook  # noqa: E402


class FakeCursor:
    """execute を記録するだけ。口が空なら一度も呼ばれないはず。"""

    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PLAYER_RESOLVER", raising=False)
    player_hook._reset_for_tests()
    yield
    monkeypatch.delenv("PLAYER_RESOLVER", raising=False)
    player_hook._reset_for_tests()


def _point_at(monkeypatch, path):
    monkeypatch.setenv("PLAYER_RESOLVER", str(path))
    player_hook._reset_for_tests()


FAKE_RESOLVER = '''
CALLS = []

def resolve_player_id(cur, source, name):
    CALLS.append((source, name))
    cur.execute("SELECT fake_resolver(%s, %s)", (source, name))
    return 42
'''


def test_empty_gate_does_nothing():
    cur = FakeCursor()
    assert player_hook.enabled() is False
    assert player_hook.resolve_player(cur, "mtgtop8", "Test Player A") is None
    assert cur.executed == []          # DB に一度も触らない


def test_blank_env_counts_as_empty(monkeypatch):
    monkeypatch.setenv("PLAYER_RESOLVER", "   ")
    player_hook._reset_for_tests()
    assert player_hook.enabled() is False


def test_empty_gate_adds_no_column():
    cols, vals = player_hook.deck_columns(["a", "b"], [1, 2], None)
    assert cols == ["a", "b"] and vals == [1, 2]
    # 引き当ての結果が仮に渡されても、口が空なら列は足さない
    cols, vals = player_hook.deck_columns(["a"], [1], 7)
    assert cols == ["a"] and vals == [1]


def test_deck_columns_does_not_mutate_input(monkeypatch, tmp_path):
    f = tmp_path / "res.py"
    f.write_text(FAKE_RESOLVER)
    _point_at(monkeypatch, f)
    cols_in, vals_in = ["a"], [1]
    cols, vals = player_hook.deck_columns(cols_in, vals_in, 42)
    assert cols_in == ["a"] and vals_in == [1]
    assert cols == ["a", "player_id"] and vals == [1, 42]


def test_pointed_gate_calls_resolver_with_source_and_name(monkeypatch, tmp_path):
    f = tmp_path / "res.py"
    f.write_text(FAKE_RESOLVER)
    _point_at(monkeypatch, f)
    cur = FakeCursor()
    assert player_hook.enabled() is True
    assert player_hook.resolve_player(cur, "mtgo_modern", "Test Player A") == 42
    assert cur.executed == [("SELECT fake_resolver(%s, %s)", ("mtgo_modern", "Test Player A"))]
    # None の名前もそのまま渡る（捨てるか決めるのは resolver 側）
    assert player_hook.resolve_player(cur, "moxfield", None) == 42
    assert cur.executed[-1][1] == ("moxfield", None)


def test_pointed_gate_adds_player_id_column(monkeypatch, tmp_path):
    f = tmp_path / "res.py"
    f.write_text(FAKE_RESOLVER)
    _point_at(monkeypatch, f)
    cols, vals = player_hook.deck_columns(["deck_name", "source"], ["x", "s"], 42)
    assert cols == ["deck_name", "source", "player_id"]
    assert vals == ["x", "s", 42]


def test_missing_file_stops(monkeypatch, tmp_path):
    _point_at(monkeypatch, tmp_path / "no_such_resolver.py")
    with pytest.raises(SystemExit) as e:
        player_hook.enabled()
    assert "読めません" in str(e.value)


def test_missing_function_stops(monkeypatch, tmp_path):
    f = tmp_path / "res_nofunc.py"
    f.write_text("X = 1\n")
    _point_at(monkeypatch, f)
    with pytest.raises(SystemExit) as e:
        player_hook.resolve_player(FakeCursor(), "mtgtop8", "Test Player A")
    assert "resolve_player_id" in str(e.value)


def test_non_callable_function_stops(monkeypatch, tmp_path):
    f = tmp_path / "res_notcallable.py"
    f.write_text("resolve_player_id = 5\n")
    _point_at(monkeypatch, f)
    with pytest.raises(SystemExit):
        player_hook.enabled()


def test_reset_makes_env_change_take_effect(monkeypatch, tmp_path):
    assert player_hook.enabled() is False
    f = tmp_path / "res.py"
    f.write_text(FAKE_RESOLVER)
    _point_at(monkeypatch, f)
    assert player_hook.enabled() is True
    monkeypatch.delenv("PLAYER_RESOLVER")
    player_hook._reset_for_tests()
    assert player_hook.enabled() is False


def test_error_message_does_not_leak_names(monkeypatch, tmp_path):
    """止まるときの文言に、渡した名前が出ない（名前はログにも出さない決まり）。"""
    f = tmp_path / "res_nofunc.py"
    f.write_text("X = 1\n")
    _point_at(monkeypatch, f)
    with pytest.raises(SystemExit) as e:
        player_hook.resolve_player(FakeCursor(), "mtgtop8", "Test Player A")
    assert "Test Player A" not in str(e.value)


# ─── 引き当ての中の例外に名前が乗っても、外へ出さない────────────

FAILING_RESOLVER = '''
def resolve_player_id(cur, source, name):
    raise ValueError(f"duplicate key value violates unique constraint: (source, player_name)=({source}, {name})")
'''


def test_resolver_failure_hides_name(monkeypatch, tmp_path):
    import traceback
    f = tmp_path / "failing_resolver.py"
    f.write_text(FAILING_RESOLVER)
    monkeypatch.setenv("PLAYER_RESOLVER", str(f))
    player_hook._reset_for_tests()
    secret = "Test Player A"   # トレースバックに出る呼び出しの行に名前の文字を書かない（ソースの行も表示されるため）
    with pytest.raises(player_hook.PlayerResolveError) as ei:
        player_hook.resolve_player(FakeCursor(), "mtgtop8", secret)
    e = ei.value
    assert "Test Player A" not in str(e)
    assert e.__cause__ is None and e.__context__ is None, "元の例外を連鎖に残さない"
    shown = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    assert "Test Player A" not in shown
    assert "ValueError" in str(e), "名前を含まない診断（例外の種類）は残す"
