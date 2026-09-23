"""test_backstage.py — 舞台裏の幕（sisho/backstage.py）の網。

守りたいこと 2 つ:
1. SDK（pydantic）の生の例外文を客に見せない（「舞台裏を客席から見せない」）
2. その回を道具ログに残す（SDK に弾かれた呼び出しは道具の本体に届かず、従来は記録が消えていた）

安全側の網も張る: 印を含まない本体・壊れた本体・解析できないバイト列は 1 バイトも変えない。
"""
import json

from sisho import backstage as B

RAW = ("Error executing tool draft_pack_stats: 1 validation error for draft_pack_statsArguments\n"
       "cards\n  Field required [type=missing, input_value={}, input_type=dict]\n"
       "    For further information visit https://errors.pydantic.dev/2.13/v/missing")

LEAKS = ("pydantic", "input_value", "input_type", "Arguments", "validation error", "https://")


def _payload(text: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"text": text, "type": "text"}], "isError": True}}


def _sse(obj: dict) -> bytes:
    return ("event: message\ndata: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()


def _text_of(raw: bytes) -> str:
    line = [x for x in raw.decode().split("\n") if x.startswith("data: ")][0]
    return json.loads(line[6:])["result"]["content"][0]["text"]


def test_sse_の生の例外文を書き換える():
    new, note = B._rewrite_body(_sse(_payload(RAW)))
    assert note == "draft_pack_stats|missing:cards"
    assert "draft_pack_stats" in _text_of(new)
    assert "cards" in _text_of(new)


def test_内部の語が一つも残らない():
    new, _ = B._rewrite_body(_sse(_payload(RAW)))
    s = new.decode()
    for leak in LEAKS:
        assert leak not in s, f"客に見えてはいけない語が残っている: {leak}"


def test_素の_json_でも効く():
    new, note = B._rewrite_body(json.dumps(_payload(RAW), ensure_ascii=False).encode())
    assert note == "draft_pack_stats|missing:cards"
    assert "pydantic" not in new.decode()


def test_型違いは別の文言になる():
    raw = ("Error executing tool search_mtg_cards: 1 validation error for search_mtg_cardsArguments\n"
           "top_k\n  Input should be a valid integer [type=int_parsing, input_value='x', input_type=str]")
    new, note = B._rewrite_body(_sse(_payload(raw)))
    assert note == "search_mtg_cards|type:top_k"
    assert "形が説明と違います" in _text_of(new)


def test_印を含まない本体は一バイトも変えない():
    body = json.dumps({"result": {"content": [{"text": "ok", "type": "text"}]}}).encode()
    new, note = B._rewrite_body(body)
    assert new == body and note is None


def test_壊れた本体は素通しする():
    for body in (b"data: {not json " + B._MARKER_B,
                 B._MARKER_B + b"\xff\xfe",
                 b"event: message\ndata: " + B._MARKER_B + b"[[[\n\n"):
        new, note = B._rewrite_body(body)
        assert new == body and note is None, "解析できない本体を書き換えてはいけない"


def test_道具名が取れない文は触らない():
    body = _sse(_payload("Error executing tool : broken"))
    new, note = B._rewrite_body(body)
    assert note is None


def test_複数の項目が欠けても列挙する():
    raw = ("Error executing tool draft_pack_stats: 2 validation errors for draft_pack_statsArguments\n"
           "cards\n  Field required [type=missing, input_value={}, input_type=dict]\n"
           "set\n  Field required [type=missing, input_value={}, input_type=dict]")
    new, note = B._rewrite_body(_sse(_payload(raw)))
    assert note == "draft_pack_stats|missing:cards・set"
    assert "cards・set" in _text_of(new)


def test_欠けと型が混ざったら片方だけを言わない():
    """query が欠け top_k の型が違う場合に『足りません』だけ言うと嘘になる。"""
    raw = ("Error executing tool search_mtg_cards: 2 validation errors for search_mtg_cardsArguments\n"
           "query\n  Field required [type=missing, input_value={}, input_type=dict]\n"
           "top_k\n  Input should be a valid integer [type=int_parsing, input_value='x', input_type=str]")
    new, note = B._rewrite_body(_sse(_payload(raw)))
    assert note == "search_mtg_cards|mixed:query・top_k"
    t = _text_of(new)
    assert "足りないか、形が説明と違います" in t
    for leak in LEAKS:
        assert leak not in new.decode()
