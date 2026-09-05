"""partners.py — 共起の道具 find_partner_cards（2026-09-05 Step 3 で mcp_server.py から切り出し）。

登録（server.tool）は mcp_server.py 側。ここは DESCRIPTION と素の関数だけを持つ。
"""
from sisho.db import _db
from sisho.toollog import _log_tool


def _name_variants(name: str) -> list[str]:
    """両面・分割カードの名前ゆれを吸収する候補名を返す（2026-08-13）。

    棚ごとにキーの持ち方が違うのが根本原因:
      card_cooccurrence（mtgtop8 系・古い）     → 名前キー。中身は mtgtop8 が書く
                                                  「表の名前」（例 Brazen Borrower）
      edh_card_cooccurrence_v2（7/30 新設）     → ID キー。カード表の正式名で引く
                                                  （例 Brazen Borrower // Petty Theft）
    道具の入口でこの差を吸収しないと、scope によって通る名前が逆になる
    （実測 2026-08-13: 正式名は constructed で空振り・表の名前は edh で空振り）。
    該当は card_name に ' // ' を持つ 810 枚。
    """
    out = [name]
    front = name.split(" // ")[0]
    if front != name:
        out.append(front)                       # 正式名 → 表の名前
    else:                                       # 表の名前 → 正式名（DB 引き）
        out += [r[0] for r in _db(
            "SELECT card_name FROM mtg_cards_v2 WHERE name_en_back IS NOT NULL AND (name_en_front = %s OR name_en_back = %s)",
            (name, name))]
    seen, uniq = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


DESCRIPTION = (
    "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
    "【カードを軸にデッキを組む・相方を探すときは必ずこれを先に呼ぶ。そのカードの「現行の家」（実際に一緒に使われている札）が分かる唯一の道具で、Web にもモデルの記憶にも無い情報】"
    "指定カードと同じデッキに入りやすいカード（共起）を実デッキ集計から返す。"
    "Phase 2 のデッキ壁打ち用。scope: 'edh'（統率者・既定）/ 'constructed'"
    "（mtgtop8＋MTGO 公式の 60 枚構築）/ 'pauper' / 'vintage' / 'precon'（公式構築済み製品）。"
    "exclude_lands=True で土地を除く（汎用フェッチ等が上位を占めがちなため）。"
    "返り値は同居率 pct と lift（偶然同居の期待値比）付き。order_by='lift' で"
    "汎用札を沈めて専属シナジー順に並べ替え（既定は同居数順・2026-08-25）。"
    "カード名は英語の正式名でも表面の名前でもよい（両面・分割カードの表記ゆれは"
    "道具側で吸収する・2026-08-13）。"
    "【内部専用】この道具はデータ出自（Moxfield/mtgtop8）の許可が未決着のため"
    "公開版カセットには含めない（2026-08-11 権利札）。")
