"""gate.py — 接続 URL の門と札の表。

接続 URL を一人ひとりに「札」（token_urlsafe(24)）として配り、
札ごとにレート制限を課す。身元は聞かない・名簿を持たない。
"""
import asyncio
import contextlib
import datetime
import fcntl
import html
import ipaddress
import logging
import os
import re
import secrets
import tempfile
import time

from sisho.context import CURRENT_CLIENT_IP, CURRENT_FUDA
from sisho.paths import repo_path
from sisho.ratelimit import RateLimiter, send_429

_gate_log = logging.getLogger("uvicorn.error")


_PER_RE = re.compile(r"[0-9]{1,6}")


def _parse_fuda_line(line: str):
    """fuda.tsv の 1 行を読む。札の事象として受け付けられる行なら (dt, 事象, 札, IP, 枠, メモ)、壊れた行なら None。

    受け付けるのは**ちょうど 6 列**・時刻が読める・事象が issue/stop・発行の行は枠の欄が数字、の行だけ
    （改行の無い最終行に次の行が連結すると 9 列の 1 行になり、以前は
    先頭 6 列を切り出して「枠が数字でない→既定 60」で受け付けていた＝壊れた札が生き、新しい札が消えた）。
    枠の欄は ASCII の数字 1〜6 桁だけ（str.isdigit は「²」も数字と言うのに int() は通らない＝台帳の 1 行で
    門の起動と scrub が止まった）。読めない物はすべて None＝壊れた行。
    """
    cols = line.rstrip("\r\n").split("\t")
    if len(cols) != 6:
        return None
    ts_s, ev_type, fuda, ip, per_s, memo = cols
    if ev_type not in ("issue", "stop") or not fuda:
        return None
    per_ok = _PER_RE.fullmatch(per_s) is not None
    if ev_type == "issue" and not per_ok:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts_s)
        if dt.tzinfo is None:
            dt = dt.astimezone()
    except Exception:
        return None
    return dt, ev_type, fuda, ip, (int(per_s) if per_ok else 60), memo


def _looks_like_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def short(fuda: str) -> str:
    """ログおよび contextvar 用の先頭 8 字。"""
    return fuda[:8] if fuda else ""


