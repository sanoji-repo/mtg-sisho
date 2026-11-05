"""pool.py — ドラフト（またはシールド）の確定ピックを数える道具 draft_pool。

決まりごとは試験 tests/test_draft_pool.py が縫っている。
発端: ChatGPT と Arena のドラフトを実戦したとき、AI がピックの枚数を何度か数え間違えた。

設計の要点:
- サーバーに状態を持たない。状態はドラフトコード（draftcode.py）で利用者の側に持ち回る。
  返り値と説明文に「確実に覚えている」と書かない。
- ドラフトはピックの位置（パック・ピック）を必須にする。位置で再送・抜け・食い違いを見分ける。
- 返り値の大きさ: picks を渡す呼び出し（足す・始める・訂正）は、ピックが増えても大きさが変わらない物だけ
  （ドラフトコード・枚数と次の位置・今回のカード・マナカーブの 8 個の数・区分の数）を返す。カードごとの詳細と集計は
  code だけを渡す照会とシールドのときだけ返す（mode の引数は作らず、入力の形で決める）。毎回全部返すと 1 回のドラフトで
  返り値の合計が約 35 万字になる（実測）。この分岐は _result の slim。
- カード名を SQL に載せない: セットは固定の SQL で読んだ一覧と Python の中で照合し、カードの一覧は
  照合済みのセット記号とイベントだけを引数にした SQL 1 本で読む（limited.fetch_set_pool）。名前の照合はその行の中。
- ログ: 入口の行は引数を書かない（{}）。本体の例外はここで受けて、名前を含まない文で返す
  （投げると backstage.py が例外の文の先頭 400 字をジャーナルに残す・SDK の runner も例外を記録する）。
- 返り値は文字列の JSON（構造化した出力のスキーマは使わない＝SDK の返り値の検証の失敗が本文つきで記録される経路を作らない）。
"""
from __future__ import annotations

import json
import logging

from pydantic import StrictBool

from sisho import draftcode as dc
from sisho import errors
from sisho import manacost as mc
from sisho import poolcalc as pc
from sisho.db import DBBusy, _db
from sisho.limited import DEFAULT_EVENT_TYPE, SetPoolError, _resolve_draft_set, _sets_line, fetch_set_pool
from sisho.names import match_in_rows, near_in_rows
from sisho.toollog import _log_tool

_log = logging.getLogger("sisho.pool")

INVALID_CODE = errors.INVALID_CODE
_PICK_KEYS = {"pack", "pick", "card"}
_NEAR = 3

DESCRIPTION = (
    "ドラフト（またはシールド）で確定したピックを数えて、プールの一覧・マナカーブ・色の要求・タイプ・役割"
    "（除去・ドロー・サーチ）と 17Lands の統計を返す。状態はサーバーに残さず、返り値の draft_code で持ち回る。"
    "使い方: ピックを確定するたびに呼ぶ。初回は set（セット記号・例 FRA）と picks を渡し、2 回目からは前回の"
    " draft_code と新しい picks を渡す。picks の要素は {\"pack\": 1〜3, \"pick\": 1〜14, \"card\": カード名（日英どちらでも）}"
    "で、Arena の画面の「パック 1・ピック 5」をそのまま書く。推薦したカードではなく、取ったと言われたカードだけを渡す。"
    "ピックを足す呼び出しの返り値は小さい（今回のカード・枚数・次の位置・マナカーブの数）。カードごとの詳細と集計は、"
    "draft_code だけを code に渡すと返る。既にある位置のカードを直すときは correct=true。colors にデッキの色（W・U・B・R・G・C の文字・例 \"UR\"）を"
    "渡すと、その色で打てる形でカーブを数える（省くと色は未決として数える）。シールドは cards にカード名を"
    "枚数ぶん並べる（位置は無い）。カード名は返り値の card を一字も変えずに使う。"
)


def _err(kind: str, message: str, **extra) -> str:
    return errors.err_json(kind, message, **extra)


