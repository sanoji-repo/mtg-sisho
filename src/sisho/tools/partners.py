"""partners.py — 共起の道具 find_partner_cards。

登録（server.tool）は mcp_server.py 側。ここは DESCRIPTION と素の関数だけを持つ。

共起の表:
表は card_cooccurrence_v2（組）・card_population_deck_counts（分母 M と S）・cooccurrence_population_stats（N と区間）・
cooccurrence_populations（フォーマット × 区間の定義）。入口はフォーマット（format）で、旧の scope（構築を混ぜて数えていた）は捨てた。
区間は「直近 90 日」を先に見て、材料が足りないときだけ「全期間」へ広げ、広げた理由を返り値で言う。

読み取りは SQL 1 文: 区間を選ぶ材料（N・M・S・組の有無）と、両方の区間の相方の上位を
1 回で読む＝夜間の集計がその母集団を適用している最中に呼ばれても、見出しの分母と一覧の同居数が必ず同じ版から出る
（別々に読むと古い M と新しい同居数が混ざって 100% を超えうる）。区間の選び方は Python 側で、読んだ結果から決める。
"""
import datetime

from sisho.db import LANE_HEAVY, LANE_LIGHT, _db
from sisho.names import ambiguous_hint, not_found_hint, resolve_card, resolve_card_ex
from sisho.toollog import _log_tool

#: 受け付けるフォーマット（表 cooccurrence_populations の format_name と同じ綴り）
FORMATS = ("Standard", "Pioneer", "Modern", "Legacy", "Premodern", "Pauper", "Vintage",
           "Duel Commander", "Commander", "Precon")
#: 大文字小文字・空白を無視した入力 → 正式な綴り。edh は多人数の統率者戦（Commander）
_FORMAT_KEYS = {f.lower().replace(" ", ""): f for f in FORMATS} | {"edh": "Commander", "duel": "Duel Commander"}

#: サイドボードの関係を作らないフォーマット（表 cooccurrence_populations の supported_relations と同じ）
NO_SIDE = ("Duel Commander", "Commander", "Precon")
#: 直近 90 日でこのカードをメインに入れたリストがこれ未満なら全期間へ広げる
WIDEN_BELOW = 5
#: lift で並べるのは、このカードのリスト（M(A)）がこれ以上・同居がこれ以上の相方だけ（少ない標本の lift の暴発を避ける）
LIFT_MIN_A, LIFT_MIN_AB = 20, 5
#: 集計がこれより古ければ「夜間の更新が止まっている」と返り値で言う（毎晩更新＝72 時間は 3 晩続けて更新が来ない状態）
STALE_AFTER = datetime.timedelta(days=3)


def _name_variants(name: str) -> list[str]:
    """入力名を 1 枚のカードに決め（names.resolve_card）、そのカードの [正式名, 表面名] を返す（決まらなければ入力のまま）。

    新しい表は card_id が鍵なので集計には使わない。verify_answer など他の道具が同じ関数を使っている。"""
    card = resolve_card(name)
    if not card:
        return [name.strip()]
    out = [card["card_name"]]
    if card["front"] != card["card_name"]:
        out.append(card["front"])
    return out


def _format_of(s) -> str | None:
    return _FORMAT_KEYS.get(str(s or "").strip().lower().replace(" ", "").replace("_", "").replace("-", ""))


def _resolve(name: str):
    """入力を 1 枚のカードに決める。mtgtop8 が書く斜線 1 本の表記（「Fire / Ice」）は、斜線 2 本に直した名前が
    カードの正式名にちょうど一致するときだけ受ける（旧の版は旧表の鍵で引けていた 18 種）。
    前半だけで通すことはしない（「Fire / でたらめ」は通さない・names.resolve_card_ex の ` // ` の規則のまま）。"""
    card, amb = resolve_card_ex(name)
    if card or amb or " / " not in name or " // " in name:
        return card, amb
    full = " // ".join(p.strip() for p in name.split(" / "))
    c2, _amb2 = resolve_card_ex(full)
    if c2 and c2["card_name"] == full:
        return c2, []
    return None, []


