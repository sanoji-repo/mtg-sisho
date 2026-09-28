"""names.py — カード名の解決を 1 箇所に置く。

DESIGN 12（名前が複数あるものは面ごとの列で持つ）の帰結として、
「渡された名前 → DB の正式名（card_name）」の解決規則が 2 箇所に別実装で存在していた:

  - sisho/tools/partners.py の `_name_variants`
      `SELECT card_name … WHERE name_en_back IS NOT NULL AND (name_en_front = %s OR name_en_back = %s)`
  - sisho/tools/rules.py の `get_card_rulings` 内の直書き
      `SELECT card_name … WHERE name_en_front = %s OR name_en_back = %s
       ORDER BY (name_en_front = %s) DESC LIMIT 1`

やっていることは同じ「面の名前 → 正式名」で、片方だけ直す事故が起きうる。
両者が呼ぶ 1 つの関数をここに置く。**返り値（相方検索・裁定検索の答え）は変えない**。

実測で確かめた 2 つの実装の意味差（開発側の DB）:
  - `name_en_front` も `name_en_back` も DB 内で重複なし（実測 0 件）＝1 つの名前に当たる行は最大 2 行
    （表で当たる行 1 つ・裏で当たる行 1 つ）。
  - 単面カードは `card_name = name_en_front`（`name_en_back IS NULL AND card_name <> name_en_front` は 0 件）。
    つまり partners 側の `name_en_back IS NOT NULL` の絞りが落とすのは「その名前そのもの」だけで、
    partners は元の名前を候補の先頭に必ず持つ＝**絞りを外しても候補の集合は変わらない**（重複除去で消える）。
  - 裏面名が別の本物のカード名と同じカードは 21 枚（例: `Emeritus of Ideation // Ancestral Recall` の
    裏面名 `Ancestral Recall`）。`ORDER BY (name_en_front = %s) DESC` は本物のカード（表面一致）を先に置く
    ＝DESIGN 12 の「正式名 → 表面名 → 裏面名」の順そのもの。
  - 大文字小文字はどちらの実装も区別する（`=` の完全一致・`lower()` を掛けていない）＝ここでも変えない。

差は「解決した後の使い方」だけ: 裁定検索は先頭 1 つを採り、相方検索は候補を全部持って ANY() に渡す。
だから解決は候補の一覧（表面一致が先）を返し、絞り込みは呼ぶ側に置く。
"""
from sisho.db import LANE_LIGHT, _db


def resolve_face_name(name: str) -> list[str]:
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
    return [r[0] for r in _db(
        "SELECT card_name FROM mtg_cards_v2"
        " WHERE name_en_front = %s OR name_en_back = %s"
        " ORDER BY (name_en_front = %s) DESC", (n, n, n), lane=LANE_LIGHT)]


def _like_exact(n: str) -> str:
    """ILIKE で「全体が一致（大文字小文字は無視）」を言うための値＝LIKE の記号を字にする（pg_trgm の索引に乗る）。"""
    return n.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_card_ex(name: str) -> tuple[dict | None, list[str]]:
    """入力名を 1 枚のカードに決める。(カード, 曖昧なときの正式名の一覧)。決まらなければ (None, [])。

    集計や裁定検索の**前に** 1 枚に決める。以前は面の名前の候補を
    全部 ANY() に渡して集計し、裏面名が別の本物のカードと同じ 21 枚で 2 枚分を合算していた（「Replenish」の同居率 103.5%）。
    決め方:
      段 1 完全一致: 正式名 → 表面名 → 裏面名（DESIGN 12）。
      段 2 大文字小文字だけ違う全体一致（段 1 が空のときだけ・「brainstorm」）。順位は段 1 と同じ。
      どちらの段でも、最上位の順位に**別のカードが 2 枚以上**あれば決めない＝曖昧として正式名を返す
      （裏面名が 3 枚で重なる 5 種がある・例「Vicious Verse」＝id の順で 1 枚に決めると実デッキ 0 本の別カードを断定した）。
      同じ順位に紙とデジタルがあれば紙を採る（デジタルだけで曖昧にはしない）。
    ` // ` を含む入力は正式名と全体一致のときだけ当たる（後半が違う入力を前半で通さない）。"""
    n = (name or "").strip()
    if not n:
        return None, []
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
    return None, []


def resolve_card(name: str) -> dict | None:
    """1 枚に決まったカード（曖昧・不在なら None）。"""
    return resolve_card_ex(name)[0]


def ambiguous_hint(names: list[str]) -> str:
    """同じ名前を面に持つカードが複数あるときの案内（1 枚に決めつけない）。"""
    return ("この名前は複数のカードの面の名前で、1 枚に決まらない: " + "・".join(names)
            + "＝どれかの正式名（表 // 裏）で呼び直す")


def find_card(name: str, extra=()) -> tuple[int, str] | None:
    """(id, name_display) を返す薄い口（空振りの理由を言い分けるとき用）。extra は互換のため受けるが使わない
    ＝元の入力名だけで決める（展開した候補名で当てると別カードを入力のカードとして扱いうる）。"""
    c = resolve_card(name)
    return (c["id"], c["display"]) if c else None


PARTIAL_CANDIDATES = 5


def card_candidates(name: str, with_rulings: bool = False) -> list[tuple[str, str, bool | None]]:
    """名前に入力を含むカード（card_name, name_display, digital）。紙を先・人気順。LIKE の記号は字として扱う。

    with_rulings=True（裁定検索）は、裁定の表にだけある名前（カード表に無い 732 種＝次元・アンセット等）も候補に足す
    （実例: 「Talon Gat」がカード表だけを見て別カード《マダラの鉤爪門/Talon Gates of Madara》の
    裁定に決まり、本物の Talon Gates を見落とした）。その名前の完成形はカード表に無いので名前そのままで書く。"""
    n = (name or "").strip()
    if len(n) < 3:
        return []
    pat = "%" + _like_exact(n) + "%"
    out = [tuple(r) for r in _db(
        "SELECT card_name, name_display, digital FROM mtg_cards_v2 WHERE card_name ILIKE %s"
        " ORDER BY digital, edhrec_rank NULLS LAST, id LIMIT %s",
        (pat, PARTIAL_CANDIDATES + 1), lane=LANE_LIGHT)]
    if with_rulings:
        # カード表の候補の数に関係なくいつも探す（カード表が 5 件以上のとき探すのをやめて「Gavon」の Gavony・
        # 「Goldmeado」の Goldmeadow を案内から落としていた）。裁定の表にだけある名前は digital=None で印を付けて返す
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


def not_found_hint(name: str, fallback: str, cands: list | None = None) -> str:
    """完全一致しなかった名前へ、カード表の部分一致の候補を添える。
    1 枚に決めつけず（採用率で推測しない）、完成形の候補を並べて選ばせる。デジタル専用も候補に残す
    （candidate_label が「（デジタル専用）」を付ける）。候補が無ければ fallback（引き方の案内）。"""
    rows = card_candidates(name) if cands is None else cands
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


def face_display(en: str, ja: str | None, digital: bool = False, preview: bool = False) -> str:
    """面の完成形。ja があれば《ja/en》・無ければ「en（日本語版なし）」・digital のカードは「en（日本語名未収録）」・
    発売前のカードは「en（日本語名は未収録・発売前）」（DB の name_display の式と同じ枝）。"""
    if ja:
        return f"《{ja}/{en}》"
    note = "日本語名未収録" if digital else ("日本語名は未収録・発売前" if preview else "日本語版なし")
    return f"{en}（{note}）"

