#!/usr/bin/env python3
"""mtg_probability と SQL 関数 mtg_* の試験（2026-09-04）。黄金値は math.comb の独立実装（LLM でも DB でもない第三の計算）。"""
import json, os, sys
from math import comb
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import mcp_server as m
fails = 0
def ok(c, label):
    global fails
    print(("ok   " if c else "FAIL ") + label); fails += (not c)
def atleast(n, k, d, mm): return sum(comb(k, i) * comb(n - k, d - i) for i in range(mm, min(k, d) + 1)) / comb(n, d)
f = m.mtg_probability.fn if hasattr(m.mtg_probability, "fn") else m.mtg_probability
def call(**kw): return json.loads(f(**kw))
r = call(kind="at_least", deck_size=60, copies=4, draws=7, at_least=1)
ok(abs(r["probability"] - round(atleast(60, 4, 7, 1), 4)) < 1e-4 and r["percent"] == "40.0%", f"at_least 60/4/7/≥1 = {r['probability']}（黄金 0.3995）")
r = call(kind="by_turn", deck_size=60, copies=4, turn=3, on_play=True)
ok(r["cards_seen"] == 9 and abs(r["probability"] - round(atleast(60, 4, 9, 1), 4)) < 1e-4, f"by_turn 先手 3T: 見る枚数 9・{r['probability']}")
r2 = call(kind="by_turn", deck_size=60, copies=4, turn=3, on_play=False)
ok(r2["cards_seen"] == 10 and r2["probability"] > r["probability"], "後手は 1 枚多く見る＝確率が上がる")
r3 = call(kind="by_turn", deck_size=60, copies=4, turn=3, on_play=True, mulligans=1)
ok(r3["cards_seen"] == 8 and r3["probability"] < r["probability"], "マリガン 1 回は見る枚数 −1")
r = call(kind="land_drops", deck_size=60, copies=24, turn=4, on_play=True)
ok(abs(r["probability"] - round(atleast(60, 24, 10, 4), 4)) < 1e-4, f"land_drops 60/24 先手 4T = {r['probability']}（黄金 {round(atleast(60,24,10,4),4)}）")
r = call(kind="land_drops", deck_size=40, copies=17, turn=3, on_play=False)
ok(abs(r["probability"] - round(atleast(40, 17, 10, 3), 4)) < 1e-4, f"land_drops 40/17 後手 3T = {r['probability']}")
r = call(kind="combo_by_turn", deck_size=60, copies=4, copies_b=4, turn=4, on_play=True)
gold = 1 - (comb(56, 10) + comb(56, 10) - comb(52, 10)) / comb(60, 10)
ok(abs(r["probability"] - round(gold, 4)) < 1e-4, f"combo 4+4 先手 4T = {r['probability']}（黄金 {round(gold,4)}）")
r = call(kind="by_turn", deck_size=60, copies=12, turn=2, on_play=True, at_least=2)
ok(abs(r["probability"] - round(atleast(60, 12, 8, 2), 4)) < 1e-4, f"色ソース 12 枚で 2T に 2 つ = {r['probability']}")
ok("error" in call(kind="foo"), "不明な kind は error")
ok("error" in call(kind="at_least", deck_size=60, copies=70), "範囲外は error")
ok("error" in call(kind="combo_by_turn", deck_size=60, copies=40, copies_b=30, turn=2), "A+B > deck は定義不能の error")
ok(all(k in call(kind="by_turn") for k in ("formula", "premise", "cards_seen", "percent", "note")), "返り値は式・前提・見る枚数・％・注記を持つ")
print("\nall passed" if not fails else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