def _valid_pick(p) -> tuple[int, str] | str:
    """picks の要素 1 つを (通しの位置, 入力のカード名) に。合わなければ理由の文字列。"""
    if not isinstance(p, dict) or set(p) != _PICK_KEYS:
        return "picks の要素は {\"pack\", \"pick\", \"card\"} の三つの鍵だけを持つ辞書"
    pack, pick, card = p["pack"], p["pick"], p["card"]
    # type(...) is int で見る: bool は int の子なので isinstance だと true が 1 として通る（型は厳密に見る）
    if type(pack) is not int or type(pick) is not int:
        return "pack と pick は整数（文字列・小数・真偽は受けない）"
    if not 1 <= pack <= dc.PACKS or not 1 <= pick <= dc.PACK_SIZE:
        return f"pack は 1〜{dc.PACKS}・pick は 1〜{dc.PACK_SIZE}"
    if not isinstance(card, str) or not card.strip() or len(card) > dc.MAX_NAME_LEN:
        return f"card は空でないカード名（{dc.MAX_NAME_LEN} 字まで）"
    return dc.position(pack, pick), card.strip()


def _resolve_names(keys: list[str], rows: list[dict], code: str) -> tuple[dict[str, str], dict]:
    """入力のカード名を、セットの一覧の正式名に。({入力: 正式名}, 問題の辞書)。問題があれば呼ぶ側が止める。"""
    out: dict[str, str] = {}
    unknown, ambiguous = [], []
    for key in dict.fromkeys(keys):
        hits = match_in_rows(key, rows)
        if len(hits) == 1:
            out[key] = hits[0]["card_name"]
        elif len(hits) > 1:
            ambiguous.append({"input": key, "cards": [h.get("name_display") or h["card_name"] for h in hits]})
        else:
            near, more = near_in_rows(key, rows, _NEAR)
            item = {"input": key, "candidates": [r.get("name_display") or r["card_name"] for r in near]}
            if more:
                item["more_with_same_score"] = more
            unknown.append(item)
    problems = {}
    if unknown:
        problems["unknown"] = unknown
    if ambiguous:
        problems["ambiguous"] = ambiguous
    return out, problems


def _names_problem(problems: dict, code: str) -> str:
    if problems.get("ambiguous"):
        return _err(errors.OUT_OF_RANGE,
                    "名前が複数のカードに当たって 1 つに決まらない。どれを指すかを英語の正式名で渡し直す（何も数えていない）。",
                    ambiguous_names=problems["ambiguous"], unknown_names=problems.get("unknown", []))
    return _err(errors.NO_MATCH,
                f"{code} の 17Lands のカード一覧に無い名前がある（何も数えていない）。候補は近い名前の案内で、"
                "置き換えはしていない。取ったカードを確かめて、カード名を直して呼び直す。"
                "一覧に無いことは、実物のブースターに入っていないことの証明ではない。",
                unknown_names=problems["unknown"])


# correct は StrictBool: SDK の型変換で "false" などの文字列が通って、訂正の意味が変わるのを防ぐ
def draft_pool(
    set: str | None = None,
    code: str | None = None,
    picks: list[dict] | None = None,
    correct: StrictBool = False,
    colors: str | None = None,
    cards: list[str] | None = None,
) -> str:
    """set: セット記号（コードが無いとき必須）。code: 前回の draft_code。picks: [{pack, pick, card}]。
    correct: 既にある位置の訂正。colors: デッキの色（WUBRGC）。cards: シールドのカード名（枚数ぶん）。"""
    _log_tool("draft_pool", {})          # 引数は書かない（カード名・コードをログに残さない）
    try:
        return _draft_pool(set, code, picks, correct, colors, cards)
    except DBBusy as e:
        return _err(errors.BUSY, str(e))
    except SetPoolError:
        return _err(errors.DB_ERROR, "セットのカード一覧が組めない（17Lands の一覧とカード表の結び付けに欠けか重複がある）。"
                    "サーバー側の問題で、入力の問題ではない。")
    except Exception as e:               # 例外の文には入力が混ざりうる＝型だけを残す
        _log.warning("[draft_pool] %s", type(e).__name__)
        return _err(errors.DB_ERROR, "サーバー側の失敗（入力の問題ではない）。少し待ってから同じ呼び出しをもう一度試す。")


