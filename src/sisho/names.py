"""names.py — カード名の解決と候補案内を 1 箇所に置く。

本番の道具（partners・rules 等）は `resolve_card_ex` / `resolve_card` を使い、
集計や裁定検索の**前に**入力名を 1 枚のカードに決める（複数候補をそのまま ANY() に渡すと
裏面名が本物のカード名と重なる札で 2 枚分が合算されるため）。
`resolve_face_name` は面の名前（表面・裏面）から正式名の候補一覧を引く低水準関数（test_names.py で順序規則を検証）。
`display_map_for_en_names`（英語名 → 完成形の辞書・sql と combos）・`resolve_names_to_en_ex`（日英の一括解決・combos）・
`lookup_card_with_face`（面つきで 1 枚・manabase）も同じ順位の規則（下の DESIGN 12・同じ順位なら紙・最上位に別カードが
2 枚以上なら決めない）で決める。verify は全件の辞書を自前で組むが順位は同じ（verify._build_names の put）。
ただし同じ順位・同じ紙区分に別カードが 2 枚以上あるとき、ここは曖昧として返し、verify の辞書は先勝ち（曖昧を持てない）。

DB（mtg_cards_v2）上の名前の不変条件（実測・DESIGN 12）:
  - `name_en_front` も `name_en_back` も各列内で重複なし（実測 0 件）＝1 つの面名に当たる行は最大 2 行
    （表面で当たる行 1 つ・裏面で当たる行 1 つ）。
  - 単面カードは常に `card_name = name_en_front`（`name_en_back IS NULL AND card_name <> name_en_front` は 0 件）。
  - 裏面名が別の本物のカード名と同じカードは 21 枚（例: `Emeritus of Ideation // Ancestral Recall` の
    裏面名 `Ancestral Recall`）。そのため必ず**正式名 → 表面名 → 裏面名**の順（DESIGN 12）で優先し、
    本物のカード（表面一致）を先に置く。
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Callable

from sisho.db import LANE_LIGHT, _db as _default_db

_db = _default_db

# 発売状態の照会だけが落ちたときの注記の頭（display_map_for_en_names が返し、呼ぶ側が利用者向けの一文に直す）
PREVIEW_UNAVAILABLE = "発売状態の照会に失敗した"


def resolve_face_name(name: str, *, _db: Callable | None = None) -> list[str]:
    """面の名前（表面名・裏面名）を正式名（card_name）に解決した候補を返す。

    - 並びは「表面で当たった行が先」（DESIGN 12 の 正式名 → 表面名 → 裏面名）。
    - 一致しなければ空リスト。複数一致は推測せず、そのまま候補として全部返す
      （どれを採るかは呼ぶ側の仕事）。
    - 正式名（`表 // 裏`）そのものを渡しても面の列には一致しない＝空が返る。
      正式名で引く経路は呼ぶ側が先に持っている（裁定検索の完全一致・相方検索の候補の先頭）。
    """
    n = (name or "").strip()
    if not n:
        return []
    if _db is None:
        _db = globals()["_db"]
    return [r[0] for r in _db(
        "SELECT card_name FROM mtg_cards_v2"
        " WHERE name_en_front = %s OR name_en_back = %s"
        " ORDER BY (name_en_front = %s) DESC", (n, n, n), lane=LANE_LIGHT)]


def _like_exact(n: str) -> str:
    """ILIKE で「全体が一致（大文字小文字は無視）」を言うための値＝LIKE の記号を字にする（pg_trgm の索引に乗る）。"""
    return n.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_card_ex(
    name: str,
    *,
    allow_slash_alias: bool = True,
    _db: Callable | None = None,
) -> tuple[dict | None, list[str]]:
    """入力名を 1 枚のカードに決める。(カード, 曖昧なときの正式名の一覧)。決まらなければ (None, [])。

    集計や裁定検索の**前に** 1 枚に決める。面の名前の候補を全部 ANY() に渡して集計すると、
    裏面名が別の本物のカードと同じ 21 枚で 2 枚分を合算する（「Replenish」の同居率が 103.5% になる）ため。
    決め方:
      段 1 完全一致: 正式名 → 表面名 → 裏面名（DESIGN 12）。
      段 2 大文字小文字だけ違う全体一致（段 1 が空のときだけ・「brainstorm」）。順位は段 1 と同じ。
      段 3 斜線 1 本（`Fire / Ice`）→ 斜線 2 本（`Fire // Ice`）に直した全体が正式名に一致するときだけ採用
           （mtgtop8 が書く表記・デッキのリストにはこの表記の名前が 55 種ある（実測）・partners・rules 等で共有）。
      どの段でも、最上位の順位に**別のカードが 2 枚以上**あれば決めない＝曖昧として正式名を返す
      （裏面名が 3 枚で重なる 5 種がある・例「Vicious Verse」＝id の順で 1 枚に決めると実デッキ 0 本の別カードを断定した）。
      同じ順位に紙とデジタルがあれば紙を採る（デジタルだけで曖昧にはしない）。
    ` // ` を含む入力は正式名と全体一致のときだけ当たる（後半が違う入力を前半で通さない）。"""
    n = (name or "").strip()
    if not n:
        return None, []
    if _db is None:
        _db = globals()["_db"]
    cols = "id, card_name, name_en_front, name_display, digital"
    stages = (
        ("SELECT " + cols + ", CASE WHEN card_name = %(n)s THEN 0 WHEN name_en_front = %(n)s THEN 1 ELSE 2 END AS r"
         " FROM mtg_cards_v2 WHERE card_name = %(n)s OR name_en_front = %(n)s OR name_en_back = %(n)s", LANE_LIGHT, False),
        # 大文字小文字の無視は完全一致が外れたときだけ・全件をなめうるので重いレーン（公開サーバーは冷えると桁が変わる）
        ("SELECT " + cols + ", CASE WHEN card_name ILIKE %(l)s THEN 0 WHEN lower(name_en_front) = lower(%(n)s) THEN 1 ELSE 2 END AS r"
         " FROM mtg_cards_v2 WHERE card_name ILIKE %(l)s OR lower(name_en_front) = lower(%(n)s)"
         " OR lower(name_en_back) = lower(%(n)s)", None, True),
    )
    for sql, lane, case_fixed in stages:
        kw = {"lane": lane} if lane else {}
        rows = _db(sql + " ORDER BY r, digital, id", {"n": n, "l": _like_exact(n)}, **kw)
        if not rows:
            continue
        best = [x for x in rows if x[5] == rows[0][5]]
        paper = [x for x in best if not x[4]] or best
        if len({x[0] for x in paper}) > 1:
            return None, [x[1] for x in paper]
        cid, cname, front, disp, digital, _r = paper[0]
        return ({"id": cid, "card_name": cname, "front": front or cname.split(" // ")[0],
                 "display": disp, "digital": digital, "case_fixed": case_fixed}, [])
    if allow_slash_alias and " / " in n and " // " not in n:
        full = " // ".join(p.strip() for p in n.split(" / "))
        c2, _amb2 = resolve_card_ex(full, allow_slash_alias=False, _db=_db)
        if c2 and c2["card_name"].lower() == full.lower():
            return c2, []
    return None, []


def resolve_card(name: str, *, _db: Callable | None = None) -> dict | None:
    """1 枚に決まったカード（曖昧・不在なら None）。"""
    return resolve_card_ex(name, _db=_db)[0]


def ambiguous_hint(names: list[str]) -> str:
    """同じ名前を面に持つカードが複数あるときの案内（1 枚に決めつけない）。"""
    return ("この名前は複数のカードの面の名前で、1 枚に決まらない: " + "・".join(names)
            + "＝どれかの正式名（表 // 裏）で呼び直す")


def find_card(name: str, extra=(), *, _db: Callable | None = None) -> tuple[int, str] | None:
    """(id, name_display) を返す薄い口（空振りの理由を言い分けるとき用）。extra は互換のため受けるが使わない
    ＝元の入力名だけで決める（展開した候補名で当てると別カードを入力のカードとして扱いうる）。"""
    c = resolve_card(name, _db=_db)
    return (c["id"], c["display"]) if c else None


PARTIAL_CANDIDATES = 5


def card_candidates(
    name: str,
    with_rulings: bool = False,
    *,
    _db: Callable | None = None,
) -> list[tuple[str, str, bool | None]]:
    """名前に入力を含むカード（card_name, name_display, digital）。紙を先・人気順。LIKE の記号は字として扱う。

    with_rulings=True（裁定検索）は、裁定の表にだけある名前（カード表に無い 732 種＝次元・アンセット等）も候補に足す
    （カード表だけを見ると、「Talon Gat」が別カード《マダラの鉤爪門/Talon Gates of Madara》の
    裁定に決まり、本物の Talon Gates を見落とす）。その名前の完成形はカード表に無いので名前そのままで書く。"""
    n = (name or "").strip()
    if len(n) < 3:
        return []
    if _db is None:
        _db = globals()["_db"]
    pat = "%" + _like_exact(n) + "%"
    out = [tuple(r) for r in _db(
        "SELECT card_name, name_display, digital FROM mtg_cards_v2 WHERE card_name ILIKE %s"
        " ORDER BY digital, edhrec_rank NULLS LAST, id LIMIT %s",
        (pat, PARTIAL_CANDIDATES + 1), lane=LANE_LIGHT)]
    if with_rulings:
        # カード表の候補の数に関係なくいつも探す（カード表が 5 件以上のとき探すのをやめると「Gavon」の Gavony・
        # 「Goldmeado」の Goldmeadow が案内から落ちる）。裁定の表にだけある名前は digital=None で印を付けて返す
        # ＝not_found_hint がカード表の候補と分けて見せる（上限で黙って消さない）。
        extra = _db("SELECT DISTINCT r.card_name FROM card_rulings r WHERE r.card_name ILIKE %s"
                    " AND NOT EXISTS (SELECT 1 FROM mtg_cards_v2 m WHERE m.card_name = r.card_name)"
                    " ORDER BY 1 LIMIT %s", (pat, PARTIAL_CANDIDATES + 1))    # 裁定の表 7.8 万行を部分一致＝重いレーン
        seen = {c[0] for c in out}
        out += [(r[0], r[0], None) for r in extra if r[0] not in seen]
    return out


def candidate_label(card_name: str, display: str, digital: bool = False) -> str:
    """候補の書き方。両面・分割カードは表面の完成形だけだと入力が見えないので正式名を併記する。
    デジタル専用は印を付ける（日本語名があるデジタルカードは完成形に「日本語名未収録」が出ない）。"""
    label = f"{display}（正式名 {card_name}）" if " // " in card_name else display
    return label + ("（デジタル専用）" if digital else "")   # digital=None は裁定の表にだけある名前（印なし）


def not_found_hint(
    name: str,
    fallback: str,
    cands: list | None = None,
    *,
    _db: Callable | None = None,
) -> str:
    """完全一致しなかった名前へ、カード表の部分一致の候補を添える。
    1 枚に決めつけず（採用率で推測しない）、完成形の候補を並べて選ばせる。デジタル専用も候補に残す
    （candidate_label が「（デジタル専用）」を付ける）。候補が無ければ fallback（引き方の案内）。"""
    rows = card_candidates(name, _db=_db) if cands is None else cands
    if not rows:
        return "カードが見つかりません・" + fallback

    def listed(xs):
        return ("・".join(candidate_label(cn, d, dg) for cn, d, dg in xs[:PARTIAL_CANDIDATES])
                + ("ほか" if len(xs) > PARTIAL_CANDIDATES else ""))
    cards = [c for c in rows if c[2] is not None]
    only_rulings = [c for c in rows if c[2] is None]
    parts = []
    if cards:
        parts.append("名前に含むカード: " + listed(cards))
    if only_rulings:
        parts.append("裁定の表にだけある名前（次元・アンセット等）: " + listed(only_rulings))
    return "この名前ちょうどのカードは無い。" + "／".join(parts) + "＝どれかの英語の正式名で呼び直す"


def face_display(
    en: str,
    ja: str | None,
    digital: bool = False,
    preview: bool = False,
    *,
    awaiting_upstream: bool = False,
) -> str:
    """面の完成形。ja があれば《ja/en》・無ければ「en（日本語版なし）」・digital や発売日経過後の反映待ちは
    「en（日本語名未収録）」・発売前のカードは「en（日本語名は未収録・発売前）」
    （DB の name_display の式と同じ枝）。"""
    if ja:
        return f"《{ja}/{en}》"
    note = (
        "日本語名未収録"
        if (digital or awaiting_upstream)
        else ("日本語名は未収録・発売前" if preview else "日本語版なし")
    )
    return f"{en}（{note}）"


def display_map_for_en_names(
    names: list[str],
    *,
    fallback_suffix: str | None = None,
    back_as_face: bool = False,
    _db: Callable | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """英語名（正式名・表面名・裏面名）の一覧から ({入力名: 完成形 name_display}, {card_name: preview_note}) を返す。

    - DESIGN 12 に従い、正式名一致（0）→ 表面名一致（1）→ 裏面名一致（2）、同順位は紙優先（ORDER BY digital, id）で決める。
    - 最上位順位（紙優先後）に異なるカードが複数当たる名前は、勝手に 1 枚目の表示で確定しない。
    - 片面だけ日本語名がある両面カードでも、取得済みの表面日本語名（name_ja_front）を消さない。
    - back_as_face=True（sql）は裏面名をその面の完成形で返す（裏面名が本物のカード名と同じ prepare は
      上の順位で本物が勝つ）。False（combos）は裏面名にも表面の完成形を返す。
    - 先行収録の状態照会には呼び出し元と同じ _db を渡す。その照会だけが落ちても取れた訳は保ち、
      失敗は注記（返り値の 2 つ目）で返して、状態の分からない行は「日本語名未収録」に倒す。
    """
    from sisho.preview import PREVIEW_EXPR, ReleaseState, preview_status

    out: dict[str, str] = {n: f"{n}{fallback_suffix}" for n in names} if fallback_suffix is not None else {}
    uniq = sorted({n for n in names if n})
    if not uniq:
        return out, {}
    if _db is None:
        _db = globals()["_db"]
    rows = _db(
        "SELECT card_name, name_en_front, name_ja_front, name_en_back, name_ja_back,"
        f" name_display, japanese_name, digital, ({PREVIEW_EXPR})"
        " FROM mtg_cards_v2"
        " WHERE card_name = ANY(%s) OR name_en_front = ANY(%s) OR name_en_back = ANY(%s)"
        " ORDER BY digital, id",
        (uniq, uniq, uniq),
        lane=LANE_LIGHT,
    )
    preview_candidates = [r[0] for r in rows if r[8]]
    # 発売状態は表示の補助。照会が落ちても取れた訳は捨てない（失敗を外側の例外処理へ流すと
    # 通常のカードの訳まで失い、combos が「DB 未収録」と出す）。落ちたことは注記で返し、状態は下で「分からない」として扱う
    try:
        pstats = preview_status(preview_candidates, _db=_db) if preview_candidates else {}
        pnotes = {k: v[1] for k, v in pstats.items()}
    except Exception as e:
        pstats = {}
        pnotes = {k: f"{PREVIEW_UNAVAILABLE}（{type(e).__name__}）＝発売前かどうか確かめられない"
                  for k in preview_candidates}
    want = set(uniq)
    cands_by_key: dict[str, list[tuple[int, bool, str, str]]] = {}
    for cn, enf, jaf, enb, jab, nd, ja, dg, is_prev in rows:
        en_front = enf or cn.split(" // ")[0]
        ja_front = jaf or (ja.split(" // ")[0] if ja and " // " in ja else ja)
        st = pstats.get(cn)[0] if cn in pstats else None
        is_upcoming = bool(is_prev) and st == ReleaseState.UPCOMING
        # 状態が分からない先行収録の行（照会の失敗）も、反映待ちと同じ中立の「日本語名未収録」に倒す
        # ＝「発売前」とも「日本語版なし」とも断定しない
        is_awaiting = bool(is_prev) and st != ReleaseState.UPCOMING
        if ja_front:
            nd = face_display(en_front, ja_front)
        elif bool(is_prev) or not nd:
            nd = face_display(en_front, None, bool(dg), is_upcoming, awaiting_upstream=is_awaiting)
        back_disp = (
            face_display(enb, jab, bool(dg), is_upcoming, awaiting_upstream=is_awaiting)
            if (enb and back_as_face)
            else nd
        )
        for key, rank, disp in ((cn, 0, nd), (enf, 1, nd), (enb, 2, back_disp)):
            if key and key in want:
                cands_by_key.setdefault(key, []).append((rank, bool(dg), cn, disp))

    for key, cands in cands_by_key.items():
        min_rank = min(r for r, _, _, _ in cands)
        best = [x for x in cands if x[0] == min_rank]
        paper = [x for x in best if not x[1]] or best
        distinct_cns = list(dict.fromkeys(cn for _, _, cn, _ in paper))
        if len(distinct_cns) > 1:
            if fallback_suffix is not None:
                out[key] = f"{key}（複数カードに該当: {'・'.join(distinct_cns)}）"
            continue
        out[key] = paper[0][3]
    return out, pnotes


def preview_unavailable_note(pnotes: dict[str, str]) -> str:
    """display_map_for_en_names の注記のうち照会の失敗だけを、道具の返り値に載せる一文にする（無ければ空文字）。
    訳は保ったまま返しているので、利用者に「発売前かどうかを確かめられなかった」ことだけを伝える。"""
    failed = sorted(k for k, v in pnotes.items() if v.startswith(PREVIEW_UNAVAILABLE))
    if not failed:
        return ""
    head = "・".join(failed[:5]) + ("ほか" if len(failed) > 5 else "")
    return (f"{PREVIEW_UNAVAILABLE}ため、先行収録のカード（{head}）が発売前かどうか確かめられない"
            "（完成形は「英語名（日本語名未収録）」にしてある・発売前とも日本語版なしとも決めつけない）")


def resolve_names_to_en_ex(
    names: list[str],
    *,
    _db: Callable | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """日英のカード名（正式名・日本語名・表面名・裏面名）を一括解決し、(一意解決辞書, 曖昧候補辞書) を返す。

    - 段 1（軽いレーン）: 索引のある 5 列（card_name, japanese_name, name_en_front, name_en_back, name_ja_back）を引く。
    - 段 2（重いレーン）: 非 ASCII の入力名のうち、段 1 で正式名（rank 0）一致していない名前に対して name_ja_front を引く。
    - DESIGN 12（正式名 0 → 表面名 1 → 裏面名 2）と紙優先で最上位候補を選び、同順位に異なるカードが 2 枚以上あれば
      勝手に 1 枚目に確定せず ambiguous に分けて返す。
    """
    uniq = sorted({n.strip() for n in names if n and n.strip()})
    if not uniq:
        return {}, {}
    if _db is None:
        _db = globals()["_db"]
    rows = _db(
        "SELECT card_name, japanese_name, name_en_front, name_en_back, name_ja_back, digital"
        " FROM mtg_cards_v2"
        " WHERE card_name = ANY(%s) OR japanese_name = ANY(%s) OR name_en_front = ANY(%s)"
        "    OR name_en_back = ANY(%s) OR name_ja_back = ANY(%s)"
        " ORDER BY digital, id",
        (uniq, uniq, uniq, uniq, uniq),
        lane=LANE_LIGHT,
    )
    want = set(uniq)
    cands_by_key: dict[str, list[tuple[int, bool, str]]] = {}
    rank0_keys: set[str] = set()
    for cn, ja, enf, enb, jab, dg in rows:
        for key, rank in ((cn, 0), (ja, 0), (enf, 1), (enb, 2), (jab, 2)):
            if key and key in want:
                cands_by_key.setdefault(key, []).append((rank, bool(dg), cn))
                if rank == 0:
                    rank0_keys.add(key)

    rest = [n for n in uniq if not n.isascii() and n not in rank0_keys]
    if rest:
        for cn, jaf, dg in _db(
            "SELECT card_name, name_ja_front, digital FROM mtg_cards_v2"
            " WHERE name_ja_front = ANY(%s) ORDER BY digital, id",
            (rest,),
        ):
            if jaf and jaf in want:
                cands_by_key.setdefault(jaf, []).append((1, bool(dg), cn))

    resolved: dict[str, str] = {}
    ambiguous: dict[str, list[str]] = {}
    for key, cands in cands_by_key.items():
        min_rank = min(r for r, _, _ in cands)
        best = [x for x in cands if x[0] == min_rank]
        paper = [x for x in best if not x[1]] or best
        distinct = list(dict.fromkeys(cn for _, _, cn in paper))
        if len(distinct) == 1:
            resolved[key] = distinct[0]
        elif len(distinct) > 1:
            ambiguous[key] = distinct
    return resolved, ambiguous


def resolve_names_to_en(names: list[str], *, _db: Callable | None = None) -> dict[str, str]:
    """一意に決まった日英カード名 → 英語正式名 card_name の辞書を返す（曖昧な名前は含めない）。"""
    return resolve_names_to_en_ex(names, _db=_db)[0]


def lookup_card_with_face(
    name: str,
    cols: str,
    *,
    _db: Callable | None = None,
) -> tuple[tuple | None, int | None, list[str]]:
    """日英のカード名（正式名・日本語名・面の名前）を 1 枚に決めて (行, 当たった面 0/1/None, 曖昧候補一覧) を返す。

    - 段 1（軽いレーン）: 索引のある 5 列（card_name, japanese_name, name_en_front, name_en_back, name_ja_back）を引く。
    - 段 2（重いレーン）: 非 ASCII の入力名で、段 1 が 0 件または最良順位が rank >= 1（裏面一致や表面一致）のときは、
      索引のない name_ja_front（rank 1）も引いて合流させる（裏面一致が日本語表面一致に勝たないようにする）。
    - DESIGN 12（正式名 0 → 表面名 1 → 裏面名 2）と紙優先で最上位順位を選び、最上位に異なるカードが複数あれば
      LIMIT 1 で勝手に 1 枚に潰さず (None, None, 正式名一覧) を返す。
    """
    n = (name or "").strip()
    if not n:
        return None, None, []
    if _db is None:
        _db = globals()["_db"]
    rows = list(_db(
        f"SELECT {cols},"
        " CASE WHEN card_name = %s OR japanese_name = %s THEN 0"
        "      WHEN name_en_front = %s OR name_ja_front = %s THEN 1 ELSE 2 END AS _r"
        " FROM mtg_cards_v2"
        " WHERE card_name = %s OR japanese_name = %s OR name_en_front = %s"
        "    OR name_en_back = %s OR name_ja_back = %s"
        " ORDER BY _r, digital, id",
        (n,) * 9,
        lane=LANE_LIGHT,
    ))
    if not n.isascii() and (not rows or min(x[-1] for x in rows) >= 1):
        rows2 = list(_db(
            f"SELECT {cols}, 1 AS _r FROM mtg_cards_v2"
            " WHERE name_ja_front = %s ORDER BY digital, id",
            (n,),
        ))
        if rows2:
            rows = sorted(rows + rows2, key=lambda x: (x[-1], bool(x[-2])))
    if not rows:
        return None, None, []
    min_rank = min(x[-1] for x in rows)
    best = [x for x in rows if x[-1] == min_rank]
    paper = [x for x in best if not x[-2]] or best
    distinct = list(dict.fromkeys(x[0] for x in paper))
    if len(distinct) > 1:
        return None, None, distinct
    chosen = paper[0]
    r = chosen[:-1]
    rank = chosen[-1]
    # cols の末尾 5 列は name_en_front(-5), name_ja_front(-4), name_en_back(-3), name_ja_back(-2), digital(-1)
    if r[-3] and rank == 2 and n in (r[-3], r[-2]):
        return r, 1, []
    if r[-3] and rank == 1 and n in (r[-5], r[-4]):
        return r, 0, []
    return r, None, []


def trgm_display_candidates(name: str, limit: int = 3, *, _db: Callable | None = None) -> list[str]:
    """pg_trgm で日英の近いカード名（name_display）を引く（similarity 閾値 0.3 を明示）。

    既定の重いレーンで引く（similarity の並べ替えは索引に乗らない＝軽いレーンの 1 秒で切られうる・
    test_db_lanes の 10 が軽いレーンの宣言に similarity( が混ざらないかを静的に見ている）。"""
    n = (name or "").strip()
    if not n:
        return []
    if _db is None:
        _db = globals()["_db"]
    rows = _db(
        "SELECT name_display FROM mtg_cards_v2"
        " WHERE (card_name %% %s OR japanese_name %% %s)"
        "   AND greatest(similarity(card_name, %s), similarity(coalesce(japanese_name, ''), %s)) > 0.3"
        " ORDER BY greatest(similarity(card_name, %s), similarity(coalesce(japanese_name, ''), %s)) DESC"
        " LIMIT %s",
        (n, n, n, n, n, n, limit),
    )
    return [r[0] for r in rows]


# ─── 渡された候補の行の中で名前を照合する（DB に触らない・draft_pack_stats と draft_pool が共有） ───
# 共有するのは一致の順位・曖昧の判定・近さの点だけで、候補をどこから取るかは入口ごとに決める
# （draft_pack_stats はカード表全体で照合してからセットの統計を付ける・draft_pool はセットの一覧の中だけ）。

_POOL_MIN_SCORE = 0.5          # セットの中の候補に出す点の下限（下の _pool_score）
_POOL_STRONG = 0.85            # これ以上が強い候補。draft_pack_stats はセットの中に強い候補が無ければ DB 全体も見る

# 名前を比べる前に落とす文字（空白・句読点・括弧・記号）。画像から読んだ名前はカンマや中黒が抜けやすい
_NAME_NOISE = re.compile(r"[\s,、・'’\"\-‐–—.:：/!！?？()（）「」『』《》]+")


def _norm_name(s: str | None) -> str:
    """比べるための名前（NFKC・小文字・空白と記号を落とす）。"""
    return _NAME_NOISE.sub("", unicodedata.normalize("NFKC", s or "").lower())


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", s or "").lower())


def _has_run(words: list[str], run: list[str]) -> bool:
    """run が words の中に、単語の切れ目を保ったまま続けて現れるか。"""
    n = len(run)
    return n > 0 and any(words[i:i + n] == run for i in range(len(words) - n + 1))


def _pool_score(key: str, names: list[str]) -> float:
    """入力 key とそのカードの名前たち（英語名・日本語名・面の名前）の近さ（0〜1）。

    ChatGPT のドラフトの実測: 画像から読んだ名前のかけら（「いたずら屋」・「眠り いざない」・
    カンマ抜けの「Greta Sweettooth Scourge」）が、pg_trgm の similarity > 0.3 ではセットの中で拾えなかった
    （短いかけらと長い名前は全体どうしの似かたが低く出る）。セットのカードは数百枚なので Python で比べる:
      1.0  空白と記号を落とすと同じ
      0.9  入力が名前の一部（2 文字以上）
      0.85 名前が入力の一部（読み取りで余計な字が付いた）
      0.5〜0.8 空白で区切ったかけらのうち、名前に含まれる割合
      それ以外は文字列の似かた（difflib の ratio）
    候補は「未確認・完全一致ではない」として返すだけで、勝手に置き換えない（誤発動は有害の非対称）。

    英語の一部一致は単語の切れ目で見る（切れ目を見ないと「the」が「hear*th e*lemental」に
    当たる）。記号を落として 3 文字以下の短い入力は、名前の一部のときだけ弱い候補（0.6）にして、
    似かたの点では拾わない（拾うと「火花」が 1 字だけ共通の《苦花》に 0.5 で当たる）。
    """
    kn = _norm_name(key)
    if not kn:
        return 0.0
    short = len(kn) <= 3
    ascii_key = kn.isascii()
    kwords = _words(key) if ascii_key else []
    toks = ([w for w in kwords if len(w) >= 2] if ascii_key
            else [t for t in (_norm_name(x) for x in key.split()) if len(t) >= 2])
    best = 0.0
    for name in names:
        nn = _norm_name(name)
        if not nn:
            continue
        if kn == nn:
            return 1.0
        nwords = _words(name) if ascii_key else []
        inside = _has_run(nwords, kwords) if ascii_key else (len(kn) >= 2 and kn in nn)
        if inside:
            best = max(best, 0.6 if short else 0.9)
            continue
        if short:
            continue
        around = _has_run(kwords, nwords) if ascii_key else (len(nn) >= 2 and nn in kn)
        if around:
            best = max(best, 0.85)
            continue
        if len(toks) >= 2:
            frac = sum((t in nwords) if ascii_key else (t in nn) for t in toks) / len(toks)
            if frac:
                best = max(best, 0.5 + 0.3 * frac)
        best = max(best, difflib.SequenceMatcher(None, kn, nn).ratio())
    return best



def name_rank(key: str, en: str | None, ja: str | None, en_front: str | None, ja_front: str | None,
              en_back: str | None, ja_back: str | None) -> int | None:
    """入力 key がそのカードのどの名前に当たるか。0＝カード自身の名前・1＝表の面の名前・2＝裏の面の名前・None＝当たらない。

    英語は大小を無視・日本語は完全一致（draft_pack_stats と同じ決まり）。順位は DESIGN 12 と同じ
    （正式名 → 表面名 → 裏面名）。小さい方が強い＝「Lightning Bolt」は《稲妻/Lightning Bolt》（0）を、
    裏面がその名前の《対立の名誉教授/Emeritus of Conflict》（2）より先に取る。
    """
    low = key.lower()
    if low == (en or "").lower() or (ja is not None and key == ja):
        return 0
    if (en_front is not None and low == en_front.lower()) or (ja_front is not None and key == ja_front):
        return 1
    if (en_back is not None and low == en_back.lower()) or (ja_back is not None and key == ja_back):
        return 2
    return None


#: match_in_rows が読む行の鍵（limited.fetch_set_pool の行がそのまま渡る）
NAME_KEYS = ("card_name", "japanese_name", "name_en_front", "name_ja_front", "name_en_back", "name_ja_back")


def match_in_rows(key: str, rows: list[dict]) -> list[dict]:
    """rows（NAME_KEYS を持つ辞書）の中で key に当たる行のうち、いちばん強い順位の行だけを返す。

    1 行なら決まり・2 行以上なら曖昧（呼ぶ側が止める・推測で選ばない）・0 行なら当たらない。
    同じ card_name の行が二度あっても 1 行に数える。
    """
    best: int | None = None
    hits: dict[str, dict] = {}
    for r in rows:
        rank = name_rank(key, r.get("card_name"), r.get("japanese_name"), r.get("name_en_front"),
                         r.get("name_ja_front"), r.get("name_en_back"), r.get("name_ja_back"))
        if rank is None:
            continue
        if best is None or rank < best:
            best, hits = rank, {}
        if rank == best:
            hits.setdefault(r["card_name"], r)
    return list(hits.values())


def near_in_rows(key: str, rows: list[dict], limit: int = 3) -> tuple[list[dict], int]:
    """完全一致しなかった key に近い行（_pool_score が _POOL_MIN_SCORE 以上）を点の高い順に limit 件。

    戻り値は (行の一覧, 上限で切った所に残った同点の数)。候補は案内だけで、置き換えには使わない。
    """
    scored = sorted(((_pool_score(key, [r.get(k) for k in NAME_KEYS if r.get(k)]), r) for r in rows),
                    key=lambda t: (-t[0], t[1].get("card_name") or ""))
    ok = [(sc, r) for sc, r in scored if sc >= _POOL_MIN_SCORE]
    more = 0
    if len(ok) > limit:
        cut = ok[limit - 1][0]
        more = sum(1 for sc, _ in ok[limit:] if sc == cut)
    return [r for _, r in ok[:limit]], more
