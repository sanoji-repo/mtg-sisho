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


def test_道具の本体の例外は原因を隠して再試行を案内する():
    """SDK は本体の例外も同じ印で包む。DB 断を『入力が不正』と見せず、原因の生の文（DB の宛先など）も見せない
    （投げられた例外は隠す形）。"""
    raw = ('Error executing tool find_combos: connection to server at "127.0.0.1", port 5432 failed: Connection refused')
    body = _sse(_payload(raw))
    new, note = B._rewrite_body(body)
    assert note == "find_combos|runtime"
    text = new.decode()
    assert "サーバー側の失敗: find_combos の実行中に" in text and "入力の問題ではありません" in text
    for leak in ("127.0.0.1", "5432", "connection to server", "Error executing tool", "入力が不正"):
        assert leak not in text, f"{leak} が客に見える"


def test_別の道具名の検証エラーは引数検証とみなさない():
    """道具の中で別のモデルの検証が失敗した文（<道具名>Arguments でない）は本体の例外＝入力の不正と言わない。"""
    raw = ("Error executing tool search_mtg_cards: 1 validation error for CardRow\n"
           "name\n  Field required [type=missing, input_value={}, input_type=dict]")
    msg, tool, kind = B._rewrite_text(raw)
    assert kind == "runtime" and tool == "search_mtg_cards"
    assert "CardRow" not in msg and "入力が不正" not in msg


# ── ASGI の外皮として: 応答の長さの宣言と本文を一致させる ──
import asyncio


def _run_asgi(start_headers, bodies, monkeypatch):
    """内側のアプリが start と body（複数に分けて）を送る。外皮を通って出たメッセージを返す。"""
    monkeypatch.setattr(B, "_log_tool", lambda *a, **k: None)
    monkeypatch.setattr(B, "_log_tool_end", lambda *a, **k: None)

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": start_headers})
        for i, b in enumerate(bodies):
            await send({"type": "http.response.body", "body": b, "more_body": i < len(bodies) - 1})

    out = []

    async def send(m):
        out.append(m)

    asyncio.run(B.BackstageASGI(inner)({"type": "http"}, None, send))
    return out


def _declared(msgs):
    return int(dict(msgs[0]["headers"])[b"content-length"])


def test_長さを宣言した応答は書き換え後の長さで宣言し直す(monkeypatch):
    """MCP の 2026年7月28日版の道は JSON 一発で Content-Length を付ける。書き換えで本文の長さが変わっても宣言と一致する。"""
    body = json.dumps(_payload(RAW), ensure_ascii=False).encode()
    msgs = _run_asgi([(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                     [body], monkeypatch)
    sent = b"".join(m.get("body", b"") for m in msgs if m["type"] == "http.response.body")
    assert "pydantic" not in sent.decode() and len(sent) != len(body)
    assert _declared(msgs) == len(sent)
    assert [k for k, _ in msgs[0]["headers"]].count(b"content-length") == 1


def test_分けて届いた本文もまとめて書き換える(monkeypatch):
    body = json.dumps(_payload(RAW), ensure_ascii=False).encode()
    half = len(body) // 2
    msgs = _run_asgi([(b"content-length", str(len(body)).encode())], [body[:half], body[half:]], monkeypatch)
    bodies = [m for m in msgs if m["type"] == "http.response.body"]
    assert len(bodies) == 1 and not bodies[0]["more_body"]
    assert "pydantic" not in bodies[0]["body"].decode() and _declared(msgs) == len(bodies[0]["body"])


def test_印の無い長さ付き応答は一バイトも変えない(monkeypatch):
    body = json.dumps({"result": {"content": [{"text": "ok", "type": "text"}]}}).encode()
    msgs = _run_asgi([(b"content-length", str(len(body)).encode())], [body], monkeypatch)
    assert msgs[1]["body"] == body and _declared(msgs) == len(body)


def test_長さを宣言しない_sse_は逐次に通す(monkeypatch):
    """SSE（chunked）は預からない＝start がすぐ出て、本文は届いた順に書き換えて通す。"""
    first = b": ping\n\n"
    msgs = _run_asgi([(b"content-type", b"text/event-stream")], [first, _sse(_payload(RAW))], monkeypatch)
    assert [m["type"] for m in msgs] == ["http.response.start", "http.response.body", "http.response.body"]
    assert msgs[1]["body"] == first and msgs[1]["more_body"] is True
    assert "pydantic" not in msgs[2]["body"].decode()
