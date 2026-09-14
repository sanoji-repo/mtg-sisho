#!/usr/bin/env python3
"""test_gate.py — 門と札の試験（2026-09-07 小片 8）。

DB は不要。時計を差し替え、偽の ASGI アプリケーションで検証する。
"""
import asyncio
import datetime
import json
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sisho.context import CURRENT_CLIENT_IP, CURRENT_FUDA
from sisho.gate import FudaStore, GateASGI, short
from sisho.ratelimit import RateLimiter, RateLimitASGI, send_429
import sisho.toollog as toollog
import sisho.tools.combos as combos


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class MockTime:
    def __init__(self, now_dt):
        if now_dt.tzinfo is None:
            now_dt = now_dt.astimezone()
        self.now_dt = now_dt

    def __call__(self):
        if self.now_dt.tzinfo is None:
            self.now_dt = self.now_dt.astimezone()
        return self.now_dt


async def run_asgi_request(app, path, method="POST", client_ip="203.0.113.10", headers=None, body=b""):
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.encode("latin1"), v.encode("latin1")))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode("latin1"),
        "headers": raw_headers,
        "client": (client_ip, 12345),
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)
    return scope, sent


def parse_response(sent):
    status = None
    headers = {}
    body = b""
    for msg in sent:
        if msg["type"] == "http.response.start":
            status = msg["status"]
            headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in msg.get("headers", [])}
        elif msg["type"] == "http.response.body":
            body += msg.get("body", b"")
    return status, headers, body


def test_fuda_store_crud(tmp_path):
    """1. FudaStore: 発行 → lookup で生きている／stop → lookup が None／ファイルを別の FudaStore で開き直しても同じ状態／壊れた行（列不足）は無視／issued_in が直近だけ数える。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    base_dt = datetime.datetime(2026, 9, 7, 12, 0, 0).astimezone()
    mock_dt = MockTime(base_dt)

    store1 = FudaStore(fuda_file, dt_now=mock_dt)
    fuda = store1.issue("192.0.2.1", memo="first token", per_min=30)
    assert len(fuda) >= 32
    assert store1.lookup(fuda) is not None
    assert store1.lookup(fuda)["alive"] is True
    assert store1.lookup(fuda)["per_min"] == 30
    assert store1.lookup(fuda)["memo"] == "first token"

    # stop -> lookup is None
    assert store1.stop(fuda) is True
    assert store1.lookup(fuda) is None
    # stopping again returns False
    assert store1.stop(fuda) is False

    # stop の TSV 行は番兵 '-' を使わず末尾空文字 \t\n
    with open(fuda_file, "r", encoding="utf-8") as f:
        stop_lines = [l for l in f if "\tstop\t" in l]
    assert len(stop_lines) == 1
    assert stop_lines[0].endswith("\t\n")
    stop_cols = stop_lines[0].rstrip("\r\n").split("\t")
    assert len(stop_cols) == 6
    assert stop_cols[5] == ""
    assert "-" not in stop_cols

    # issue a second fuda
    fuda2 = store1.issue("192.0.2.1", memo="second token")

    # Reopen with store2 -> sees same state
    store2 = FudaStore(fuda_file, dt_now=mock_dt)
    assert store2.lookup(fuda) is None
    assert store2.lookup(fuda2) is not None

    # Broken lines in TSV are ignored
    with open(fuda_file, "a", encoding="utf-8") as f:
        f.write("broken line\n")
        f.write("too\tfew\tcols\n")
        f.write("bad-timestamp\tissue\tfuda_bad\t192.0.2.1\t60\tmemo\n")
    store3 = FudaStore(fuda_file, dt_now=mock_dt)
    assert store3.broken_lines == 3
    assert store3.lookup(fuda2) is not None

    # issued_in: counts recent issues
    mock_dt.now_dt = base_dt + datetime.timedelta(seconds=100)
    store3.issue("192.0.2.2")
    assert store3.issued_in("192.0.2.1", seconds=86400) == 2
    assert store3.issued_in("192.0.2.2", seconds=86400) == 1
    # After 86401 seconds from base_dt, 192.0.2.1 expires
    mock_dt.now_dt = base_dt + datetime.timedelta(seconds=86405)
    assert store3.issued_in("192.0.2.1", seconds=86400) == 0
    assert store3.issued_in("192.0.2.2", seconds=86400) == 1

    # After 86400 seconds from 192.0.2.2 issue time (100 + 86401)
    mock_dt.now_dt = base_dt + datetime.timedelta(seconds=86505)
    assert store3.issued_in("192.0.2.2", seconds=86400) == 0


def test_fuda_store_reload_if_changed(tmp_path):
    """2. reload_if_changed: 別の store が stop を追記したら、1 秒以上経った後の lookup が None になる。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    clock1 = Clock(1000.0)
    store1 = FudaStore(fuda_file, clock=clock1)
    fuda = store1.issue("192.0.2.1")
    assert store1.lookup(fuda) is not None

    store2 = FudaStore(fuda_file)
    store2.stop(fuda)

    # 1 秒未満なら再読み込みしない（キャッシュされた生きている状態を返す）
    clock1.t += 0.5
    assert store1.lookup(fuda) is not None

    # 1 秒以上経過後は再読み込みして None になる
    clock1.t += 0.6
    assert store1.lookup(fuda) is None


