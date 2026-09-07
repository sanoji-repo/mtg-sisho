"""gate.py — 接続 URL の門と札の表（2026-09-07 小片 8）。

接続 URL を一人ひとりに「札」（token_urlsafe(24)）として配り、
札ごとにレート制限を課す。身元は聞かない・名簿を持たない。
"""
import datetime
import html
import logging
import os
import re
import secrets
import time

from sisho.context import CURRENT_CLIENT_IP, CURRENT_FUDA
from sisho.paths import repo_path
from sisho.ratelimit import RateLimiter, send_429

_gate_log = logging.getLogger("uvicorn.error")


def short(fuda: str) -> str:
    """ログおよび contextvar 用の先頭 8 字。"""
    return fuda[:8] if fuda else ""


class FudaStore:
    """追記だけの TSV（fuda.tsv）による札の台帳。

    1 行 1 事象（issue / stop）。最新の行が真。
    行形式: ISO時刻 \\t 事象 \\t 札 \\t IP \\t 分あたりの枠 \\t メモ
    """

    def __init__(self, path: str, clock=time.monotonic, dt_now=None):
        self.path = path
        self._clock = clock
        self._dt_now = dt_now if dt_now is not None else datetime.datetime.now
        self.records: dict[str, dict] = {}
        self.events: list[tuple[datetime.datetime, str, str, str]] = []
        self.broken_lines: int = 0
        self._last_mtime: float = 0.0
        self._last_size: int = 0
        self._last_check: float = 0.0
        self._load()

    def _load(self) -> None:
        self.records = {}
        self.events = []
        self.broken_lines = 0
        if not os.path.exists(self.path):
            self._last_mtime = 0.0
            self._last_size = 0
            return
        try:
            st = os.stat(self.path)
            self._last_mtime = st.st_mtime
            self._last_size = st.st_size
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\r\n")
                    if not line:
                        continue
                    cols = line.split("\t")
                    if len(cols) < 6:
                        self.broken_lines += 1
                        continue
                    ts_s, ev_type, fuda, ip, per_s, memo = cols[:6]
                    try:
                        dt = datetime.datetime.fromisoformat(ts_s)
                        per_min = int(per_s) if per_s.isdigit() else 60
                    except Exception:
                        self.broken_lines += 1
                        continue
                    if ev_type == "issue":
                        self.records[fuda] = {
                            "alive": True,
                            "per_min": per_min,
                            "issued_at": dt,
                            "ip": ip,
                            "memo": memo,
                        }
                        self.events.append((dt, "issue", fuda, ip))
                    elif ev_type == "stop":
                        if fuda in self.records:
                            self.records[fuda]["alive"] = False
                        self.events.append((dt, "stop", fuda, ip))
                    else:
                        self.broken_lines += 1
        except OSError:
            pass

    def reload_if_changed(self) -> None:
        """mtime または size が変わっていたら再読み込み（確認は 1 秒に 1 回まで）。"""
        now = self._clock()
        if now - self._last_check < 1.0:
            return
        self._last_check = now
        try:
            st = os.stat(self.path)
            mtime = st.st_mtime
            size = st.st_size
        except OSError:
            return
        if mtime != self._last_mtime or size != self._last_size:
            self._load()

    def issue(self, ip: str, memo: str = "", per_min: int | None = None) -> str:
        """新しい札を発行して TSV に追記する。"""
        fuda = secrets.token_urlsafe(24)
        if per_min is None:
            per_min = int(os.environ.get("MCP_FUDA_PER_MIN", "60"))
        memo_clean = " ".join((memo or "").split())
        now_dt = self._dt_now()
        ts_s = now_dt.isoformat()
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"{ts_s}\tissue\t{fuda}\t{ip}\t{per_min}\t{memo_clean}\n")
        try:
            st = os.stat(self.path)
            self._last_mtime = st.st_mtime
            self._last_size = st.st_size
        except OSError:
            pass
        self.records[fuda] = {
            "alive": True,
            "per_min": per_min,
            "issued_at": now_dt,
            "ip": ip,
            "memo": memo_clean,
        }
        self.events.append((now_dt, "issue", fuda, ip))
        return fuda

    def stop(self, fuda: str) -> bool:
        """生きている札を停止する（stop 行を追記）。"""
        self.reload_if_changed()
        if fuda not in self.records or not self.records[fuda].get("alive", False):
            return False
        rec = self.records[fuda]
        rec["alive"] = False
        now_dt = self._dt_now()
        ts_s = now_dt.isoformat()
        ip = rec.get("ip", "")
        per_min = str(rec.get("per_min", ""))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"{ts_s}\tstop\t{fuda}\t{ip}\t{per_min}\t\n")
        try:
            st = os.stat(self.path)
            self._last_mtime = st.st_mtime
            self._last_size = st.st_size
        except OSError:
            pass
        self.events.append((now_dt, "stop", fuda, ip))
        return True

    def lookup(self, fuda: str) -> dict | None:
        """生きている札の情報を返す（死んでいる・未登録なら None）。"""
        self.reload_if_changed()
        rec = self.records.get(fuda)
        if rec and rec.get("alive"):
            return dict(rec)
        return None

    def issued_in(self, ip: str, seconds: float = 86400) -> int:
        """直近 seconds 秒間の指定 IP からの発行回数を数える。"""
        self.reload_if_changed()
        now = self._dt_now()
        count = 0
        for dt, ev_type, fuda, ev_ip in self.events:
            if ev_type == "issue" and ev_ip == ip:
                diff = (now - dt).total_seconds()
                if 0 <= diff <= seconds:
                    count += 1
        return count


