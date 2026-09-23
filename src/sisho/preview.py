"""preview.py — 発売前のカード（先行収録）の注記。

発売前のカードは Scryfall が全フォーマット not_legal にしている。そのまま返すと「どこでも使えないカード」か、
逆に「発売したらスタンダードで使える」と読まれうる。統率者セットの新カードはスタンダード・パイオニア・
モダンでは使えないので、発売後に使えるフォーマットをセットごとに書く。

判定は行の状態から（日付や印を持たない）: digital でなく vintage が not_legal ＝ 先行収録の入口
（sync_oracle_cards.py --preview-days）しか通らない状態。発売日に Scryfall が legal へ切り替えれば外れる。
"""
from sisho.db import _db, LANE_LIGHT

PREVIEW_SQL = "NOT c.digital AND c.legalities->>'vintage' = 'not_legal'"

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


def preview_notes(card_names: list[str]) -> dict[str, str]:
    """発売前のカードだけ {card_name: 注記} を返す。"""
    if not card_names:
        return {}
    rows = _db(
        "SELECT c.card_name, c.set_code, s.set_name, s.released_at, s.set_type"
        " FROM mtg_cards_v2 c LEFT JOIN mtg_sets s ON s.set_code = c.set_code"
        f" WHERE c.card_name = ANY(%s) AND {PREVIEW_SQL}", (list(card_names),), lane=LANE_LIGHT)
    out = {}
    for name, code, set_name, rel, set_type in rows:
        when = f"{rel} 発売" if rel else "発売日未確認"
        out[name] = (f"発売前のカード（{set_name or code}・{when}）。今はどのフォーマットでも使えない"
                     f"（legalities は発売日に切り替わる）。発売後に使えるフォーマット: {_legality(code, set_type)}。"
                     "仮組みの相談には使ってよいが、採用率・共起・17Lands の数字はまだ無い")
    return out