def test_gate_asgi_404(tmp_path):
    """3. GateASGI 404: 知らないパス・無い札・止めた札・legacy_on=False の旧パス → すべて 404・内側の app は呼ばれない。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    store = FudaStore(fuda_file)
    fuda_stopped = store.issue("192.0.2.1")
    store.stop(fuda_stopped)

    calls = []

    async def inner_app(scope, receive, send):
        calls.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    limiter = RateLimiter(per_ip=60, global_=300)
    issue_limiter = RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="")
    gate = GateASGI(inner_app, store, limiter, issue_limiter,
                   inner_path="/mcp", prefix="/mcp/", legacy_on=False)

    for bad_path in ["/unknown", "/mcp/nonexistentfuda123456789012345", f"/mcp/{fuda_stopped}", "/mcp"]:
        _, sent = asyncio.run(run_asgi_request(gate, bad_path))
        status, headers, body = parse_response(sent)
        assert status == 404, f"Path {bad_path} should be 404, got {status}"
        assert body == b"not found"
        assert headers.get("content-length") == "9"
        assert len(calls) == 0, f"Inner app must not be called for {bad_path}"


def test_gate_asgi_pass_and_scope_preservation(tmp_path):
    """4. GateASGI 通過: 生きている札 → 内側の app が inner_path で呼ばれ、元の scope は不変・CURRENT_FUDA が先頭 8 字で見え終了後リセット。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    store = FudaStore(fuda_file)
    fuda = store.issue("192.0.2.1")

    inner_scopes = []
    fuda_inside = []

    async def inner_app(scope, receive, send):
        inner_scopes.append(dict(scope))
        fuda_inside.append(CURRENT_FUDA.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"inner_ok"})

    limiter = RateLimiter(per_ip=60, global_=300)
    issue_limiter = RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="")
    gate = GateASGI(inner_app, store, limiter, issue_limiter, inner_path="/mcp", prefix="/mcp/")

    path_called = f"/mcp/{fuda}"
    orig_scope, sent = asyncio.run(run_asgi_request(gate, path_called))
    status, _, body = parse_response(sent)
    assert status == 200
    assert body == b"inner_ok"

    # 元の scope は不変
    assert orig_scope["path"] == path_called
    assert orig_scope["raw_path"] == path_called.encode("latin1")

    # 内側の app には inner_path が渡る
    assert len(inner_scopes) == 1
    assert inner_scopes[0]["path"] == "/mcp"
    assert inner_scopes[0]["raw_path"] == b"/mcp"

    # 内側から CURRENT_FUDA が先頭 8 字で見える
    assert fuda_inside[0] == short(fuda)
    assert len(fuda_inside[0]) == 8

    # 呼び出し終了後は CURRENT_FUDA が未設定（空文字）に戻る
    assert CURRENT_FUDA.get() == ""


def test_gate_asgi_fuda_rate_limit(tmp_path):
    """5. 札ごとの枠: per_min=2 の札で 3 回目が 429（本文に「上限」・retry-after あり）・別の札は巻き添えなし・127.0.0.1 からでも札の枠は効く。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    store = FudaStore(fuda_file)
    fuda_limited = store.issue("192.0.2.1", per_min=2)
    fuda_other = store.issue("192.0.2.2", per_min=10)

    async def inner_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    clock = Clock(1000.0)
    limiter = RateLimiter(per_ip=60, global_=300, clock=clock)
    issue_limiter = RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="")
    gate = GateASGI(inner_app, store, limiter, issue_limiter, inner_path="/mcp", prefix="/mcp/")

    # 127.0.0.1 からアクセス（IP 免除があっても札キーは免除されないことの検証）
    ip = "127.0.0.1"

    # 1回目
    _, sent1 = asyncio.run(run_asgi_request(gate, f"/mcp/{fuda_limited}", client_ip=ip))
    assert parse_response(sent1)[0] == 200

    # 2回目
    _, sent2 = asyncio.run(run_asgi_request(gate, f"/mcp/{fuda_limited}", client_ip=ip))
    assert parse_response(sent2)[0] == 200

    # 3回目 -> 429
    _, sent3 = asyncio.run(run_asgi_request(gate, f"/mcp/{fuda_limited}", client_ip=ip))
    status, headers, body = parse_response(sent3)
    assert status == 429
    assert "retry-after" in headers and headers["retry-after"].isdigit()
    d = json.loads(body)
    assert "上限" in d["error"]["message"]

    # 別の札は巻き添えにならない
    _, sent_other = asyncio.run(run_asgi_request(gate, f"/mcp/{fuda_other}", client_ip=ip))
    assert parse_response(sent_other)[0] == 200


def test_gate_asgi_legacy_path(tmp_path):
    """6. 旧パス: legacy_on=True で inner_path への要求が通り CURRENT_FUDA == 'legacy'・IP の枠で数えられる。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    store = FudaStore(fuda_file)
    fuda_inside = []
    client_ip_inside = []

    async def inner_app(scope, receive, send):
        fuda_inside.append(CURRENT_FUDA.get())
        client_ip_inside.append(CURRENT_CLIENT_IP.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    clock = Clock(1000.0)
    limiter = RateLimiter(per_ip=1, global_=100, clock=clock)
    issue_limiter = RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="")
    gate = GateASGI(inner_app, store, limiter, issue_limiter, inner_path="/mcp", prefix="/mcp/", legacy_on=True)

    # 1回目は通る
    _, sent1 = asyncio.run(run_asgi_request(gate, "/mcp", client_ip="198.51.100.1"))
    assert parse_response(sent1)[0] == 200
    assert fuda_inside == ["legacy"]
    assert client_ip_inside == ["198.51.100.1"]
    assert CURRENT_CLIENT_IP.get() == ""

    # IP枠が per_ip=1 のため、同一 IP からの 2 回目は 429
    _, sent2 = asyncio.run(run_asgi_request(gate, "/mcp", client_ip="198.51.100.1"))
    assert parse_response(sent2)[0] == 429


