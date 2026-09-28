"""probvary.py — mtg_probability の vary（数字を 1〜3 つ振って 1 回で比べる）。

要点:
  - 振れる数字は kind ごとに、その計算に使う数字だけ（使わない数字を振ると同じ確率が並ぶ偽の比較になる）。
  - 振れる数字は 3 つまで。組み合わせは全部の掛け算で、全部で VARY_MAX 通りまで（公開サーバーの計算能力の限界として実測から引いた線）。
  - 値は整数だけ（本体で type(x) is int を見る＝入口の pydantic に黙って直させない）。重複は初出順にまとめ、どの数字も 2 個以上。
  - 全部の組み合わせを代入して検査し、1 つでも破れたら表を返さない（一部の行だけ返すと欠けた行に気づかず比べてしまう）。
  - DB は 1 本の SELECT（数字ごとの unnest WITH ORDINALITY を掛け合わせる）で、確率と「計算に実際に使った見る枚数」を同じ列で返す。重いレーン。
  - 返り値は fixed（全行に共通の条件）・rows（組み合わせごとの結果と数字を入れた式）・table（前提の一文＋表）。
    表は 1 変数なら縦・2 変数なら行×列・3 変数なら 3 つ目の値ごとに行×列の表を分ける。
"""
import itertools
import json

from sisho import errors
from sisho.db import LANE_HEAVY, DBBusy, _db

#: kind → 振れる数字（その計算に使う数字だけ）
VARY_ARGS: dict[str, tuple[str, ...]] = {
    "at_least": ("deck_size", "copies", "draws", "at_least"),
    "by_turn": ("deck_size", "copies", "turn", "at_least", "mulligans"),
    "land_drops": ("deck_size", "copies", "turn", "mulligans"),
    "combo_by_turn": ("deck_size", "copies", "copies_b", "turn", "mulligans"),
}
#: 全部で何通り計算するかの上限（公開サーバーの計算能力の限界として実測から引いた線）。
#: 現実の問い（機知の戦いの 250〜300 枚のデッキを含む）は 50 通りで数十 ms。
#: 重いのは at_least で引く枚数と当たりが両方数百の入力だけ（公開サーバーで 500 枚・250 枚・50 通り 1.7 秒・重いレーンの 10 秒には届かない）。
VARY_MAX = 50
#: 同時に振れる数字の数（3 まで・実際に使われるかを見て決める）
VARY_DIMS = 3

#: 引数 vary の説明（MCP の引数の形に載る）
VARY_DESCRIPTION = (
    "数字を振って 1 回で比べる: {引数名: [整数, …]}。振れるのは 3 つまで・組み合わせは全部の掛け算で全部で 50 通りまで"
    "（重複はまとめる・渡した順に返す）。2 つなら行×列の表・3 つ目は値ごとに表を分ける。"
    "振れる引数: at_least=deck_size/copies/draws/at_least・by_turn=deck_size/copies/turn/at_least/mulligans・"
    "land_drops=deck_size/copies/turn/mulligans・combo_by_turn=deck_size/copies/copies_b/turn/mulligans。"
    "省けば 1 回の計算。")

_MULL_CAVEAT = "初手を 7−マリガン回数 枚とする計算で、何を戻すか・キープするかの判断は含まない"
_CALC_CAVEAT = "超幾何分布の計算値・実戦の記録ではない"
_NOTE = "超幾何分布の厳密値（17Lands 等の実測ではない）。デッキ全体を無作為に切った前提。"


def _label(kind: str, name: str) -> str:
    """表の見出し。"""
    if name == "copies":
        return "土地の枚数" if kind == "land_drops" else "当たりの枚数"
    return {"deck_size": "デッキの枚数", "copies_b": "相方の枚数", "draws": "引く枚数",
            "turn": "ターン", "at_least": "必要な枚数", "mulligans": "マリガン回数"}[name]


