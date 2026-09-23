"""backstage.py — 舞台裏の幕。SDK が作る生の例外文を客に見せない。

発端: 実接続で確かめたところ、私たちが文言を作ったエラー（set が不正・入力が空・
該当なし・SQL エラー・no_match）はすべて isError=False で返り、isError=True が立つ唯一の経路が
SDK（pydantic）の引数検証だった。そしてその本文が

    Error executing tool draft_pack_stats: 1 validation error for draft_pack_statsArguments
    cards
      Field required [type=missing, input_value={}, input_type=dict]
        For further information visit https://errors.pydantic.dev/2.13/v/missing

で、内部のクラス名・型コード・pydantic のドキュメント URL が客に見える。

もう一つの穴: この経路は道具の本体に届かないので、道具ログに一行も残らない＝「引数の書き方で
躓いた客」が統計から丸ごと消える。

直し方の選び方: 道具の signature を緩める（必須を任意にする）方法もあるが、それは公開する
JSON Schema の required を削ることになり、「必ず要る物は必須と宣言する」という設計の芯を壊す。
なので契約には触れず、応答の側で幕を引く。

安全側の設計: 書き換えるのは印（marker）を含む本体だけ。解析に少しでも失敗したら元のまま通す。
"""
import json
import logging
import re

from sisho.toollog import _log_tool, _log_tool_end

_log = logging.getLogger("sisho.backstage")

_MARKER = "Error executing tool "
_MARKER_B = _MARKER.encode()
# 「Error executing tool <名前>: 1 validation error for ...」から道具名を取る
_TOOL_RE = re.compile(r"^Error executing tool ([A-Za-z0-9_]+): (.*)$", re.S)
# pydantic の本文は「項目名」の行と「  説明 [type=...]」の行が交互に来る
_FIELD_RE = re.compile(r"^([A-Za-z0-9_]+)$", re.M)


def _rewrite_text(text: str) -> tuple[str, str, str] | None:
    """生の例外文を客に見せてよい文へ。(新しい本文, 道具名, 記録用の要旨) か None。"""
    m = _TOOL_RE.match(text.strip())
    if not m:
        return None
    tool, body = m.group(1), m.group(2)
    fields = [f for f in _FIELD_RE.findall(body) if f not in ("type", "input_value", "input_type")]
    where = "・".join(dict.fromkeys(fields)) if fields else ""
    # 「足りない」と「型が違う」は混ざって来る（一方だけを言うと嘘になる）
    missing = "Field required" in body
    wrong_type = "Input should be" in body or "type=" in body and not missing
    if missing and wrong_type:
        tail, kind = "が足りないか、形が説明と違います。", "mixed"
    elif missing:
        tail, kind = "が足りません。", "missing"
    else:
        tail, kind = "の形が説明と違います。", "type"
    msg = (f"入力が不正: {tool} の引数" + (f"「{where}」" if where else "") + tail
           + "道具の説明にある引数の名前と型に合わせて呼び直してください。")
    return msg, tool, f"{kind}:{where}" if where else kind


def _rewrite_payload(obj) -> str | None:
    """JSON-RPC の応答を書き換える。書き換えたら記録用の要旨、しなければ None。"""
    result = obj.get("result")
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if not isinstance(content, list):
        return None
    note = None
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        text = part.get("text")
        if not isinstance(text, str) or _MARKER not in text:
            continue
        done = _rewrite_text(text)
        if not done:
            continue
        part["text"], tool, summary = done
        note = f"{tool}|{summary}"
    return note


def _rewrite_body(raw: bytes) -> tuple[bytes, str | None]:
    """SSE（data: 行）と素の JSON の両方を受ける。失敗したら元のまま返す。"""
    try:
        s = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw, None
    note = None
    if s.lstrip().startswith("{"):
        try:
            obj = json.loads(s)
        except ValueError:
            return raw, None
        note = _rewrite_payload(obj)
        return (json.dumps(obj, ensure_ascii=False).encode("utf-8"), note) if note else (raw, None)
    out = []
    for line in s.split("\n"):
        if line.startswith("data: ") and _MARKER in line:
            try:
                obj = json.loads(line[6:])
            except ValueError:
                out.append(line)
                continue
            n = _rewrite_payload(obj)
            if n:
                note = n
                out.append("data: " + json.dumps(obj, ensure_ascii=False))
                continue
        out.append(line)
    return ("\n".join(out).encode("utf-8"), note) if note else (raw, None)


class BackstageASGI:
    """SDK の生の例外文を客に見せず、その回を道具ログに残す外皮。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message.get("type") == "http.response.body":
                body = message.get("body") or b""
                if _MARKER_B in body:
                    try:
                        new, note = _rewrite_body(body)
                    except Exception:          # 幕が原因で応答を壊さない
                        new, note = body, None
                    if note:
                        tool, summary = note.split("|", 1)
                        # 道具の本体に届かない回なので、ここでしか記録できない。
                        # 入口と出口の 2 行を揃える＝道具ログの集計（end 行で失敗を数える）に出る。
                        _log_tool(tool, {"_rejected_by": "schema", "detail": summary})
                        _log_tool_end(tool, "schema_rejected", 0.0, 0, 0.0)
                        _log.info("[backstage] %s の引数検証で弾いた（%s）", tool, summary)
                        message = dict(message, body=new)
            await send(message)

        await self.app(scope, receive, _send)