def _draft_pool(set_, code, picks, correct, colors, cards) -> str:
    # ── 入力の形 ──
    if picks is not None and not isinstance(picks, list):
        return _err(errors.OUT_OF_RANGE, "picks は配列で渡す")
    if cards is not None and not isinstance(cards, list):
        return _err(errors.OUT_OF_RANGE, "cards は配列で渡す")
    # 「渡したか」はどれも is not None で見る（空の配列を「渡していない」と取り違えると、code と cards=[] が
    # 排他の検査を抜けて 0 枚のシールドになり、次に読めないコードを返してしまう）
    if picks is not None and not 1 <= len(picks) <= dc.MAX_PICKS:
        return _err(errors.OUT_OF_RANGE, f"picks は 1〜{dc.MAX_PICKS} 件（照会だけなら picks を省く）")
    if cards is not None and not 1 <= len(cards) <= dc.MAX_SEALED:
        return _err(errors.OUT_OF_RANGE, f"cards は 1〜{dc.MAX_SEALED} 件")
    if picks is not None and cards is not None:
        return _err(errors.OUT_OF_RANGE, "picks（ドラフト）と cards（シールド）は一緒に渡さない")
    try:
        deck = mc.parse_colors(colors)
    except ValueError as e:
        return _err(errors.OUT_OF_RANGE, str(e))
    has_code = code is not None
    if not has_code and picks is None and cards is None:
        return _err(errors.EMPTY_QUERY,
                    "数える物が無い。初回は set と picks（ドラフト）か cards（シールド）を、2 回目からは前回の draft_code を渡す。")
    if correct and (not has_code or picks is None):
        return _err(errors.OUT_OF_RANGE, "correct=true は、code と直す位置の picks を一緒に渡したときだけ使える")

    # ── コード（形と検査の印だけ・名前の照合は一覧を読んだ後） ──
    version, code_set, old = None, None, []
    if has_code:
        try:
            version, code_set, old = dc.decode(code)
        except dc.CodeError as e:
            return _err(INVALID_CODE, f"draft_code が読めない（何も数えていない）: {e.message}。"
                        + _code_hint(e.reason), reason=e.reason)
        if version == dc.SEALED and (picks is not None or cards is not None):
            return _err(errors.OUT_OF_RANGE, "シールドのコード（SS1）には足さない。シールドを変えるときは、完全な cards を code 無しで渡し直す")
        if version == dc.DRAFT and cards is not None:
            return _err(errors.OUT_OF_RANGE, "ドラフトのコード（SD1）に cards は足さない（ドラフトとシールドを混ぜない）")

    # ── セット（固定の SQL の一覧と Python の中で照合・入力を SQL に渡さない） ──
    want = set_ if (set_ is not None and str(set_).strip()) else None
    if want is None and code_set is None:
        return _err(errors.EMPTY_QUERY, "set にセット記号を渡す（例 set=\"FRA\"）。手持ち: " + _sets_line(_db=_db))
    resolved = _resolve_draft_set(want, propagate_db_error=True, _db=_db) if want is not None else None
    if want is not None and resolved is None:
        return _err(errors.UNKNOWN_OPTION, "set が手持ちに無いか、複数のセットに当たって 1 つに決まらない（Cube は初版では受けない）。"
                    "手持ち: " + _sets_line(_db=_db))
    if code_set is not None:
        code_resolved = _resolve_draft_set(code_set, propagate_db_error=True, _db=_db)
        if code_resolved != code_set:
            return _err(INVALID_CODE, "draft_code のセットが手持ちに無い（何も数えていない）。", reason=dc.MALFORMED)
        if resolved is not None and resolved != code_set:
            return _err(errors.OUT_OF_RANGE, "set と draft_code のセットが違う（別のドラフトのコードかもしれない）",
                        set=resolved, code_set=code_set)
        resolved = code_set
    rows = fetch_set_pool(resolved, _db=_db)
    if not rows:
        return _err(errors.NOT_READY, f"{resolved} の 17Lands のカード一覧が空（取り込みの途中かもしれない）。時間を置いて呼び直す。")
    by_name = {r["card_name"]: r for r in rows}

    # ── 入力の名前の照合 ──
    if cards is not None:
        bad = [i for i, c in enumerate(cards) if not isinstance(c, str) or not c.strip() or len(c) > dc.MAX_NAME_LEN]
        if bad:
            return _err(errors.OUT_OF_RANGE, f"cards の要素は空でないカード名（{dc.MAX_NAME_LEN} 字まで）", bad_indexes=bad)
        keys = [c.strip() for c in cards]
        mapping, problems = _resolve_names(keys, rows, resolved)
        if problems:
            return _names_problem(problems, resolved)
        names = [mapping[k] for k in keys]
        try:
            new_code = dc.encode(dc.SEALED, resolved, names)
        except dc.CodeError as e:
            return _err(errors.OUT_OF_RANGE, e.message)
        return _result(dc.SEALED, resolved, new_code, sorted(names), by_name, deck, None)

    if version == dc.SEALED:                       # シールドの照会
        missing = [n for n in old if n not in by_name]
        if missing:
            return _catalog_mismatch(dc.SEALED, resolved, [{"card_name": n} for n in dict.fromkeys(missing)])
        return _result(dc.SEALED, resolved, code, old, by_name, deck, None)

    parsed = []
    for idx, p in enumerate(picks or []):
        v = _valid_pick(p)
        if isinstance(v, str):
            return _err(errors.OUT_OF_RANGE, f"picks の {idx + 1} 番目: {v}")
        parsed.append(v)
    mapping, problems = _resolve_names([k for _, k in parsed], rows, resolved)
    if problems:
        return _names_problem(problems, resolved)
    resolved_picks = [(i, mapping[k]) for i, k in parsed]
    if not has_code and min(i for i, _ in resolved_picks) != 1:
        return _err(errors.OUT_OF_RANGE, "code が無いので、パック 1・ピック 1 から渡す（続きを足すなら前回の draft_code を一緒に渡す）。何も数えていない。")

    # 訂正で救う道: 名前の照合は、訂正を当てた後の並びに対して行う
    try:
        applied = dc.apply_picks(old, resolved_picks, bool(correct))
    except dc.PositionError as e:
        detail = {k: _display(v, by_name) for k, v in e.detail.items()}
        return _err(errors.OUT_OF_RANGE, e.message + "（何も数えていない）", **detail)
    names = applied["names"]
    missing = [(i + 1, n) for i, n in enumerate(names) if n not in by_name]
    if missing:
        return _catalog_mismatch(dc.DRAFT, resolved, [{"position": dc.pack_pick(i), "card_name": n} for i, n in missing])
    try:
        new_code = dc.encode(dc.DRAFT, resolved, names)
    except dc.CodeError as e:
        return _err(errors.OUT_OF_RANGE, e.message)
    return _result(dc.DRAFT, resolved, new_code, names, by_name, deck, applied, slim=picks is not None)


