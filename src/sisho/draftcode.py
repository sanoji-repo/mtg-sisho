"""draftcode.py — ドラフトコードの読み書きとピックの位置の決まり（draft_pool・純粋な関数で DB に触らない）。

コードはサーバーが持たない状態を利用者の側で持ち回るための文字列:
  ドラフト  SD1|<セット>|<枚数>|<CRC32>|<正式名 1>|…   並びがピックの順（n 番目＝パック (n-1)÷14+1・ピック (n-1)%14+1）
  シールド  SS1|<セット>|<枚数>|<CRC32>|<正式名>|…     正式名をコードポイント順に（同名の重複は残す）
            並びが正規でない SS1 は止める（NOT_CANONICAL）＝照会で同じコードを返すため・黙って並べ替えて通さない
書き方は英語の正式名（カード表の番号や 17Lands の一覧の番号は、DB の改名や一覧の追加で
黙って別のカードに化ける）。CRC32 は写し間違いを見つけるための物で、改ざんを防ぐ目的は無い（秘密の鍵を使わない）。

読むときは空白を削らず Unicode も正規化しない（写し間違いを隠さない）。
正式名の照合は呼ぶ側（draft_pool）が、いまのセットの一覧に対して完全一致だけで行う。
"""
from __future__ import annotations

import re
import zlib

DRAFT, SEALED = "SD1", "SS1"
PACKS, PACK_SIZE = 3, 14
MAX_PICKS = PACKS * PACK_SIZE          # 42＝3 パック × 14 枚
MAX_SEALED = 100                       # シールドのプールの上限（cards は 100 件）
MAX_CODE_LEN = 6000                    # 返す前にも検査する＝次の呼び出しで読めないコードを作らない。42 枚で 800〜880 字が目安
MAX_NAME_LEN = 200
SEP = "|"

# invalid_code の理由
MALFORMED = "malformed"
CHECKSUM_MISMATCH = "checksum_mismatch"
UNSUPPORTED_VERSION = "unsupported_version"
COUNT_OUT_OF_RANGE = "count_out_of_range"
NOT_CANONICAL = "not_canonical"

_SET_RE = re.compile(r"^[A-Za-z0-9_\-]{2,40}$")
_COUNT_RE = re.compile(r"^(?:0|[1-9][0-9]{0,3})$")
_CRC_RE = re.compile(r"^[0-9a-f]{8}$")
_VERSION_LIKE = re.compile(r"^S[DS][0-9]+$")


