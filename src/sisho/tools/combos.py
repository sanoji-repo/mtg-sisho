"""combos.py — コンボ検索の道具 find_combos（2026-09-05 Step 3 で mcp_server.py から切り出し）。

登録（server.tool）は mcp_server.py 側。ここは DESCRIPTION と素の関数だけを持つ。
外部 API（Commander Spellbook）を叩く唯一の道具＝試験は _spellbook_post を
このモジュールで差し替える（tests/test_find_combos.py）。
"""
import json
import os

from sisho import errors
from sisho.context import CURRENT_CLIENT_IP, CURRENT_FUDA
from sisho.db import LANE_LIGHT, _db
from sisho.ratelimit import RateLimiter
from sisho.toollog import _log_tool

_COMBOS_PER_MIN = int(os.environ.get("MCP_COMBOS_PER_MIN", "10"))
_COMBOS_GLOBAL_MIN = int(os.environ.get("MCP_COMBOS_GLOBAL_MIN", "60"))
_combos_limiter = RateLimiter(per_ip=_COMBOS_PER_MIN, global_=_COMBOS_GLOBAL_MIN, exempt="")


# ─── Commander Spellbook（2026-09-04 本人 GO「API 解放されてるんだから使わせてもよくね」）───
# 取り込まず都度呼ぶ（8/29 survey: 第三者ツールからの API 表示は公式 docs で許容・データのライセンスは明文なし＝再配布は灰
# → DB に持たない）。返り値に出典と前提の原文を必ず載せる（本人「2 枚だけでは成立しない前提付きが多い」）。
# bracketTag・結果タグは正確性に欠ける実例（7/28・EDH Build Helper）があるので「参考」と明記。箱の砂場から backend へは
# AF_INET 許可で到達済み（9/4 実測 200・1.0 秒）。時間制限 6 秒・失敗はこの道具だけが「届かない」を返す。
_SPELLBOOK_URL = os.environ.get("SPELLBOOK_URL", "https://backend.commanderspellbook.com/find-my-combos")
_SPELLBOOK_TIMEOUT = float(os.environ.get("SPELLBOOK_TIMEOUT", "6"))


