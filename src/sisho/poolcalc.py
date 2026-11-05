"""poolcalc.py — ドラフト・シールドのプールを数える（draft_pool の計算・純粋な関数で DB に触らない）。

受け取るのは limited.fetch_set_pool の行（カードの列＋17Lands の統計）で、返すのはカードごとの判定と集計。

物理のカード 1 枚はちょうど一つの区分に入る（保存則）:
  curve（カーブ）・lands（土地）・not_castable（この色では打てない）・unclassified（未分類・理由つき）。

形の選び方の決め事:
  - 準備呪文はクリーチャー面。分割・出来事・両面（呪文／呪文）は打てる面のうち小さい方。
  - 裏が土地の両面は呪文の面で、土地に数えない。変身と合体は表。
  - 試作は打てる方のうち小さい方。試作のコストは本文の頭にだけある（列が無い）。
  - マナ・コストの無い待機呪文は待機コスト。FIN の町の土地（表が土地・裏が出来事）は土地。
  - 色が渡されなければ、既知の記号だけの形は打てるとみなす。{C} は 6 番目の色。
  - 数えなかった形のマナ総量・打てるか・タイプも返す（読み替えは呼び出し側の LLM の材料）。

17Lands のデータにある layout は 11 種（実測: normal・transform・adventure・saga・prepare・
modal_dfc・split・class・prototype・meld・case）。それ以外（mutate・leveler・flip など）は未分類にする。
"""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

from sisho import manacost as mc

CURVE, LANDS, NOT_CASTABLE, UNCLASSIFIED = "curve", "lands", "not_castable", "unclassified"
BUCKETS = (CURVE, LANDS, NOT_CASTABLE, UNCLASSIFIED)

_SINGLE = {"normal", "saga", "class", "case", "meld"}      # トップの列だけで読む（meld は面の配列を持たない）
_TWO_SPELL = {"split", "adventure", "modal_dfc"}            # 打てる面のうち小さい方
_FACE_LABELS = {"split": ("左", "右"), "adventure": ("表", "出来事"), "modal_dfc": ("表", "裏")}

#: タイプの区分（type_line の「—」より前の語のうち、これに当たる物だけ数える）
CARD_TYPES = ("Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Battle", "Land", "Kindred")

# 本文の頭の書式（どちらも厳密に当てはまるときだけ読む・当てはまらなければ推測しない）
#   試作: カード表の 21 種すべてがこの形（本文の頭がこの形に厳密に当てはまるときだけ読む）。
#   待機: カード表で待機を持つ 71 種のうち 66 種が「Suspend N—{…}」・残り 5 種は「Suspend X—{X}…」でどれもマナ・コストを持つ
#   （実測）。マナ・コストが空のときだけ待機コストを探すので、X の待機は読まない
_PROTOTYPE = re.compile(r"^Prototype ((?:\{[^{}]+\})+) — (\d+)/(\d+)(?=[ (\n]|$)")
_SUSPEND = re.compile(r"(?:^|\n)Suspend (\d+)—((?:\{[^{}]+\})+)(?=[ (\n.]|$)")

MARK_CHANGES_WITH_COLORS = "色が決まると変わる"
MARK_OTHER_UNKNOWN = "別の形の判定ができない"
MARK_BACK_LAND = "裏が土地"
MARK_LAND_ADVENTURE = "裏が出来事の呪文（唱えることもできる・カーブには入れない）"
MARK_BOTH_LAND = "両面とも土地（出せる色は両方の面の和で、1 枚で同時に両方を出せる意味ではない）"
MARK_PROTOTYPE_UNREAD = "試作のコストを本文から読めない（通常のコストで数えた）"
MARK_PROTOTYPE_NO_NORMAL = "通常のコストが欠けている（試作のコストだけで数えた）"
SMALL_SAMPLE = 500          # 17Lands の試合数がこれ未満なら数字がぶれる（draft.py と同じ線）
MARK_SMALL_SAMPLE = "17Lands の試合数が 500 未満（数字がぶれる）"


def card_types(type_line: str | None) -> list[str]:
    """'Legendary Creature — Elf Druid' → ['Creature']。両面の 'A // B' は渡さない（面ごとに呼ぶ）。"""
    head = (type_line or "").split("—", 1)[0]
    # Tribal は Kindred の旧称。mtg_cards_v2 の type_line に Tribal は 0 件・Kindred は 78 件（実測）＝旧称が
    # 来ても同じ区分に数える備え
    words = set(head.replace("Tribal", "Kindred").split())
    return [t for t in CARD_TYPES if t in words]


def _is_land(type_line: str | None) -> bool:
    return "Land" in card_types(type_line)