class FudaStore:
    """追記だけの TSV（fuda.tsv）による札の台帳。

    1 行 1 事象（issue / stop）。最新の行が真。
    行形式: ISO時刻 \\t 事象 \\t 札 \\t IP \\t 分あたりの枠 \\t メモ
    IP は発行の行だけに書く（直近 24 時間の発行数を数えるため）。古い行の IP は scrub_ips で空にする。
    追記と書き換えは隣の .lock への flock で順番に通す（書き換えの最中の追記を失わない）。
    """

    def __init__(self, path: str, clock=time.monotonic, dt_now=None):
        self.path = path
        self._clock = clock
        self._dt_now = dt_now if dt_now is not None else (lambda: datetime.datetime.now().astimezone())
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
                    if not line.rstrip("\r\n"):
                        continue
                    parsed = _parse_fuda_line(line)
                    if parsed is None:
                        self.broken_lines += 1
                        continue
                    dt, ev_type, fuda, ip, per_min, memo = parsed
                    if ev_type == "issue":
                        self.records[fuda] = {
                            "alive": True,
                            "per_min": per_min,
                            "issued_at": dt,
                            "ip": ip,
                            "memo": memo,
                        }
                        self.events.append((dt, "issue", fuda, ip))
                    else:
                        if fuda in self.records:
                            self.records[fuda]["alive"] = False
                        self.events.append((dt, "stop", fuda, ip))
            if self.broken_lines > 0:
                _gate_log.warning("[gate] fuda.tsv に壊れた行 %d", self.broken_lines)
        except OSError as e:
            _gate_log.error("[gate] fuda.tsv を読めない path=%s err=%s", self.path, e)

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
        while fuda.startswith("-"):     # 先頭の - は argparse がオプションと読む＝bin/fuda stop に渡せない
            fuda = secrets.token_urlsafe(24)
        if per_min is None:
            per_min = int(os.environ.get("MCP_FUDA_PER_MIN", "60"))
        if not (isinstance(per_min, int) and 0 <= per_min <= 999999):
            # 台帳が読める範囲（ASCII 数字 1〜6 桁）でだけ発行する＝発行できたのに再起動で消える札を作らない
            raise ValueError(f"per_min は 0〜999999 の整数: {per_min!r}")
        memo_clean = " ".join((memo or "").split())
        now_dt = self._dt_now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.astimezone()
        ts_s = now_dt.isoformat()
        try:
            self._append(f"{ts_s}\tissue\t{fuda}\t{ip}\t{per_min}\t{memo_clean}\n")
        except OSError as e:
            _gate_log.error("[gate] fuda.tsv への書き込み失敗 path=%s err=%s", self.path, e)
            raise

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
        now_dt = self._dt_now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.astimezone()
        ts_s = now_dt.isoformat()
        per_min = str(rec.get("per_min", ""))
        try:
            self._append(f"{ts_s}\tstop\t{fuda}\t\t{per_min}\t\n")
        except OSError as e:
            _gate_log.error("[gate] fuda.tsv への書き込み失敗 path=%s err=%s", self.path, e)
            raise

        rec["alive"] = False
        try:
            st = os.stat(self.path)
            self._last_mtime = st.st_mtime
            self._last_size = st.st_size
        except OSError:
            pass
        self.events.append((now_dt, "stop", fuda, ""))
        return True

    @contextlib.contextmanager
    def _locked(self):
        """隣の .lock に排他の flock をかける（追記と scrub_ips の書き換えを順番に通す）。"""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fd = os.open(self.path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _append(self, line: str) -> None:
        with self._locked():
            fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "r+b") as f:
                # 最終行に改行が無ければ先に足す（途中で切れた行に新しい行を連結させない）
                size = f.seek(0, os.SEEK_END)
                if size > 0:
                    f.seek(size - 1)
                    if f.read(1) != b"\n":
                        line = "\n" + line
                f.write(line.encode("utf-8"))

    def scrub_ips(self, days: float = 35) -> int:
        """days 日より古い行の IP 列を空にする。空にした行の数を返す（0 ならファイルに触らない）。

        濫用への対処に要るのは直近 24 時間の発行数だけ＝古い IP は持たない（約 5 週間で消去）。
        壊れた行（_parse_fuda_line が受け付けない行＝札として数えない行）は、時刻に関係なく IP に見える列を
        すべて空にする（以前は壊れた行を触らず、IP が無期限に残った）。
        """
        now = self._dt_now()
        if now.tzinfo is None:
            now = now.astimezone()
        cutoff = now - datetime.timedelta(days=days)
        with self._locked():
            if not os.path.exists(self.path):
                return 0
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            changed = 0
            out = []
            for line in lines:
                body = line.rstrip("\r\n")
                if body:
                    cols = body.split("\t")
                    parsed = _parse_fuda_line(body)
                    if parsed is not None:
                        hit = [3] if cols[3] and parsed[0] < cutoff else []
                    else:
                        # 4 列目（IP の欄）は中身に関係なく空に＝連結した行では「IP＋次の行の時刻」の一続きになり IP に見えない
                        hit = [i for i, c in enumerate(cols) if c and (i == 3 or _looks_like_ip(c))]
                    if hit:
                        for i in hit:
                            cols[i] = ""
                        line = "\t".join(cols) + "\n"
                        changed += 1
                out.append(line)
            if changed == 0:
                return 0
            d = os.path.dirname(os.path.abspath(self.path))
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".fuda.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.writelines(out)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        self._load()
        return changed

    def lookup(self, fuda: str) -> dict | None:
        """生きている札の情報を返す（死んでいる・未登録なら None）。"""
        self.reload_if_changed()
        rec = self.records.get(fuda)
        if rec and rec.get("alive"):
            return dict(rec)
        return None

    def issued_in(self, ip: str | None, seconds: float = 86400) -> int:
        """直近 seconds 秒間の発行回数。ip を指定すればその IP・None なら全体。"""
        self.reload_if_changed()
        now = self._dt_now()
        if now.tzinfo is None:
            now = now.astimezone()
        count = 0
        for dt, ev_type, fuda, ev_ip in self.events:
            if ev_type == "issue" and (ip is None or ev_ip == ip):
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


_ISSUE_BODY_MAX = 8192      # 発行ページへの POST の本文の上限（バイト）
_ISSUE_BODY_SEC = 5.0       # 本文を読み切るまでの上限（秒）