def _spellbook_post(payload: dict) -> dict:
    import urllib.request
    req = urllib.request.Request(_SPELLBOOK_URL, data=json.dumps(payload).encode("utf-8"),
                                 headers={"content-type": "application/json", "accept": "application/json",
                                          "user-agent": "mtg-sisho-mcp/1.0 (+https://github.com/)"}, method="POST")
    with urllib.request.urlopen(req, timeout=_SPELLBOOK_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _display_map(names: list[str]) -> dict[str, str]:
    """英語名 → 完成形 name_display（DB に無い名前は「英語名（DB 未収録）」）。"""
    out = {n: f"{n}（DB 未収録）" for n in names}
    if not names:
        return out
    try:
        for cn, nd in _db("SELECT card_name, name_display FROM mtg_cards_v2 WHERE card_name = ANY(%s)", (names,),
                          lane=LANE_LIGHT):
            out[cn] = nd
    except Exception:
        pass
    return out



DESCRIPTION = (
    "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く。】"
    "【コンボを探すときはこれ。記憶で組み合わせを書かない】手持ちのカード名（英語名・日本語名どちらでも）を渡すと、"
    "Commander Spellbook（公開 API・都度照会・出典を必ず添える）から、いま組めるコンボ（included）・あと 1 枚で組めるコンボ"
    "（almost_included）・色を足せば組めるコンボ（by_adding_colors）を返す。各コンボに使う札（完成形）・生み出す効果・"
    "**前提の原文（prerequisites）**・人気・出典 URL。前提付きのコンボは前提をそのまま書く（2 枚で成立するとは限らない）。"
    "bracket タグは参考（正確性に欠ける実例あり）。commanders に統率者名を入れると統率者領域を考慮する。"
    "デッキ 1 本（最大 120 枚）を渡す使い方が本来の形。日本語名は DB で英語名に直してから照会する。")
def find_combos(card_names: list[str], commanders: list[str] | None = None, limit: int = 10) -> str:
    _log_tool("find_combos", {"n": len(card_names or []), "commanders": commanders, "limit": limit})
    names = [n.strip() for n in (card_names or []) if n and n.strip()]
    cmds = [n.strip() for n in (commanders or []) if n and n.strip()]
    if not names:
        return errors.err_json(
            errors.EMPTY_QUERY,
            "card_names が空です。コンボを探したいカード名（英語名・日本語名どちらでも）を"
            " 1 枚以上入れて呼び直す（デッキ 1 本ぶん・最大 120 枚を渡すのが本来の使い方）")
    if len(names) > 120:
        return errors.err_json(
            errors.OUT_OF_RANGE,
            f"card_names が多すぎます: {len(names)} 枚（上限 120 枚）。デッキ 1 本ぶんに絞って呼び直す")
    limit = max(1, min(int(limit), 30))

    # 外部 API（Commander Spellbook）を守るための枠（B-4・C-1）
    fuda = CURRENT_FUDA.get()
    if fuda == "legacy":
        key = "legacy:" + (CURRENT_CLIENT_IP.get() or "?")
    elif fuda:
        key = "fuda:" + fuda
    else:
        key = "anon"
    ok, retry = _combos_limiter.check(key)
    if not ok:
        return errors.err_json(
            errors.RATE_LIMITED,
            f"コンボ検索の回数が多すぎます（この接続 URL は 1 分に {_COMBOS_PER_MIN} 回まで）。{retry} 秒待ってから呼び直す")
    # 日本語名 → 英語名（DB）。英語名はそのまま。見つからない名前はそのまま送る（Spellbook 側で無視される）
    resolved: dict[str, str] = {}
    try:
        rows = _db("SELECT card_name, japanese_name, name_ja_front FROM mtg_cards_v2"
                   " WHERE card_name = ANY(%s) OR japanese_name = ANY(%s) OR name_ja_front = ANY(%s)",
                   (names + cmds, names + cmds, names + cmds), lane=LANE_LIGHT)
        for cn, ja, jaf in rows:
            resolved[cn] = cn
            if ja: resolved[ja] = cn
            if jaf: resolved[jaf] = cn
    except Exception:
        pass
    en_main = [resolved.get(n, n) for n in names]
    en_cmd = [resolved.get(n, n) for n in cmds]
    unresolved = [n for n in names + cmds if n not in resolved and not n.isascii()]
    payload = {"main": [{"card": n, "quantity": 1} for n in en_main], "commanders": [{"card": n, "quantity": 1} for n in en_cmd]}
    try:
        data = _spellbook_post(payload)
    except Exception as e:
        # 外の世界の障害は生の例外の型と文をそのまま載せる（丸めない）
        return errors.err_json(
            errors.UPSTREAM_UNREACHABLE,
            f"Commander Spellbook に届かない（{type(e).__name__}: {str(e)[:120]}）。少し待って再試行。この道具以外は影響なし")
    res = data.get("results", data) if isinstance(data, dict) else {}
    sections = {"included": "いま組める", "almostIncluded": "あと 1 枚で組める", "almostIncludedByAddingColors": "色を足せば組める"}
    all_names: set[str] = set()
    for key in sections:
        for c in (res.get(key) or [])[:limit]:
            for u in c.get("uses") or []:
                all_names.add((u.get("card") or {}).get("name", ""))
    disp = _display_map(sorted(n for n in all_names if n))
    out: dict = {"source": "Commander Spellbook（https://commanderspellbook.com/・公開 API・照会時点の内容）",
                 "input": {"main": en_main, "commanders": en_cmd},
                 "note": ("前提（prerequisites）は原文のまま。前提付きのコンボは書かれた条件が揃わないと成立しない＝『2 枚で成立』と書かない。"
                          "bracket タグは参考（正確性に欠ける実例あり・断定しない）。人気（popularity）は Spellbook 側の集計。"
                          "答えには出典「Commander Spellbook」と各コンボの URL を添える。カード名は uses_display の完成形をそのまま書く")}
    if unresolved:
        out["unresolved_names"] = unresolved
        out["unresolved_note"] = "DB で英語名に直せなかった名前（綴りを search_mtg_cards で確認して呼び直す）"
    for key, label in sections.items():
        items = res.get(key) or []
        rows = []
        for c in items[:limit]:
            uses = [(u.get("card") or {}).get("name", "") for u in c.get("uses") or []]
            rows.append({"id": c.get("id"), "url": f"https://commanderspellbook.com/combo/{c.get('id')}/",
                         "uses": uses, "uses_display": [disp.get(n, n) for n in uses],
                         "requires_templates": [(t.get("template") or {}).get("name", "") for t in c.get("requires") or []],
                         "produces": [(p.get("feature") or {}).get("name", "") for p in c.get("produces") or []],
                         "prerequisites": " / ".join(x for x in [c.get("easyPrerequisites") or "", c.get("notablePrerequisites") or ""] if x) or None,
                         "description": (c.get("description") or "")[:600] or None,
                         "popularity": c.get("popularity"), "bracket_tag": c.get("bracketTag"), "identity": c.get("identity")})
        out[key] = {"label": label, "count_total": len(items), "shown": len(rows), "combos": rows}
    return json.dumps(out, ensure_ascii=False, indent=1)
