"""manabase.py — マナ基盤の確率（mtg_probability の kind=castable・hand）の入口。

計算の本体は DB の SQL 関数（sql/prob_functions_mana.sql・PL/pgSQL・厳密）。ここはカード名を DB で引いて
「土地が何色を出すか」「呪文の色の記号」を構造化し、関数に渡す整数の配列を作るだけ。LLM に分解させない。

- castable: t ターン目までに、土地だけで呪文を唱えられる確率。唱えられる＝置ける土地 min(引いた土地, t) ≥ 必要な土地の数
  かつ色の記号 1 つにつき別々の土地を割り当てられる（Hall の条件）。タップイン・マナ・アーティファクト・ランプは入れない（前提に書く）。
- hand: 呼ぶ側が決めた互いに重ならない束が、それぞれ min〜max 枚に入る確率（キープの基準はデッキ次第なので呼ぶ側が決める）。
"""
import json
import re
from math import prod

from sisho import errors
from sisho.db import LANE_HEAVY, LANE_LIGHT, _db

BIT = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16, "C": 32}
_SYM = re.compile(r"\{([^}]+)\}")
MAX_LAND_ROWS = 20
MAX_TYPES = 6          # 色の記号の種類（SQL 側の Hall の部分集合は 2^種類）
MAX_TWOBRID = 3        # 単色混成 {2/G} の数（払い方の選択肢は 2^個）
MAX_WORK = 300_000     # 数え上げる組み合わせの概算（全ターン分の和）。公開サーバーの 2 コアを守る上限


def _mask(letters) -> int:
    m = 0
    for c in set(letters):          # ビットの OR（同じ色を 2 回書かれても別の色へ繰り上がらない）
        m |= BIT.get(c, 0)
    return m


def parse_cost(cost: str):
    """マナ・コスト → (色の記号のマスクの列, 汎用マナ, 単色混成のマスクの列, 注記)。対象外の記号は ValueError。"""
    pips, twobrid, notes, generic = [], [], [], 0
    rest = _SYM.sub("", cost or "").strip()
    if rest:   # 記号以外の文字が残っていたら黙って捨てない（"garbage"・"{1}{G}trailing" 等）
        raise ValueError(f"マナ・コストとして読めない文字がある（{rest!r}）。{{1}}{{G}} の形で渡す")
    if not _SYM.findall(cost or ""):
        raise ValueError("マナ・コストの記号が 1 つも無い（0 マナなら {0} と書く）")
    for s in _SYM.findall(cost or ""):
        s = s.upper()
        if s.isdigit():
            generic += int(s)
        elif s in ("X", "Y", "Z"):
            notes.append(f"{{{s}}} は 0 として数えた")
        elif s in BIT:
            pips.append(BIT[s])
        elif s == "S":
            raise ValueError("氷雪マナ {S} は対象外（氷雪の土地かどうかを判定しない）")
        elif s.endswith("/P") and all(c in "WUBRG" for c in s[:-2].split("/")) and 1 <= len(s[:-2].split("/")) <= 2:
            notes.append(f"{{{s}}} はライフで払う前提で土地を数えない")
        elif "/" in s:
            a, b = s.split("/", 1)
            if a == "2" and b in BIT:
                twobrid.append(BIT[b])
            elif a in BIT and b in BIT:
                pips.append(BIT[a] | BIT[b])
            else:
                raise ValueError(f"{{{s}}} は解釈できない記号")
        else:
            raise ValueError(f"{{{s}}} は解釈できない記号")
    return pips, generic, twobrid, notes


_COLS = ("card_name, name_display, type_line, produced_mana, mana_cost, card_faces_json,"
         " name_en_front, name_ja_front, name_en_back, name_ja_back, digital")


def _lookup(name: str):
    """カード名（正式名・日本語名・面の名前）を完全一致で引く。紙のカードを先に。戻り値 (行, 当たった面 0/1/None)。"""
    n = (name or "").strip()
    rows = _db(f"SELECT {_COLS} FROM mtg_cards_v2"
               " WHERE card_name = %s OR japanese_name = %s OR name_en_front = %s OR name_ja_front = %s"
               "    OR name_en_back = %s OR name_ja_back = %s"
               " ORDER BY digital, (card_name = %s OR name_en_front = %s OR name_ja_front = %s) DESC LIMIT 1",
               (n,) * 9, lane=LANE_LIGHT)
    if not rows:
        return None, None
    r = rows[0]
    if r[8] and n in (r[8], r[9]) and n not in (r[0], r[6], r[7]):
        return r, 1          # 裏面の名前で当たった
    if r[8] and n in (r[6], r[7]):
        return r, 0          # 両面カードの表面の名前で当たった
    return r, None


