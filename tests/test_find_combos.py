#!/usr/bin/env python3
"""find_combos の試験（2026-09-04）。既定は偽の応答（ネットに出ない）。--live で Commander Spellbook を 1 回だけ実呼び。"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m
fails = 0
def ok(c, label):
    global fails
    print(("ok   " if c else "FAIL ") + label); fails += (not c)
f = m.find_combos.fn if hasattr(m.find_combos, "fn") else m.find_combos
FAKE = {"results": {"included": [{"id": "742-1295", "uses": [{"card": {"name": "Demonic Consultation"}}, {"card": {"name": "Thassa's Oracle"}}],
                                  "produces": [{"feature": {"name": "Win the game"}}], "easyPrerequisites": "", "notablePrerequisites": "", "popularity": 148605, "bracketTag": "R"}],
                    "almostIncluded": [{"id": "1295-3093", "uses": [{"card": {"name": "Tainted Pact"}}, {"card": {"name": "Thassa's Oracle"}}],
                                       "produces": [{"feature": {"name": "Win the game"}}], "easyPrerequisites": "No two cards in library share a name.", "popularity": 131648, "bracketTag": "R"}],
                    "almostIncludedByAddingColors": []}}
captured = {}
def fake_post(payload):
    captured["payload"] = payload; return FAKE
m._spellbook_post = fake_post
d = json.loads(f(["タッサの神託者", "Demonic Consultation"], None, 10))
ok(captured["payload"]["main"][0]["card"] == "Thassa's Oracle", f"日本語名を英語名に直して送る（{captured['payload']['main'][0]['card']}）")
ok(d["included"]["count_total"] == 1 and d["included"]["combos"][0]["id"] == "742-1295", "included を id 付きで返す")
_nd = {r[0]: r[1] for r in m._db("SELECT card_name, name_display FROM mtg_cards_v2 WHERE card_name = ANY(%s)", (["Demonic Consultation", "Thassa's Oracle"],))}
ok(d["included"]["combos"][0]["uses_display"] == [_nd["Demonic Consultation"], _nd["Thassa's Oracle"]], f"uses_display は DB の完成形そのまま（{d['included']['combos'][0]['uses_display']}）")
ok(d["almostIncluded"]["combos"][0]["prerequisites"] == "No two cards in library share a name.", "前提の原文をそのまま載せる")
ok(d["included"]["combos"][0]["url"].endswith("/combo/742-1295/"), "出典 URL")
ok("Commander Spellbook" in d["source"] and "前提" in d["note"], "出典と注記")
ok("error" in json.loads(f([], None, 10)), "空は error")
def boom(payload): raise TimeoutError("timed out")
m._spellbook_post = boom
ok("届かない" in json.loads(f(["Thassa's Oracle"], None, 10)).get("error", ""), "届かないときは error（他の道具に影響なし）")
if "--live" in sys.argv:
    m._spellbook_post = m.__dict__["_spellbook_post"] if False else None
    import importlib; importlib.reload(m); f = m.find_combos.fn if hasattr(m.find_combos, "fn") else m.find_combos
    d = json.loads(f(["Thassa's Oracle", "Demonic Consultation"], None, 3))
    ok("error" not in d and d["included"]["count_total"] >= 1, f"実呼び: included {d.get('included', {}).get('count_total')}・あと 1 枚 {d.get('almostIncluded', {}).get('count_total')}")
print("\nall passed" if not fails else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
