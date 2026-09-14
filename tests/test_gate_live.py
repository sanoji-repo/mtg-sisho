#!/usr/bin/env python3
"""test_gate_live.py — GateASGI と mcp SDK 実通信・contextvar 伝播の live 試験（2026-09-07 小片 8）。

DB が要る試験（VM で走らせる・@requires_db で印）。
公開サーバーの設定（MCP_STATELESS=1）でしか保証していない（内部レビュー C-5）。
"""
import asyncio
import os
import socket
import sys
import threading
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from conftest import requires_db
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings
import uvicorn

import mcp_server
from sisho.gate import FudaStore, GateASGI, short
from sisho.ratelimit import RateLimiter


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@requires_db
@pytest.mark.anyio
async def test_gate_live_mcp_health_and_contextvar(tmp_path, monkeypatch):
    """GateASGI 経由で本物の mcp SDK サーバーと通信し、mtg_rag_health の実行と toollog への contextvar 伝播を検証。"""
    fuda_file = str(tmp_path / "fuda.tsv")
    tool_log_file = str(tmp_path / "mcp_tools.log")

    monkeypatch.setenv("MCP_FUDA_FILE", fuda_file)
    monkeypatch.setenv("MCP_TOOL_LOG", tool_log_file)
    monkeypatch.setattr("sisho.toollog.TOOL_LOG", tool_log_file)

    store = FudaStore(fuda_file)
    fuda = store.issue("127.0.0.1", memo="live_test")
    fuda_short = short(fuda)

    port = get_free_port()
    http_path = "/mcp"

    base_app = mcp_server.server.streamable_http_app(
        streamable_http_path=http_path,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        host="127.0.0.1",
    )

    limiter = RateLimiter(per_ip=60, global_=300)
    issue_limiter = RateLimiter(per_ip=3, global_=100, window=86400.0, exempt="")
    gate_app = GateASGI(
        base_app,
        store,
        limiter,
        issue_limiter,
        inner_path=http_path,
        prefix="/mcp/",
        legacy_on=True,
    )

    config = uvicorn.Config(gate_app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    srv_thread = threading.Thread(target=srv.run, daemon=True)
    srv_thread.start()

    # サーバーの起動待ち
    time.sleep(0.5)

    try:
        # (a) prefix + 札 経由の mtg_rag_health が ok
        valid_url = f"http://127.0.0.1:{port}/mcp/{fuda}"
        async with streamable_http_client(valid_url) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("mtg_rag_health", {})
                assert res.content and len(res.content) > 0
                assert "cards" in res.content[0].text or "ok" in res.content[0].text

        # (b) 無い札の URL は失敗（404 または例外）
        invalid_url = f"http://127.0.0.1:{port}/mcp/nonexistentfuda123456789012345"
        with pytest.raises(Exception):
            async with streamable_http_client(invalid_url) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    await s.call_tool("mtg_rag_health", {})

        # (c) 道具ログの入口・出口の行の末尾列が札の先頭 8 字＝contextvar が SDK 経路を越えて届いたか
        log_content = open(tool_log_file, "r", encoding="utf-8").read()
        lines = [l for l in log_content.splitlines() if l.strip()]
        assert len(lines) >= 2, f"ログ行が足りない: {lines}"

        # 入口行（mtg_rag_health）
        entry_cols = lines[0].split("\t")
        assert len(entry_cols) == 4, f"入口行の列数が 4 でない: {entry_cols}"
        assert entry_cols[1] == "mtg_rag_health"
        assert entry_cols[3] == fuda_short, f"入口行の札列が {fuda_short} でない: {entry_cols[3]}"

        # 出口行（end mtg_rag_health）
        exit_cols = lines[1].split("\t")
        assert len(exit_cols) == 8, f"出口行の列数が 8 でない: {exit_cols}"
        assert exit_cols[1] == "end"
        assert exit_cols[2] == "mtg_rag_health"
        assert exit_cols[7] == fuda_short, f"出口行の札列が {fuda_short} でない: {exit_cols[7]}"

    finally:
        srv.should_exit = True
        srv_thread.join(timeout=3)