def _candidates(name: str) -> list[str]:
    rows = _db("SELECT name_display FROM mtg_cards_v2 WHERE card_name %% %s OR japanese_name %% %s"
               " ORDER BY greatest(similarity(card_name, %s), similarity(coalesce(japanese_name, ''), %s)) DESC LIMIT 3",
               (name, name, name, name))   # 重い線（similarity の並べ替えは索引に乗らないため）
    return [r[0] for r in rows]


def _no_match(name: str, what: str) -> str:
    c = _candidates(name)
    return errors.err_json(errors.NO_MATCH, f"{what}「{name}」が DB に無い（名前は一字違いでも当たらない）。"
                           + (f"近い候補: {'・'.join(c)}。正しい名前で呼び直す" if c else "英語名で呼び直す"),
                           candidates=c)


def _resolve_lands(lands):
    """lands の各行 → [(表示名, 枚数, 色のマスク, 色の一覧)]。誤りは err_json の文字列を返す。"""
    if not isinstance(lands, list) or not lands:
        return None, errors.err_json(errors.EMPTY_QUERY, "lands が空。[{\"name\": \"Forest\", \"count\": 9}, …] の形で土地を渡す")
    if len(lands) > MAX_LAND_ROWS:
        return None, errors.err_json(errors.OUT_OF_RANGE, f"lands は {MAX_LAND_ROWS} 行まで（同じ土地は 1 行にまとめる）")
    out = []
    for row in lands:
        if not isinstance(row, dict) or "name" not in row or "count" not in row:
            return None, errors.err_json(errors.OUT_OF_RANGE, "lands の各行は {\"name\": 土地の名前, \"count\": 枚数, \"produces\": 省略可} の形")
        name, cnt = str(row["name"]), row["count"]
        if not isinstance(cnt, int) or cnt < 0:
            return None, errors.err_json(errors.OUT_OF_RANGE, f"「{name}」の count は 0 以上の整数")
        r, _face = _lookup(name)
        if r is None:
            return None, _no_match(name, "土地")
        if "Land" not in (r[2] or ""):
            return None, errors.err_json(errors.OUT_OF_RANGE, f"{r[1]} は土地ではない（タイプ行: {r[2]}）。lands には土地だけを入れる")
        prod_given = row.get("produces")
        if prod_given is not None:
            letters = [str(x).upper() for x in (prod_given if isinstance(prod_given, list) else [prod_given])]
            if not letters or any(x not in BIT for x in letters):
                return None, errors.err_json(errors.OUT_OF_RANGE, f"「{name}」の produces は W・U・B・R・G・C の一覧（例: [\"G\", \"R\"]）")
            src = "指定"
        else:
            letters = list(r[3] or [])
            if not letters:
                return None, errors.err_json(
                    errors.OUT_OF_RANGE,
                    f"{r[1]} は DB に出る色が無い（フェッチランド等＝何が出るかはデッキの基本土地次第）。"
                    "推測しないので、この行に produces（例: [\"G\", \"U\"]）を付けて呼び直す")
            src = "DB"
        out.append({"name_display": r[1], "count": cnt, "mask": _mask(letters), "produces": sorted(set(letters), key="WUBRGC".index), "source": src})
    return out, None


def _resolve_spell(spell, mana_cost):
    if mana_cost:
        return None, str(mana_cost), None
    if not spell:
        return None, None, errors.err_json(errors.EMPTY_QUERY, "spell（呪文のカード名）か mana_cost（例: \"{1}{G}{G}\"）のどちらかを渡す")
    r, face = _lookup(str(spell))
    if r is None:
        return None, None, _no_match(str(spell), "呪文")
    cost = r[4] or ""
    faces = r[5] if isinstance(r[5], list) else (json.loads(r[5]) if r[5] else [])
    if face is not None and faces and len(faces) > face:
        cost = faces[face].get("mana_cost") or ""
        disp = r[1] if face == 0 else f"{r[1]} の裏面 {faces[face].get('name')}"
        if not cost:
            return None, None, errors.err_json(
                errors.OUT_OF_RANGE, f"{disp} は通常のマナ・コストを持たない（変身後の面など）＝土地で唱える確率は定義できない")
    elif not cost:
        # マナ・コストが無いカード（待機だけで唱える物など）を 0 マナとして扱わない（{0} とは別物）
        return None, None, errors.err_json(
            errors.OUT_OF_RANGE, f"{r[1]} は通常のマナ・コストを持たない＝通常の方法では唱えられないので、この道具の対象外")
    elif " // " in cost:
        return None, None, errors.err_json(errors.OUT_OF_RANGE, f"{r[1]} は面ごとにコストが違う。唱える面の名前で呼び直す（例: 表面か裏面の名前）")
    else:
        disp = r[1]
    return disp, cost, None