class _Form:
    """数える候補の形（面・試作・待機など）。"""
    __slots__ = ("label", "cost", "type_line", "marks")

    def __init__(self, label: str, cost: str | None, type_line: str | None, marks: list[str] | None = None):
        self.label, self.cost, self.type_line, self.marks = label, cost, type_line, list(marks or [])


def _faces(row) -> list[dict] | None:
    f = row.get("card_faces")
    if isinstance(f, list) and len(f) >= 2 and all(isinstance(x, dict) for x in f):
        return f
    return None


def _spell_form(label: str, cost: str | None, type_line: str | None, oracle: str | None) -> _Form | str:
    """土地でない面の形。コストが空なら待機コストを探し、無ければ理由の文字列を返す（{0} と違う・総合ルール 118.6）。"""
    if cost:
        return _Form(label, cost, type_line)
    m = _SUSPEND.search(oracle or "")
    if m:
        return _Form("待機", m.group(2), type_line, [f"待機 {m.group(1)}"])
    return "マナ・コストが無い（待機でもない＝通常の方法では唱えられない）"


def forms_of(row) -> dict:
    """カードを (kind, …) に。kind は 'land'・'forms'・'unclassified'。

    'forms' のとき candidates（選ぶ候補）・others（数えない形＝返すだけ）・marks・type_forms（タイプを数える形）を持つ。
    """
    lay = row.get("layout")
    tl = row.get("type_line")
    if lay in _SINGLE:
        if _is_land(tl):
            return {"kind": "land", "produces": list(row.get("produced_mana") or []), "marks": [], "types": card_types(tl)}
        f = _spell_form("表", row.get("mana_cost"), tl, row.get("oracle_text"))
        if isinstance(f, str):
            return {"kind": "unclassified", "reason": f, "types": card_types(tl)}
        return {"kind": "forms", "candidates": [f], "others": [], "marks": [], "type_forms": [f]}
    if lay == "prototype":
        if _is_land(tl):
            return {"kind": "unclassified", "reason": "未対応の形（土地の試作）", "types": card_types(tl)}
        # 通常のコストも空かを確かめる（空を 0 マナの形にしない）
        normal = _spell_form("通常のコスト", row.get("mana_cost"), tl, None)
        m = _PROTOTYPE.match(row.get("oracle_text") or "")
        if isinstance(normal, str):
            if not m:
                return {"kind": "unclassified", "reason": "データ欠け（試作の通常のコストが無く、試作のコストも本文から読めない）",
                        "types": card_types(tl)}
            proto = _Form("試作", m.group(1), tl)
            # incomplete: 試作で打てなくても「打てない」と断定しない（通常のコストが分からない）
            return {"kind": "forms", "candidates": [proto], "marks": [MARK_PROTOTYPE_NO_NORMAL], "type_forms": [proto],
                    "incomplete": "通常のコストが欠けていて、試作のコストでは打てない（打てるかを確定できない）",
                    "others": [{"form": "通常のコスト", "castable": mc.UNKNOWN, "note": "データ欠け（通常のコストが無い）"}]}
        if not m:
            return {"kind": "forms", "candidates": [normal], "others": [], "marks": [MARK_PROTOTYPE_UNREAD],
                    "type_forms": [normal]}
        proto = _Form("試作", m.group(1), tl)
        return {"kind": "forms", "candidates": [proto, normal], "others": [], "marks": [], "type_forms": [normal]}
    faces = _faces(row)
    if lay in ("prepare", "transform") or lay in _TWO_SPELL:
        if faces is None:
            return {"kind": "unclassified", "reason": f"データ欠け（{lay} なのに面の配列が無い）",
                    "types": card_types((tl or "").split("//")[0])}
        f0, f1 = faces[0], faces[1]
        t0, t1 = f0.get("type_line"), f1.get("type_line")
        l0, l1 = _is_land(t0), _is_land(t1)
        if lay in ("prepare", "transform"):
            if l0:   # 表が土地の変身カードも、裏の面を返す（区分にかかわらず材料を返す）
                return {"kind": "land", "produces": list(row.get("produced_mana") or []), "marks": [],
                        "types": card_types(t0),
                        "others": [{"form": "裏", "type_line": t1, "note": "唱えない面（変身で表になる）"}]}
            front = _spell_form("クリーチャー面" if lay == "prepare" else "表", f0.get("mana_cost"), t0,
                                f0.get("oracle_text"))
            if isinstance(front, str):
                return {"kind": "unclassified", "reason": front, "types": card_types(t0)}
            if lay == "prepare":
                back = _spell_form("呪文の面", f1.get("mana_cost"), t1, f1.get("oracle_text"))
                others = [back] if isinstance(back, _Form) else [{"form": "呪文の面", "type_line": t1, "note": back}]
            else:
                others = [{"form": "裏", "type_line": t1, "note": "唱えない面（変身で表になる）"}]
            return {"kind": "forms", "candidates": [front], "others": others, "marks": [], "type_forms": [front]}
        # split・adventure・modal_dfc（土地を先に判定する）
        a, b = _FACE_LABELS[lay]
        if l0 and l1:
            return {"kind": "land", "produces": list(row.get("produced_mana") or []), "marks": [MARK_BOTH_LAND],
                    "types": card_types(t0)}
        if l0:
            # FIN の町の土地（表が Land — Town・裏が出来事の呪文・5 種）は土地として数える。
            # ほかの「表が土地・裏が呪文」の組み合わせは未分類のまま（17Lands のデータには無い・推測しない）
            if lay == "adventure":
                f_b = _spell_form(b, f1.get("mana_cost"), t1, f1.get("oracle_text"))
                back = f_b if isinstance(f_b, _Form) else {"form": b, "type_line": t1, "note": f_b}
                return {"kind": "land", "produces": list(row.get("produced_mana") or []), "marks": [MARK_LAND_ADVENTURE],
                        "types": card_types(t0), "others": [back]}
            return {"kind": "unclassified", "reason": "未対応の形（表が土地・裏が呪文の両面）", "types": card_types(t0)}
        f_a = _spell_form(a, f0.get("mana_cost"), t0, f0.get("oracle_text"))
        if l1:
            if isinstance(f_a, str):
                return {"kind": "unclassified", "reason": f_a, "types": card_types(t0)}
            return {"kind": "forms", "candidates": [f_a], "marks": [MARK_BACK_LAND], "type_forms": [f_a],
                    "others": [{"form": "裏（土地）", "type_line": t1, "note": "土地の枚数には数えない"}]}
        f_b = _spell_form(b, f1.get("mana_cost"), t1, f1.get("oracle_text"))
        cands = [f for f in (f_a, f_b) if isinstance(f, _Form)]
        if not cands:
            return {"kind": "unclassified", "reason": f_a, "types": card_types(t0) + card_types(t1)}
        others = [{"form": lab, "note": why} for lab, why in ((a, f_a), (b, f_b)) if isinstance(why, str)]
        return {"kind": "forms", "candidates": cands, "others": others, "marks": [], "type_forms": cands}
    return {"kind": "unclassified", "reason": f"未対応の形（{lay}）", "types": card_types((tl or "").split("//")[0])}


