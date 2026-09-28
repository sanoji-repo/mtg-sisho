#!/usr/bin/env python3
"""find_partner_cards（共起）の振る舞い。

実デッキは毎晩増える＝数字は動く。黄金値は「行の書式」「フォーマットごとに空でない」
「不正な引数の断り方」「名前ゆれの吸収」「区間を広げたらそう言う」の形で縫い、pct や lift の値では縫わない。
ただし分母と同居の関係（pct ≤ 100・見出しの本数＝表の分母）は数字で縫う＝数え方の食い違いは値に出るため。

走らせ方: pytest tests/test_tools_partners.py -v
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m  # noqa: E402
import sisho.tools.partners as partners  # noqa: E402  monkeypatch はこちら側
from conftest import requires_db  # noqa: E402

fp = m.find_partner_cards.fn if hasattr(m.find_partner_cards, "fn") else m.find_partner_cards

# 「《日本語名/英語名》: 12.3%（45 本同居・lift 6.7）」／日本語版なしのカードは完成形が英語名（…）
ROW = re.compile(r"^(《[^》]+》|.+（(?:日本語版なし|日本語名未収録)）): [\d.]+%（\d+ 本同居・lift [\d.?]+）$")
HEAD_M = re.compile(r"このカードをメインに入れたリスト ([\d,]+) 本")


def _rows(text):
    return [ln for ln in text.split("\n")[1:] if ln.strip()]


def _num(s):
    return int(s.replace(",", ""))


# ─── DB に触らない断り（引数の検査は名前の解決より前）──────────────────────────

@pytest.fixture
def no_db(monkeypatch):
    def _fail(*a, **k):
        raise AssertionError("引数が不正なら DB へ行かない")
    monkeypatch.setattr(partners, "_db", _fail)
    monkeypatch.setattr(partners, "resolve_card_ex", _fail)


def test_rejects_bad_arguments_without_db(no_db):
    assert fp("Sol Ring", "Commander", order_by="nope") == "order_by が不正: nope（count / lift）"
    assert fp("Sol Ring", "Commander", relation="both") == "relation が不正: both（main / side）"
    r = fp("Sol Ring", "constructed")
    assert r.startswith("format が不正: constructed（Standard / Pioneer / Modern / Legacy / Premodern / Pauper / Vintage / "
                        "Duel Commander / Commander / Precon）"), r
    assert fp("Sol Ring", "").startswith("format が不正: （空）"), "format は必須"


def test_format_names_are_forgiving():
    """大文字小文字・空白・区切りは無視。edh は多人数の統率者戦、duel はデュエル・コマンダー。"""
    assert partners._format_of("pioneer") == "Pioneer"
    assert partners._format_of(" Duel_Commander ") == "Duel Commander"
    assert partners._format_of("duelcommander") == "Duel Commander"
    assert partners._format_of("EDH") == "Commander"
    assert partners._format_of("duel") == "Duel Commander"
    assert partners._format_of("constructed") is None and partners._format_of(None) is None


# ─── ここから DB ────────────────────────────────────────────────────────

@requires_db
def test_name_variants_absorbs_two_faced_names():
    """_name_variants は他の道具（verify_answer）も使う＝1 枚に決めて [正式名, 表面名]。"""
    assert partners._name_variants("Brazen Borrower") == ["Brazen Borrower // Petty Theft", "Brazen Borrower"]
    assert partners._name_variants("Petty Theft") == ["Brazen Borrower // Petty Theft", "Brazen Borrower"]
    assert partners._name_variants("Sol Ring") == ["Sol Ring"]
    assert partners._name_variants("Replenish") == ["Replenish"], "裏面名が同じ別カードに滑らない"


@requires_db
def test_commander_row_format_and_head():
    r = fp("Sol Ring", "Commander", limit=3)
    head, rows = r.split("\n")[0], _rows(r)
    assert head.startswith("《太陽の指輪/Sol Ring》 の相方。format=Commander・全期間（大会日の無いリスト）・relation=main・order_by=count。"), head
    assert "完成形《日本語名/英語名》を一字も変えず" in head, "名前の掟が返り値に自己完結して載る"
    assert "この一覧に無いカードを、この集計に出た相方として補わない" in head
    assert "【区間を広げた】" not in head, "Commander には直近の区間が無い＝広げる段が無い"
    assert len(rows) == 3 and all(ROW.match(ln) for ln in rows), rows
    assert rows[0].startswith("《統率の塔/Command Tower》"), "Sol Ring の相方の筆頭は統率の塔（順位が入れ替わったら気づく）"


@requires_db
@pytest.mark.parametrize("card,fmt", [
    ("Lightning Bolt", "Modern"), ("Lightning Bolt", "Legacy"), ("Fatal Push", "Pioneer"),
    ("Counterspell", "Vintage"), ("Counterspell", "Pauper"), ("Counterspell", "Premodern"),
    ("Swords to Plowshares", "Duel Commander"), ("Sol Ring", "Precon"), ("Sol Ring", "edh"),
])
def test_every_format_answers(card, fmt):
    r = fp(card, fmt, limit=3)
    assert not r.startswith("共起なし") and "不正" not in r.split("\n")[0], r[:300]
    assert f"format={partners._format_of(fmt)}・" in r.split("\n")[0]
    assert all(ROW.match(ln) for ln in _rows(r)), _rows(r)


@requires_db
def test_standard_uses_recent_window_for_a_staple():
    """直近のリストが多いカードは直近 90 日で答え、広げない。"""
    card = partners._db(
        "SELECT c.card_name FROM card_population_deck_counts d JOIN mtg_cards_v2 c ON c.id = d.card_id"
        " JOIN cooccurrence_populations p USING (population_id)"
        " WHERE p.format_name = 'Standard' AND p.window_code = 'recent_90d' AND c.type_line !~ 'Land'"
        " ORDER BY d.main_deck_count DESC, c.id LIMIT 1", ())[0][0]
    head = fp(card, "Standard", limit=3).split("\n")[0]
    assert "・直近 90 日（" in head and "【区間を広げた】" not in head, head


@requires_db
def test_widens_to_all_when_recent_lists_are_few():
    """直近 90 日のメインのリストが 5 本未満なら全期間へ広げ、理由を見出しで言う。"""
    row = partners._db(
        "SELECT c.card_name, r.main_deck_count FROM card_population_deck_counts a"
        " JOIN card_population_deck_counts r ON r.card_id = a.card_id"
        " JOIN mtg_cards_v2 c ON c.id = a.card_id"
        " WHERE a.population_id = (SELECT population_id FROM cooccurrence_populations WHERE format_name = 'Pioneer' AND window_code = 'all')"
        "   AND r.population_id = (SELECT population_id FROM cooccurrence_populations WHERE format_name = 'Pioneer' AND window_code = 'recent_90d')"
        "   AND r.main_deck_count BETWEEN 1 AND 4 AND a.main_deck_count >= 30 AND c.card_name NOT LIKE '%%//%%'"
        " ORDER BY a.main_deck_count DESC, c.id LIMIT 1", ())
    assert row, "直近は少なく全期間は多い Pioneer のカードが 1 枚はある"
    name, recent = row[0]
    head = fp(name, "Pioneer", limit=3).split("\n")[0]
    assert f"【区間を広げた】直近 90 日ではこのカードをメインに入れたリストが {recent} 本（5 本未満）なので全期間で答えた" in head, head
    assert "・全期間（" in head and "古い環境のリストを含む" in head, head


@requires_db
def test_widens_to_all_for_a_card_absent_from_recent_lists():
    """直近 90 日のリストに 1 本も無いカードも全期間へ（0 本も『5 本未満』）。"""
    row = partners._db(
        "SELECT c.card_name FROM card_population_deck_counts a JOIN mtg_cards_v2 c ON c.id = a.card_id"
        " WHERE a.population_id = (SELECT population_id FROM cooccurrence_populations WHERE format_name = 'Modern' AND window_code = 'all')"
        "   AND a.main_deck_count >= 50 AND c.card_name NOT LIKE '%%//%%'"
        "   AND NOT EXISTS (SELECT 1 FROM card_population_deck_counts r WHERE r.card_id = a.card_id AND r.population_id ="
        "       (SELECT population_id FROM cooccurrence_populations WHERE format_name = 'Modern' AND window_code = 'recent_90d'))"
        " ORDER BY a.main_deck_count DESC, c.id LIMIT 1", ())
    assert row
    head = fp(row[0][0], "Modern", limit=3).split("\n")[0]
    assert "リストが 0 本（5 本未満）なので全期間で答えた" in head, head


@requires_db
@pytest.mark.parametrize("card,fmt,rel", [
    ("Lightning Bolt", "Modern", "main"), ("Sol Ring", "Commander", "main"),
    ("Duress", "Pioneer", "side"), ("Counterspell", "Pauper", "side"),
])
def test_pct_uses_the_same_population_as_the_head(card, fmt, rel):
    """見出しの M(A) は表の分母そのもの（表と独立に照合）・pct = 同居 ÷ M(A)・100% を超えない。"""
    r = fp(card, fmt, relation=rel, limit=10)
    ma = _num(HEAD_M.search(r).group(1))
    window = "recent_90d" if "・直近 90 日（" in r.split("\n")[0] else "all"
    want = partners._db(
        "SELECT d.main_deck_count FROM card_population_deck_counts d JOIN cooccurrence_populations p USING (population_id)"
        " JOIN mtg_cards_v2 c ON c.id = d.card_id WHERE p.format_name = %s AND p.window_code = %s AND c.card_name = %s",
        (partners._format_of(fmt), window, card))
    assert want and want[0][0] == ma, (want, ma)
    assert len(_rows(r)) == 10, "相方が 10 行ある（空の一覧で素通りしない）"
    for ln in _rows(r):
        pct, n_ab = re.search(r": ([\d.]+)%（(\d+) 本同居", ln).groups()
        assert float(pct) <= 100.0 and int(n_ab) <= ma, ln
        assert abs(float(pct) - round(100.0 * int(n_ab) / ma, 1)) < 0.051, (ln, ma)


@requires_db
def test_side_relation_reports_the_same_card_in_the_sideboard():
    """サイドの関係: 相方の一覧には自分自身を出さず、『自身をサイドにも置く』本数を別に言う。"""
    r = fp("Duress", "Pioneer", relation="side", limit=30)
    assert "relation=side" in r.split("\n")[0]
    assert "このカード自身をサイドにも置くリスト:" in r, r[:400]
    assert not any(ln.startswith("《強迫/Duress》") for ln in _rows(r)), "相方の一覧に自分を出さない"


@requires_db
def test_side_relation_is_refused_for_commander_formats():
    for fmt in ("Commander", "Duel Commander", "Precon"):
        r = fp("Sol Ring", fmt, relation="side")
        assert r.startswith(f"relation='side' は {fmt} には無い"), r


@requires_db
def test_basic_land_is_explained():
    assert fp("Island", "Modern").startswith("共起なし: 《島/Island》（基本土地は同居の集計から除いている")


@requires_db
def test_limit_bounds():
    assert len(_rows(fp("Sol Ring", "Commander", limit=999))) == 30, "上限 30"
    assert len(_rows(fp("Sol Ring", "Commander", limit=0))) == 1, "下限 1"


@requires_db
def test_exclude_lands():
    r = fp("Sol Ring", "Commander", limit=20, exclude_lands=True)
    names = [ln.split("》:")[0].rsplit("/", 1)[-1] for ln in _rows(r)]
    assert len(names) == 20, "相方が 20 行ある（空の一覧で素通りしない）"
    lands = partners._db("SELECT card_name FROM mtg_cards_v2 WHERE card_name = ANY(%s) AND type_line ~ 'Land'", (names,))
    assert not lands, lands


@requires_db
def test_order_by_lift():
    r = fp("Sol Ring", "Commander", limit=10, order_by="lift")
    lifts = [float(x) for x in re.findall(r"lift ([\d.]+)）", r)]
    assert lifts == sorted(lifts, reverse=True) and len(lifts) == 10, lifts
    assert all(int(x) >= partners.LIFT_MIN_AB for x in re.findall(r"（(\d+) 本同居", r))


@requires_db
def test_two_faced_and_case_insensitive_names():
    base = _rows(fp("Brazen Borrower", "Modern", limit=5))
    assert base and base == _rows(fp("Petty Theft", "Modern", limit=5)) == _rows(fp("brazen borrower", "Modern", limit=5))


@requires_db
def test_unknown_partial_and_ambiguous_names():
    r = fp("Zzzqqq Xxxyyy", "Commander")
    assert r.startswith("共起なし: Zzzqqq Xxxyyy") and "英語の正式カード名" in r, r
    r = fp("Shang-Chi", "Commander")
    assert r.startswith("共起なし: Shang-Chi（この名前ちょうどのカードは無い。名前に含むカード: 《"), r
    r = fp("Vicious Verse", "Commander")
    assert r.startswith("共起なし: Vicious Verse（この名前は複数のカードの面の名前で、1 枚に決まらない: "), r
    r = fp("Brazen Borrower // Totally Wrong", "Modern")
    assert r.startswith("共起なし: Brazen Borrower // Totally Wrong（カードが見つかりません"), r[:200]


@requires_db
def test_known_card_without_lists_is_not_blamed_on_the_name():
    """カードが在るのに実デッキに無いときは、名前を疑わせず『収録済み・0 本』を返す。"""
    row = partners._db(
        "SELECT m.card_name FROM mtg_cards_v2 m WHERE NOT m.digital AND m.card_name NOT LIKE '%%//%%'"
        " AND m.type_line !~ '^Basic' AND NOT EXISTS (SELECT 1 FROM card_population_deck_counts d"
        " JOIN cooccurrence_populations p USING (population_id) WHERE d.card_id = m.id AND p.format_name = 'Pioneer')"
        " ORDER BY m.id LIMIT 1", ())
    r = fp(row[0][0], "Pioneer")
    assert "カードは収録済み・名前は正しい" in r and "メインに入れたリストが 0 本" in r, r
    assert "英語の正式カード名" not in r


@requires_db
def test_population_tables_are_consistent():
    """母集団 18 個の stats がそろい、分母は N を超えず、組の両端に分母があり、同居は上限と下限（M(A)+分母(B)−同居 ≤ N）を
    守る（夜間の集計の検算と同じ不等式を、全母集団の保存済みの表で見る）。"""
    n = partners._db("SELECT count(*) FROM cooccurrence_population_stats", ())[0][0]
    assert n == 18, n
    bad = partners._db(
        "SELECT count(*) FROM card_population_deck_counts d JOIN cooccurrence_population_stats s USING (population_id)"
        " WHERE d.main_deck_count > s.deck_count OR d.side_deck_count > s.deck_count", ())[0][0]
    assert bad == 0
    orphan, over, under = partners._db(
        "SELECT count(*) FILTER (WHERE a.card_id IS NULL OR nb IS NULL),"
        "       count(*) FILTER (WHERE c.cooccurrence_count > least(a.main_deck_count, nb)),"
        "       count(*) FILTER (WHERE a.main_deck_count + nb - c.cooccurrence_count > s.deck_count)"
        " FROM card_cooccurrence_v2 c"
        " JOIN cooccurrence_population_stats s USING (population_id)"
        " LEFT JOIN card_population_deck_counts a ON a.population_id = c.population_id AND a.card_id = c.card_id_a"
        " LEFT JOIN card_population_deck_counts b ON b.population_id = c.population_id AND b.card_id = c.card_id_b"
        " CROSS JOIN LATERAL (SELECT CASE c.relation WHEN 'm' THEN b.main_deck_count ELSE b.side_deck_count END AS nb) x", ())[0]
    assert (orphan, over, under) == (0, 0, 0), (orphan, over, under)


# ─── 読み取りの一貫性・準備中・古さ・lift の戻し・斜線の名前 ──────────────────────────

def _spy_db(monkeypatch, edit=None):
    """partners._db を包む: 呼ばれた SQL を記録し、1 文の読み取りの返り値（見出し・一覧）を edit で書き換えられる。"""
    calls, real = [], partners._db

    def spy(sql, params, lane=None):
        calls.append(sql)
        out = real(sql, params, lane=lane) if lane else real(sql, params)
        if edit and "json_agg(row_to_json(hdr))" in sql:
            hdr, rows = out[0]
            out = [edit(hdr, rows)]
        return out
    monkeypatch.setattr(partners, "_db", spy)
    return calls


@requires_db
@pytest.mark.parametrize("card,fmt,rel", [("Duress", "Pioneer", "side"), ("Insidious Roots", "Pioneer", "main"),
                                          ("Sol Ring", "Commander", "main")])
def test_reads_header_and_rows_in_one_statement(monkeypatch, card, fmt, rel):
    """区間を選ぶ材料・見出しの分母・一覧を SQL 1 文で読む（別々に読むと夜間の適用をまたいで版が混ざる）。"""
    calls = _spy_db(monkeypatch)
    r = fp(card, fmt, relation=rel, limit=5)
    assert _rows(r), r[:200]
    touching = [q for q in calls if "card_cooccurrence_v2" in q or "card_population_deck_counts" in q
                or "cooccurrence_population_stats" in q]
    assert len(touching) == 1, f"集計の表を読む文が {len(touching)} 本"


@requires_db
def test_not_ready_population_is_not_reported_as_zero(monkeypatch):
    """分母の表が stats の行数とそろっていない（公開サーバーへの写しの途中）なら『準備中』と言い、0 本とは言わない。"""
    def half(hdr, rows):
        for h in hdr:
            h["card_rows"] = h["card_row_count"] // 2
        return hdr, rows
    _spy_db(monkeypatch, half)
    r = fp("Lightning Bolt", "Modern")
    assert r.startswith("集計が準備中: Modern の共起の集計がまだそろっていない（all・recent_90d）"), r
    assert "0 本" not in r


@requires_db
def test_missing_recent_stats_is_not_silently_widened(monkeypatch):
    """直近 90 日の stats が無い（未生成）ときに、黙って全期間で答えない。"""
    def no_recent(hdr, rows):
        for h in hdr:
            if h["w"] == "recent_90d":
                h["n"] = None
        return hdr, rows
    _spy_db(monkeypatch, no_recent)
    r = fp("Lightning Bolt", "Modern")
    assert r.startswith("集計が準備中: Modern の共起の集計がまだそろっていない（recent_90d）"), r


@requires_db
def test_stale_population_says_so(monkeypatch):
    """夜間の更新が止まって古い集計なら、見出しでそう言う（答えは返す）。"""
    def old(hdr, rows):
        for h in hdr:
            h["computed_at"] = "2026-01-01T00:00:00+00:00"
        return hdr, rows
    _spy_db(monkeypatch, old)
    r = fp("Lightning Bolt", "Modern", limit=3)
    assert "【注意】直近 90 日の集計は 2026-01-01 の時点（夜間の更新が止まっている）" in r.split("\n")[0] and _rows(r), r[:300]
    r = fp("Sol Ring", "Commander", limit=3)
    assert "【注意】この集計は 2026-01-01 の時点" in r.split("\n")[0], "区間が 1 つのフォーマットは『この集計』"


@requires_db
def test_stale_recent_used_for_widening_is_reported(monkeypatch):
    """直近だけ古く全期間は新しい状態で全期間へ広げたとき、判断に使った直近の古さを言う（答えは全期間で返す）。"""
    def old_recent(hdr, rows):
        for h in hdr:
            if h["w"] == "recent_90d":
                h["computed_at"] = "2026-01-01T00:00:00+00:00"
        return hdr, rows
    _spy_db(monkeypatch, old_recent)
    head = fp("Insidious Roots", "Pioneer", limit=3).split("\n")[0]
    assert "【区間を広げた】" in head and "【注意】直近 90 日の集計は 2026-01-01 の時点" in head, head
    assert "【注意】全期間" not in head, "新しい全期間には注意を出さない"


@requires_db
def test_stale_whole_is_not_reported_when_recent_answers(monkeypatch):
    """直近で答えたときは、判断に使っていない全期間の古さは言わない。"""
    def old_whole(hdr, rows):
        for h in hdr:
            if h["w"] == "all":
                h["computed_at"] = "2026-01-01T00:00:00+00:00"
        return hdr, rows
    _spy_db(monkeypatch, old_whole)
    head = fp("Lightning Bolt", "Modern", limit=3).split("\n")[0]
    assert "・直近 90 日（" in head and "【注意】" not in head, head


@requires_db
def test_lift_without_candidates_falls_back_to_count(monkeypatch):
    """lift 順の候補（同居 5 本以上）が無ければ、空で返さず同居数の順で返してそう言う。"""
    def drop_lift(hdr, rows):
        return hdr, [x for x in rows if x[1] != "lift"]
    _spy_db(monkeypatch, drop_lift)
    r = fp("Sol Ring", "Commander", limit=3, order_by="lift")
    assert "同居 5 本以上の相方が無いので lift 順は使えない＝同居数の順で返した" in r and len(_rows(r)) == 3, r[:400]


SLASH_NAMES = ["Bedeck / Bedazzle", "Claim / Fame", "Collision / Colossus", "Commit / Memory", "Connive / Concoct",
               "Consign / Oblivion", "Cut / Ribbons", "Dead / Gone", "Defiled Crypt / Cadaver Lab", "Fire / Ice",
               "Incubation / Incongruity", "Never / Return", "Repudiate / Replicate", "Roaring Furnace / Steaming Sauna",
               "Spring / Mind", "Start / Finish", "Unholy Annex / Ritual Chamber", "Walk-In Closet / Forgotten Cellar"]


@requires_db
def test_single_slash_names_resolve_only_to_the_exact_full_name():
    """mtgtop8 の斜線 1 本の表記は、斜線 2 本の正式名にちょうど一致するときだけ受ける（旧の版で引けていた 18 種）。"""
    for n in SLASH_NAMES:
        card, _ = partners._resolve(n)
        assert card and card["card_name"] == n.replace(" / ", " // "), (n, card)
    for n in ("Fire / Totally Wrong", "Totally / Wrong", "Fire /", "Claim / Fame / Extra"):
        assert partners._resolve(n) == (None, []), n
    assert _rows(fp("Fire / Ice", "Modern", limit=3)) == _rows(fp("Fire // Ice", "Modern", limit=3))


@requires_db
def test_zero_lists_keeps_the_widening_reason():
    """全期間へ広げても 0 本だったとき、広げた理由を落とさない。"""
    r = fp("Sol Ring", "Pioneer")
    assert r.startswith("共起なし: 《太陽の指輪/Sol Ring》（カードは収録済み・名前は正しい。"), r
    assert "【区間を広げた】直近 90 日ではこのカードをメインに入れたリストが 0 本（5 本未満）なので全期間で答えた" in r, r


def test_old_scope_call_through_mcp_gets_the_format_list():
    """旧の呼び方（scope だけ・format なし）でも、MCP の引数検査の定型文でなく、使える format の一覧が届く。"""
    import asyncio

    async def main():
        res = await m.server.call_tool("find_partner_cards", {"card_name": "Sol Ring", "scope": "edh"})
        return res.content[0].text if hasattr(res, "content") else res[0].text
    text = asyncio.run(main())
    assert text.startswith("format が不正: （空）（Standard / Pioneer / Modern"), text
    assert "旧の scope 引数は廃止" in text
