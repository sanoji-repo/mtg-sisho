"""preview.py — 発売前のカード（先行収録）の状態判定と注記をここ 1 箇所に置く。

発売前のカードは Scryfall が全フォーマット not_legal にしている。そのまま返すと「どこでも使えないカード」か、
逆に「発売したらスタンダードで使える」と読まれうる。統率者セットの新カードはスタンダード・パイオニア・
モダンでは使えないので、発売後に使えるフォーマットをセットごとに書く。

判定は 2 段:
  1. 先行収録かは行の状態から（印を持たない）: digital でなく vintage が not_legal ＝ 先行収録の入口
     （sync_oracle_cards.py --preview-days）しか通らない状態。発売日に Scryfall が legal へ切り替えれば外れる。
  2. 先行収録の行を mtg_sets.released_at と今日で分ける: 発売日の前は UPCOMING・過ぎていれば AWAITING_UPSTREAM
     （Scryfall の反映待ち）。
呼び出し側（cards.py・names.display_map_for_en_names 経由の sql と combos・verify.py）が「発売前」とも「日本語版なし」とも
誤表示しないよう状態型で区別する。
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Callable

from sisho.db import LANE_LIGHT, _db
from sisho.names import face_display

PREVIEW_EXPR = "NOT digital AND legalities->>'vintage' = 'not_legal'"
PREVIEW_SQL = "NOT c.digital AND c.legalities->>'vintage' = 'not_legal'"


class ReleaseState(str, Enum):
    ORDINARY = "ordinary"
    UPCOMING = "upcoming"
    AWAITING_UPSTREAM = "awaiting_upstream"


# 公式の告知で確かめたセットだけ書く（無いセットは set_type から控えめに言う）。
_SET_LEGALITY = {
    "fra": "全フォーマットで使える（Wizards の告知）。MTG Arena は 9/29 から",
    "frc": "統率者・レガシー・ヴィンテージだけ（スタンダード・パイオニア・モダンでは使えない。Wizards の告知）",
}


def _legality(set_code: str, set_type: str | None) -> str:
    if set_code in _SET_LEGALITY:
        return _SET_LEGALITY[set_code]
    if set_type == "commander":
        return ("統率者セットの新カードは通常、統率者・レガシー・ヴィンテージだけ（スタンダード・パイオニア・"
                "モダンでは使えない）。確定は公式の告知で")
    return "未確認（公式の告知で確かめること。スタンダードで使えると決めつけない）"


def classify_release(released_at: date | None, *, today: date | None = None) -> ReleaseState:
    """先行収録条件（PREVIEW_SQL）に当たった行を、発売日から UPCOMING / AWAITING_UPSTREAM に分類する。

    発売日が NULL（mtg_sets に無い・未確認）は今は UPCOMING に倒す＝「発売前」と断定しうる
    （「分からない」を表す状態はまだ持たない。今の DB で当たる行は 0）。"""
    now = today or date.today()
    if released_at is not None and released_at <= now:
        return ReleaseState.AWAITING_UPSTREAM
    return ReleaseState.UPCOMING


def preview_status(
    card_names: list[str],
    *,
    today: date | None = None,
    _db: Callable | None = None,
) -> dict[str, tuple[ReleaseState, str]]:
    """先行収録条件に当たるカードについて {card_name: (ReleaseState, 注記)} を返す。"""
    if not card_names:
        return {}
    if _db is None:
        _db = globals()["_db"]
    rows = _db(
        "SELECT c.card_name, c.set_code, s.set_name, s.released_at, s.set_type"
        " FROM mtg_cards_v2 c LEFT JOIN mtg_sets s ON s.set_code = c.set_code"
        f" WHERE c.card_name = ANY(%s) AND {PREVIEW_SQL}",
        (list(card_names),),
        lane=LANE_LIGHT,
    )
    out: dict[str, tuple[ReleaseState, str]] = {}
    now = today or date.today()
    for name, code, set_name, rel, set_type in rows:
        when = f"{rel} 発売" if rel else "発売日未確認"
        state = classify_release(rel, today=now)
        if state == ReleaseState.AWAITING_UPSTREAM:
            note = (
                f"{set_name or code} のカード（{when}・発売日を過ぎたが、この DB の legalities は Scryfall の反映待ち）。"
                f"使えるフォーマット: {_legality(code, set_type)}。採用率・共起・17Lands の数字はまだ無い"
            )
        else:
            note = (
                f"発売前のカード（{set_name or code}・{when}）。今はどのフォーマットでも使えない"
                f"（legalities は発売日に切り替わる）。発売後に使えるフォーマット: {_legality(code, set_type)}。"
                "仮組みの相談には使ってよいが、採用率・共起・17Lands の数字はまだ無い"
            )
        out[name] = (state, note)
    return out


def preview_notes(
    card_names: list[str],
    *,
    today: date | None = None,
    _db: Callable | None = None,
) -> dict[str, str]:
    """互換口: 先行収録のカードだけ {card_name: 注記} を返す。"""
    return {k: v[1] for k, v in preview_status(card_names, today=today, _db=_db).items()}


def apply_preview_to_cards(
    cards: list[dict],
    *,
    today: date | None = None,
    _db: Callable | None = None,
) -> None:
    """検索結果のカード群に preview_note と、状態（UPCOMING / AWAITING_UPSTREAM）に応じた表示・name_note を付与する。

    - 両面カードで片面だけ日本語名がある場合（japanese_name=None かつ faces[0]["ja"] あり）に、取得済みの
      表面日本語名を消さない。
    - 発売日経過後（AWAITING_UPSTREAM）で日本語名が未収録の面は「日本語名未収録」とし、「日本語版なし」とも
      「発売前」とも断定しない。
    """
    if not cards:
        return
    statuses = preview_status([c["card_name"] for c in cards if c.get("card_name")], today=today, _db=_db)
    for c in cards:
        item = statuses.get(c.get("card_name", ""))
        if not item:
            continue
        state, note = item
        c["preview_note"] = note
        is_upcoming = state == ReleaseState.UPCOMING
        is_awaiting = state == ReleaseState.AWAITING_UPSTREAM
        faces = c.get("faces") or []
        en_front = (faces[0].get("en") if faces else None) or c["card_name"].split(" // ")[0]
        ja_front = (faces[0].get("ja") if faces else None) or (
            c["japanese_name"].split(" // ")[0]
            if c.get("japanese_name") and " // " in c["japanese_name"]
            else c.get("japanese_name")
        )
        c["name_display"] = face_display(
            en_front, ja_front, False, is_upcoming, awaiting_upstream=is_awaiting
        )
        if ja_front is None:
            if is_upcoming:
                c["name_note"] = "日本語名は未収録（発売前）＝name_display をそのまま使う（訳名を作らない）"
            else:
                c["name_note"] = "日本語名は未収録（Scryfall の反映待ち）＝name_display をそのまま使う（訳名を作らない）"
        elif c.get("japanese_name") is None and any(f.get("ja") is None for f in faces[1:]):
            if is_upcoming:
                c["name_note"] = "裏面の日本語名は未収録（発売前）＝faces の display をそのまま使う（訳名を作らない）"
            else:
                c["name_note"] = "裏面の日本語名は未収録（Scryfall の反映待ち）＝faces の display をそのまま使う（訳名を作らない）"
        for f in faces:
            f["display"] = face_display(
                f["en"], f["ja"], False, is_upcoming, awaiting_upstream=is_awaiting
            )
        if c.get("matched_face") == "back" and len(faces) > 1:
            c["face_display"] = faces[1]["display"]