def _display(v, by_name: dict):
    """PositionError の detail の正式名を完成形《日本語名/英語名》に（位置の辞書はそのまま）。"""
    if isinstance(v, str):
        r = by_name.get(v)
        return (r.get("name_display") or v) if r else v
    if isinstance(v, list):
        return [_display(x, by_name) for x in v]
    return v


def _code_hint(reason: str) -> str:
    return {
        dc.CHECKSUM_MISMATCH: "写すときに変わった見込み。前に返ったコードをそのまま渡し直すか、位置つきの picks を最初から（code 無しで）渡して取り込み直す。",
        dc.MALFORMED: "途中で切れたか、区切りが欠けた見込み。前に返ったコードをそのまま渡し直すか、位置つきの picks を最初から渡して取り込み直す。",
        dc.UNSUPPORTED_VERSION: "この道具が読めない形の版。この版を作った道具で読むか、picks を最初から渡して取り込み直す。",
        dc.COUNT_OUT_OF_RANGE: "枚数が範囲の外（ドラフトは 1〜42・シールドは 1〜100）。",
        dc.NOT_CANONICAL: "道具が返したコードをそのまま渡す（並べ替えない）。",
    }.get(reason, "")


def _catalog_mismatch(version: str, code: str, items: list[dict]) -> str:
    # 直し方は版で違う（シールドには位置が無いので correct は使えない）
    how = ("いまの正式名を確かめて、その位置を correct=true で置き換えるか、一覧が直るのを待つ。" if version == dc.DRAFT
           else "いまの正式名を確かめて、完全な cards を code 無しで渡し直すか、一覧が直るのを待つ。")
    return _err(errors.NO_MATCH,
                f"コードの形は正しいが、中のカード名が {code} のいまのカード一覧に無い（コードの破損とは断定しない。"
                "DB のカード名か一覧が変わった見込み）。何も数えていない。" + how + "同じ名前のまま入れ直しても治らない。",
                reason="catalog_mismatch", cards_not_in_list=items)


