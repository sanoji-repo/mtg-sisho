"""rules.py — 総合ルールと公式裁定の道具 lookup_mtg_rule・get_card_rulings。

登録（server.tool）は mcp_server.py 側。道具が 2 本あるので説明は道具ごとに
LOOKUP_MTG_RULE_DESCRIPTION・GET_CARD_RULINGS_DESCRIPTION と名前を分ける。
"""
from sisho.db import LANE_LIGHT, _db
from sisho.names import (_like_exact, ambiguous_hint, candidate_label, card_candidates, not_found_hint,
                         resolve_card, resolve_card_ex)
from sisho.toollog import _log_tool


# ─── ローカル DB 直結の道具 ───
# 試作サーバーは VM に住んでいるので、mtg_rules / card_rulings（ローカルのみ・
# Aurora 未搬入）に直接手が届く。恒久版ではこの 2 本のデータを搬入 or 焼き込みする
# （工程表 v0 の 1 番・Aurora/イメージ/VPS の裁定とセット）。読み取り専用クエリのみ。


LOOKUP_MTG_RULE_DESCRIPTION = (
    "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
    "【ルールの疑問・処理の順番・用語の定義は、Web で調べる前にこれ】"
    "MTG 総合ルール（Comprehensive Rules・条文3,317＋用語集739）を引く。"
    "条番号（例: '702.19' '601.2b'）または英語キーワード（例: 'trample' 'state-based'）"
    "で検索できる。条文の原文は英語なので、必要に応じて日本語に訳して伝えること。"
    "裁定の根拠を条番号つきで示したいときに使う。")
def lookup_mtg_rule(query: str, limit: int = 12) -> str:
    _log_tool("lookup_mtg_rule", {"query": query})

    query = query.strip()
    # 空の検索語を断る。最終フォールバックが ILIKE '%%' になるため、
    # 以前は用語集の先頭から任意の条文を「正常な結果」として返していた。
    if not query:
        return "検索語が空です。条番号（例: 702.19）か、調べたい語（例: trample・呪禁）を渡してください。"
    limit = max(1, min(int(limit), 30))
    import re as _re
    if _re.fullmatch(r"\d{1,3}(\.\d+[a-z]?)?\.?", query):
        # 前方一致は禁物: '702.19%' は 702.190（別条文）まで拾う。枝は「702.19a」
        # 形式なので「自身・直下の英字枝・直下の数字枝」だけを正規表現で許す。
        q = query.rstrip('.')
        pat = '^' + _re.escape(q) + r'(\.\d+[a-z]?|[a-z])?$'
        rows = _db(
            "SELECT rule_number, is_glossary, text_en FROM mtg_rules"
            " WHERE rule_number ~ %s ORDER BY rule_number LIMIT %s",
            (pat, limit), lane=LANE_LIGHT)
    else:
        # 語検索は FTS の AND（クライアントからのバグ報告「dies trigger simultaneous で
        # 該当なし」＝旧実装は句全体の部分一致で複数語に無力だった）。plainto_tsquery は
        # 全語 AND・語形正規化つき。ts_rank 順で条文らしさの高い順に返す。
        rows = _db(
            "SELECT rule_number, is_glossary, text_en FROM mtg_rules"
            " WHERE to_tsvector('english', text_en) @@ plainto_tsquery('english', %s)"
            " ORDER BY ts_rank(to_tsvector('english', text_en),"
            "                  plainto_tsquery('english', %s)) DESC,"
            "          is_glossary DESC, rule_number LIMIT %s",
            (query, query, limit), lane=LANE_LIGHT)
        if not rows:
            # 全語 AND が不発なら OR に降りて「多く当たる順」（ts_rank が自然にやる）。
            # クライアントの実クエリは概念の羅列（dies trigger simultaneous…）なので全語一致は稀。
            import re as _re2
            words = _re2.findall(r"[A-Za-z][A-Za-z'-]+", query)
            if len(words) >= 2:
                orq = " | ".join(words)
                rows = _db(
                    "SELECT rule_number, is_glossary, text_en FROM mtg_rules"
                    " WHERE to_tsvector('english', text_en) @@ to_tsquery('english', %s)"
                    " ORDER BY ts_rank(to_tsvector('english', text_en),"
                    "                  to_tsquery('english', %s)) DESC, rule_number"
                    " LIMIT %s", (orq, orq, limit), lane=LANE_LIGHT)
        if not rows:   # FTS 不発（一語・固有表現等）は従来の部分一致で救う
            rows = _db(
                "SELECT rule_number, is_glossary, text_en FROM mtg_rules"
                " WHERE text_en ILIKE %s OR (is_glossary AND rule_number ILIKE %s)"
                " ORDER BY is_glossary DESC, rule_number LIMIT %s",
                (f"%{query}%", f"%{query}%", limit), lane=LANE_LIGHT)   # mtg_rules は 4 千行
    if not rows:
        return f"該当なし: {query}（条番号または英語キーワードで検索してください）"
    out = []
    for num, glos, text in rows:
        tag = "用語集" if glos else "条文"
        out.append(f"[{tag} {num}] {text}")
    return "\n\n".join(out)


