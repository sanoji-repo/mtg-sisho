"""probability.py — 確率計算の道具 mtg_probability（2026-09-05 Step 2 で mcp_server.py から切り出し）。

登録（server.tool）は mcp_server.py 側。ここは DESCRIPTION と素の関数だけを持つ。
"""
import json

from sisho import errors
from sisho.db import _db
from sisho.toollog import _log_tool


# ─── 確率計算の入口（2026-09-04 本人「あらゆる確率計算をどこかに格納して…」→「ひとまず最低限だけ」）───
# 計算の本体は DB の SQL 関数（sql/prob_functions.sql・超幾何・IMMUTABLE）。ここは名前付き引数で受けて同じ関数を呼び、
# 数字と式と入力の復唱を返す薄い入口。LLM に算術をさせない・自由なコード実行は置かない（箱は 2 コア・公開口）。
# データと結合したいときは query_mtg_database から関数を直接呼ぶ（例: SELECT mtg_land_drops(60, 24, 4, true)）。
_PROB_KINDS = ("at_least", "by_turn", "land_drops", "combo_by_turn")


DESCRIPTION = (
    "【確率の計算はこれ。自分で計算しない】デッキの確率を超幾何分布で厳密に計算する（DB の SQL 関数・決定的・1 ミリ秒未満）。"
    "kind: at_least=N 枚のデッキから D 枚引いて K 枚入りの札が m 枚以上／by_turn=turn ターン目までに K 枚入りの札を m 枚以上"
    "（見る枚数=初手 7−マリガン＋引き・先手は turn−1 回・後手は turn 回）／land_drops=turn ターン目まで毎ターン土地を置ける"
    "（見た札に土地が turn 枚以上）／combo_by_turn=turn ターン目までに A（copies）と B（copies_b）を両方 1 枚以上。"
    "引数: deck_size（60/40/99 等）・copies（当たりの枚数・land_drops では土地の枚数）・copies_b（combo の相方）・"
    "draws（at_least の引く枚数）・turn・on_play（先手 true／後手 false）・at_least（m・既定 1）・mulligans（既定 0）。"
    "返り値の probability を percent と一緒にそのまま書き、formula と cards_seen を添えると読者が検算できる。"
    "色マナ源の問い（例: 2 ターン目に青 2 つ）は by_turn で copies=その色のソース枚数・at_least=必要数。"
    "答えに『前提: 先手／後手・マリガン n 回』を必ず添える。")


def mtg_probability(kind: str, deck_size: int = 60, copies: int = 4, copies_b: int = 0, draws: int = 7,
                    turn: int = 1, on_play: bool = True, at_least: int = 1, mulligans: int = 0) -> str:
    _log_tool("mtg_probability", {"kind": kind, "deck_size": deck_size, "copies": copies, "copies_b": copies_b,
                                  "draws": draws, "turn": turn, "on_play": on_play, "at_least": at_least, "mulligans": mulligans})
    if kind not in _PROB_KINDS:
        return errors.err_json(errors.UNKNOWN_OPTION,
                               f"kind は {', '.join(_PROB_KINDS)} のどれか（受け取った値: {kind!r}）")
    bad = []
    if not (1 <= deck_size <= 500): bad.append("deck_size は 1〜500")
    if not (0 <= copies <= deck_size): bad.append("copies は 0〜deck_size")
    if not (0 <= copies_b <= deck_size): bad.append("copies_b は 0〜deck_size")
    if not (0 <= draws <= deck_size): bad.append("draws は 0〜deck_size")
    if not (1 <= turn <= 60): bad.append("turn は 1〜60")
    if not (0 <= at_least <= deck_size): bad.append("at_least は 0〜deck_size")
    if not (0 <= mulligans <= 6): bad.append("mulligans は 0〜6")
    if bad:
        return errors.err_json(errors.OUT_OF_RANGE, "引数の範囲外: " + "・".join(bad))
    premise = f"先手（{turn} ターン目までの引き {max(turn - 1, 0)} 回）" if on_play else f"後手（{turn} ターン目までの引き {turn} 回）"
    if mulligans:
        premise += f"・マリガン {mulligans} 回（初手 {7 - mulligans} 枚）"
    try:
        if kind == "at_least":
            rows = _db("SELECT mtg_hypergeom_atleast(%s, %s, %s, %s)", (deck_size, copies, draws, at_least))
            seen = draws; premise = f"{draws} 枚引く"
            formula = f"P(X ≥ {at_least}) = Σ C({copies}, i)·C({deck_size - copies}, {draws}−i) / C({deck_size}, {draws})  (i = {at_least}..min({copies},{draws}))"
        elif kind == "by_turn":
            rows = _db("SELECT mtg_prob_by_turn(%s, %s, %s, %s, %s, %s), mtg_cards_seen(%s, %s, %s)",
                       (deck_size, copies, turn, on_play, at_least, mulligans, turn, on_play, mulligans))
            seen = rows[0][1]
            formula = f"見る枚数 {seen} = 7−{mulligans}＋{seen - 7 + mulligans}・P(X ≥ {at_least}) 超幾何(N={deck_size}, K={copies}, D={seen})"
        elif kind == "land_drops":
            rows = _db("SELECT mtg_land_drops(%s, %s, %s, %s, %s), mtg_cards_seen(%s, %s, %s)",
                       (deck_size, copies, turn, on_play, mulligans, turn, on_play, mulligans))
            seen = rows[0][1]
            formula = f"見る枚数 {seen}・P(土地 ≥ {turn} 枚) 超幾何(N={deck_size}, K={copies}, D={seen})＝{turn} ターン目まで毎ターン土地を置ける"
        else:
            rows = _db("SELECT mtg_combo_by_turn(%s, %s, %s, %s, %s, %s), mtg_cards_seen(%s, %s, %s)",
                       (deck_size, copies, copies_b, turn, on_play, mulligans, turn, on_play, mulligans))
            seen = rows[0][1]
            formula = f"見る枚数 {seen}・1 − P(A なし) − P(B なし) ＋ P(両方なし)（包除・A={copies} 枚・B={copies_b} 枚・N={deck_size}）"
    except Exception as e:
        # 想定外は丸めず素通し（生の例外文の先頭 200 字）
        return errors.err_json(errors.DB_ERROR, f"計算に失敗: {str(e)[:200]}（SQL 関数 mtg_* が無い環境の可能性）")
    p = rows[0][0]
    if p is None:
        return errors.err_json(errors.OUT_OF_RANGE,
                               "この入力では定義できない（枚数の整合を確認: copies+copies_b ≤ deck_size 等）")
    p = float(p)
    out = {"kind": kind, "inputs": {"deck_size": deck_size, "copies": copies, "copies_b": copies_b if kind == "combo_by_turn" else None,
                                    "draws": draws if kind == "at_least" else None, "turn": None if kind == "at_least" else turn,
                                    "on_play": None if kind == "at_least" else on_play, "at_least": at_least if kind in ("at_least", "by_turn") else None,
                                    "mulligans": None if kind == "at_least" else mulligans},
           "cards_seen": seen, "probability": round(p, 4), "percent": f"{p * 100:.1f}%", "premise": premise, "formula": formula,
           "note": "超幾何分布の厳密値（17Lands 等の実測ではない）。デッキ全体を無作為に切った前提。土地の連続配置は『見た札に土地が turn 枚以上』の近似ではなく同値。"}
    out["inputs"] = {k: v for k, v in out["inputs"].items() if v is not None}
    return json.dumps(out, ensure_ascii=False, indent=1)