def castable(deck_size, lands, spell, mana_cost, turn, on_play, mulligans) -> str:
    resolved, err = _resolve_lands(lands)
    if err:
        return err
    total = sum(x["count"] for x in resolved)
    if total > deck_size:
        return errors.err_json(errors.OUT_OF_RANGE, f"土地の合計 {total} 枚が deck_size {deck_size} を超えている")
    disp, cost, err = _resolve_spell(spell, mana_cost)
    if err:
        return err
    try:
        pips, generic, twobrid, notes = parse_cost(cost)
    except ValueError as e:
        return errors.err_json(errors.OUT_OF_RANGE, f"マナ・コスト {cost}: {e}")
    if len(twobrid) > MAX_TWOBRID:
        return errors.err_json(errors.OUT_OF_RANGE, f"単色混成 {{2/色}} は {MAX_TWOBRID} 個まで")
    types = sorted(set(pips) | set(twobrid))
    if len(types) > MAX_TYPES:
        return errors.err_json(errors.OUT_OF_RANGE, f"色の記号の種類が多すぎる（{len(types)} 種・上限 {MAX_TYPES}）")
    # 払い方の選択肢（単色混成ごとに「色で払う／2 マナで払う」）
    need, mv = [], []
    for bits in range(1 << len(twobrid)):
        cnt = {t: pips.count(t) for t in types}
        g = generic
        for j, m in enumerate(twobrid):
            if (bits >> j) & 1:
                g += 2
            else:
                cnt[m] += 1
        need.append([cnt[t] for t in types] or [0])   # 色の記号が無いコスト（{3} 等）は種類 0 個＝ダミーの 1 列
        mv.append(g + sum(cnt.values()))
    # 土地の類: 払える記号の種類のビットで束ねる（どれも払えない土地は 1 類にまとめる）
    groups: dict[int, int] = {}
    for x in resolved:
        cp = sum(1 << i for i, t in enumerate(types) if x["mask"] & t)
        groups[cp] = groups.get(cp, 0) + x["count"]
    counts = [c for c in groups.values() if c > 0]
    canpay = [cp for cp, c in groups.items() if c > 0]
    seen_last = min(7 - mulligans + max(turn - (1 if on_play else 0), 0), deck_size)
    work = turn * prod(min(c, seen_last) + 1 for c in counts) * len(mv)
    if work > MAX_WORK:
        return errors.err_json(errors.OUT_OF_RANGE,
                               f"計算量の上限を超える（土地の類 {len(counts)} 種・ターン {turn}）。"
                               "払える色が同じ土地は 1 行にまとめるか、turn を小さくして呼び直す")
    try:
        rows = _db("SELECT t, mtg_castable(%s, %s, %s, %s, %s, t, %s, %s), mtg_cards_seen(t, %s, %s)"
                   " FROM generate_series(1, %s) t ORDER BY t",
                   (deck_size, counts or [], canpay or [], need, mv, on_play, mulligans, on_play, mulligans, turn),
                   lane=LANE_HEAVY)
    except Exception as e:
        return errors.err_json(errors.DB_ERROR, f"計算に失敗: {str(e)[:200]}（SQL 関数 mtg_castable が無い環境の可能性）")
    by_turn = {str(t): (round(float(p), 4) if p is not None else None) for t, p, _ in rows}
    p = rows[-1][1]
    if p is None:
        return errors.err_json(errors.OUT_OF_RANGE, "この入力では定義できない（土地の合計とデッキ枚数を確認）")
    p = float(p)
    seen = rows[-1][2]
    lands_needed = " または ".join(sorted({str(m) for m in mv}))
    premise = (f"{deck_size} 枚・{'先手' if on_play else '後手'}・{turn} ターン目（見る枚数 {seen}）・マリガン {mulligans} 回"
               "（初手は 7−マリガン枚として数えるだけ）・全部の土地がアンタップで出ると仮定・"
               "マナ・アーティファクトやマナ・クリーチャー・ランプ呪文・コスト軽減は数えない・土地は置く順を自由に選べる")
    if any(x["mask"] & 31 == 31 and x["source"] == "DB" for x in resolved):
        premise += "・5 色を出せる土地は DB の値のまま数えた（統率者の固有色に依存する土地も 5 色として数えている）"
    out = {"kind": "castable", "probability": round(p, 4), "percent": f"{p * 100:.1f}%",
           "spell": disp, "mana_cost": cost,
           "condition": (f"{turn} ターン目までに置ける土地 min(引いた土地, {turn}) ≥ {lands_needed}、かつ色の記号 1 つにつき"
                         "別々の土地を割り当てられる（2 色土地は 1 回に 1 色）"),
           "premise": premise, "cards_seen": seen,
           "lands_resolved": [{k: v for k, v in x.items() if k != "mask"} for x in resolved],
           "other_cards": deck_size - total,
           "by_turn": by_turn,
           "note": "多変量超幾何分布の厳密値（17Lands 等の実測ではない）。デッキ全体を無作為に切った前提。"
                   "呪文そのものを引いているかは問わない（引いていたら唱えられるか＝マナの問い）"}
    if notes:
        out["cost_notes"] = notes
    return json.dumps(out, ensure_ascii=False, indent=1)