def _range_errors(p: dict, kind: str) -> list[str]:
    """1 回の計算と同じ範囲検査を**全部の引数**に（計算に使わない引数も＝同じ入力が
    vary の有り無しで通ったり弾かれたりしない）＋ combo の合計の制約。p は振った値を代入した後の引数
    （振った数字と同名の普通の引数は代入で上書きされるので検査にかからない）。"""
    n = p["deck_size"]
    bad = []
    if not (1 <= n <= 500):
        return ["deck_size は 1〜500"]
    for name in ("copies", "copies_b", "draws", "at_least"):
        if not (0 <= p[name] <= n):
            bad.append(f"{name} は 0〜deck_size")
    if not (1 <= p["turn"] <= 60):
        bad.append("turn は 1〜60")
    if not (0 <= p["mulligans"] <= 6):
        bad.append("mulligans は 0〜6")
    if kind == "combo_by_turn" and p["copies"] + p["copies_b"] > n:
        bad.append("copies＋copies_b は deck_size 以下")
    return bad


def _premise(kind: str, p: dict) -> str:
    """1 回の計算の premise と同じ書き方（at_least は引く枚数・他は先手／後手と引きの回数）。"""
    if kind == "at_least":
        return f"{p['draws']} 枚引く"
    t = p["turn"]
    s = f"先手（{t} ターン目までの引き {max(t - 1, 0)} 回）" if p["on_play"] else f"後手（{t} ターン目までの引き {t} 回）"
    if p["mulligans"]:
        s += f"・マリガン {p['mulligans']} 回（初手 {7 - p['mulligans']} 枚）"
    return s


def _formula(kind: str, p: dict, seen: int) -> str:
    n, k = p["deck_size"], p["copies"]
    if kind == "at_least":
        d, m = p["draws"], p["at_least"]
        return f"P(X ≥ {m}) = Σ C({k}, i)·C({n - k}, {d}−i) / C({n}, {d})  (i = {m}..min({k},{d}))"
    if kind == "by_turn":
        return f"超幾何(N={n}, K={k}, D={seen})・P(X ≥ {p['at_least']})"
    if kind == "land_drops":
        return f"超幾何(N={n}, K={k}, D={seen})・P(土地 ≥ {p['turn']} 枚)"
    return f"超幾何(N={n}, A={k}, B={p['copies_b']}, D={seen})・1 − P(A なし) − P(B なし) ＋ P(両方なし)"


def _caption(kind: str, vars_: tuple[str, ...], p: dict, rows: list[dict]) -> str:
    """表の前提の一文（全行に共通の条件だけ・振った数字は書かない）。
    見る枚数（初手＋引き・デッキの枚数で頭打ち）は、全行で同じならここに書き、行ごとに違えばその旨を書く
    （8 枚のデッキの 3 ターン目は 8 枚しか見ない＝表だけ貼っても分かるように）。"""
    parts = []
    if "deck_size" not in vars_:
        parts.append(f"{p['deck_size']} 枚のデッキ")
    if "copies" not in vars_:
        parts.append(f"土地 {p['copies']} 枚" if kind == "land_drops" else f"当たり {p['copies']} 枚")
    if kind == "combo_by_turn" and "copies_b" not in vars_:
        parts.append(f"相方 {p['copies_b']} 枚")
    if kind == "at_least" and "draws" not in vars_:
        parts.append(f"{p['draws']} 枚引く")
    if kind != "at_least":
        parts.append("先手" if p["on_play"] else "後手")
        if "mulligans" not in vars_:
            parts.append(f"マリガン {p['mulligans']} 回")
    t = "そのターン" if "turn" in vars_ else f"{p['turn']} ターン目"
    need = "必要な枚数" if "at_least" in vars_ else f" {p['at_least']} 枚"
    what = {"at_least": f"当たりを{need}以上引く確率",
            "by_turn": f"{t}までに当たりを{need}以上引く確率",
            "land_drops": f"{t}まで毎ターン土地を置ける確率",
            "combo_by_turn": f"{t}までに当たりと相方を両方 1 枚以上引く確率"}[kind]
    # at_least の見る枚数は引く枚数そのもの（上に「N 枚引く」と書く）ので、添えるのは他の 3 種類だけ
    seen = {r["cards_seen"] for r in rows}
    if kind != "at_least" and len(seen) == 1:
        parts.append(f"見る {next(iter(seen))} 枚")
    parts.append(what)
    cap = "前提: " + "・".join(parts) + f"（{_CALC_CAVEAT}）"
    if "mulligans" in vars_:
        cap += f"。{_MULL_CAVEAT}"
    return cap


