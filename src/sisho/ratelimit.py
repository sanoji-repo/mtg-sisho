"""ratelimit.py — HTTP の入口のレート制限（2026-09-05 Step 2 で mcp_server.py から切り出し）。

公開名は RateLimiter／RateLimitASGI（切り出し前の名前は _RateLimiter／_RateLimitASGI で、
mcp_server からは旧名でも届く＝tests 互換）。DB には触らない。
"""
import json
import os

# ─── レート制限（2026-09-02 本人 GO「レート制限から」＝配布（接続 URL を一般に開く）前の門）───
# 入口は Funnel → 127.0.0.1:8765 の uvicorn 直結で前段の代理は無い。client IP は uvicorn の proxy_headers
# （X-Forwarded-For・127.0.0.1 からだけ信用）が解決済み＝tailnet からは 100.x・claude.ai からは Anthropic の
# 出口 160.79.106.x（一人の会話でも 30 個ほどの IP を回る・7 日 3,525 POST の実測）。だから IP 別の枠は
# 「一人当たり」ではなく「直叩きの脚本 1 本を抑える」粗い門で、箱を守る本命は総量の枠。同時実行は
# _db_slot（席 5）が別に抑える。実測の最大は IP 別 8/分・総量 22/分（脳 1 会話）。既定は IP 別 60/分・
# 総量 300/分（環境変数 MCP_RATE_PER_IP_MIN／MCP_RATE_GLOBAL_MIN・0 で無効）。tailnet（100.64.0.0/10）と
# 127.0.0.0/8 は数えない（自分の healthwatch・pingwatch）。超過は HTTP 429＋Retry-After＋JSON-RPC の error
# （脳に日本語で理由が届く）。秘密のパス以外への探り（8/30 に GCP の IP から 404 を 12 連発）も同じ枠で数える。
# 窓は滑走（直近 60 秒の時刻を deque で持つ）・拒否した呼び出しは数えない（Retry-After を正直に保つ）。
import collections as _collections
import ipaddress as _ipaddress
import logging as _logging
import time as _time

_rate_log = _logging.getLogger("uvicorn.error")


class RateLimiter:
    """IP 別＋総量の滑走窓。uvicorn は単一イベントループなのでロック無し。clock は試験で差し替える。"""

    def __init__(self, per_ip: int, global_: int, window: float = 60.0,
                 exempt: str = "127.0.0.0/8,100.64.0.0/10", clock=_time.monotonic, max_keys: int = 5000):
        self.per_ip, self.global_, self.window, self.clock, self.max_keys = per_ip, global_, window, clock, max_keys
        self.exempt = [_ipaddress.ip_network(c.strip()) for c in exempt.split(",") if c.strip()]
        self.buckets: "_collections.OrderedDict[str, _collections.deque]" = _collections.OrderedDict()
        self.total: _collections.deque = _collections.deque()
        self.denied = 0
        self._last_warn: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> "RateLimiter":
        return cls(per_ip=int(os.environ.get("MCP_RATE_PER_IP_MIN", "60")),
                   global_=int(os.environ.get("MCP_RATE_GLOBAL_MIN", "300")),
                   exempt=os.environ.get("MCP_RATE_EXEMPT", "127.0.0.0/8,100.64.0.0/10"))

    def _is_exempt(self, ip: str) -> bool:
        try:
            a = _ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a in n for n in self.exempt)

    def _prune(self, dq: _collections.deque, now: float) -> None:
        cut = now - self.window
        while dq and dq[0] <= cut:
            dq.popleft()

    def check(self, ip: str) -> tuple[bool, int]:
        """(通すか, 待つ秒数)。通すときはその時刻を記録する。"""
        if self._is_exempt(ip):
            return True, 0
        now = self.clock()
        self._prune(self.total, now)
        dq = self.buckets.get(ip)
        if dq is None:
            if len(self.buckets) >= self.max_keys:  # 鍵が溢れたら空の鍵を捨て、足りなければ古い順に捨てる
                for k in list(self.buckets):
                    self._prune(self.buckets[k], now)
                    if not self.buckets[k]:
                        del self.buckets[k]
                while len(self.buckets) >= self.max_keys:
                    self.buckets.popitem(last=False)
            dq = self.buckets[ip] = _collections.deque()
        else:
            self.buckets.move_to_end(ip)
            self._prune(dq, now)
        if self.per_ip and len(dq) >= self.per_ip:
            retry = max(1, int(dq[0] + self.window - now) + 1)
            self._deny(ip, f"IP 別 {self.per_ip}/分", now)
            return False, retry
        if self.global_ and len(self.total) >= self.global_:
            retry = max(1, int(self.total[0] + self.window - now) + 1)
            self._deny(ip, f"総量 {self.global_}/分", now)
            return False, retry
        dq.append(now)
        self.total.append(now)
        return True, 0

    def _deny(self, ip: str, why: str, now: float) -> None:
        self.denied += 1
        if now - self._last_warn.get(ip, -1e9) >= self.window:  # IP ごと 1 分に 1 行（洪水でログを埋めない）
            self._last_warn[ip] = now
            _rate_log.warning("[rate] 429 ip=%s reason=%s denied_total=%d", ip, why, self.denied)


class RateLimitASGI:
    """ASGI の外皮。HTTP だけ数え、超過は 429（JSON-RPC の error 本文つき）で返し、本体には渡さない。"""

    def __init__(self, app, limiter: RateLimiter):
        self.app, self.limiter = app, limiter

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        client = scope.get("client")
        ip = client[0] if client else "?"
        ok, retry = self.limiter.check(ip)
        if ok:
            return await self.app(scope, receive, send)
        body = json.dumps({"jsonrpc": "2.0", "id": None, "error": {
            "code": -32000,
            "message": (f"混雑: 呼び出しが多すぎます（この接続元からの上限に達しました）。{retry} 秒待ってから"
                        "もう一度呼んでください。まとめて引ける問いは 1 回の SQL に寄せると回数が減ります。")}},
            ensure_ascii=False).encode("utf-8")
        headers = [(b"content-type", b"application/json; charset=utf-8"),
                   (b"retry-after", str(retry).encode()),
                   (b"content-length", str(len(body)).encode())]
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": body})