# 1 文で読む。hdr＝区間ごとの見出しの材料・rows＝区間ごとの相方の上位（同居数の順と、頼まれたら lift の順）。
_SQL = """
WITH pops AS (
  SELECT p.population_id AS pid, p.window_code AS w, (p.definition -> 'supported_relations') ? 's' AS has_side,
         s.deck_count AS n, s.card_row_count, s.window_start, s.window_end, s.first_event_date, s.latest_event_date,
         s.computed_at
  FROM cooccurrence_populations p LEFT JOIN cooccurrence_population_stats s USING (population_id)
  WHERE p.format_name = %(fmt)s),
hdr AS (
  SELECT pops.*, COALESCE(d.main_deck_count, 0) AS ma, d.side_deck_count AS sa,
         (SELECT count(*) FROM card_population_deck_counts k WHERE k.population_id = pops.pid) AS card_rows,
         (EXISTS (SELECT 1 FROM card_cooccurrence_v2 c WHERE c.population_id = pops.pid AND c.relation = %(rel)s
                  AND c.card_id_a = %(c)s AND c.card_id_b <> %(c)s)
          OR (%(rel)s = 'm' AND EXISTS (SELECT 1 FROM card_cooccurrence_v2 c WHERE c.population_id = pops.pid
                  AND c.relation = 'm' AND c.card_id_b = %(c)s))) AS has_pairs,
         (SELECT c.cooccurrence_count FROM card_cooccurrence_v2 c WHERE c.population_id = pops.pid AND c.relation = 's'
                  AND c.card_id_a = %(c)s AND c.card_id_b = %(c)s) AS self_side
  FROM pops LEFT JOIN card_population_deck_counts d ON d.population_id = pops.pid AND d.card_id = %(c)s),
pairs AS (
  SELECT h.pid, x.partner, x.n_ab FROM hdr h CROSS JOIN LATERAL (
      SELECT c.card_id_b AS partner, c.cooccurrence_count AS n_ab FROM card_cooccurrence_v2 c
      WHERE c.population_id = h.pid AND c.relation = %(rel)s AND c.card_id_a = %(c)s AND c.card_id_b <> %(c)s
      UNION ALL
      SELECT c.card_id_a, c.cooccurrence_count FROM card_cooccurrence_v2 c
      WHERE %(rel)s = 'm' AND c.population_id = h.pid AND c.relation = 'm' AND c.card_id_b = %(c)s) x
  WHERE h.ma > 0),
-- 上位は ID と同居数だけで並べ、土地の判定は並びの上から要る分だけ（OFFSET 0 で並べ替えの外に出す＝LIMIT で止まる）。
-- カード名・分母は返す行だけ読む（以前は 3 件返すのに 1.6 万枚分の名前と分母を読み、
-- VM が混んだ時間に Sol Ring の Commander が 10 秒で打ち切られた）。
top_count AS (
  SELECT h.pid, t.partner, t.n_ab FROM hdr h CROSS JOIN LATERAL (
      SELECT s.partner, s.n_ab FROM (
          SELECT p.partner, p.n_ab FROM pairs p WHERE p.pid = h.pid ORDER BY p.n_ab DESC, p.partner OFFSET 0) s
      WHERE NOT %(no_lands)s OR NOT EXISTS (SELECT 1 FROM mtg_cards_v2 c WHERE c.id = s.partner AND c.type_line ~ 'Land')
      LIMIT %(limit)s) t),
-- lift 順は候補（同居 lift_min_ab 本以上）の分母が要る＝その母集団の分母を主キーで引く。頼まれたときだけ
top_lift AS (
  SELECT h.pid, t.partner, t.n_ab FROM hdr h CROSS JOIN LATERAL (
      SELECT s.partner, s.n_ab FROM (
          SELECT p.partner, p.n_ab,
                 p.n_ab::numeric * h.n / (h.ma::numeric * nullif(CASE WHEN %(rel)s = 's' THEN d.side_deck_count
                                                                      ELSE d.main_deck_count END, 0)) AS lift
          FROM pairs p LEFT JOIN card_population_deck_counts d ON d.population_id = p.pid AND d.card_id = p.partner
          WHERE p.pid = h.pid AND p.n_ab >= %(lift_min_ab)s
          ORDER BY round(p.n_ab::numeric * h.n / (h.ma::numeric * nullif(CASE WHEN %(rel)s = 's' THEN d.side_deck_count
                                                                      ELSE d.main_deck_count END, 0)), 1) DESC NULLS LAST,
                   p.n_ab DESC, p.partner OFFSET 0) s
      WHERE NOT %(no_lands)s OR NOT EXISTS (SELECT 1 FROM mtg_cards_v2 c WHERE c.id = s.partner AND c.type_line ~ 'Land')
      LIMIT %(limit)s) t
  WHERE %(want_lift)s),
shown AS (
  SELECT r.pid, r.k, r.partner, r.n_ab, c.name_display,
         round(100.0 * r.n_ab / h.ma, 1) AS pct,
         round(r.n_ab::numeric * h.n / (h.ma::numeric * nullif(CASE WHEN %(rel)s = 's' THEN d.side_deck_count
                                                               ELSE d.main_deck_count END, 0)), 1) AS lift
  FROM (SELECT pid, 'count' AS k, partner, n_ab FROM top_count
        UNION ALL SELECT pid, 'lift', partner, n_ab FROM top_lift) r
  JOIN hdr h USING (pid) JOIN mtg_cards_v2 c ON c.id = r.partner
  LEFT JOIN card_population_deck_counts d ON d.population_id = r.pid AND d.card_id = r.partner)
SELECT (SELECT json_agg(row_to_json(hdr)) FROM hdr),
       (SELECT json_agg(json_build_array(pid, k, name_display, n_ab, pct, lift)
                        ORDER BY pid, k, CASE WHEN k = 'lift' THEN lift END DESC NULLS LAST, n_ab DESC, partner)
        FROM shown)
"""