def _cell(name: str, v: int, seen: int | None) -> str:
    """表の見出しの 1 マス。turn の見る枚数は、同じ見出しの下で見る枚数が 1 つに決まるときだけ添える。"""
    if name == "turn":
        return f"{v} ターン目（{seen} 枚）" if seen is not None else f"{v} ターン目"
    if name == "mulligans":
        return f"{v} 回（初手 {7 - v} 枚）"
    return str(v)


def validate(kind: str, vary) -> tuple[tuple[str, ...] | None, list[list[int]] | None, str | None]:
    """(振る数字の名前の並び, 数字ごとのまとめた値, エラーの JSON)。検査の順番は契約の 1〜8。"""
    if not isinstance(vary, dict) or not vary:
        return None, None, errors.err_json(
            errors.UNKNOWN_OPTION, "vary は {引数名: [整数, …]} の形（例 {\"copies\": [15, 16, 17, 18]}）")
    if len(vary) > VARY_DIMS:
        return None, None, errors.err_json(
            errors.UNKNOWN_OPTION, f"vary で同時に振れる数字は {VARY_DIMS} つまで。絞ってから呼び直す（受け取った数: {len(vary)}）")
    if kind not in VARY_ARGS:
        return None, None, errors.err_json(
            errors.UNKNOWN_OPTION, "vary が使えるのは at_least・by_turn・land_drops・combo_by_turn だけ。"
                                   f"{kind} は vary を外して 1 回ずつ呼ぶ")
    names = tuple(vary)
    bad_names = [n for n in names if n not in VARY_ARGS[kind]]
    if bad_names:
        return None, None, errors.err_json(
            errors.UNKNOWN_OPTION, f"{kind} で振れる数字は {'・'.join(VARY_ARGS[kind])}（受け取った名前: "
                                   + "・".join(repr(n) for n in bad_names) + "）")
    lists = []
    for name in names:
        raw = vary[name]
        if not isinstance(raw, list):
            return None, None, errors.err_json(errors.OUT_OF_RANGE, f"vary の {name} の値は整数の配列（受け取った値: {raw!r}）")
        if len(raw) > VARY_MAX:
            return None, None, errors.err_json(
                errors.OUT_OF_RANGE, f"計算は全部で {VARY_MAX} 通りまで。比べる値を絞ってから呼び直す（{name} の値の数: {len(raw)}）")
        bad = [x for x in raw if type(x) is not int]
        if bad:
            return None, None, errors.err_json(
                errors.OUT_OF_RANGE, f"vary の {name} の値は整数だけ（文字列・真偽値・小数・null は使えない）: "
                                     + "・".join(json.dumps(x, ensure_ascii=False) for x in bad))
        vals = list(dict.fromkeys(raw))
        if len(vals) < 2:
            return None, None, errors.err_json(
                errors.OUT_OF_RANGE, f"vary の {name} は比べる値を 2 個以上（重複をまとめた後の数: {len(vals)}）。"
                                     "1 つに決めた数字は vary でなく普通の引数で渡す")
        lists.append(vals)
    total = 1
    for vals in lists:
        total *= len(vals)
    if total > VARY_MAX:
        shape = "×".join(str(len(v)) for v in lists)
        return None, None, errors.err_json(
            errors.OUT_OF_RANGE, f"計算は全部で {VARY_MAX} 通りまで。比べる値を絞ってから呼び直す（{shape}＝{total} 通り）")
    return names, lists, None