def find_partner_cards(card_name: str, scope: str = "edh",
                       limit: int = 15, exclude_lands: bool = False,
                       order_by: str = "count") -> str:
    """共起ペアは片方向格納＝両面 UNION で引く（2026-08-11 実査）。

    名前は表記ゆれを吸収して引く（_name_variants・2026-08-13）。結果側の JOIN も
    同じ理由で表の名前を許す＝相方が両面カードのとき静かに落ちるのを防ぐ
    （実測: 相方名 215 種・7,958 ペアが落ちていた。うち 156 種は表の名前で救える）。

    2026-08-25 分母導入（本人裁定「パーセンテージ表記に賛成」）:
    - pct = n_ab / (このカード入りデッキ数)。分母は毎回 deck_cards から実測
      （edh_card_strength の play_decks は母集団の一致が未検証なので借りない）。
    - lift = pct / (相方カードの全体出現率)。1.0 ≈ 偶然同居（汎用札）・高いほど専属シナジー。
    - order_by='lift' は同居 10 本以上・生カウント上位 400 の中で並べ替え
      （少数サンプルの lift 暴発と全相方の分母計算の重さを両方避ける近似）。
    - 分母の deck_list 直数えは共起集計の重複除去・MTGO 転載除外を再現しない
      ＝pct は 2〜3% 控えめに出る近似（構築 scope でやや大きめ）。"""
    _log_tool("find_partner_cards", {"card_name": card_name, "scope": scope,
                                     "exclude_lands": exclude_lands, "order_by": order_by})
    name = card_name.strip()
    limit = max(1, min(int(limit), 30))
    if order_by not in ("count", "lift"):
        return f"order_by が不正: {order_by}（count / lift）"
    land_cond = " AND c.type_line NOT ILIKE '%%Land%%'" if exclude_lands else ""
    names = _name_variants(name)
    # lift 順は母集団を広めに取ってから並べ替える（count 順は最初から limit で足りる）
    pool_limit = 400 if order_by == "lift" else limit
    min_ab = 10 if order_by == "lift" else 1
    order_sql = "lift DESC NULLS LAST" if order_by == "lift" else "n_ab DESC"
    # デッキ側 source（分母とペアの母集団を揃える）
    deck_src = {"edh": ["moxfield_edh", "mtgtop8_edh"],
                "commander": ["moxfield_edh", "mtgtop8_edh"],
                "constructed": ["mtgtop8", "mtgo", "mtgo_other"],
                "pauper": ["mtgtop8_pauper", "mtgo_pauper"],
                "vintage": ["mtgtop8_vintage", "mtgo_vintage"],
                "precon": ["mtgjson_precon"]}.get(scope)
    if not deck_src:
        return f"scope が不正: {scope}（edh/constructed/pauper/vintage/precon）"
    if scope in ("edh", "commander"):
        pair_sql = (
            "  SELECT e.card_id_b AS pid, e.deck_count AS cnt"
            "  FROM edh_card_cooccurrence_v2 e JOIN mtg_cards_v2 a ON a.id=e.card_id_a"
            "  WHERE a.card_name = ANY(%(names)s)"
            "  UNION ALL"
            "  SELECT e.card_id_a, e.deck_count"
            "  FROM edh_card_cooccurrence_v2 e JOIN mtg_cards_v2 b ON b.id=e.card_id_b"
            "  WHERE b.card_name = ANY(%(names)s)")
        resolve = "SELECT x.pid, sum(x.cnt) AS n_ab FROM (" + pair_sql + ") x GROUP BY x.pid"
    else:
        # 2026-08-22: MTGO 公式を source に追加（mtgtop8 側は MTGO 転載行を被覆期間で除外済み＝二重なし）
        # 相方名 → カード表。正式名でも表の名前でも当たるようにする。
        # 衝突は実測ゼロ（唯一の一致は SP//dr が自分自身に当たる自己一致）。
        pair_sql = (
            "  SELECT card_name_b AS pname, co_count AS cnt FROM card_cooccurrence"
            "  WHERE card_name_a = ANY(%(names)s) AND source = ANY(%(csrc)s)"
            "  UNION ALL"
            "  SELECT card_name_a, co_count FROM card_cooccurrence"
            "  WHERE card_name_b = ANY(%(names)s) AND source = ANY(%(csrc)s)")
        resolve = (
            "SELECT cc.id AS pid, sum(x.cnt) AS n_ab FROM (" + pair_sql + ") x"
            " JOIN mtg_cards_v2 cc"
            # 2026-09-05: split_part(card_name) → 列 name_en_front（全行で同値・実測 0 差）。関数を掛けた式は
            # 索引に無く OR で前半の索引も死んで 32,730×1,821 の全比較（VM 5.9 秒・箱は 10 秒で timeout）だった。
            # name_en_front に索引（mtg_cards_v2_name_en_front_idx・VM と箱の両方に張る）→ BitmapOr で 0.6 秒。
            "  ON (cc.card_name = x.pname OR cc.name_en_front = x.pname)"
            " GROUP BY cc.id")
    rows = _db(
        "WITH pool AS (SELECT id FROM deck_list WHERE source = ANY(%(dsrc)s)),"
        " npool AS (SELECT count(*) AS n FROM pool),"
        " da AS (SELECT count(DISTINCT dc.deck_id) AS n FROM deck_cards dc"
        "        JOIN pool p ON p.id = dc.deck_id"
        "        WHERE dc.card_id IN (SELECT id FROM mtg_cards_v2 WHERE card_name = ANY(%(names)s))),"
        " agg AS (" + resolve + "),"
        " top AS (SELECT agg.pid, agg.n_ab FROM agg JOIN mtg_cards_v2 c ON c.id = agg.pid"
        "         WHERE agg.n_ab >= %(min_ab)s" + land_cond +
        "         ORDER BY agg.n_ab DESC LIMIT %(pool_limit)s),"
        " nb AS (SELECT dc.card_id, count(DISTINCT dc.deck_id) AS n FROM deck_cards dc"
        "        JOIN pool p ON p.id = dc.deck_id"
        "        WHERE dc.card_id IN (SELECT pid FROM top) GROUP BY dc.card_id)"
        " SELECT c.card_name, c.name_display, t.n_ab,"
        "        round(100.0 * t.n_ab / greatest(da.n, 1), 1) AS pct,"
        "        round((t.n_ab::numeric / greatest(da.n, 1))"
        "              / nullif(nb.n::numeric / npool.n, 0), 1) AS lift"
        " FROM top t JOIN mtg_cards_v2 c ON c.id = t.pid"
        " LEFT JOIN nb ON nb.card_id = t.pid, da, npool"
        " ORDER BY " + order_sql + " LIMIT %(limit)s",
        {"names": names, "dsrc": deck_src, "csrc": deck_src,
         "min_ab": min_ab, "pool_limit": pool_limit, "limit": limit})
    if not rows:
        return f"共起なし: {name}（scope={scope}・英語の正式カード名で指定してください）"
    out = [f"{disp}: {pct}%（{n_ab} 本同居・lift {lift if lift is not None else '?'}）"
           for en, disp, n_ab, pct, lift in rows]
    return (f"scope={scope}・order_by={order_by} の同居カード上位。"
            "pct=このカード入りデッキのうち相方も入れている割合・lift=偶然同居の期待値比"
            "（1.0≈どのデッキにも入る汎用札・高いほどこのカード専属のシナジー・"
            "order_by='lift' で専属順に並べ替え可）。"
            "名前は完成形《日本語名/英語名》を一字も変えずに使う・略称禁止・"
            "「英語名（日本語版なし）」もそのまま:\n" + "\n".join(out))