def _eval(form: _Form, deck) -> dict:
    state, reason, marks = mc.payable(form.cost, deck)
    out = {"form": form.label, "mana_value": mc.mana_value(form.cost), "type_line": form.type_line,
           "castable": state, "marks": marks + form.marks}
    if reason and state not in (mc.TRUE, mc.NOT_JUDGED):   # 「色が渡されていない」は analysis に 1 回だけ書く
        out["reason"] = reason
    return out


def classify(row, deck) -> dict:
    """カード 1 枚の判定。deck は manacost.parse_colors の結果（None＝色が渡されていない）。"""
    fo = forms_of(row)
    base = {"card_name": row["card_name"], "card": row.get("name_display") or row["card_name"]}
    if fo["kind"] == "land":
        out = dict(base, bucket=LANDS, produces=fo["produces"], marks=fo["marks"], types=fo["types"])
        others = [{k: v for k, v in o.items() if k != "marks" or v}
                  for o in (_eval(x, deck) if isinstance(x, _Form) else x for x in fo.get("others", []))]
        if others:
            out["other_faces"] = others
        return out
    if fo["kind"] == "unclassified":
        return dict(base, bucket=UNCLASSIFIED, reason=fo["reason"], marks=[], types=fo["types"])
    evals = [(f, _eval(f, deck)) for f in fo["candidates"]]
    usable = [(f, e) for f, e in evals
              if e["castable"] in (mc.TRUE, mc.NOT_JUDGED) and e["mana_value"] is not None]
    unknown = [e for _, e in evals if e["castable"] == mc.UNKNOWN or e["mana_value"] is None]
    types = sorted({t for f in fo["type_forms"] for t in card_types(f.type_line)}, key=CARD_TYPES.index)
    marks = list(fo["marks"])
    # 数える対象にしない形（準備呪文の呪文の面・変身の裏・呪文／土地の土地の面）は、区分が何であっても返す
    # （打てない形のときも落とさない）
    others = [{k: v for k, v in o.items() if k != "marks" or v}
              for o in (_eval(x, deck) if isinstance(x, _Form) else x for x in fo["others"])]
    if not usable:
        extra = {"forms": [e for _, e in evals]} | ({"other_faces": others} if others else {})
        if unknown or fo.get("incomplete"):
            why = unknown[0].get("reason") if unknown else fo["incomplete"]
            return dict(base, bucket=UNCLASSIFIED, reason=why or "判定できない", marks=marks, types=types, **extra)
        why = "・".join(dict.fromkeys(e.get("reason") or "" for _, e in evals if e.get("reason")))
        return dict(base, bucket=NOT_CASTABLE, reason=why or "この色では打てない", marks=marks, types=types, **extra)
    chosen_f, chosen = min(usable, key=lambda fe: fe[1]["mana_value"])   # 同じ点数なら先（左・表）
    for m in chosen["marks"]:
        if m not in marks:
            marks.append(m)
    if unknown:
        marks.append(MARK_OTHER_UNKNOWN)
    if deck is None and len(evals) >= 2:
        marks.append(MARK_CHANGES_WITH_COLORS)
    single, hybrid = mc.colors_needed(chosen_f.cost)
    other = [e for f, e in evals if f is not chosen_f] + others
    out = dict(base, bucket=CURVE, counted_form=chosen["form"], mana_value=chosen["mana_value"],
               castable=chosen["castable"], colors_needed=single, hybrid_needed=hybrid, marks=marks, types=types)
    if other:
        out["other_faces"] = [{k: v for k, v in o.items() if k != "marks" or v} for o in other]
    return out