def compute(kind: str, names: tuple[str, ...], lists: list[list[int]], base: dict) -> str:
    """base は 1 回の計算と同じ引数（deck_size・copies・copies_b・draws・turn・on_play・at_least・mulligans）。"""
    # 全部の組み合わせを代入して検査（振った数字と同名の普通の引数は使わない＝検査にもかけない）
    broken = []
    for combo in itertools.product(*lists):
        p = dict(base, **dict(zip(names, combo)))
        bad = _range_errors(p, kind)
        if bad:
            broken.append("・".join(f"{n}={v}" for n, v in zip(names, combo)) + f"（{'・'.join(bad)}）")
    if broken:
        more = f" ほか {len(broken) - 10} 通り" if len(broken) > 10 else ""
        return errors.err_json(errors.OUT_OF_RANGE, "範囲外の組み合わせがあるので表は返さない: "
                               + "／".join(broken[:10]) + more)

    # SELECT を 1 本組む。振った数字の位置に t{j}.v、他は %s。並びは確率の式 → 見る枚数の式 → unnest の配列（数字の順）
    pos = {n: j for j, n in enumerate(names)}

    def arg(name):
        return (f"t{pos[name]}.v", []) if name in pos else ("%s", [base[name]])

    def call(fn, args):
        exprs, params = [], []
        for nm in args:
            if nm == "on_play":
                exprs.append("%s"); params.append(bool(base["on_play"]))
            else:
                e, pp = arg(nm); exprs.append(e); params += pp
        return f"{fn}({', '.join(exprs)})", params

    if kind == "at_least":
        prob, pp = call("mtg_hypergeom_atleast", ("deck_size", "copies", "draws", "at_least"))
        seen, sp = arg("draws")
    else:
        fn, args = {"by_turn": ("mtg_prob_by_turn", ("deck_size", "copies", "turn", "on_play", "at_least", "mulligans")),
                    "land_drops": ("mtg_land_drops", ("deck_size", "copies", "turn", "on_play", "mulligans")),
                    "combo_by_turn": ("mtg_combo_by_turn",
                                      ("deck_size", "copies", "copies_b", "turn", "on_play", "mulligans"))}[kind]
        prob, pp = call(fn, args)
        cs, cp = call("mtg_cards_seen", ("turn", "on_play", "mulligans"))
        dk, dp = arg("deck_size")
        seen, sp = f"least({cs}, {dk})", cp + dp
    cols = ", ".join(f"t{j}.v" for j in range(len(names)))
    froms = " CROSS JOIN ".join(f"unnest(%s::int[]) WITH ORDINALITY AS t{j}(v, i)" for j in range(len(names)))
    order = ", ".join(f"t{j}.i" for j in range(len(names)))
    sql = f"SELECT {cols}, {prob}, {seen} FROM {froms} ORDER BY {order}"
    try:
        got = _db(sql, tuple(pp + sp + lists), lane=LANE_HEAVY)
    except DBBusy as e:
        return errors.err_json(errors.BUSY, str(e))
    except Exception as e:
        return errors.err_json(errors.DB_ERROR, f"計算に失敗: {str(e)[:200]}（SQL 関数 mtg_* が無い環境の可能性）")
    d = len(names)
    total = 1
    for vals in lists:
        total *= len(vals)
    if len(got) != total or any(r[d] is None for r in got):
        nulls = ["・".join(f"{n}={v}" for n, v in zip(names, r[:d])) for r in got if r[d] is None]
        if not nulls:
            return errors.err_json(errors.DB_ERROR, f"計算の行が欠けた（期待 {total} 行・返った {len(got)} 行）ので表は返さない")
        more = f" ほか {len(nulls) - 10} 通り" if len(nulls) > 10 else ""
        return errors.err_json(errors.OUT_OF_RANGE,
                               "この入力では定義できない組み合わせがあるので表は返さない: " + "／".join(nulls[:10]) + more)

    per_row_premise = bool({"turn", "mulligans"} & set(names)) or (kind == "at_least" and "draws" in names)
    rows = []
    for r in got:
        combo, prob_v, seen_v = r[:d], r[d], int(r[d + 1])
        p = dict(base, **dict(zip(names, combo)))
        pv = float(prob_v)
        row = {**dict(zip(names, combo)), "probability": round(pv, 4), "percent": f"{pv * 100:.1f}%",
               "cards_seen": seen_v, "formula": _formula(kind, p, seen_v)}
        if per_row_premise:
            row["premise"] = _premise(kind, p)
        rows.append(row)

    fixed = {nm: base[nm] for nm in VARY_ARGS[kind] if nm not in names}
    if kind != "at_least":
        fixed["on_play"] = base["on_play"]
    out = {"kind": kind, "vary": list(names), "fixed": fixed, "rows": rows}
    if not per_row_premise:
        out["premise"] = _premise(kind, base)
    out["table"] = _caption(kind, names, base, rows) + "\n\n" + _tables(kind, names, lists, rows)
    out["note"] = _NOTE + (f" {_MULL_CAVEAT}。" if "mulligans" in names else "")
    return json.dumps(out, ensure_ascii=False, indent=1)