def test_gate_asgi_issue_page(tmp_path):
    """7. 発行ページ: GET が 200・POST が 200 で prefix+札・4 回目 429・PUT 405・MCP_PUBLIC_BASE あり/なしの URL 生成。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    store = FudaStore(fuda_file)

    async def inner_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    clock = Clock(1000.0)
    limiter = RateLimiter(per_ip=60, global_=300, clock=clock)
    issue_limiter = RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="", clock=clock)
    gate = GateASGI(inner_app, store, limiter, issue_limiter,
                   inner_path="/mcp", prefix="/mcp/", public_base="https://base.example.com")

    ip = "203.0.113.50"

    # GET -> 200 html no-store
    _, sent_get = asyncio.run(run_asgi_request(gate, "/issue", method="GET", client_ip=ip))
    status, headers, body = parse_response(sent_get)
    assert status == 200
    assert "text/html" in headers["content-type"]
    assert "no-store" in headers["cache-control"]
    assert '<form method="post">' in body.decode("utf-8").lower()

    # POST 1回目 -> 200
    _, sent_p1 = asyncio.run(run_asgi_request(gate, "/issue", method="POST", client_ip=ip))
    status, headers, body = parse_response(sent_p1)
    body_str = body.decode("utf-8")
    assert status == 200
    assert "https://base.example.com/mcp/" in body_str
    assert "readonly" in body_str
    assert "ボタンを押すと、あなた専用の接続 URL が出ます" not in body_str
    assert "URL は合言葉と同じです。人に見せないでください" in body_str
    assert "無くしたら、もう一度ここで発行できます" in body_str
    assert "claude.ai の設定 → コネクタ → カスタムコネクタを追加、に貼る" in body_str

    # POST 2回目・3回目
    _, sent_p2 = asyncio.run(run_asgi_request(gate, "/issue", method="POST", client_ip=ip))
    assert parse_response(sent_p2)[0] == 200
    _, sent_p3 = asyncio.run(run_asgi_request(gate, "/issue", method="POST", client_ip=ip))
    assert parse_response(sent_p3)[0] == 200

    # POST 4回目 -> 429 HTML
    _, sent_p4 = asyncio.run(run_asgi_request(gate, "/issue", method="POST", client_ip=ip))
    status, headers, body = parse_response(sent_p4)
    assert status == 429
    assert "text/html" in headers["content-type"]
    assert "今日はもう発行できません" in body.decode("utf-8")

    # PUT -> 405 (content-length あり)
    _, sent_put = asyncio.run(run_asgi_request(gate, "/issue", method="PUT", client_ip=ip))
    status_put, hdr_put, _ = parse_response(sent_put)
    assert status_put == 405
    assert hdr_put.get("content-length") == str(len(b"Method Not Allowed"))

    # MCP_PUBLIC_BASE が空の場合は X-Forwarded-Host を見ず Host ヘッダから組む（B-2）
    gate_no_base = GateASGI(inner_app, store, limiter,
                            RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="", clock=clock),
                            inner_path="/mcp", prefix="/mcp/", public_base="")
    _, sent_fwd = asyncio.run(run_asgi_request(
        gate_no_base, "/issue", method="POST", client_ip="203.0.113.60",
        headers={"host": "sisho.example", "x-forwarded-host": "evil.example.com", "x-forwarded-proto": "http"}
    ))
    status, _, body = parse_response(sent_fwd)
    assert status == 200
    assert "https://sisho.example/mcp/" in body.decode("utf-8")
    assert "evil.example.com" not in body.decode("utf-8")


def test_send_429_shared_shape():
    """8. 429 の共用: send_429 を RateLimitASGI と GateASGI の両方が使い、本文の形が同じ。"""
    sent_rl = []
    async def mock_send_rl(msg): sent_rl.append(msg)
    asyncio.run(send_429(mock_send_rl, 45, "エラーメッセージA"))
    status1, hdr1, body1 = parse_response(sent_rl)

    sent_gate = []
    async def mock_send_gate(msg): sent_gate.append(msg)
    asyncio.run(send_429(mock_send_gate, 45, "エラーメッセージB"))
    status2, hdr2, body2 = parse_response(sent_gate)

    assert status1 == status2 == 429
    assert hdr1["content-type"] == hdr2["content-type"] == "application/json; charset=utf-8"
    assert hdr1["retry-after"] == hdr2["retry-after"] == "45"
    d1 = json.loads(body1)
    d2 = json.loads(body2)
    assert d1["jsonrpc"] == d2["jsonrpc"] == "2.0"
    assert d1["error"]["code"] == d2["error"]["code"] == -32000
    assert d1["error"]["message"] == "エラーメッセージA"
    assert d2["error"]["message"] == "エラーメッセージB"


def test_toollog_columns(tmp_path, monkeypatch):
    """9. toollog: 入口 4 列・出口 8 列・CURRENT_FUDA 未設定なら末尾が空文字・設定中なら先頭 8 字。"""
    log_file = tmp_path / "mcp_tools.log"
    monkeypatch.setattr("sisho.toollog.TOOL_LOG", str(log_file))

    # CURRENT_FUDA 未設定
    toollog._log_tool("test_tool", {"arg": "val"})
    toollog._log_tool_end("test_tool", "ok", 0.05, 1, 0.01)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    entry_cols = lines[0].split("\t")
    exit_cols = lines[1].split("\t")
    assert len(entry_cols) == 4
    assert entry_cols[3] == ""
    assert len(exit_cols) == 8
    assert exit_cols[7] == ""

    # CURRENT_FUDA 設定中
    token = CURRENT_FUDA.set("12345678")
    try:
        toollog._log_tool("test_tool", {"arg": "val2"})
        toollog._log_tool_end("test_tool", "ok", 0.10, 2, 0.02)
    finally:
        CURRENT_FUDA.reset(token)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    entry2 = lines[2].split("\t")
    exit2 = lines[3].split("\t")
    assert len(entry2) == 4
    assert entry2[3] == "12345678"
    assert len(exit2) == 8
    assert exit2[7] == "12345678"


def test_combos_fuda_rate_limit(monkeypatch):
    """10. combos: CURRENT_FUDA を設定し _spellbook_post を偽物に差し替え、11 回目が error_kind == 'rate_limited'（10 回は通る）・別の札は巻き添えなし・retry 秒の案内あり。"""
    def fake_post(payload):
        return {"results": {"included": [], "almostIncluded": [], "almostIncludedByAddingColors": []}}

    monkeypatch.setattr(combos, "_spellbook_post", fake_post)

    # _combos_limiter のバケットをテスト用に初期化
    clock = Clock(1000.0)
    test_limiter = RateLimiter(per_ip=10, global_=60, clock=clock, exempt="")
    monkeypatch.setattr(combos, "_combos_limiter", test_limiter)

    token = CURRENT_FUDA.set("fuda_aaa")
    try:
        # 1〜10 回目は通る
        for i in range(10):
            res = combos.find_combos(["Sol Ring"])
            d = json.loads(res)
            assert "error_kind" not in d, f"{i+1} 回目が失敗: {d}"

        # 11 回目は拒否
        res11 = combos.find_combos(["Sol Ring"])
        d11 = json.loads(res11)
        assert d11.get("error_kind") == "rate_limited"
        assert f"1 分に {combos._COMBOS_PER_MIN} 回まで" in d11.get("error", "")
        assert "秒待ってから呼び直す" in d11.get("error", "")
    finally:
        CURRENT_FUDA.reset(token)

    # 別の札は巻き添えにならない
    token_b = CURRENT_FUDA.set("fuda_bbb")
    try:
        res_b = combos.find_combos(["Sol Ring"])
        d_b = json.loads(res_b)
        assert "error_kind" not in d_b, "別札が巻き添えになった"
    finally:
        CURRENT_FUDA.reset(token_b)


def test_gate_asgi_issue_persistence_after_restart(tmp_path):
    """11. 再起動後の 24h 枠: 既存 TSV に 1 時間前の同一 IP issue 3 行がある状態で新しい FudaStore＋新しい issue_limiter を組んで POST → 429。25 時間前なら 200。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    base_dt = datetime.datetime(2026, 9, 7, 12, 0, 0).astimezone()
    ip = "203.0.113.88"

    async def dummy_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    # 1. 1時間前の issue が 3 行ある状態
    t_1h_ago = base_dt - datetime.timedelta(hours=1)
    with open(fuda_file, "w", encoding="utf-8") as f:
        for i in range(3):
            f.write(f"{t_1h_ago.isoformat()}\tissue\tfuda_old_{i}\t{ip}\t60\tmemo\n")

    # 新しい FudaStore + 新しい issue_limiter（メモリは空）
    mock_dt = MockTime(base_dt)
    clock = Clock(base_dt.timestamp())
    store1 = FudaStore(fuda_file, dt_now=mock_dt)
    issue_limiter1 = RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="", clock=clock)
    limiter1 = RateLimiter(per_ip=60, global_=300, clock=clock)
    gate1 = GateASGI(dummy_app, store1, limiter1, issue_limiter1,
                     inner_path="/mcp", prefix="/mcp/")

    _, sent1 = asyncio.run(run_asgi_request(gate1, "/issue", method="POST", client_ip=ip))
    status1, headers1, body1 = parse_response(sent1)
    assert status1 == 429
    assert "今日はもう発行できません" in body1.decode("utf-8")
    assert "retry-after" in headers1
    retry_sec = int(headers1["retry-after"])
    # 1時間前(3600秒前)なので、残り時間は約 23時間 (86400 - 3600 = 82800秒) 付近
    assert 82700 <= retry_sec <= 82900

    # 2. 25時間前の issue が 3 行ある状態
    t_25h_ago = base_dt - datetime.timedelta(hours=25)
    with open(fuda_file, "w", encoding="utf-8") as f:
        for i in range(3):
            f.write(f"{t_25h_ago.isoformat()}\tissue\tfuda_older_{i}\t{ip}\t60\tmemo\n")

    store2 = FudaStore(fuda_file, dt_now=mock_dt)
    issue_limiter2 = RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="", clock=clock)
    limiter2 = RateLimiter(per_ip=60, global_=300, clock=clock)
    gate2 = GateASGI(dummy_app, store2, limiter2, issue_limiter2,
                     inner_path="/mcp", prefix="/mcp/")

    _, sent2 = asyncio.run(run_asgi_request(gate2, "/issue", method="POST", client_ip=ip))
    status2, _, body2 = parse_response(sent2)
    assert status2 == 200
    assert "あなたの接続 URL" in body2.decode("utf-8")