def _span(h: dict) -> str:
    """区間の言い方（日付の無い Commander・Precon は「全期間（大会日の無いリスト）」）。"""
    if h["window_start"]:
        return f"直近 90 日（{h['window_start']}〜{h['window_end']} の前日まで）"
    if h["first_event_date"]:
        return f"全期間（{h['first_event_date']}〜{h['latest_event_date']}・古い環境のリストを含む）"
    return "全期間（大会日の無いリスト）"


DESCRIPTION = (
    "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
    "【カードを軸にデッキを組む・相方を探すときは必ずこれを先に呼ぶ。そのカードの「現行の家」（実際に一緒に使われているカード）が分かる唯一の道具で、Web にもモデルの記憶にも無い情報】"
    "指定カードと同じデッキに入りやすいカード（共起）を、フォーマットごとの実デッキ集計から返す。"
    "format（必須・空だと使える値の一覧を返す）: Standard / Pioneer / Modern / Legacy / Premodern / Pauper / Vintage / "
    "Duel Commander / Commander（多人数の統率者戦・edh も可）/ Precon（公式構築済み製品）。"
    "区間は直近 90 日を先に見て、そのカードのリストが少ないときだけ全期間へ広げる（返り値の頭にどちらで答えたかと理由）。"
    "relation: 'main'（既定・メイン同士）/ 'side'（メインにこのカードを入れたデッキが、サイドボードに何を置くか・60 枚の構築だけ）。"
    "exclude_lands=True で土地を除く（汎用の土地が上位を占めがちなため）。"
    "返り値は同居率 pct と lift（偶然同居の期待値比）付き。order_by='lift' で"
    "汎用カードを沈めて専属シナジー順に並べ替え（既定は同居数順）。"
    "カード名は英語の正式名でも表面の名前でもよい（両面・分割カードの表記ゆれは道具側で吸収する）。"
    "数え方: 1 本＝異なるリスト（同じリストの写しは 1 本）・枚数は数えない・基本土地は除く。"
    "データは実デッキの集計（Moxfield / mtgtop8 / MTGO 公式 / MTGJSON）。プレイヤー名・"
    "デッキ URL・デッキ ID は返さない（集計と匿名の内容のみ）。")