def hand(deck_size, groups, turn, on_play, mulligans) -> str:
    if not isinstance(groups, list) or not groups:
        return errors.err_json(errors.EMPTY_QUERY, "groups が空。[{\"label\": \"土地\", \"count\": 17, \"min\": 2, \"max\": 4}, …] の形で束を渡す")
    if len(groups) > 6:
        return errors.err_json(errors.OUT_OF_RANGE, "groups は 6 個まで")
    labels, counts, lo, hi = [], [], [], []
    for g in groups:
        if not isinstance(g, dict) or any(k not in g for k in ("count", "min", "max")):
            return errors.err_json(errors.OUT_OF_RANGE, "groups の各行は {\"label\": 名前, \"count\": 枚数, \"min\": 下限, \"max\": 上限} の形")
        c, a, b = g["count"], g["min"], g["max"]
        if not all(isinstance(v, int) for v in (c, a, b)) or c < 0 or a < 0 or b < a:
            return errors.err_json(errors.OUT_OF_RANGE, f"束「{g.get('label', '?')}」: count・min・max は 0 以上の整数で min ≤ max")
        labels.append(str(g.get("label", f"束{len(labels) + 1}"))); counts.append(c); lo.append(a); hi.append(b)
    if sum(counts) > deck_size:
        return errors.err_json(errors.OUT_OF_RANGE, f"束の合計 {sum(counts)} 枚が deck_size {deck_size} を超えている（束は重ならないこと）")
    if not (0 <= turn <= 20):
        return errors.err_json(errors.OUT_OF_RANGE, "hand の turn は 0（初手だけ）〜20")
    seen_h = min(7 - mulligans + (max(turn - (1 if on_play else 0), 0) if turn > 0 else 0), deck_size)
    if prod(max(min(b, c, seen_h) - a, 0) + 1 for c, a, b in zip(counts, lo, hi)) > MAX_WORK:
        return errors.err_json(errors.OUT_OF_RANGE, f"計算量の上限を超える（束 {len(counts)} 個の範囲が広すぎる）。min〜max を狭めるか束を減らす")
    try:
        rows = _db("SELECT mtg_hand_prob(%s, %s, %s, %s, %s, %s, %s)",
                   (deck_size, counts, lo, hi, turn, on_play, mulligans), lane=LANE_LIGHT)
    except Exception as e:
        return errors.err_json(errors.DB_ERROR, f"計算に失敗: {str(e)[:200]}（SQL 関数 mtg_hand_prob が無い環境の可能性）")
    p = rows[0][0]
    if p is None:
        return errors.err_json(errors.OUT_OF_RANGE, "この入力では定義できない（枚数の整合を確認）")
    p = float(p)
    seen = min(7 - mulligans + (max(turn - (1 if on_play else 0), 0) if turn > 0 else 0), deck_size)
    when = "初手" if turn == 0 else f"{turn} ターン目まで（{'先手' if on_play else '後手'}）"
    out = {"kind": "hand", "probability": round(p, 4), "percent": f"{p * 100:.1f}%",
           "groups": [{"label": l, "count": c, "min": a, "max": b} for l, c, a, b in zip(labels, counts, lo, hi)],
           "other_cards": deck_size - sum(counts), "cards_seen": seen,
           "premise": f"{deck_size} 枚・{when}に見る {seen} 枚・マリガン {mulligans} 回・束は互いに重ならない前提（重なっていると誤った値になる）",
           "formula": "Σ Π C(count_i, x_i)·C(その他, 見る枚数−Σx_i) / C(deck_size, 見る枚数)（x_i は min_i〜max_i）",
           "note": "キープの基準は呼ぶ側が決めたもの。道具は確率を厳密に計算するだけ"}
    return json.dumps(out, ensure_ascii=False, indent=1)