class CodeError(ValueError):
    """コードが読めない。reason は上の 5 つのどれか。message は利用者向け（コードの中身は繰り返さない）。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class PositionError(ValueError):
    """ピックの位置の決まりに合わない。detail は返り値にそのまま載せる（位置と正式名だけ）。"""

    def __init__(self, message: str, **detail):
        super().__init__(message)
        self.message = message
        self.detail = detail


def position(pack: int, pick: int) -> int:
    """(パック, ピック) → 通しの位置 1〜42。"""
    return PACK_SIZE * (pack - 1) + pick


def pack_pick(i: int) -> dict:
    """通しの位置 → {"pack": …, "pick": …}。"""
    return {"pack": (i - 1) // PACK_SIZE + 1, "pick": (i - 1) % PACK_SIZE + 1}


def _crc(fields: list[str]) -> str:
    return format(zlib.crc32(SEP.join(fields).encode("utf-8")) & 0xFFFFFFFF, "08x")


def _check_name(n: str) -> None:
    # 区切りの | と制御文字を名前に許さない理由: DB の全カードで正式名に | を含むのは 0 件。将来来ても、
    # 黙って分けずにこの版では未対応として止める
    if not n:
        raise CodeError(MALFORMED, "コードに空の名前がある")
    if len(n) > MAX_NAME_LEN:
        raise CodeError(MALFORMED, f"コードの名前が長すぎる（{MAX_NAME_LEN} 字まで）")
    if SEP in n or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in n):
        raise CodeError(MALFORMED, "コードの名前に区切りか制御文字（改行など）が入っている")


def encode(version: str, set_code: str, names: list[str]) -> str:
    """コードを作る。シールドは正規の順に並べ替える。返すコードが上限を超えるなら CodeError（全体を止める）。"""
    if version not in (DRAFT, SEALED) or not _SET_RE.fullmatch(set_code or ""):
        raise CodeError(MALFORMED, "コードの版かセットの形が違う（作らない）")
    hi = MAX_PICKS if version == DRAFT else MAX_SEALED
    if not 1 <= len(names) <= hi:
        raise CodeError(COUNT_OUT_OF_RANGE, f"{version} の枚数は 1〜{hi}（読めないコードを作らない）")
    if version == SEALED:
        names = sorted(names)
    for n in names:
        _check_name(n)
    head = [version, set_code, str(len(names))]
    code = SEP.join(head + [_crc(head + names)] + names)
    if len(code) > MAX_CODE_LEN:
        raise CodeError(MALFORMED, f"コードが {MAX_CODE_LEN} 字を超える（次の呼び出しで読めなくなるので作らない）")
    return code


def decode(code) -> tuple[str, str, list[str]]:
    """コードを (版, セット, 正式名の並び) に。読めなければ CodeError。セットの照合は呼ぶ側。"""
    if not isinstance(code, str) or not code:
        raise CodeError(MALFORMED, "code が文字列でないか空")
    if len(code) > MAX_CODE_LEN:
        raise CodeError(MALFORMED, f"code が長すぎる（{MAX_CODE_LEN} 字まで）")
    parts = code.split(SEP)
    if len(parts) < 4:
        raise CodeError(MALFORMED, "code の欄が足りない（版・セット・枚数・検査の印・名前の順）")
    version, set_code, count_s, crc = parts[0], parts[1], parts[2], parts[3]
    names = parts[4:]
    if version not in (DRAFT, SEALED):
        if _VERSION_LIKE.fullmatch(version):
            raise CodeError(UNSUPPORTED_VERSION, f"この道具は {DRAFT}（ドラフト）と {SEALED}（シールド）の形だけを読める")
        raise CodeError(MALFORMED, "code の先頭が SD1 か SS1 でない")
    # 欄はどれも fullmatch で見る（re の $ は末尾の改行の手前にも当たる＝"1\n" が通ってしまう）
    if not _SET_RE.fullmatch(set_code):
        raise CodeError(MALFORMED, "code のセットの欄の形が違う")
    if not _COUNT_RE.fullmatch(count_s):
        raise CodeError(MALFORMED, "code の枚数の欄の形が違う（先頭のゼロ・符号の無い半角の数字）")
    if not _CRC_RE.fullmatch(crc):
        raise CodeError(MALFORMED, "code の検査の印の形が違う（小文字の 16 進 8 字）")
    if int(count_s) != len(names):
        raise CodeError(MALFORMED, "code の枚数と名前の数が違う（途中で切れたか、区切りが欠けた）")
    for n in names:
        _check_name(n)
    if _crc([version, set_code, count_s] + names) != crc:
        raise CodeError(CHECKSUM_MISMATCH, "code の検査の印が中身と合わない（写すときに変わった）")
    hi = MAX_PICKS if version == DRAFT else MAX_SEALED
    if not 1 <= len(names) <= hi:
        raise CodeError(COUNT_OUT_OF_RANGE, f"{version} の枚数は 1〜{hi}")
    if version == SEALED and names != sorted(names):
        raise CodeError(NOT_CANONICAL, "SS1 の名前の並びが正規の順でない（道具が返したコードをそのまま渡す）")
    return version, set_code, names


def apply_picks(old: list[str], picks: list[tuple[int, str]], correct: bool) -> dict:
    """位置の決まりを当てて新しい並びを作る。合わなければ PositionError（何も作らない）。

    old は元のコードの正式名の並び（コード無しなら []）。picks は (通しの位置, 照合済みの正式名)。
    戻り値は {"names", "added", "resent", "corrected", "unchanged"}（位置は通しの番号・1 回ずつ）。
    """
    n = len(old)
    by_pos: dict[int, str] = {}
    for i, name in picks:
        if i in by_pos and by_pos[i] != name:
            raise PositionError("同じ位置に違うカードが来た（一回の呼び出しの中）",
                                position=pack_pick(i), cards=[by_pos[i], name])
        by_pos[i] = name
    new = list(old)
    added, resent, corrected, unchanged = [], [], [], []
    if correct:
        if n == 0:
            raise PositionError("訂正の印は、コードを渡したときだけ使える")
        outside = sorted(i for i in by_pos if i > n)
        if outside:
            raise PositionError("訂正と追加は分けて呼ぶ（訂正の印のときは、既にある位置だけ）",
                                new_positions=[pack_pick(i) for i in outside])
        for i in sorted(by_pos):
            if new[i - 1] == by_pos[i]:
                unchanged.append(i)
            else:
                corrected.append({"position": i, "before": new[i - 1], "after": by_pos[i]})
                new[i - 1] = by_pos[i]
    else:
        for i in sorted(by_pos):
            if i <= n:
                if old[i - 1] != by_pos[i]:
                    raise PositionError("既にある位置に違うカードが来た（訂正なら correct=true）",
                                        position=pack_pick(i), recorded=old[i - 1], received=by_pos[i])
                resent.append(i)
        tail = sorted(i for i in by_pos if i > n)
        if tail:
            if n >= MAX_PICKS:
                raise PositionError(f"ドラフトは {MAX_PICKS} ピックで終わっている（追加はできない）")
            expect = list(range(n + 1, n + 1 + len(tail)))
            if tail != expect:
                missing = [i for i in range(n + 1, max(tail) + 1) if i not in by_pos]
                raise PositionError("ピックの位置が飛んでいる（抜けている位置を先に足す）",
                                    expected_next=pack_pick(n + 1), missing=[pack_pick(i) for i in missing])
            for i in tail:
                new.append(by_pos[i])
                added.append(i)
    return {"names": new, "added": added, "resent": resent, "corrected": corrected, "unchanged": unchanged}
