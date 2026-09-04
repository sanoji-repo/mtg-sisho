#!/usr/bin/env python3
"""レート制限（_RateLimiter／_RateLimitASGI）の試験（2026-09-02・配布前の門）。

走らせ方: PYTHONPATH=/home/claude/pylibs /mnt/new_hdd/my_rag_env/bin/python tests/test_ratelimit.py
DB には触らない（時計を差し替えた純粋な試験）。誤発動ゼロ＝枠の内側は必ず通す、が背骨。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402

fails = 0


def ok(cond, label):
    global fails
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        fails += 1


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# 1) IP 別の枠: 3/分なら 3 回通って 4 回目は拒否・待ち秒数は窓の残り
c = Clock()
rl = m._RateLimiter(per_ip=3, global_=100, clock=c)
ok(all(rl.check("203.0.113.5")[0] for _ in range(3)), "IP 別: 枠の内側 3 回は通る")
allowed, retry = rl.check("203.0.113.5")
ok(not allowed and 59 <= retry <= 61, f"IP 別: 4 回目は拒否・Retry-After≒60（実測 {retry}）")
ok(rl.check("203.0.113.6")[0], "IP 別: 別の IP は巻き添えにならない")
c.t += 30
ok(not rl.check("203.0.113.5")[0], "IP 別: 30 秒後もまだ拒否")
c.t += 31
ok(rl.check("203.0.113.5")[0], "IP 別: 61 秒後（窓が滑った）は通る")

# 2) 拒否した呼び出しは数えない（叩き続けても永久に閉じない）
c = Clock(); rl = m._RateLimiter(per_ip=2, global_=100, clock=c)
rl.check("198.51.100.1"); rl.check("198.51.100.1")
for _ in range(50):
    rl.check("198.51.100.1")
c.t += 61
ok(rl.check("198.51.100.1")[0], "拒否は数えない: 50 回叩いても 61 秒後には通る")
ok(rl.denied == 50, f"拒否の計数 50（実測 {rl.denied}）")

# 3) 総量の枠: IP を変えても総量で止まる
c = Clock(); rl = m._RateLimiter(per_ip=100, global_=5, clock=c)
ok(all(rl.check(f"192.0.2.{i}")[0] for i in range(5)), "総量: 5 回は通る")
allowed, retry = rl.check("192.0.2.99")
ok(not allowed and retry >= 1, "総量: 6 回目は別 IP でも拒否")

# 4) 免除: tailnet と 127 は数えない（0 の枠でも通る）
rl = m._RateLimiter(per_ip=1, global_=1, clock=Clock())
ok(all(rl.check(ip)[0] for ip in ["100.79.2.87", "127.0.0.1", "100.111.128.9"] * 5), "免除: tailnet・127 は無制限")
ok(rl.check("?")[0] and not rl.check("?")[0], "不明な client（'?'）は免除しない")

# 5) 0 は無効
rl = m._RateLimiter(per_ip=0, global_=0, clock=Clock())
ok(all(rl.check("203.0.113.9")[0] for _ in range(500)), "枠 0 は無効＝全部通す")

# 6) 鍵の溢れ: max_keys を超えても落ちず、空の鍵から捨てる
c = Clock(); rl = m._RateLimiter(per_ip=10, global_=100000, clock=c, max_keys=100)
for i in range(150):
    rl.check(f"10.9.{i // 250}.{i % 250}")
ok(len(rl.buckets) <= 100, f"鍵は max_keys 以下に収まる（実測 {len(rl.buckets)}）")

# 7) ASGI の外皮: 超過は 429＋Retry-After＋JSON-RPC error 本文・枠の内側は本体へ
calls = []


async def body_app(scope, receive, send):
    calls.append(scope["client"])
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def run(app, ip, typ="http"):
    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await app({"type": typ, "client": (ip, 12345), "method": "POST", "path": "/x"}, receive, send)
    return sent


rl = m._RateLimiter(per_ip=1, global_=100, clock=Clock())
app = m._RateLimitASGI(body_app, rl)
s1 = asyncio.run(run(app, "203.0.113.7"))
ok(s1[0]["status"] == 200 and calls == [("203.0.113.7", 12345)], "ASGI: 枠の内側は本体に届く")
s2 = asyncio.run(run(app, "203.0.113.7"))
hdr = dict(s2[0]["headers"])
body = json.loads(s2[1]["body"])
ok(s2[0]["status"] == 429 and hdr[b"retry-after"].isdigit(), "ASGI: 超過は 429＋Retry-After")
ok(body["jsonrpc"] == "2.0" and body["error"]["code"] == -32000 and "混雑" in body["error"]["message"],
   "ASGI: 本文は JSON-RPC の error（日本語の理由つき）")
ok(len(calls) == 1, "ASGI: 拒否した呼び出しは本体に渡さない")
seen = []


async def passthrough_app(scope, receive, send):
    seen.append(scope["type"])


rl2 = m._RateLimiter(per_ip=0, global_=1, clock=Clock())
rl2.check("203.0.113.8")  # 総量の枠を使い切る
app2 = m._RateLimitASGI(passthrough_app, rl2)
asyncio.run(run(app2, "203.0.113.8", typ="lifespan"))
asyncio.run(run(app2, "203.0.113.8", typ="websocket"))
ok(seen == ["lifespan", "websocket"], "ASGI: http 以外の scope は枠を使い切っていても素通し")

print("\nall passed" if fails == 0 else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