_HOW_DRAFT = (
    "次のピックを足すときは、この draft_code と新しいピック（pack・pick・card）を渡す。推薦したカードではなく、"
    "取ったと言われたカードだけを渡す。サーバーは覚えていない: 最新の draft_code を持つのは会話（書き置きやファイルの"
    "機能があれば、そこにも写しておく）。会話が切れたら、このコードを貼り直せば続きから数えられる（いまの道具が"
    "このコードを読める間は）。コードを失ったら、Arena の画面の取ったカードの一覧から、位置つきで最初から（code 無しで）"
    "渡し直す。返事が来なかったときは、前のコードと同じ picks でやり直してよい。")
_DETAILS_HINT = ("カードごとの詳細と集計（色の要求・タイプ・役割・土地・17Lands の統計・数えなかった面）は、"
                 "この draft_code だけを code に渡すと返る（colors も一緒に渡せる）。")


def _brief(i, n, by_name, memo) -> dict:
    """足す呼び出しで返す今回のカード 1 枚（名前・区分・マナ総量か理由）。"""
    res = memo[n]
    out = dc.pack_pick(i) | {"card": by_name[n].get("name_display") or n, "bucket": res["bucket"]}
    if res["bucket"] == pc.CURVE:
        out["mana_value"] = res["mana_value"]
    elif res.get("reason"):
        out["reason"] = res["reason"]
    return out