def test_gate_asgi_issue_post_drains_body(tmp_path):
    """12. POST 本文の読み捨て: _handle_issue の POST で応答前に receive() を more_body が False になるまで読む。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    store = FudaStore(fuda_file)

    async def dummy_app(scope, receive, send):
        pass

    limiter = RateLimiter(per_ip=60, global_=300)
    issue_limiter = RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="")
    gate = GateASGI(dummy_app, store, limiter, issue_limiter, inner_path="/mcp", prefix="/mcp/")

    chunks = [
        {"type": "http.request", "body": b"field1=val1&", "more_body": True},
        {"type": "http.request", "body": b"field2=val2&", "more_body": True},
        {"type": "http.request", "body": b"field3=val3", "more_body": False},
    ]
    receive_calls = []

    async def chunked_receive():
        idx = len(receive_calls)
        receive_calls.append(idx)
        return chunks[idx]

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/issue",
        "raw_path": b"/issue",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        "client": ("203.0.113.19", 12345),
    }
    sent = []
    async def send(msg):
        sent.append(msg)

    asyncio.run(gate(scope, chunked_receive, send))
    status, _, _ = parse_response(sent)
    assert status == 200
    assert len(receive_calls) == 3, f"receive() should have been called 3 times until more_body=False, but got {len(receive_calls)}"


def test_bin_fuda_cli(tmp_path):
    """13. bin/fuda CLI の動作検証（issue, list, stop, list --all, 番兵'-'の不在）。"""
    import subprocess
    fuda_file = str(tmp_path / "fuda.tsv")
    bin_fuda = os.path.join(os.path.dirname(__file__), "..", "bin", "fuda")
    env = {**os.environ, "PYTHONPATH": os.path.join(os.path.dirname(__file__), "..", "src")}

    # issue
    proc = subprocess.run([sys.executable, bin_fuda, "--file", fuda_file, "issue", "--memo", "cli test"],
                          capture_output=True, text=True, check=True, env=env)
    out = proc.stdout
    assert "札: " in out
    fuda = [line for line in out.splitlines() if line.startswith("札: ")][0].split(": ")[1].strip()

    # list
    proc_list = subprocess.run([sys.executable, bin_fuda, "--file", fuda_file, "list"],
                               capture_output=True, text=True, check=True, env=env)
    list_out = proc_list.stdout.strip()
    assert short(fuda) in list_out
    assert "alive" in list_out
    assert "cli" in list_out
    assert "cli test" in list_out

    # stop
    proc_stop = subprocess.run([sys.executable, bin_fuda, "--file", fuda_file, "stop", fuda[:10]],
                               capture_output=True, text=True, check=True, env=env)
    assert "停止しました" in proc_stop.stdout

    # list (without --all should be empty now)
    proc_list2 = subprocess.run([sys.executable, bin_fuda, "--file", fuda_file, "list"],
                                capture_output=True, text=True, check=True, env=env)
    assert proc_list2.stdout.strip() == ""

    # list --all
    proc_list_all = subprocess.run([sys.executable, bin_fuda, "--file", fuda_file, "list", "--all"],
                                   capture_output=True, text=True, check=True, env=env)
    assert "stopped" in proc_list_all.stdout

    # TSV の stop 行は番兵 '-' を含まず末尾 \t\n で終わる
    with open(fuda_file, "r", encoding="utf-8") as f:
        tsv_lines = f.readlines()
    stop_line = [l for l in tsv_lines if "\tstop\t" in l][0]
    assert stop_line.endswith("\t\n")
    cols = stop_line.rstrip("\r\n").split("\t")
    assert len(cols) == 6
    assert cols[5] == ""
    assert "-" not in cols


def test_gate_asgi_probes_are_rate_limited(tmp_path):
    """14. 探り（無い札・知らないパス・閉じた旧パス・発行ページ）は IP の枠で数える（2026-09-07 指摘＝旧 RateLimitASGI の振る舞いの復元）。
    札付きの正しい呼び出しは IP の枠を消費しない（claude.ai の出口 IP は共有）。除外 IP の探りは数えない。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    store = FudaStore(fuda_file)
    fuda = store.issue("192.0.2.1")

    async def inner_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    clock = Clock(1000.0)
    limiter = RateLimiter(per_ip=3, global_=1000, clock=clock)
    issue_limiter = RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="", clock=clock)
    gate = GateASGI(inner_app, store, limiter, issue_limiter, inner_path="/mcp", prefix="/mcp/", legacy_on=False)
    ip = "203.0.113.77"

    # 正しい札の呼び出しは IP の枠を減らさない（5 回呼んでも探りの枠 3 は残る）
    for _ in range(5):
        _, sent = asyncio.run(run_asgi_request(gate, f"/mcp/{fuda}", client_ip=ip))
        assert parse_response(sent)[0] == 200
    # 探り 3 回は 404・4 回目は 429（存在の情報は与えない・Retry-After だけ）
    probes = ["/mcp/" + "x" * 32, "/nothing", "/mcp"]
    for path in probes:
        _, sent = asyncio.run(run_asgi_request(gate, path, client_ip=ip))
        assert parse_response(sent)[0] == 404, path
    _, sent = asyncio.run(run_asgi_request(gate, "/mcp/" + "y" * 32, client_ip=ip))
    status, headers, body = parse_response(sent)
    assert status == 429 and headers["retry-after"].isdigit()
    assert b"not found" not in body
    # 別の IP は巻き添えにならない・除外 IP（127.0.0.1）は何度でも 404
    _, sent = asyncio.run(run_asgi_request(gate, "/nothing", client_ip="203.0.113.78"))
    assert parse_response(sent)[0] == 404
    for _ in range(10):
        _, sent = asyncio.run(run_asgi_request(gate, "/nothing", client_ip="127.0.0.1"))
        assert parse_response(sent)[0] == 404
    # 窓が滑れば戻る
    clock.t += 61
    _, sent = asyncio.run(run_asgi_request(gate, "/nothing", client_ip=ip))
    assert parse_response(sent)[0] == 404
    # 発行ページの GET も IP の枠（この IP は今 1 回使った → あと 2 回は 200・次は 429）
    for _ in range(2):
        _, sent = asyncio.run(run_asgi_request(gate, "/issue", method="GET", client_ip=ip))
        assert parse_response(sent)[0] == 200
    _, sent = asyncio.run(run_asgi_request(gate, "/issue", method="GET", client_ip=ip))
    assert parse_response(sent)[0] == 429