def _seen_if_fixed(rows: list[dict], name: str, v: int) -> int | None:
    """同じ見出し（name=v）の下で見る枚数が 1 つに決まるならその枚数（turn の見出しに添える）。"""
    s = {r["cards_seen"] for r in rows if r[name] == v}
    return s.pop() if len(s) == 1 else None


def _tables(kind: str, names: tuple[str, ...], lists: list[list[int]], rows: list[dict]) -> str:
    """1 変数＝縦の表／2 変数＝行（1 つ目）×列（2 つ目）の表／3 変数＝3 つ目の値ごとに行×列の表を分ける。"""
    if len(names) == 1:
        (a,) = names
        head = "ターン（見る枚数）" if a == "turn" else ("マリガン回数（初手の枚数）" if a == "mulligans" else _label(kind, a))
        # 見る枚数が行ごとに違い、見出し（turn）でも示していないときは列を足す（at_least の draws は値そのものが枚数）
        extra = (a not in ("turn",) and not (kind == "at_least" and a == "draws")
                 and len({r["cards_seen"] for r in rows}) > 1)
        if extra:
            body = "\n".join(f"| {_cell(a, r[a], None)} | {r['cards_seen']} 枚 | {r['percent']} |" for r in rows)
            return f"| {head} | 見る枚数 | 確率 |\n|---|---|---|\n" + body
        body = "\n".join(f"| {_cell(a, r[a], r['cards_seen'])} | {r['percent']} |" for r in rows)
        return f"| {head} | 確率 |\n|---|---|\n" + body

    all_seen = {r["cards_seen"] for r in rows}

    def matrix(sub: list[dict], covered_by_third: bool = False) -> str:
        """行×列の表。見る枚数（初手＋引き・デッキの枚数で頭打ち）は、表全体で 1 つなら前提の一文に・
        ターンの見出しで分かるならその見出しに書く。どちらでも分からないマスがあれば、マスの中に「44.5%（9 枚）」と添える
        （表だけ貼っても伝わるように）。"""
        a, b = names[0], names[1]
        cols = lists[1]
        row_seen = {va: _seen_if_fixed(sub, a, va) for va in lists[0]}
        col_seen = {vb: _seen_if_fixed(sub, b, vb) for vb in cols}
        head = (f"| {_label(kind, a)} ＼ {_label(kind, b)} | "
                + " | ".join(_cell(b, v, col_seen[v]) for v in cols) + " |")
        sep = "|---|" + "---|" * len(cols)
        by = {(r[a], r[b]): r for r in sub}

        def covered(va, vb):
            return (kind == "at_least" or len(all_seen) == 1 or covered_by_third
                    or (a == "turn" and row_seen[va] is not None) or (b == "turn" and col_seen[vb] is not None))

        annotate = not all(covered(va, vb) for va in lists[0] for vb in cols)

        def mark(va, vb):
            r = by[(va, vb)]
            return f"{r['percent']}（{r['cards_seen']} 枚）" if annotate else r["percent"]
        lines = [f"| {_cell(a, va, row_seen[va])} | " + " | ".join(mark(va, vb) for vb in cols) + " |" for va in lists[0]]
        return "\n".join([head, sep] + lines)

    if len(names) == 2:
        return matrix(rows)
    c = names[2]
    parts = []
    for vc in lists[2]:
        sub = [r for r in rows if r[c] == vc]
        third_seen = _seen_if_fixed(sub, c, vc)
        parts.append(f"{_label(kind, c)}: {_cell(c, vc, third_seen)}\n\n"
                     + matrix(sub, covered_by_third=(c == "turn" and third_seen is not None)))
    return "\n\n".join(parts)