def _result(version, set_code, new_code, names, by_name, deck, applied, slim=False) -> str:
    """slim は picks を渡した呼び出し（足す・始める・訂正）。大きさが変わらない物だけを返す。
    42 ピックまで毎回全部の詳細を返すと、ドラフト 1 回分の返り値の合計が約 35 万字になる（実測）。"""
    memo: dict[str, dict] = {}
    pairs, pool = [], []
    for i, n in enumerate(names, 1):
        row = by_name[n]
        if n not in memo:
            memo[n] = pc.classify(row, deck)
        res = memo[n]
        pairs.append((row, res))
        entry = {k: v for k, v in res.items() if k not in ("card_name",) and v not in (None, [], {})}
        entry.update({k: v for k, v in pc.stats_of(row).items() if v is not None})
        if version == dc.DRAFT:
            entry = dc.pack_pick(i) | entry
        pool.append(entry)
    if version == dc.SEALED:                       # シールドは同じカードを 1 行にまとめて枚数を付ける
        merged: dict[str, dict] = {}
        for n, e in zip(names, pool):
            if n in merged:
                merged[n]["count"] += 1
            else:
                merged[n] = {"count": 1} | e
        pool = list(merged.values())
    summary = pc.summarize(pairs)
    total = len(names)
    if slim:
        nxt = dc.pack_pick(total + 1) if total < dc.MAX_PICKS else None
        return json.dumps({
            "draft_code": new_code,
            "how_to_continue": _HOW_DRAFT,
            "set": set_code,
            "analysis": {"colors": mc.colors_label(deck), "colors_given": deck is not None},
            "picks": {"total": total, "expected": dc.MAX_PICKS, "next": nxt},
            "this_call": {
                "added": [_brief(i, names[i - 1], by_name, memo) for i in applied["added"]],
                "resent": [dc.pack_pick(i) for i in applied["resent"]],
                "corrected": [_brief(c["position"], c["after"], by_name, memo) | {
                    "before": by_name[c["before"]]["name_display"] if c["before"] in by_name else c["before"]}
                    for c in applied["corrected"]],
                "unchanged": [dc.pack_pick(i) for i in applied["unchanged"]],
            },
            "curve": summary["curve"],
            "buckets": summary["buckets"],
            "details": _DETAILS_HINT,
        }, ensure_ascii=False, separators=(",", ":"))
    out: dict = {}
    if version == dc.DRAFT:
        out["draft_code"] = new_code
        out["how_to_continue"] = _HOW_DRAFT
    else:
        out["pool_code"] = new_code
        out["how_to_continue"] = (
            "照会し直すときは、この pool_code を code に渡す（colors は変えてよい）。シールドのカードを変えるときは、"
            "完全な cards を code 無しで渡し直す。サーバーは覚えていない。")
    out["set"] = set_code
    out["event_type"] = DEFAULT_EVENT_TYPE
    out["analysis"] = {"colors": mc.colors_label(deck), "colors_given": deck is not None}
    if deck is None:
        out["analysis"]["note"] = ("デッキの色が渡されていないので、castable は not_judged（判定していない）。"
                                   "既知の記号だけの形は打てるとみなし、面が複数あれば小さい方で数えた（色が決まると変わる）。")
    if version == dc.DRAFT:
        nxt = dc.pack_pick(total + 1) if total < dc.MAX_PICKS else None
        out["picks"] = {"total": total, "expected": dc.MAX_PICKS, "next": nxt}
        if applied is not None:
            out["this_call"] = {
                "added": [dc.pack_pick(i) | {"card": by_name[names[i - 1]].get("name_display")} for i in applied["added"]],
                "resent": [dc.pack_pick(i) for i in applied["resent"]],
                "corrected": [dc.pack_pick(c["position"]) | {
                    "before": by_name[c["before"]]["name_display"] if c["before"] in by_name else c["before"],
                    "after": by_name[c["after"]].get("name_display")} for c in applied["corrected"]],
                "unchanged": [dc.pack_pick(i) for i in applied["unchanged"]],
            }
    else:
        out["cards_total"] = total
    out["pool"] = pool
    out.update(summary)
    out["notes"] = [
        "区分（buckets）の curve・lands・not_castable・unclassified の合計は物理の枚数（total）と一致する。",
        "色の要求とタイプの合計は物理の枚数と一致しない（1 枚が複数に数えられる）。",
        "「打てる」は色の条件だけの分類（土地の枚数・場の状況・追加コストまでは見ていない）。",
        "役割の列には取りこぼし・数えすぎの候補がある＝名前で確かめる。",
        "17Lands の勝率は足したり平均したりしていない。",
        "other_faces は数えなかった形（読み替えの材料・面の指定の引数は無い）。",
    ]
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))   # 42 ピックで約 2 割軽い（字下げ無し）
