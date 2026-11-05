"""manacost.py — マナ・コストの記号を読む（draft_pool 用）。

manabase.parse_cost とは別の物。あちらはマナベースの要求（色の源の枚数）を数えるための物で、
{B/P}・{W/U}・{2/W}・NULL・分割のコストで意味が変わるか止まる。ここは
  1. 点数（マナ総量）を読む mana_value
  2. 指定のデッキの色で払えるかを判定する payable
の二つに分ける。純粋な関数で DB に触らない。

決め事:
  - {C}（無色マナ限定）は 6 番目の色として扱う＝デッキの色に C があるときだけ払える。
  - ファイレクシア・マナ（{B/P}・混成の {G/W/P} も）はその色が無くても払える＝「ライフで払う」の印。
  - 知らない記号（{S}・{Y}・半マナなど）と記号の外の文字は「判定できない」（推測しない）。
    17Lands のデータの面のコストには {S}・{Y}・{Z}・半マナは無い（実測）。

空のコスト（None・""）は {0} と違う（総合ルール 118.6）。ここでは「記号が 0 個」とだけ返し、
唱えられるかどうかの意味づけ（待機・土地・通常の方法では唱えられない）は呼ぶ側（poolcalc）が決める。
"""
from __future__ import annotations

import re

#: デッキの色に書ける文字（C は 6 番目の色）
COLOR_LETTERS = "WUBRGC"
_FIVE = frozenset("WUBRG")
_SIX = frozenset(COLOR_LETTERS)

_TOKEN = re.compile(r"\{([^{}]*)\}")

# 状態の名前（返り値の castable にそのまま載る）
TRUE = "true"            # 払える
FALSE = "false"          # 払えない（足りない色がある）
UNKNOWN = "unknown"      # 判定できない（知らない記号・記号の外の文字）
NOT_JUDGED = "not_judged"  # 色が渡されていないので判定していない（既知の記号だけでできている）

MARK_X = "X を含む"
MARK_PHYREXIAN = "ライフで払う"


def tokens(cost: str | None) -> list[str] | None:
    """'{2}{W/U}' → ['2', 'W/U']。None と '' は []。記号の外に文字があれば None（判定できない）。"""
    if cost is None or cost == "":
        return []
    out: list[str] = []
    pos = 0
    for m in _TOKEN.finditer(cost):
        if m.start() != pos:
            return None
        out.append(m.group(1))
        pos = m.end()
    if pos != len(cost):
        return None
    return out


def classify_symbol(sym: str) -> tuple[int, frozenset | None, str | None] | None:
    """記号 1 つを (点数, 払うのに要る色の集まり, 印) に。要る色が None なら常に払える。知らない記号は None。

    要る色の集まりは「どれか 1 つがデッキにあれば払える」の意味（単色は 1 要素・混成は 2 要素）。
    """
    if sym.isdigit():
        return int(sym), None, None
    if sym == "X":
        return 0, None, MARK_X
    if sym in _SIX:
        return 1, frozenset(sym), None
    parts = sym.split("/")
    if len(parts) == 2:
        a, b = parts
        if b == "P" and a in _FIVE:                      # 単色のファイレクシア {B/P}
            return 1, None, MARK_PHYREXIAN
        if a == "2" and b in _FIVE:                      # 2 で払える混成 {2/W}
            return 2, None, None
        if a in _SIX and b in _SIX and a != b:           # 混成 {W/U}・無色との混成 {C/W}
            return 1, frozenset((a, b)), None
        return None
    if len(parts) == 3 and parts[2] == "P" and parts[0] in _FIVE and parts[1] in _FIVE and parts[0] != parts[1]:
        return 1, None, MARK_PHYREXIAN                    # 混成のファイレクシア {G/W/P}（単色の場合と同じ扱い）
    return None


def mana_value(cost: str | None) -> int | None:
    """点数（マナ総量）。知らない記号・記号の外の文字があれば None。空のコストは 0（意味づけは呼ぶ側）。"""
    toks = tokens(cost)
    if toks is None:
        return None
    total = 0
    for t in toks:
        c = classify_symbol(t)
        if c is None:
            return None
        total += c[0]
    return total


def payable(cost: str | None, deck: frozenset | None) -> tuple[str, str | None, list[str]]:
    """(状態, 理由, 印)。deck は parse_colors の結果（None＝色が渡されていない）。

    状態は TRUE・FALSE・UNKNOWN・NOT_JUDGED。UNKNOWN は deck の有無にかかわらず先に返す
    （色が渡されないときも、知らない記号まで「打てる」とはみなさない）。
    """
    toks = tokens(cost)
    if toks is None:
        return UNKNOWN, "マナ・コストに記号の外の文字がある", []
    marks: list[str] = []
    needs: list[frozenset] = []
    for t in toks:
        c = classify_symbol(t)
        if c is None:
            return UNKNOWN, f"判定できない記号 {{{t}}}", []
        _, need, mark = c
        if mark and mark not in marks:
            marks.append(mark)
        if need is not None:
            needs.append(need)
    if deck is None:
        return NOT_JUDGED, "デッキの色が渡されていない", marks
    missing = sorted({"/".join(sorted(n, key=COLOR_LETTERS.index)) for n in needs if not (n & deck)},
                     key=lambda s: COLOR_LETTERS.index(s[0]))
    if missing:
        return FALSE, "・".join(missing) + " が無い", marks
    return TRUE, None, marks


def colors_needed(cost: str | None) -> tuple[list[str], list[str]]:
    """(必須の色, 混成の組)。必須は単色の記号の色（C を含む）、混成は 'W か U' の形。

    {2/W} とファイレクシア・マナは払い方の選択肢なので、どちらにも入れない（「必須の白」と書かない）。
    同じ色の記号が 2 つあっても 1 回だけ（カード枚数で数えるため）。知らない記号は飛ばす。
    """
    toks = tokens(cost) or []
    single: set[str] = set()
    hybrid: set[tuple[str, ...]] = set()
    for t in toks:
        c = classify_symbol(t)
        if c is None or c[1] is None:
            continue
        need = c[1]
        if len(need) == 1:
            single |= need
        else:
            hybrid.add(tuple(sorted(need, key=COLOR_LETTERS.index)))
    return (sorted(single, key=COLOR_LETTERS.index),
            [" か ".join(h) for h in sorted(hybrid, key=lambda h: [COLOR_LETTERS.index(x) for x in h])])


def parse_colors(s) -> frozenset | None:
    """デッキの色の入力を集まりに。None は None（未決）。不正なら ValueError（文は利用者向け）。

    文字は W・U・B・R・G・C（大小を問わない）。空白と重複は無視。空（空白だけを含む）は止める
    （「色を決めない」と「無色だけ」を取り違えないため・無色だけのデッキは C）。
    """
    if s is None:
        return None
    if not isinstance(s, str):
        raise ValueError("colors は文字列で渡す（例 \"UR\"・無色マナを含めるなら C を足す）")
    letters = "".join(s.split()).upper()
    if not letters:
        raise ValueError("colors が空。色を決めていないなら colors を省く。無色だけのデッキなら \"C\"")
    bad = sorted({ch for ch in letters if ch not in _SIX})
    if bad:
        raise ValueError(f"colors に使えない文字 {''.join(bad)}。使えるのは W・U・B・R・G・C")
    return frozenset(letters)


def colors_label(deck: frozenset | None) -> str | None:
    """集まりを WUBRGC の順の文字列に（返り値の analysis.colors 用）。"""
    if deck is None:
        return None
    return "".join(ch for ch in COLOR_LETTERS if ch in deck)
