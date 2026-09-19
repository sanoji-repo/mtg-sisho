#!/usr/bin/env python
"""sisho_stop_verify.py — Claude Code の Stop フック: 答案のカード名を DB と突き合わせ、駄目なら書き直させる。

何をするか:
  Claude が応答を終えようとした瞬間に呼ばれ、最後の assistant 本文を transcript から取り出し、
  Sisho の verify_answer（mcp_server.py の同じ関数）に通す。
    - 未確認の名前（DB に無い《》・日本語半分が記憶の訳）があれば decision=block → 理由に候補を添えて返す
      → Claude は続行し、search_mtg_cards で引き直して答えを直してから再び終わろうとする（そこでまた検査）。
    - 機械修正だけ（裸の英語名・2 回目の《》落ち等）なら block して「修正版をそのまま答えにする」よう求める。
    - 何も無ければ黙って通す（exit 0）。
  日本語を含まない答案・カード名を含まない答案には何もしない。
なぜ: LLM は指示を 97〜99% しか守らない（docs/bench/README.md の 5〜6 便目）。残りは器の側で「完成→検査→表示」を
  強制するしかない。これは Claude Code（Pro 以上）の器でだけ動く。claude.ai には差し込めない。
無限ループ防止: stop_hook_active=true（既にこのフックで続行させた後）なら検査せず通す。Claude Code 側にも
  連続 block の上限（8 回）がある。
設定（.claude/settings.json）:
  {"hooks": {"Stop": [{"hooks": [{"type": "command", "timeout": 60,
     "command": "PYTHONPATH=<mcp SDK の場所> <venv>/bin/python <repo>/hooks/sisho_stop_verify.py"}]}]}}
  DB 接続は <repo>/.env（db_config.py が読む）。SISHO_SRC で src の場所を上書きできる。
ログ: <repo>/logs/stop_hook.log（1 行 1 判定・答案本文は書かない）。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("SISHO_SRC", os.path.join(os.path.dirname(HERE), "src"))
LOG = os.environ.get("SISHO_HOOK_LOG", os.path.join(os.path.dirname(HERE), "logs", "stop_hook.log"))
sys.path.insert(0, SRC)

_JA = re.compile(r"[぀-ヿ一-鿿]")


def _log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        import datetime
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%m-%d %H:%M:%S}\t{msg}\n")
    except Exception:
        pass


def _last_assistant(path: str):
    """transcript(JSONL) の末尾から最後の assistant レコードを返す（(本文, tool_use だけか) の組）。形式は Claude Code 内部仕様。"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") != "assistant":
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            return content, False
        if isinstance(content, list):
            text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            only_tools = bool(content) and all(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
            return text, only_tools
        return "", False
    return None


def last_assistant_text(path: str, wait_s: float = 4.0) -> str:
    """Stop フックは最終本文が transcript に書かれる前に呼ばれることがある（実測・数秒の競合）。
    最後の assistant が tool_use だけ／無い間は少し待って読み直す（上限 wait_s）。"""
    import time
    deadline = time.time() + wait_s
    while True:
        got = _last_assistant(path)
        if got is not None and got[0].strip() and not got[1]:
            return got[0]
        if time.time() >= deadline:
            return got[0] if got else ""
        time.sleep(0.25)


def main() -> int:
    try:
        inp = json.load(sys.stdin)
    except Exception:
        return 0
    if inp.get("hook_event_name") not in (None, "Stop"):
        return 0
    if inp.get("stop_hook_active"):
        _log("pass\tstop_hook_active（再検査せず通す）")
        return 0
    text = last_assistant_text(inp.get("transcript_path", ""))
    if not text or not _JA.search(text):
        _log(f"pass\t本文なし/日本語なし len={len(text)} path={inp.get('transcript_path','')[-60:]}")
        return 0
    try:
        import mcp_server as m            # verify_answer を同じ実装で使う（DB 直結）
        out = m.verify_answer(text)
    except Exception as e:                # DB に届かない等＝フックの都合で答えを止めない
        _log(f"pass\tverify 不能: {str(e)[:80]}")
        return 0
    head = out.split("---- 修正版", 1)[0]
    m_unknown = re.search(r"未確認の名前 (\d+) 件", head)
    n_unknown = int(m_unknown.group(1)) if m_unknown else 0
    m_fix = re.search(r"機械修正 (\d+) 箇所", head)
    n_fix = int(m_fix.group(1)) if m_fix else 0
    if n_unknown == 0 and n_fix == 0:
        _log("pass\tok")
        return 0
    if n_unknown:
        reason = ("【送信前検査で止めました】答案のカード名に DB と一致しないものがあります。下の候補を search_mtg_cards で引き直し、"
                  "返り値の完成形《日本語名/英語名》に一字も変えずに直してから、答えを書き直してください（略称・自分の訳は禁止）。\n"
                  + head.strip())
    else:
        reason = ("【送信前検査で止めました】カード名の書式だけ直す必要があります（裸の英語名・裸の日本語名・《》の落ち）。"
                  "次の修正版をそのまま最終回答にしてください。\n" + out.split("---- 修正版（未確認ゼロならこのまま使う） ----", 1)[-1].strip())
    _log(f"block\tunknown={n_unknown}\tfix={n_fix}\tlen={len(text)}")
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