def test_uvicorn_kwargs_disables_access_log():
    """15. A-1: uvicorn.run に渡す引数に access_log=False が含まれ、札の全文が journal に流れない。"""
    from mcp_server import _uvicorn_kwargs
    kw = _uvicorn_kwargs(8765, "info")
    assert kw.get("access_log") is False
    assert kw.get("host") == "127.0.0.1"
    assert kw.get("port") == 8765
    assert kw.get("timeout_graceful_shutdown") == 3


def test_gate_asgi_issue_csrf_sec_fetch_site(tmp_path):
    """16. B-7: /issue の POST は Sec-Fetch-Site: cross-site なら 403、same-origin または無しなら 200。C-8: /issue/ 末尾スラッシュも受ける。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    store = FudaStore(fuda_file)
    async def dummy(scope, receive, send): pass
    limiter = RateLimiter(per_ip=60, global_=300)
    issue_limiter = RateLimiter(per_ip=10, global_=100)
    gate = GateASGI(dummy, store, limiter, issue_limiter)

    # 1. cross-site -> 403
    _, sent_cross = asyncio.run(run_asgi_request(
        gate, "/issue", method="POST", client_ip="203.0.113.1",
        headers={"sec-fetch-site": "cross-site"}
    ))
    status, hdr, body = parse_response(sent_cross)
    assert status == 403
    assert "このページの「発行する」ボタンから発行してください" in body.decode("utf-8")
    assert "content-length" in hdr

    # 2. same-origin -> 200
    _, sent_same = asyncio.run(run_asgi_request(
        gate, "/issue", method="POST", client_ip="203.0.113.2",
        headers={"sec-fetch-site": "same-origin"}
    ))
    assert parse_response(sent_same)[0] == 200

    # 3. ヘッダ無し (curl等) -> 200
    _, sent_none = asyncio.run(run_asgi_request(
        gate, "/issue", method="POST", client_ip="203.0.113.3"
    ))
    assert parse_response(sent_none)[0] == 200

    # 4. 末尾スラッシュ /issue/ も受け付ける (C-8)
    _, sent_slash = asyncio.run(run_asgi_request(
        gate, "/issue/", method="GET", client_ip="203.0.113.4"
    ))
    assert parse_response(sent_slash)[0] == 200


def test_gate_asgi_from_env_warns_empty_public_base(tmp_path, monkeypatch, caplog):
    """17. B-2: MCP_PUBLIC_BASE が空のとき起動時に警告を出す。"""
    import logging
    monkeypatch.delenv("MCP_PUBLIC_BASE", raising=False)
    monkeypatch.setenv("MCP_FUDA_FILE", str(tmp_path / "f.tsv"))     # 環境の実ファイルを読まない（内部レビュー C-15）
    async def dummy(scope, receive, send): pass
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        GateASGI.from_env(dummy)
    assert any("[gate] MCP_PUBLIC_BASE が空" in r.message for r in caplog.records)


def test_combos_legacy_per_ip_rate_limit(monkeypatch, caplog):
    """18. B-4: 旧パス (CURRENT_FUDA="legacy") の find_combos は CURRENT_CLIENT_IP ごとに数える。
    片方の IP が 10 回枠を使い切っても、もう片方の IP は通る。
    C-1: 札の拒否ログが [gate] 429 fuda= になる。"""
    import logging

    def fake_post(payload):
        return {"results": {"included": [], "almostIncluded": [], "almostIncludedByAddingColors": []}}

    monkeypatch.setattr(combos, "_spellbook_post", fake_post)
    clock = Clock(1000.0)
    test_limiter = RateLimiter(per_ip=10, global_=60, clock=clock, exempt="")
    monkeypatch.setattr(combos, "_combos_limiter", test_limiter)

    token_fuda = CURRENT_FUDA.set("legacy")
    token_ip1 = CURRENT_CLIENT_IP.set("198.51.100.1")
    try:
        for _ in range(10):
            res = combos.find_combos(["Sol Ring"])
            assert "error_kind" not in json.loads(res)

        # 11 回目は IP 1 が 429
        res11 = combos.find_combos(["Sol Ring"])
        assert json.loads(res11).get("error_kind") == "rate_limited"

        # 別の IP 2 は巻き添えにならず通る
        token_ip2 = CURRENT_CLIENT_IP.set("198.51.100.2")
        try:
            res_ip2 = combos.find_combos(["Sol Ring"])
            assert "error_kind" not in json.loads(res_ip2)
        finally:
            CURRENT_CLIENT_IP.reset(token_ip2)
    finally:
        CURRENT_CLIENT_IP.reset(token_ip1)
        CURRENT_FUDA.reset(token_fuda)

    # C-1: 札の拒否ログが [gate] 429 fuda= で記録されることの確認
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        token_fuda2 = CURRENT_FUDA.set("tokn1234")
        try:
            for _ in range(11):
                combos.find_combos(["Sol Ring"])
        finally:
            CURRENT_FUDA.reset(token_fuda2)
    assert any("[gate] 429 fuda=tokn1234" in r.message for r in caplog.records)


def test_fuda_store_errors_and_permissions(tmp_path, monkeypatch, caplog):
    """19. B-5: 読めないパスで _load が空で起動し caplog に error。壊れた行で warning。
    C-4: 新規作成時のパーミッションが 0o600 (umask 0o022 下でも)。
    書き込み先が無いときの POST が 503。"""
    import logging
    import stat

    # 1. C-4: 新規作成後の stat が 0o600
    old_umask = os.umask(0o022)
    try:
        fuda_file = str(tmp_path / "fuda_perm.tsv")
        store = FudaStore(fuda_file)
        fuda = store.issue("192.0.2.1")
        mode = stat.S_IMODE(os.stat(fuda_file).st_mode)
        assert mode == 0o600
    finally:
        os.umask(old_umask)

    # 2. B-5: 読めないパス（ディレクトリを path に渡すなど）で error ログ
    unreadable_dir = str(tmp_path / "unreadable_dir")
    os.makedirs(unreadable_dir, exist_ok=True)
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        store_bad = FudaStore(unreadable_dir)
        assert len(store_bad.records) == 0
    assert any("[gate] fuda.tsv を読めない" in r.message for r in caplog.records)

    # 3. B-5: 壊れた行で warning ログ
    broken_file = str(tmp_path / "broken.tsv")
    with open(broken_file, "w", encoding="utf-8") as f:
        f.write("bad\tline\n")
        f.write("too\tfew\tcols\t3\n")
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        store_broken = FudaStore(broken_file)
        assert store_broken.broken_lines == 2
    assert any("[gate] fuda.tsv に壊れた行 2" in r.message for r in caplog.records)

    # 4. B-5: 書き込み先が無い（OSError）ときの POST が 503 HTML + Retry-After: 60
    def fail_open(*args, **kwargs):
        raise OSError("Permission denied (test)")

    async def dummy(scope, receive, send): pass
    limiter = RateLimiter(per_ip=60, global_=300)
    issue_limiter = RateLimiter(per_ip=10, global_=100)
    fuda_file503 = str(tmp_path / "fuda_503.tsv")
    store503 = FudaStore(fuda_file503)
    gate = GateASGI(dummy, store503, limiter, issue_limiter)

    monkeypatch.setattr(os, "open", fail_open)
    _, sent = asyncio.run(run_asgi_request(gate, "/issue", method="POST", client_ip="203.0.113.77"))
    status, headers, body = parse_response(sent)
    assert status == 503
    assert headers.get("retry-after") == "60"
    assert "今は発行できません" in body.decode("utf-8")


def test_uvicorn_kwargs_defined_before_main():
    """20. B-9: _uvicorn_kwargs の定義が __main__ ブロックより前にある（後ろだと HTTP 起動が NameError＝Opus A-3）。
    実行せず ast で行番号を比べる。"""
    import ast
    src_path = os.path.join(os.path.dirname(__file__), "..", "src", "mcp_server.py")
    tree = ast.parse(open(src_path, encoding="utf-8").read())
    def_line = next(n.lineno for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_uvicorn_kwargs")
    main_line = next(n.lineno for n in tree.body if isinstance(n, ast.If)
                     and isinstance(n.test, ast.Compare) and getattr(n.test.left, "id", "") == "__name__")
    assert def_line < main_line, f"_uvicorn_kwargs（{def_line} 行）は __main__（{main_line} 行）より前に置く"


def test_server_http_startup_smoke(tmp_path):
    """21. B-9: 本物の起動経路（python src/mcp_server.py http <port>）で uvicorn が待ち受けに入り /issue が 200 を返す。
    import 経路だけの試験では A-3（定義順の NameError）を捕まえられなかった。DB は要らない（起動と発行ページだけ）。"""
    import subprocess, socket, time, urllib.request
    src_path = os.path.join(os.path.dirname(__file__), "..", "src", "mcp_server.py")
    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0)); port = s_.getsockname()[1]
    env = dict(os.environ)
    env.update({"MCP_FUDA_FILE": str(tmp_path / "fuda.tsv"), "MCP_TOOL_LOG": str(tmp_path / "tools.log"),
                "MCP_PUBLIC_BASE": "https://smoke.example", "MCP_STATELESS": "1", "MCP_HTTP_PATH": "/old-secret"})
    proc = subprocess.Popen([sys.executable, src_path, "http", str(port)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read()
                raise AssertionError(f"サーバーが起動前に終了した（rc={proc.returncode}）:\n{out[-2000:]}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("15 秒待っても待ち受けに入らない")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/issue", timeout=5) as r:
            assert r.status == 200 and "text/html" in r.headers.get("content-type", "")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/issue", data=b"", timeout=5) as r:
            body = r.read().decode("utf-8")
            assert r.status == 200 and "https://smoke.example/mcp/" in body
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_issue_global_daily_limit_from_tsv(tmp_path):
    """22. B-10: 全体の 1 日の枠も TSV の issue 行で数える（再起動で消えない）。新しい IP でも 429。25 時間前なら 200。"""
    import datetime as _dt
    fuda_file = str(tmp_path / "fuda.tsv")
    now = _dt.datetime.now().astimezone()

    def write_rows(age_hours, n):
        ts = (now - _dt.timedelta(hours=age_hours)).isoformat()
        with open(fuda_file, "w", encoding="utf-8") as f:
            for i in range(n):
                f.write(f"{ts}\tissue\t{'x' * 24}{i:08d}\t198.51.100.{i % 250}\t60\t\n")

    async def inner_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    def gate_for():
        store = FudaStore(fuda_file, dt_now=lambda: _dt.datetime.now().astimezone())
        return GateASGI(inner_app, store, RateLimiter(per_ip=60, global_=300),
                        RateLimiter(per_ip=3, global_=100, window=86400.0, exempt=""),
                        inner_path="/mcp", prefix="/mcp/", public_base="https://base.example.com")

    write_rows(1, 100)
    _, sent = asyncio.run(run_asgi_request(gate_for(), "/issue", method="POST", client_ip="203.0.113.99"))
    status, headers, _ = parse_response(sent)
    assert status == 429 and headers["retry-after"].isdigit()
    write_rows(25, 100)
    _, sent = asyncio.run(run_asgi_request(gate_for(), "/issue", method="POST", client_ip="203.0.113.99"))
    assert parse_response(sent)[0] == 200


def test_issue_never_starts_with_dash(tmp_path, monkeypatch):
    """23. C-12: 先頭が - の札は引き直す（argparse がオプションと読んで bin/fuda stop に渡せない）。"""
    import sisho.gate as gate_mod
    draws = iter(["-" + "a" * 31, "-" + "b" * 31, "c" * 32])
    monkeypatch.setattr(gate_mod.secrets, "token_urlsafe", lambda n: next(draws))
    store = FudaStore(str(tmp_path / "fuda.tsv"))
    assert store.issue("192.0.2.9") == "c" * 32


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