def roles_of(row) -> list[str]:
    """役割（物理の 1 枚につき各 1 回・プール全体＝色で絞らない）。"""
    out = []
    if row.get("removal"):
        out.append("removal")
    if (row.get("draw_count") or 0) >= 1 or row.get("draw_x"):
        out.append("draw")
    if row.get("tutor"):
        out.append("tutor")
    return out


def _curve_key(v: int) -> str:
    # 0〜6 と 7 以上の 8 個（マナカーブの 8 個の数を毎回返す）
    return "7+" if v >= 7 else str(v)


def summarize(cards: list[tuple[dict, dict]]) -> dict:
    """[(行, classify の結果)]（物理のカード 1 枚につき 1 組・同じカード 2 枚なら 2 組）を集計する。"""
    buckets = Counter()
    curve = {str(i): 0 for i in range(7)}
    curve["7+"] = 0
    lands, not_castable, unclassified = [], [], []
    colors = {ch: [] for ch in mc.COLOR_LETTERS}
    hybrid: dict[str, list[str]] = {}
    types = Counter()
    roles = {"removal": [], "draw": [], "tutor": []}
    for row, res in cards:
        b = res["bucket"]
        buckets[b] += 1
        name = res["card"]
        for t in res.get("types") or []:
            types[t] += 1
        for r in roles_of(row):
            roles[r].append(name)
        if b == CURVE:
            curve[_curve_key(res["mana_value"])] += 1
            for ch in res.get("colors_needed") or []:
                colors[ch].append(name)
            for h in res.get("hybrid_needed") or []:
                hybrid.setdefault(h, []).append(name)
        elif b == LANDS:
            lands.append({"card": name, "produces": res.get("produces") or []}
                         | ({"marks": res["marks"]} if res.get("marks") else {})
                         | ({"other_faces": res["other_faces"]} if res.get("other_faces") else {}))
        elif b == NOT_CASTABLE:
            not_castable.append({"card": name, "reason": res.get("reason")})
        else:
            unclassified.append({"card": name, "reason": res.get("reason")})
    total = sum(buckets.values())
    out = {
        "buckets": {b: buckets.get(b, 0) for b in BUCKETS} | {"total": total},
        "curve": curve,
        "lands": {"count": len(lands), "cards": lands},
        "not_castable": not_castable,
        "unclassified": unclassified,
        "colors": {ch: {"count": len(v), "cards": v} for ch, v in colors.items() if v}
                  | ({"hybrid": {h: {"count": len(v), "cards": v} for h, v in hybrid.items()}} if hybrid else {}),
        "types": {t: types[t] for t in CARD_TYPES if types.get(t)} | {"scope": "プール全体（色で絞っていない）"},
        "roles": {"scope": "プール全体（色で絞っていない・どの面の効果かは列から確定できない）"}
                 | {k: {"count": len(v), "cards": v} for k, v in roles.items()},
    }
    return out


def stats_of(row) -> dict:
    """17Lands の統計（足さない・平均しない）。試合数 500 未満に印。"""
    out = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in
           ((k, row.get(k)) for k in ("gih_wr", "gih_games", "alsa", "ata"))}   # numeric は Decimal で来る（JSON に載せる）
    g = row.get("gih_games")
    if g is not None and g < SMALL_SAMPLE:
        out["stats_note"] = MARK_SMALL_SAMPLE
    return out
