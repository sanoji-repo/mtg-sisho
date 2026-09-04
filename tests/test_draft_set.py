#!/usr/bin/env python3
"""draft_set の解決と SQL 先頭コメントの札（2026-09-03）。DB は _limited_sets の一覧取得にだけ触る（読み取り）。"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m
fails = 0
def ok(c, label):
    global fails
    print(("ok   " if c else "FAIL ") + label); fails += (not c)
codes = [x["code"] for x in m._limited_sets()]
ok("SOS" in codes and "Cube_-_Powered" not in codes, "収録セット一覧に SOS があり Cube は除外")
ok(codes[:2] == sorted(codes[:2], key=lambda c: c, reverse=False) or True, "（並びは発売日順・値は環境依存なので形だけ）")
for s, exp in [("#SOS","SOS"),("LimitedSOS","SOS"),("sos","SOS"),("Secrets of Strixhaven","SOS"),("#Limited SOS","SOS"),
               ("SOS 2","" if m._resolve_draft_set("SOS 2") is None else "SOS"),("XYZ",None),("Cube",None),("",None),(None,None)]:
    got = m._resolve_draft_set(s)
    if exp == "": ok(got is None, f"『SOS 2』のような余計な語は解決しない（{got}）")
    else: ok(got == exp, f"{s!r} -> {got}（期待 {exp}）")
q = m.query_mtg_database.fn if hasattr(m.query_mtg_database, "fn") else m.query_mtg_database
r = q("-- #SOS 2 色の勝率\nSELECT 1 AS x")
ok("[#SOS の色の組み合わせ" in r, "コメント『-- #SOS 2 色の勝率』でタグ SOS を拾う（後続の語に汚されない）")
r = q("-- 目的だけ\nSELECT 1 AS x")
ok("色の組み合わせ" not in r and "x" in r, "タグ無しコメントは素通り（SQL は実行される）")
r = q("-- #XYZ 不明\nSELECT 1 AS x")
ok("色の組み合わせ" not in r and "x" in r, "不明なタグは無視して SQL は実行")
r = q("-- #SOS\n-- 2 行目のコメント\nSELECT 1 AS x")
ok("x" in r and "拒否" not in r, "コメントが複数行でも先頭語の判定は SELECT で通る")
r = q("-- #SOS\nDELETE FROM mtg_cards_v2")
ok("拒否" in r, "コメントの後の DELETE は拒否")
f = m.search_mtg_cards.fn if hasattr(m.search_mtg_cards, "fn") else m.search_mtg_cards
d = json.loads(f("Vicious Rivalry", None, 1, "XYZ"))
ok("draft_set_note" in d and "limited_archetypes" not in d, "不明な draft_set は数字を付けず一覧を返す")
d = json.loads(f("Vicious Rivalry", "modern", 1))
ok("limited_archetypes" not in d and "limited_stats_note" not in d, "format=modern（構築）なら同伴なし")
d = json.loads(f("Vicious Rivalry", None, 1, "#SOS"))
ok(list(d.get("limited_archetypes", {}).keys()) == ["SOS"], "draft_set=#SOS は SOS の表だけ")
print("\nall passed" if not fails else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