async def _respond_plain(send, status: int, body: bytes) -> None:
    """短い平文の応答。本文を読み切らずに返すときに使う＝接続は閉じる。"""
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()),
                            (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": body})


_DEFAULT_PORT = {"http": "80", "https": "443"}


def _origin_of(url: str, *, strict: bool = False) -> str | None:
    """URL の origin（scheme://host[:port]・小文字・既定のポートは落とす）。形が origin でなければ None。
    strict=True は要求の Origin ヘッダ用＝パスも末尾の / も付かない完全な形だけ受ける（ブラウザはそう送る）。
    設定の値（公開 URL・MCP_ALLOWED_ORIGINS）は strict=False＝パスと末尾の / を落とす。
    設定と要求を同じ関数で揃える（設定の大文字が許可されず、
    https://allowed.example/anything のような不正な値が丸められて通っていた）。"""
    m = re.fullmatch(r"([A-Za-z][A-Za-z0-9+.-]*)://([^/?#@\s]+)(.*)", url.strip(), re.S)
    if not m or (strict and m.group(3)):
        return None
    scheme, hostport = m.group(1).lower(), m.group(2).lower()
    host, sep, port = hostport.rpartition(":")
    if not sep or not port.isdigit():
        host, port = hostport, ""
    if port == _DEFAULT_PORT.get(scheme):
        port = ""
    return f"{scheme}://{host}" + (f":{port}" if port else "")


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
                 legacy_on: bool = True, public_base: str = "",
                 allowed_origins: frozenset[str] = frozenset(),
                 probe_limiter: RateLimiter | None = None,
                 contact_url: str = ""):
        self.app = app
        self.store = store
        # limiter＝MCP 本体まで届く呼び出し（札・旧パス）の枠。probe_limiter＝DB に触らない口（発行ページ・
        # 無い札や知らないパスへの探り・Origin で断った接続）の枠。総量を分けて、探りが利用者の総量を食えないようにする。
        # 省略時は limiter と同じ数字で別の窓を作る。
        self.limiter = limiter
        if probe_limiter is None:
            probe_limiter = RateLimiter(per_ip=limiter.per_ip, global_=limiter.global_,
                                        window=limiter.window, exempt="", clock=limiter.clock)
            probe_limiter.exempt = list(limiter.exempt)
        self.probe_limiter = probe_limiter
        self.issue_limiter = issue_limiter
        # 問い合わせの窓口（Google フォーム）。https のときだけ発行ページに出す。
        # URL はコードに書かず環境変数で渡す＝自分で立てたサーバーに他人の窓口が出ない
        self.contact_url = contact_url if contact_url.startswith("https://") else ""
        self.inner_path = inner_path
        self.prefix = prefix if prefix.endswith("/") else prefix + "/"
        self.issue_path = issue_path
        self.legacy_on = legacy_on
        self.public_base = public_base
        # Origin の許可リスト: 公開 URL の origin と、明示して足した物だけ。
        self.allowed_origins = frozenset(o for o in (_origin_of(x) for x in (*allowed_origins, public_base) if x) if o)
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
        allowed_origins = frozenset(o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip())
        contact_url = os.environ.get("MCP_CONTACT_URL", "").strip()

        if not public_base:
            _gate_log.warning("[gate] MCP_PUBLIC_BASE が空: 発行ページの URL は Host ヘッダから組む（公開サーバーでは必ず設定）")

        store = FudaStore(fuda_file)
        limiter = RateLimiter.from_env()
        probe_limiter = RateLimiter(per_ip=limiter.per_ip,
                                    global_=int(os.environ.get("MCP_PROBE_GLOBAL_MIN", str(limiter.global_))),
                                    exempt=os.environ.get("MCP_RATE_EXEMPT", "127.0.0.0/8,100.64.0.0/10"))
        issue_limiter = RateLimiter(per_ip=issue_per_day, global_=issue_global_day,
                                    window=86400.0, exempt="")
        return cls(app, store, limiter, issue_limiter,
                   inner_path=inner_path, prefix=prefix,
                   issue_path=issue_path, legacy_on=legacy_on,
                   public_base=public_base, allowed_origins=allowed_origins,
                   probe_limiter=probe_limiter, contact_url=contact_url)

    async def _respond_404(self, send, ip: str | None = None) -> None:
        """404。ip を渡したときは**探りとして IP の枠で数える**（指摘）。枠は probe_limiter（利用者の総量とは別）。

        無い札・知らないパスへの探りは DB に触らず軽いので、枠の外に置くと「無料で叩き放題の口」
        になる（旧 RateLimitASGI は全要求を IP で数えていた＝GCP の IP からの 404 連発が実例）。
        札の総当たりは 192 ビットで現実に当たらないが、CPU とログの洪水は枠で止める。
        枠を超えた探りには 404 でなく 429 を返す（存在の情報は与えない・待ち秒だけ）。
        """
        if ip is not None:
            ok, retry = self.probe_limiter.check(ip)
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

    async def _respond_403_origin(self, send, ip: str, origin: str) -> None:
        """Origin が許可リストに無い（MCP の Streamable HTTP の要件「不正な Origin は 403」）。
        DNS rebinding（ブラウザに別サイトから公開サーバーの口を叩かせる）への備え。Origin を送らない客（claude.ai・CLI）は対象外。
        探りと同じく probe_limiter の IP の枠で数える。Origin は秘密ではないので記録に残す（先頭 120 字）。"""
        ok, retry = self.probe_limiter.check(ip)
        if not ok:
            return await send_429(send, retry, f"混雑: 呼び出しが多すぎます。{retry} 秒待ってからもう一度呼んでください。")
        _gate_log.warning("[gate] origin 拒否 ip=%s origin=%s", ip, origin[:120])
        body = b"forbidden origin"
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    def _contact_html(self) -> str:
        """発行ページの問い合わせの一行（窓口が無ければ空）。"""
        if not self.contact_url:
            return ""
        return (f'<p>困ったとき・不具合の報告: <a href="{html.escape(self.contact_url)}" '
                'rel="noopener noreferrer">問い合わせフォーム</a></p>\n')

    def _issue_retry_after(self, ip: str | None) -> int:
        """発行の枠を超えたときの待ち秒＝直近 24 時間で最も古い issue 行が窓を出るまで。ip None は全体。"""
        now = self.store._dt_now()
        if now.tzinfo is None:
            now = now.astimezone()
        dts = [dt for dt, ev, _, ev_ip in self.store.events
               if ev == "issue" and (ip is None or ev_ip == ip) and 0 <= (now - dt).total_seconds() <= 86400]
        if not dts:
            return 86400
        return max(1, int(86400 - (now - min(dts)).total_seconds()) + 1)

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
                '<button type="submit">発行する / Issue</button>\n'   # 英語の読み手向けにボタンだけ併記
                '</form>\n' + self._contact_html() + '</body>\n</html>'
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
            # フォームの本文が残ったまま応答すると h11 が接続を切るため読み捨てる。
            # 本文は使わないので上限と時間を切る＝巨大な本文・極端に遅い本文で
            # 接続を握り続けられないように。発行ページのフォームは空（数十バイト）。
            clen = _get_header(scope, "content-length")
            if clen.isdigit() and int(clen) > _ISSUE_BODY_MAX:
                return await _respond_plain(send, 413, b"payload too large")
            total, deadline, more_body = 0, time.monotonic() + _ISSUE_BODY_SEC, True
            while more_body:
                try:
                    msg = await asyncio.wait_for(receive(), max(0.0, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    return await _respond_plain(send, 408, b"request timeout")
                if msg.get("type") == "http.disconnect":
                    return
                total += len(msg.get("body") or b"")
                if total > _ISSUE_BODY_MAX:
                    return await _respond_plain(send, 413, b"payload too large")
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

            # TSV の永続記録から直近 24 時間の発行枠を検査（IP ごと・全体とも＝再起動で消えない）。
            # issue_limiter は数値（per_ip・global_）の置き場で、メモリの deque は使わない（失敗した発行で枠が痩せない）
            per_ip, global_ = self.issue_limiter.per_ip, self.issue_limiter.global_
            over_ip = bool(per_ip) and self.store.issued_in(ip, 86400) >= per_ip
            over_all = bool(global_) and self.store.issued_in(None, 86400) >= global_
            if over_ip or over_all:
                retry = self._issue_retry_after(ip if over_ip else None)
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


            try:
                fuda = self.store.issue(ip, memo="web")
            except OSError:
                body = (
                    '<!DOCTYPE html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
                    '<title>発行エラー</title>\n</head>\n<body>\n'
                    '<h1>今は発行できません。しばらくしてからもう一度</h1>\n'
                    '</body>\n</html>'
                ).encode("utf-8")
                headers = [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"retry-after", b"60"),
                    (b"content-length", str(len(body)).encode()),
                ]
                await send({"type": "http.response.start", "status": 503, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return

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
                + self._contact_html() +
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
            # 同名のヘッダが複数なら先頭だけ見ずに断る
            origins = [v.decode("latin1") for k, v in scope.get("headers", []) if k.lower() == b"origin" and v.strip()]
            if len(origins) > 1 or (origins and _origin_of(origins[0], strict=True) not in self.allowed_origins):
                return await self._respond_403_origin(send, ip, " , ".join(origins))

            if path.rstrip("/") == self.issue_path.rstrip("/"):
                ok, retry = self.probe_limiter.check(ip)     # 発行ページも探りと同じ IP の枠（60/分）で数える
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