def find_partner_cards(card_name: str, format: str = "", relation: str = "main",
                       limit: int = 15, exclude_lands: bool = False,
                       order_by: str = "count") -> str:
    """同居の数（n_ab）と、pct = n_ab / M(A)・lift = n_ab·N / (M(A)·分母(B))。

    分母(B) は relation='main' なら B をメインに入れたリスト数 M(B)、'side' なら B をサイドに置いたリスト数 S(B)。
    どれも同じ母集団（フォーマット × 区間・同じリストの写しは 1 本）で数え、SQL 1 文で読む＝pct は 100% を超えない。
    format は必須の扱いだが、既定を空にしてある＝省いた客（旧の scope で呼ぶ客）にも、MCP の引数検査の定型文でなく
    この道具の断り（使える値の一覧）を届ける。"""
    _log_tool("find_partner_cards", {"card_name": card_name, "format": format, "relation": relation,
                                     "exclude_lands": exclude_lands, "order_by": order_by})
    name = (card_name or "").strip()
    limit = max(1, min(int(limit), 30))
    if order_by not in ("count", "lift"):
        return f"order_by が不正: {order_by}（count / lift）"
    if relation not in ("main", "side"):
        return f"relation が不正: {relation}（main / side）"
    fmt = _format_of(format)
    if not fmt:
        return (f"format が不正: {format or '（空）'}（" + " / ".join(FORMATS) +
                "）。共起はフォーマットごとに数えている＝どのフォーマットの相談かを format で指定する"
                "（旧の scope 引数は廃止・constructed のように複数のフォーマットを混ぜた集計は無い）")
    rel = "s" if relation == "side" else "m"
    if rel == "s" and fmt in NO_SIDE:
        return (f"relation='side' は {fmt} には無い（サイドボードの関係は 60 枚の構築の 7 フォーマットだけ・"
                "統率者戦・デュエル・コマンダー・構築済み製品には作っていない）。relation='main' で呼び直す")

    card, amb = _resolve(name)
    if amb:
        return f"共起なし: {name}（" + ambiguous_hint(amb) + "）"
    if not card:
        return f"共起なし: {name}（" + not_found_hint(name, "英語の正式カード名か表面の名前で指定してください") + "）"
    cid, disp = card["id"], card["display"]
    if _db("SELECT 1 FROM mtg_cards_v2 WHERE id = %s AND type_line ~ '^Basic'", (cid,), lane=LANE_LIGHT):
        return f"共起なし: {disp}（基本土地は同居の集計から除いている＝どのデッキにも入るので相方の手がかりにならない）"

    hdr_json, rows_json = _db(_SQL, {"fmt": fmt, "rel": rel, "c": cid, "no_lands": bool(exclude_lands),
                                     "want_lift": order_by == "lift", "lift_min_ab": LIFT_MIN_AB, "limit": limit},
                              lane=LANE_HEAVY)[0]
    hdrs = {h["w"]: h for h in (hdr_json or [])}
    # 集計がそろっていない（未生成・公開サーバーへの写しの途中）なら、カードの観測 0 件に化けさせず「使えない」と言う
    not_ready = [w for w, h in hdrs.items() if h["n"] is None or h["card_rows"] != h["card_row_count"]]
    if not hdrs or not_ready:
        return (f"集計が準備中: {fmt} の共起の集計がまだそろっていない（" + "・".join(sorted(not_ready)) +
                "）。カードの観測が 0 件という意味ではない。時間を置いて呼び直す")

    # 区間を選ぶ: 直近 90 日を先に・足りなければ全期間
    widened = ""
    recent, whole = hdrs.get("recent_90d"), hdrs.get("all")
    h = recent or whole
    if recent and whole:
        if recent["ma"] < WIDEN_BELOW:
            widened = f"直近 90 日ではこのカードをメインに入れたリストが {recent['ma']} 本（{WIDEN_BELOW} 本未満）なので全期間で答えた"
        elif not recent["has_pairs"]:
            widened = ("直近 90 日にはこのカードの" + ("サイドの相方" if rel == "s" else "同居") +
                       "が保存されていない（数が少ない）ので全期間で答えた")
        if widened:
            h = whole
    pid, n, ma, sa = h["pid"], h["n"], h["ma"], h["sa"]
    # 古さは、答えに使った区間だけでなく、広げる判断に使った直近の区間も見る（
    # 直近の検算だけ止まって前の版が残り、全期間は新しい、という状態で「直近は足りない」の判断が古い版になりうる）
    notes = []
    consulted = [("直近 90 日", recent), ("全期間", whole)] if recent and whole else [("", h)]
    for label, x in consulted:
        if x is not h and not widened and label:
            continue            # 直近で答えたときの全期間は判断に使っていない
        at = datetime.datetime.fromisoformat(x["computed_at"])
        if datetime.datetime.now(datetime.timezone.utc) - at > STALE_AFTER:
            notes.append(f"【注意】{label + 'の' if label else 'この'}集計は {at.date()} の時点（夜間の更新が止まっている）"
                         "＝それより後のリストは入っていない")
    head = (f"format={fmt}・{_span(h)}・relation={relation}・order_by={order_by}。"
            + (f"【区間を広げた】{widened}。" if widened else "")
            + f"この区間の異なるリスト {n:,} 本のうち、このカードをメインに入れたリスト {ma:,} 本"
            + (f"（サイドに置いたリスト {sa:,} 本）" if sa else "") + "。"
            + "".join(x + "。" for x in notes))
    if ma == 0:
        return (f"共起なし: {disp}（カードは収録済み・名前は正しい。{head}"
                + "メインに入れたリストが無いので同居の集計に出ない。新しいカードや発売前のカードは実デッキが少ない）")

    rows = [r for r in (rows_json or []) if r[0] == pid]
    use = [r for r in rows if r[1] == "lift"] if order_by == "lift" and ma >= LIFT_MIN_A else []
    extra = []
    if order_by == "lift" and ma < LIFT_MIN_A:
        extra.append(f"lift 順は、このカードのリストが {LIFT_MIN_A} 本以上のときだけ使う（今は {ma} 本）＝同居数の順で返した")
    elif order_by == "lift" and not use:
        extra.append(f"同居 {LIFT_MIN_AB} 本以上の相方が無いので lift 順は使えない＝同居数の順で返した")
    if not use:
        use = [r for r in rows if r[1] == "count"]
    if rel == "s" and h["self_side"]:
        k = h["self_side"]
        extra.append(f"このカード自身をサイドにも置くリスト: {k:,} 本（メインに入れたリストの {100.0 * k / ma:.1f}%・"
                     "メインに何枚かを入れて残りをサイドに回す採用）")
    if not use:
        return (f"共起なし: {disp}（{head}" + "".join(x + "。" for x in extra)
                + ("サイドに置かれた相方" if rel == "s" else "同居するカード") + "が集計に出るほど無い）")
    out = [f"{d}: {pct}%（{n_ab} 本同居・lift {lift if lift is not None else '?'}）" for _p, _k, d, n_ab, pct, lift in use]
    what = ("メインにこのカードを入れたリストのうち、サイドボードに相方を置いた割合" if rel == "s"
            else "このカードを入れたリストのうち相方も入れている割合")
    return (f"{disp} の相方。{head}" + "".join(x + "。" for x in extra) +
            f"pct={what}・lift=偶然同居の期待値比"
            "（1.0≈どのデッキにも入る汎用カード・高いほどこのカード専属・order_by='lift' で専属順に並べ替え可）。"
            "この一覧に無いカードを、この集計に出た相方として補わない。"
            "名前は完成形《日本語名/英語名》を一字も変えずに使う・略称禁止・"
            "「英語名（日本語版なし）」もそのまま:\n" + "\n".join(out))