def _get_header(scope: dict, name: str) -> str:
    name_b = name.lower().encode("ascii")
    for k, v in scope.get("headers", []):
        if k.lower() == name_b:
            return v.decode("latin1")
    return ""


def _build_base_url(scope: dict, public_base: str) -> str:
    if public_base:
        return public_base.rstrip("/")
    host = _get_header(scope, "host") or "localhost"
    return f"https://{host}".rstrip("/")


class GateASGI:
    """門の外皮。札の照合・レート制限・発行ページを束ねる ASGI アプリケーション。"""

    def __init__(self, app, store: FudaStore, limiter: RateLimiter,
                 issue_limiter: RateLimiter, *, inner_path: str = "/mcp",
                 prefix: str = "/mcp/", issue_path: str = "/issue",
                 legacy_on: bool = True, public_base: str = ""):
        self.app = app
        self.store = store
        self.limiter = limiter
        self.issue_limiter = issue_limiter
        self.inner_path = inner_path
        self.prefix = prefix if prefix.endswith("/") else prefix + "/"
        self.issue_path = issue_path
        self.legacy_on = legacy_on
        self.public_base = public_base
        self._fuda_re = re.compile(re.escape(self.prefix) + r"([A-Za-z0-9_-]{16,64})")

    @classmethod
    def from_env(cls, app, inner_path: str = "/mcp") -> "GateASGI":
        fuda_file = os.environ.get("MCP_FUDA_FILE", repo_path("state", "fuda.tsv"))
        prefix = os.environ.get("MCP_FUDA_PREFIX", "/mcp/")
        issue_per_day = int(os.environ.get("MCP_ISSUE_PER_DAY", "3"))
        issue_global_day = int(os.environ.get("MCP_ISSUE_GLOBAL_DAY", "100"))
        legacy_on = os.environ.get("MCP_LEGACY_PATH", "1") in ("1", "true", "yes")
        public_base = os.environ.get("MCP_PUBLIC_BASE", "")
        issue_path = os.environ.get("MCP_ISSUE_PATH", "/issue")

        if not public_base:
            _gate_log.warning("[gate] MCP_PUBLIC_BASE が空: 発行ページの URL は Host ヘッダから組む（公開サーバーでは必ず設定）")

        store = FudaStore(fuda_file)
        limiter = RateLimiter.from_env()
        issue_limiter = RateLimiter(per_ip=issue_per_day, global_=issue_global_day,
                                    window=86400.0, exempt="")
        return cls(app, store, limiter, issue_limiter,
                   inner_path=inner_path, prefix=prefix,
                   issue_path=issue_path, legacy_on=legacy_on,
                   public_base=public_base)

    async def _respond_404(self, send, ip: str | None = None) -> None:
        """404。ip を渡したときは**探りとして IP の枠で数える**（2026-09-07 本人指摘）。

        無い札・知らないパスへの探りは DB に触らず軽いので、枠の外に置くと「無料で叩き放題の口」
        になる（旧 RateLimitASGI は全要求を IP で数えていた＝8/30 の GCP からの 404 連発の実例）。
        札の総当たりは 192 ビットで現実に当たらないが、CPU とログの洪水は枠で止める。
        枠を超えた探りには 404 でなく 429 を返す（存在の情報は与えない・待ち秒だけ）。
        """
        if ip is not None:
            ok, retry = self.limiter.check(ip)
            if not ok:
                msg = (f"混雑: 呼び出しが多すぎます（この接続元からの上限に達しました）。{retry} 秒待ってから"
                       "もう一度呼んでください。")
                return await send_429(send, retry, msg)
        headers = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", b"9"),
        ]
        await send({"type": "http.response.start", "status": 404, "headers": headers})
        await send({"type": "http.response.body", "body": b"not found"})

    async def _handle_issue(self, scope, receive, send, ip: str) -> None:
        method = scope.get("method", "GET").upper()
        if method == "GET":
            content = (
                '<!DOCTYPE html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
                '<title>Sisho の接続 URL を発行する</title>\n</head>\n<body>\n'
                '<h1>Sisho の接続 URL を発行する</h1>\n'
                '<p>ボタンを押すと、あなた専用の接続 URL が出ます</p>\n'
                '<p>URL は合言葉と同じです。人に見せないでください</p>\n'
                '<p>無くしたら、もう一度ここで発行できます</p>\n'
                '<form method="post">\n'
                '<button type="submit">発行する</button>\n'
                '</form>\n</body>\n</html>'
            ).encode("utf-8")
            headers = [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(content)).encode()),
            ]
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await send({"type": "http.response.body", "body": content})
            return

        if method == "POST":
            # フォームの本文が残ったまま応答すると h11 が接続を切るため読み捨てる
            more_body = True
            while more_body:
                msg = await receive()
                more_body = msg.get("more_body", False)

            # 第三者サイトからの自動投稿（CSRF）を防ぐ: Sec-Fetch-Site が cross-site なら 403
            sec_site = _get_header(scope, "sec-fetch-site").lower()
            if sec_site == "cross-site":
                body = (
                    '<!DOCTYPE html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
                    '<title>発行エラー</title>\n</head>\n<body>\n'
                    '<h1>このページの「発行する」ボタンから発行してください</h1>\n'
                    '</body>\n</html>'
                ).encode("utf-8")
                headers = [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"content-length", str(len(body)).encode()),
                ]
                await send({"type": "http.response.start", "status": 403, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return

            # TSV の永続記録から直近 24 時間の発行枠を検査（サーバー再起動対策）
            if self.issue_limiter.per_ip and self.store.issued_in(ip, 86400) >= self.issue_limiter.per_ip:
                now = self.store._dt_now()
                matching_dts = [dt for dt, ev_type, _, ev_ip in self.store.events
                                if ev_type == "issue" and ev_ip == ip and 0 <= (now - dt).total_seconds() <= 86400]
                if matching_dts:
                    oldest = min(matching_dts)
                    retry = max(1, int(86400 - (now - oldest).total_seconds()) + 1)
                else:
                    retry = 86400
                body = (
                    '<!DOCTYPE html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
                    '<title>発行上限</title>\n</head>\n<body>\n'
                    '<h1>今日はもう発行できません。明日また来てください</h1>\n'
                    '</body>\n</html>'
                ).encode("utf-8")
                headers = [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"retry-after", str(retry).encode()),
                    (b"content-length", str(len(body)).encode()),
                ]
                await send({"type": "http.response.start", "status": 429, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return

            ok, retry = self.issue_limiter.check(ip)
            if not ok:
                body = (
                    '<!DOCTYPE html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
                    '<title>発行上限</title>\n</head>\n<body>\n'
                    '<h1>今日はもう発行できません。明日また来てください</h1>\n'
                    '</body>\n</html>'
                ).encode("utf-8")
                headers = [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"retry-after", str(retry).encode()),
                    (b"content-length", str(len(body)).encode()),
                ]
                await send({"type": "http.response.start", "status": 429, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return

            fuda = self.store.issue(ip, memo="web")
            _gate_log.info("[gate] issue ip=%s fuda=%s", ip, short(fuda))
            base = _build_base_url(scope, self.public_base)
            full_url = f"{base}{self.prefix}{fuda}"
            url_esc = html.escape(full_url)
            body = (
                f'<!DOCTYPE html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
                f'<title>あなたの接続 URL</title>\n</head>\n<body>\n'
                f'<h1>あなたの接続 URL</h1>\n'
                f'<p><code>{url_esc}</code></p>\n'
                f'<p><input readonly value="{url_esc}" style="width: 100%; max-width: 600px;"></p>\n'
                f'<p>URL は合言葉と同じです。人に見せないでください</p>\n'
                f'<p>無くしたら、もう一度ここで発行できます</p>\n'
                f'<p>claude.ai の設定 → コネクタ → カスタムコネクタを追加、に貼る</p>\n'
                f'</body>\n</html>'
            ).encode("utf-8")
            headers = [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(body)).encode()),
            ]
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        body_405 = b"Method Not Allowed"
        headers = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"allow", b"GET, POST"),
            (b"content-length", str(len(body_405)).encode()),
        ]
        await send({"type": "http.response.start", "status": 405, "headers": headers})
        await send({"type": "http.response.body", "body": body_405})

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        client = scope.get("client")
        ip = client[0] if client else "?"

        ip_token = CURRENT_CLIENT_IP.set(ip)
        try:
            if path.rstrip("/") == self.issue_path.rstrip("/"):
                ok, retry = self.limiter.check(ip)     # 発行ページも IP の枠（60/分）で数える
                if not ok:
                    return await send_429(send, retry, f"混雑: 呼び出しが多すぎます。{retry} 秒待ってからもう一度開いてください。")
                return await self._handle_issue(scope, receive, send, ip)

            m = self._fuda_re.fullmatch(path)
            if m:
                fuda = m.group(1)
                rec = self.store.lookup(fuda)
                if rec is None:
                    return await self._respond_404(send, ip)
                ok, retry = self.limiter.check("fuda:" + fuda, per=rec["per_min"])
                if not ok:
                    msg = (f"混雑: 呼び出しが多すぎます（この接続 URL からの上限に達しました）。{retry} 秒待ってから"
                           "もう一度呼んでください。まとめて引ける問いは 1 回の SQL に寄せると回数が減ります。")
                    return await send_429(send, retry, msg)
                token = CURRENT_FUDA.set(short(fuda))
                new_scope = dict(scope)
                new_scope["path"] = self.inner_path
                new_scope["raw_path"] = self.inner_path.encode("latin1")
                try:
                    return await self.app(new_scope, receive, send)
                finally:
                    CURRENT_FUDA.reset(token)

            if path == self.inner_path:
                if not self.legacy_on:
                    return await self._respond_404(send, ip)
                ok, retry = self.limiter.check(ip)
                if not ok:
                    msg = (f"混雑: 呼び出しが多すぎます（この接続元からの上限に達しました）。{retry} 秒待ってから"
                           "もう一度呼んでください。まとめて引ける問いは 1 回の SQL に寄せると回数が減ります。")
                    return await send_429(send, retry, msg)
                token = CURRENT_FUDA.set("legacy")
                try:
                    return await self.app(scope, receive, send)
                finally:
                    CURRENT_FUDA.reset(token)

            return await self._respond_404(send, ip)
        finally:
            CURRENT_CLIENT_IP.reset(ip_token)