GET_CARD_RULINGS_DESCRIPTION = (
    "【名前の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く（略称・通称・省略・自作の訳は禁止）。記憶のカード名は書かず必ず道具で引く。答えを出す前に verify_answer に全文を通す。】"
    "【特定カードの挙動・裁定は、Web の解説記事より先にこれ（公式一次情報）】"
    "カードの公式裁定（Wizards 公式 rulings・77,998 件収録）をカード名で引く。"
    "カード名は英語の正式名（例: 'Ragavan, Nimble Pilferer'）。部分一致も可。"
    "裁定の原文は英語なので、必要に応じて日本語に訳して伝えること。")
def get_card_rulings(card_name: str, limit: int = 20) -> str:
    _log_tool("get_card_rulings", {"card_name": card_name})

    # 空のカード名を断る（下のフォールバックが ILIKE '%%' でアルファベット順の
    # 先頭から任意の裁定を返していた）。
    if not card_name.strip():
        return "カード名が空です。英語の正式カード名（例: Lightning Bolt）を渡してください。"
    limit = max(1, min(int(limit), 40))
    n = card_name.strip()
    sql = "SELECT card_name, published_at, comment FROM card_rulings WHERE card_name = %s ORDER BY published_at, id LIMIT %s"
    note = ""
    # 引く順:
    #   1 裁定の表を入力そのままで（完全一致）。裁定の表の名前のうち 732 種（次元・アンセット等）はカード表に無い＝
    #     カード表を先に見ると「見つかりません」や別カードへ滑る（Rules Lawyer・The Maelstrom・Raven's Run）。
    #   2 カード表で 1 枚に決め（names.resolve_card_ex: 完全一致 → 大文字小文字だけ違う全体一致・面の名前が複数の
    #     カードで重なれば決めない）、そのカードの正式名で引く。0 件なら「収録済み・裁定がまだ無い」＝別カードへ降りない
    #     （以前は「Fog」が《目つぶしの霧/Blinding Fog》の裁定を黙って返していた）。
    #   3 裁定の表を大文字小文字だけ無視した全体一致で（カード表に無い名前の小文字入力）。
    #   4 カード表の部分一致。候補がちょうど 1 枚のときだけ、その 1 枚だと明示して引く。2 枚以上は選ばせる。
    #   LIKE の記号（_ %）は字として扱う（「R_gavan」で Ragavan の裁定を返していた）。
    rows = _db(sql, (n, limit), lane=LANE_LIGHT)
    if not rows:
        card, amb = resolve_card_ex(n)
        if amb:
            return f"裁定なし: {n}（" + ambiguous_hint(amb) + "）"
        if card:
            rows = _db(sql, (card["card_name"], limit), lane=LANE_LIGHT)
            if card["case_fixed"]:
                note = f"「{n}」は {candidate_label(card['card_name'], card['display'], card['digital'])} として引いた:\n\n"
            if not rows:
                how = (f"「{n}」はこのカードとして解釈した" if card["case_fixed"] else "カードは収録済み・名前は正しい")
                disp = card["display"] + ("（デジタル専用）" if card["digital"] else "")
                return (f"裁定なし: {disp}（{how}。Wizards の公式裁定がこのカードにはまだ無い。"
                        "挙動は lookup_mtg_rule で総合ルールの条文から確かめる）")
    if not rows and not note:
        rows = _db("SELECT card_name, published_at, comment FROM card_rulings WHERE card_name ILIKE %s"
                   " ORDER BY published_at, id LIMIT %s", (_like_exact(n), limit))   # 大文字小文字の無視＝重いレーン
        if rows and rows[0][0] != n:
            note = f"「{n}」は {rows[0][0]} として引いた:\n\n"
    if not rows and not note:
        cands = card_candidates(n, with_rulings=True)   # 裁定の表にだけある名前も候補
        if len(cands) != 1:
            return f"裁定なし: {n}（" + not_found_hint(n, "英語の正式カード名で検索してください", cands) + "）"
        label = candidate_label(*cands[0])
        rows = _db(sql, (cands[0][0], limit), lane=LANE_LIGHT)   # 候補の正式名＝裁定の表の名前（カード表に無い名前もそのまま）
        if not rows:
            return (f"裁定なし: {label}（「{n}」を名前に含むカードはこの 1 枚。Wizards の公式裁定がこのカードにはまだ無い。"
                    "挙動は lookup_mtg_rule で総合ルールの条文から確かめる）")
        note = f"「{n}」は {label} として引いた:\n\n"
    out = [f"{name}（{date}）: {comment}" for name, date, comment in rows]
    return note + "\n\n".join(out)
