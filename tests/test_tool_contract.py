#!/usr/bin/env python3
"""道具の契約試験。

分割（src/sisho/ へのパッケージ化）で **道具名・description・引数の署名・docstring が
一字でもずれたら落ちる** ための機械の目。DESIGN の掟「MCP の返り値（と description）は
クライアントに届く契約」＝文言は振る舞いの一部なので、リファクタリングの前後で同一であることを
snapshot と突き合わせて確かめる。

snapshot（tests/snapshots/tools.json）は **分割前の main の実物**から採った。
採り直しは `python tests/test_tool_contract.py --update`（内容を変えたい正当な理由が
あるときだけ・その理由を WORKLOG に書くこと）。

DB 依存の注意: search_mtg_cards と query_mtg_database の description は起動時に DB から
読んだ収録セット一覧（_SETS_HEAD／_SETS_BLURB）を埋め込む＝環境で変わる。
その部分は {SETS_HEAD}／{SETS_BLURB} のプレースホルダに置き換えて（正規化して）比べ、
DB が無くて一覧が空の環境では description の照合だけ skip する（署名・docstring は照合する）。

走らせ方: pytest tests/test_tool_contract.py -v
"""
import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402

SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "snapshots", "tools.json")

# description に「起動時に DB から読んだ収録セット一覧」が入る道具（環境で変わる）。
# search_mtg_cards: _SETS_HEAD ＋ 固定文 ＋ _SETS_BLURB
# query_mtg_database: _SETS_BLURB があるときだけ 1 文が増える
_DB_DEPENDENT_DESCRIPTION = {"search_mtg_cards", "query_mtg_database"}


def _normalize(text):
    """環境で変わる収録セット一覧をプレースホルダに置き換える（長い方から先に）。"""
    if not text:
        return text
    for token, value in sorted((("{SETS_BLURB}", m._SETS_BLURB), ("{SETS_HEAD}", m._SETS_HEAD)),
                               key=lambda kv: len(kv[1] or ""), reverse=True):
        if value:
            text = text.replace(value, token)
    return text


def capture() -> dict:
    """いま登録されている道具の契約を辞書に採る（名前・description・引数の署名・docstring）。"""
    tools = m.server._tool_manager.list_tools()
    out = {"server_name": m.server.name,
           "instructions": m.server.instructions,
           "tool_order": [t.name for t in tools],
           "tools": {}}
    for t in tools:
        fn = t.fn
        out["tools"][t.name] = {
            "description": _normalize(t.description),
            "description_db_dependent": t.name in _DB_DEPENDENT_DESCRIPTION,
            "title": t.title,
            "is_async": t.is_async,
            "annotations": t.annotations.model_dump() if t.annotations is not None else None,
            # 引数の名前・型注釈・既定値（JSON Schema と inspect の両面から採る）
            "parameters": t.parameters,
            "signature": str(inspect.signature(fn)),
            "function_name": fn.__name__,
            "doc": fn.__doc__,
        }
    return out


def _cleandoc(doc):
    return inspect.cleandoc(doc) if doc else doc


def load_snapshot() -> dict:
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        return json.load(f)


# 採り直し（--update）の初回は snapshot がまだ無いので空で始める
_UPDATING = __name__ == "__main__" and "--update" in sys.argv
SNAP = {"tools": {}} if (_UPDATING and not os.path.exists(SNAPSHOT_PATH)) else load_snapshot()
NOW = capture()


def test_server_identity():
    assert NOW["server_name"] == SNAP["server_name"], "サーバー名は変えない"
    assert NOW["instructions"] == SNAP["instructions"], "instructions は一字も変えない"


def test_tool_names_and_order():
    assert NOW["tool_order"] == SNAP["tool_order"], (
        "登録済み道具の名前と登録順が snapshot と一致すること"
        f"（今: {NOW['tool_order']}／snapshot: {SNAP['tool_order']}）")


@pytest.mark.parametrize("name", sorted(SNAP["tools"]))
def test_tool_contract(name):
    exp = SNAP["tools"][name]
    assert name in NOW["tools"], f"道具 {name} が登録されていない"
    got = NOW["tools"][name]

    if exp.get("description_db_dependent") and not m._SETS_BLURB:
        pytest.skip(f"{name} の description は収録セット一覧（DB 起因）を含む。"
                    "DB に繋がらず一覧が空なので description の照合はしない（署名は下で照合）")
    else:
        assert got["description"] == exp["description"], f"{name}: description を一字も変えない"

    assert got["title"] == exp["title"], f"{name}: title"
    assert got["is_async"] == exp["is_async"], f"{name}: async かどうか"
    assert got["annotations"] == exp["annotations"], f"{name}: annotations"
    assert got["function_name"] == exp["function_name"], f"{name}: 関数名"
    assert got["signature"] == exp["signature"], f"{name}: 引数の名前・型注釈・既定値"
    assert got["parameters"] == exp["parameters"], f"{name}: 引数の JSON Schema"
    # Python 3.13+ はコンパイル時に docstring の共通インデントを剥がす（3.12 の snapshot と公開サーバー 3.14 で
    # 空白だけ違う・公開サーバーで実測）→ 両側を inspect.cleandoc で揃えて比べる
    assert _cleandoc(got["doc"]) == _cleandoc(exp["doc"]), f"{name}: docstring は移動しても消さない・変えない"


def test_no_extra_tools():
    extra = sorted(set(NOW["tools"]) - set(SNAP["tools"]))
    assert not extra, f"snapshot に無い道具が増えている: {extra}（増やすなら snapshot を採り直す）"


@pytest.mark.parametrize("attr", [
    # tests が mcp_server 越しに触る名前（モジュールを分けても同じ名前で届くこと）
    "_RateLimiter", "_RateLimitASGI", "_db", "_db_slot", "_db_readonly", "_log_tool",
    "mtg_probability", "verify_answer", "find_combos", "_spellbook_post",
    "query_mtg_database", "search_mtg_cards", "_limited_sets", "_resolve_draft_set",
])
def test_module_reexports(attr):
    assert hasattr(m, attr), f"mcp_server.{attr} が届かない（分割したなら再輸出する）"


if __name__ == "__main__":
    if "--update" in sys.argv:
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
        with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(capture(), f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        print(f"snapshot を更新: {SNAPSHOT_PATH}")
    else:
        pytest.main([__file__, "-v"])
