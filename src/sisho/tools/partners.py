"""partners.py — 共起の道具 find_partner_cards（2026-09-05 Step 3 で mcp_server.py から切り出し）。

登録（server.tool）は mcp_server.py 側。ここは DESCRIPTION と素の関数だけを持つ。
"""
from sisho.db import LANE_HEAVY, _db
from sisho.names import resolve_face_name
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

    2026-09-05（Step 6 作業 1）: 「面の名前 → 正式名」の DB 引きは sisho/names.py の
    resolve_face_name に寄せた（同じ規則が rules.py にも別実装であった＝片方だけ直す事故を防ぐ）。
    候補の集合はそのまま（実測の根拠は names.py の冒頭）。
    """
    out = [name]
    front = name.split(" // ")[0]
    if front != name:
        out.append(front)                       # 正式名 → 表の名前
    else:                                       # 表の名前 → 正式名（DB 引き・名前の解決は names.py に 1 つ）
        out += resolve_face_name(name)
    seen, uniq = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


# scope → デッキ側 source（分母とペアの母集団を揃える）。edh と commander は同じ。
# DESCRIPTION の scope の説明と scope が不正 の文言は手で同期している（commander は別名なので文言には出さない）。
_SCOPE_SOURCES: dict[str, list[str]] = {
    "edh": ["moxfield_edh", "mtgtop8_edh"],
    "commander": ["moxfield_edh", "mtgtop8_edh"],
    "constructed": ["mtgtop8", "mtgo", "mtgo_other"],
    "pauper": ["mtgtop8_pauper", "mtgo_pauper"],
    "vintage": ["mtgtop8_vintage", "mtgo_vintage"],
    "precon": ["mtgjson_precon"],
}


DESCRIPTION = (
    "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
    "【カードを軸にデッキを組む・相方を探すときは必ずこれを先に呼ぶ。そのカードの「現行の家」（実際に一緒に使われているカード）が分かる唯一の道具で、Web にもモデルの記憶にも無い情報】"
    "指定カードと同じデッキに入りやすいカード（共起）を実デッキ集計から返す。"
    "Phase 2 のデッキ壁打ち用。scope: 'edh'（統率者・既定）/ 'constructed'"
    "（mtgtop8＋MTGO 公式の 60 枚構築）/ 'pauper' / 'vintage' / 'precon'（公式構築済み製品）。"
    "exclude_lands=True で土地を除く（汎用フェッチ等が上位を占めがちなため）。"
    "返り値は同居率 pct と lift（偶然同居の期待値比）付き。order_by='lift' で"
    "汎用カードを沈めて専属シナジー順に並べ替え（既定は同居数順・2026-08-25）。"
    "カード名は英語の正式名でも表面の名前でもよい（両面・分割カードの表記ゆれは"
    "道具側で吸収する・2026-08-13）。"
    "データは実デッキの集計（Moxfield / mtgtop8 / MTGO 公式）。プレイヤー名・"
    "デッキ URL・デッキ ID は返さない（集計と匿名の内容のみ）。")
def find_partner_cards(card_name: str, scope: str = "edh",
                       limit: int = 15, exclude_lands: bool = False,
                       order_by: str = "count") -> str:
    """共起ペアは片方向格納＝両面 UNION で引く（2026-08-11 実査）。

    名前は表記ゆれを吸収して引く（_name_variants・2026-08-13）。結果側の JOIN も
    同じ理由で表の名前を許す＝相方が両面カードのとき静かに落ちるのを防ぐ
    （実測: 相方名 215 種・7,958 ペアが落ちていた。うち 156 種は表の名前で救える）。

    2026-08-25 分母導入（設計判断「パーセンテージ表記に賛成」）:
    - pct = n_ab / (このカード入りデッキ数)。分母は毎回 deck_cards から実測
      （edh_card_strength の play_decks は母集団の一致が未検証なので借りない）。
    - lift = pct / (相方カードの全体出現率)。1.0 ≈ 偶然同居（汎用カード）・高いほど専属シナジー。
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
    land_where = " WHERE c.type_line NOT ILIKE '%%Land%%'" if exclude_lands else ""
    names = _name_variants(name)
    # lift 順は母集団を広めに取ってから並べ替える（count 順は最初から limit で足りる）
    pool_limit = 400 if order_by == "lift" else limit
    min_ab = 10 if order_by == "lift" else 1
    # 2026-09-14: mtg_cards_v2 と突き合わせる前に候補を絞る（下の top）。土地かどうかは
    # mtg_cards_v2 にしか無いので、exclude_lands のときは落ちる分を見込んで多めに取る。
    # 実測（《太陽の指輪/Sol Ring》= EDH の最悪ケース）: 上位 20 件のうち非土地 6・
    # 上位 50 件のうち 27・上位 100 件のうち 62。10 倍取れば実用上は一度で足りるが、
    # 足りなければ下で cand_limit=None（LIMIT NULL＝無制限）にして引き直す＝答えは変わらない。
    cand_limit = pool_limit * 10 if exclude_lands else pool_limit
    # 同値の決着まで決めて並びを決定的にする（2026-09-14）。同居数が同じカードは多く
    # （例: 「6 本同居」が何十枚もある）、決着手段が無いと「どれが上位 15 に残るか」が
    # 実行計画に左右される＝候補の絞り方を変えると別のカードが返っていた。id で決着させる。
    order_sql = ("lift DESC NULLS LAST, t.n_ab DESC, t.pid" if order_by == "lift"
                 else "n_ab DESC, t.pid")
    # デッキ側 source（分母とペアの母集団を揃える）
    deck_src = _SCOPE_SOURCES.get(scope)
    if not deck_src:
        return f"scope が不正: {scope}（edh/constructed/pauper/vintage/precon）"
    # 分母の表（card_scope_deck_counts・scope_deck_counts）の鍵。commander は edh の別名なので
    # 表には edh だけを入れてある（2026-09-14・#819）。
    scope_key = "edh" if scope == "commander" else scope
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
            # 索引に無く OR で前半の索引も死んで 32,730×1,821 の全比較（VM 5.9 秒・公開サーバーは 10 秒で timeout）だった。
            # name_en_front に索引（mtg_cards_v2_name_en_front_idx・VM と公開サーバーの両方に張る）→ BitmapOr で 0.6 秒。
            "  ON (cc.card_name = x.pname OR cc.name_en_front = x.pname)"
            " GROUP BY cc.id")
    # 2026-09-14（#819）: 分母を夜間ジョブの表から引く。以前は呼ばれるたびに
    # pool（deck_list の source 絞り込み・constructed は 33 万行）を作り、そこへ
    # deck_cards（1,376 万行）を JOIN して da と nb を数え直していた。実測ではこれが
    # constructed で全体の 48%・edh で 31% を占めていた（残りは共起の集計）。
    # 表は card_scope_deck_counts（scope, card_id → n_decks）と scope_deck_counts。
    # 数え方は同じ（board を区別せず・土地も含み・二重計上も除かない）＝答えは変わらない。
    # 表が無い/その scope の行が無いときは 0 件でなく従来どおり数えるのでなく、
    # pct と lift が NULL になる（下の COALESCE で分母 0 を避ける）＝夜間ジョブが回れば埋まる。
    sql = (
        "WITH da AS (SELECT COALESCE(max(d.n_decks), 0) AS n FROM card_scope_deck_counts d"
        "            JOIN mtg_cards_v2 m ON m.id = d.card_id"
        "            WHERE d.scope = %(scope)s AND m.card_name = ANY(%(names)s)),"
        " npool AS (SELECT COALESCE(max(n_decks), 0) AS n FROM scope_deck_counts"
        "           WHERE scope = %(scope)s),"
        " agg AS (" + resolve + "),"
        # 候補を先に絞ってから mtg_cards_v2 と突き合わせる（2026-09-14）。以前は agg の全行
        # （《太陽の指輪/Sol Ring》で 12,651 行）を JOIN してから並べて上位を取っていた＝
        # 3 枚返すのに 12,651 枚の type_line を引いていた。実測 371MB・箱では 10 秒 timeout。
        # 先に絞ると 75MB・答えは一字一句同じ（docs/me/db_diagnostics_20260914_partners_cold.md）。
        " top AS (SELECT cand.pid, cand.n_ab FROM"
        "           (SELECT agg.pid, agg.n_ab FROM agg WHERE agg.n_ab >= %(min_ab)s"
        "            ORDER BY agg.n_ab DESC, agg.pid LIMIT %(cand_limit)s) cand"
        "         JOIN mtg_cards_v2 c ON c.id = cand.pid" + land_where +
        "         ORDER BY cand.n_ab DESC, cand.pid LIMIT %(pool_limit)s),"
        " nb AS (SELECT d.card_id, d.n_decks AS n FROM card_scope_deck_counts d"
        "        WHERE d.scope = %(scope)s AND d.card_id IN (SELECT pid FROM top))"
        " SELECT c.card_name, c.name_display, t.n_ab,"
        "        round(100.0 * t.n_ab / greatest(da.n, 1), 1) AS pct,"
        "        round((t.n_ab::numeric / greatest(da.n, 1))"
        "              / nullif(nb.n::numeric / nullif(npool.n, 0), 0), 1) AS lift"
        " FROM top t JOIN mtg_cards_v2 c ON c.id = t.pid"
        " LEFT JOIN nb ON nb.card_id = t.pid, da, npool"
        " ORDER BY " + order_sql + " LIMIT %(limit)s")
    params = {"names": names, "dsrc": deck_src, "csrc": deck_src, "scope": scope_key,
              "min_ab": min_ab, "pool_limit": pool_limit, "limit": limit,
              "cand_limit": cand_limit}
    rows = _db(sql, params, lane=LANE_HEAVY)
    if exclude_lands and len(rows) < limit:
        # 候補の中の非土地が足りなかった＝絞らずに引き直す（LIMIT NULL は無制限）。
        # 正しさを候補の数に依存させないための受け皿で、実測では走らない。
        rows = _db(sql, {**params, "cand_limit": None}, lane=LANE_HEAVY)
    if not rows:
        return f"共起なし: {name}（scope={scope}・英語の正式カード名で指定してください）"
    out = [f"{disp}: {pct}%（{n_ab} 本同居・lift {lift if lift is not None else '?'}）"
           for en, disp, n_ab, pct, lift in rows]
    return (f"scope={scope}・order_by={order_by} の同居カード上位。"
            "pct=このカード入りデッキのうち相方も入れている割合・lift=偶然同居の期待値比"
            "（1.0≈どのデッキにも入る汎用カード・高いほどこのカード専属のシナジー・"
            "order_by='lift' で専属順に並べ替え可）。"
            "名前は完成形《日本語名/英語名》を一字も変えずに使う・略称禁止・"
            "「英語名（日本語版なし）」もそのまま:\n" + "\n".join(out))
